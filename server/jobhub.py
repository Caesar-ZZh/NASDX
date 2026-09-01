"""后台任务中心 —— 把慢分析从 HTTP 连接里摘出来。

要解决的问题
------------
深度分析（30-120s）和多空辩论（100-180s）原本是「一条同步请求 = 一次分析」：
前端一点开始就只能干等，切走页面组件卸载、结果被丢弃，回来还得重跑。

现在的模型
----------
提交任务 → 立刻拿 ``job_id``；任务在线程池里跑，把过程事件累积到内存；
前端随时用 ``job_id`` 轮询（支持 cursor 增量取事件）。切页面、刷新浏览器、
甚至重开标签页都不影响——只要服务还在，任务和结果就还在。

刻意保持简单
------------
单进程内存态，不做持久化、不做跨进程队列。本机桌面 / 单人自托管是唯一目标
场景，上 Redis/Celery 在这个规模是过度设计，只会多一个要运维的组件。

runner 协议
-----------
``runner(handle)``，handle 提供 ``publish(event)`` / ``progress(message, **extra)``
/ ``cancelled``。runner 可以：

1. 返回一个值 → 该值作为 ``job.result``；
2. 是一个生成器，yield 事件 dict → hub 逐条 publish（辩论走这条）。

生成器 runner 里 ``{"type": "done"}`` 会作为 result 保存；
``{"type": "error"}`` 带 ``stage`` 视为单角色失败（不致命），不带 ``stage`` 视为整场失败。
"""
from __future__ import annotations

import inspect
import threading
import time
import traceback
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Any, Callable, Iterator

PENDING = "pending"
RUNNING = "running"
DONE = "done"
ERROR = "error"
CANCELLED = "cancelled"

TERMINAL_STATUSES = frozenset({DONE, ERROR, CANCELLED})

#: 事件日志上限。辩论的 delta 很碎（上千条），不封顶会白占内存。
#: 超限丢最旧的，前端拿到的 cursor 会失效——届时服务端回 ``replay: true``
#: 让前端全量重放，不会静默丢事件。
MAX_EVENTS = 5000

#: 终态任务保留时长。给「切走逛一圈再回来」留足窗口，同时不让内存无限涨。
RESULT_TTL_SECONDS = 3600


def _default_max_workers() -> int:
    import os

    raw = os.environ.get("NASDX_JOB_MAX_WORKERS", "").strip()
    if raw:
        try:
            return max(1, min(int(raw), 16))
        except ValueError:
            pass
    return 3


@dataclass
class Job:
    id: str
    kind: str
    params: dict = field(default_factory=dict)
    title: str = ""
    status: str = PENDING
    progress: dict = field(default_factory=dict)
    events: list = field(default_factory=list)
    #: 已丢弃的最旧事件数，cursor 是相对「原始事件流」的绝对下标
    dropped_events: int = 0
    result: Any = None
    error: str = ""
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    started_at: float | None = None
    finished_at: float | None = None
    cancel_event: threading.Event = field(default_factory=threading.Event)

    @property
    def elapsed(self) -> float:
        return max(0.0, round((self.finished_at or time.time()) - self.created_at, 1))

    @property
    def done(self) -> bool:
        return self.status in TERMINAL_STATUSES


class JobHandle:
    """传给 runner 的句柄：只暴露「发事件 / 报进度 / 问是否取消」。

    刻意不给 runner 直接改 status 的能力——终态只能由 hub 判定，
    否则 runner 漏处理异常就会留下永远 running 的僵尸任务。
    """

    def __init__(self, job: Job, lock: threading.Lock) -> None:
        self._job = job
        self._lock = lock

    @property
    def cancelled(self) -> bool:
        return self._job.cancel_event.is_set()

    def publish(self, event: dict) -> None:
        if not isinstance(event, dict):
            return
        with self._lock:
            job = self._job
            job.events.append(event)
            job.updated_at = time.time()
            # status 事件同时镜像进 progress，前端不用为了看一句话去翻事件流
            if event.get("type") == "status" and event.get("message"):
                job.progress = {**job.progress, "message": str(event["message"])}
            if len(job.events) > MAX_EVENTS:
                drop = len(job.events) - MAX_EVENTS
                del job.events[:drop]
                job.dropped_events += drop

    def progress(self, message: str, **extra: Any) -> None:
        with self._lock:
            self._job.progress = {**self._job.progress, "message": message, **extra}
            self._job.updated_at = time.time()


class JobHub:
    """单进程任务中心。线程安全，可直接被 HTTP 层并发调用。"""

    def __init__(self, max_workers: int | None = None, ttl: float = RESULT_TTL_SECONDS) -> None:
        self._lock = threading.RLock()
        self._jobs: dict[str, Job] = {}
        self._ttl = ttl
        self._max_workers = max_workers if max_workers is not None else _default_max_workers()
        self._executor = ThreadPoolExecutor(
            max_workers=self._max_workers, thread_name_prefix="nasdx-job"
        )
        self._counter = 0

    # -- 提交 -------------------------------------------------------------
    def submit(
        self,
        kind: str,
        runner: Callable[[JobHandle], Any],
        params: dict | None = None,
        title: str = "",
    ) -> Job:
        job = Job(id=self._new_id(kind), kind=kind, params=dict(params or {}), title=title)
        with self._lock:
            self._jobs[job.id] = job
            self._reap_locked()
        self._executor.submit(self._run, job, runner)
        return job

    def _new_id(self, kind: str) -> str:
        self._counter += 1
        return f"{kind}-{self._counter:04d}-{uuid.uuid4().hex[:8]}"

    # -- 执行 -------------------------------------------------------------
    def _run(self, job: Job, runner: Callable[[JobHandle], Any]) -> None:
        handle = JobHandle(job, self._lock)
        with self._lock:
            job.status = RUNNING
            job.started_at = time.time()
            job.updated_at = time.time()
        try:
            outcome = runner(handle)
            if inspect.isgenerator(outcome):
                self._drain_generator(job, handle, outcome)
            elif job.status == RUNNING:
                job.result = outcome
                job.status = CANCELLED if handle.cancelled else DONE
        except Exception as exc:  # noqa: BLE001 — runner 的任何异常都要落成终态
            traceback.print_exc()
            job.status = ERROR
            job.error = _safe_error(exc)
        finally:
            with self._lock:
                job.finished_at = time.time()
                job.updated_at = time.time()
                if job.status == RUNNING:  # runner 提前 return 但没置终态
                    job.status = CANCELLED if handle.cancelled else DONE

    def _drain_generator(self, job: Job, handle: JobHandle, gen: Iterator[dict]) -> None:
        try:
            for event in gen:
                if handle.cancelled:
                    break
                handle.publish(event)
                etype = event.get("type")
                if etype == "done":
                    job.result = event
                elif etype == "error" and not event.get("stage"):
                    # 不带 stage 的 error = 整场失败（如「取不到任何客观数据」）
                    job.error = str(event.get("message", "") or "辩论失败")
        finally:
            # 主动 close：让生成器里的 finally / 上下文管理器正常收尾，
            # 否则中止时上游 HTTP 连接会挂到超时。
            close = getattr(gen, "close", None)
            if callable(close):
                try:
                    close()
                except Exception:  # noqa: BLE001 — 收尾失败不影响主流程
                    pass
        with self._lock:
            if job.status != RUNNING:
                return
            if handle.cancelled:
                job.status = CANCELLED
            elif job.error:
                job.status = ERROR
            else:
                job.status = DONE

    # -- 查询 -------------------------------------------------------------
    def snapshot(self, job_id: str, cursor: int = 0) -> dict | None:
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                return None
            base = job.dropped_events
            idx = cursor - base
            replay = idx < 0 or idx > len(job.events)
            if replay:
                idx = 0
            events = list(job.events[idx:])
            return {
                "id": job.id,
                "kind": job.kind,
                "title": job.title,
                "params": job.params,
                "status": job.status,
                "progress": dict(job.progress),
                "events": events,
                "cursor": base + len(job.events),
                "replay": replay,
                "result": job.result,
                "error": job.error,
                "created_at": job.created_at,
                "updated_at": job.updated_at,
                "elapsed": job.elapsed,
            }

    def get(self, job_id: str) -> Job | None:
        with self._lock:
            return self._jobs.get(job_id)

    def cancel(self, job_id: str) -> bool:
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None or job.done:
                return False
            job.cancel_event.set()
            job.updated_at = time.time()
            return True

    def stats(self) -> dict:
        with self._lock:
            by_status: dict[str, int] = {}
            for job in self._jobs.values():
                by_status[job.status] = by_status.get(job.status, 0) + 1
            return {
                "total": len(self._jobs),
                "by_status": by_status,
                "max_workers": self._max_workers,
                "ttl_seconds": self._ttl,
            }

    # -- 回收 -------------------------------------------------------------
    def _reap_locked(self) -> None:
        """清掉超过 TTL 的终态任务。调用方需已持锁。"""
        now = time.time()
        stale = [
            jid
            for jid, job in self._jobs.items()
            if job.done and job.finished_at is not None and now - job.finished_at > self._ttl
        ]
        for jid in stale:
            self._jobs.pop(jid, None)

    def reap(self) -> int:
        """公开给测试 / 运维调用：立即回收一次，返回清理掉的任务数。"""
        with self._lock:
            before = len(self._jobs)
            self._reap_locked()
            return before - len(self._jobs)


def _safe_error(exc: Exception) -> str:
    """异常文案入日志可以，但别把上游响应体（可能含 key / 内网地址）整段回给前端。"""
    text = str(exc).strip()
    if not text:
        return exc.__class__.__name__
    return text if len(text) <= 300 else text[:300] + "…"


#: 进程级单例。HTTP 层与测试都用它；测试要干净状态就调 hub.reap() 或直接换实例。
hub = JobHub()
