// 后台任务 hook —— 让慢分析「点了就能走，回来就完事」。
//
// 关键设计：job_id 存 localStorage，轮询挂在 hook 上而不是组件里。
// 于是三种情况都能续上：
//   1. 切到别的页面（组件卸载）→ 回来重新 mount，从 localStorage 恢复轮询；
//   2. 刷新浏览器 → 同上；
//   3. 任务早就跑完了 → 首次轮询直接拿到 result，不再等待。
// 任务本身在服务端线程池里跑，跟前端连不连着毫无关系。

import { useCallback, useEffect, useRef, useState } from "react";
import { api, type JobEvent, type JobSnapshot, type JobStatus } from "@/lib/api";

const STORAGE_PREFIX = "nasdx-job:";

function readStored(storageKey: string): string | null {
  try {
    return localStorage.getItem(STORAGE_PREFIX + storageKey);
  } catch {
    return null; // 隐私模式下 localStorage 不可用：退化为「刷新即丢失」，不影响主流程
  }
}

function writeStored(storageKey: string, jobId: string | null) {
  try {
    const key = STORAGE_PREFIX + storageKey;
    if (jobId) localStorage.setItem(key, jobId);
    else localStorage.removeItem(key);
  } catch {
    /* 同上 */
  }
}

export interface UseBackgroundJobOptions {
  /** localStorage 的 key。同一页面同一类任务共用一个槽位（如 "deep-analysis"）。 */
  storageKey: string;
  /** 提交任务，返回 job_id。 */
  start: () => Promise<string>;
  /** 收到增量事件（只传本次轮询拿到的新事件）。 */
  onEvent?: (ev: JobEvent) => void;
  /** 服务端要求全量重放（游标失效）时回调，前端应清空本地累积的事件状态。 */
  onReplay?: () => void;
  /** 任务跑完。 */
  onDone?: (result: unknown, snap: JobSnapshot) => void;
  /** 任务失败/取消。 */
  onError?: (message: string, snap: JobSnapshot) => void;
  /** 轮询间隔，默认 1500ms。 */
  pollMs?: number;
}

export interface BackgroundJobState {
  jobId: string | null;
  status: JobStatus | "idle";
  progress: { message?: string; step?: number; total?: number };
  result: unknown;
  error: string;
  elapsed: number;
  /** 是否正在跟一个未完成任务（含刚提交还没回包） */
  busy: boolean;
}

export function useBackgroundJob(options: UseBackgroundJobOptions) {
  const { storageKey, start, onEvent, onReplay, onDone, onError, pollMs = 1500 } = options;

  const [state, setState] = useState<BackgroundJobState>({
    jobId: null,
    status: "idle",
    progress: {},
    result: null,
    error: "",
    elapsed: 0,
    busy: false,
  });

  // 轮询游标 + 是否已有轮询在跑：用 ref 而非 state，避免 StrictMode 双挂载
  // 或快速切页时重复起轮询循环。
  const cursorRef = useRef(0);
  const pollingRef = useRef(false);
  const aliveRef = useRef(true);
  const timerRef = useRef<number | null>(null);

  // 回调放 ref：调用方每次渲染都传新函数也不会重启轮询。
  const handlersRef = useRef({ onEvent, onReplay, onDone, onError, start });
  handlersRef.current = { onEvent, onReplay, onDone, onError, start };

  const applySnapshot = useCallback((snap: JobSnapshot) => {
    if (snap.replay) {
      cursorRef.current = 0;
      handlersRef.current.onReplay?.();
    }
    if (snap.events?.length) {
      for (const ev of snap.events) handlersRef.current.onEvent?.(ev);
    }
    cursorRef.current = snap.cursor ?? cursorRef.current;

    setState({
      jobId: snap.id,
      status: snap.status,
      progress: snap.progress ?? {},
      result: snap.result ?? null,
      error: snap.error ?? "",
      elapsed: snap.elapsed ?? 0,
      busy: snap.status === "pending" || snap.status === "running",
    });

    if (snap.status === "done") handlersRef.current.onDone?.(snap.result, snap);
    else if (snap.status === "error" || snap.status === "cancelled") {
      handlersRef.current.onError?.(
        snap.status === "cancelled" ? "已中止" : snap.error || "任务失败",
        snap,
      );
    }
  }, []);

  /** 轮询直到终态。重复调用安全（pollingRef 去重）。 */
  const poll = useCallback(
    async (jobId: string) => {
      if (pollingRef.current) return;
      pollingRef.current = true;

      try {
        while (aliveRef.current) {
          let snap: JobSnapshot;
          try {
            snap = await api.getJob(jobId, cursorRef.current);
          } catch {
            // 网络抖动 / 后端重启：不要立刻判死，退避后重试；
            // 只有后端明确回 404（任务没了）才放弃。
            if (!aliveRef.current) return;
            await new Promise((r) => setTimeout(r, pollMs));
            let probeOk = true;
            try {
              await api.getJob(jobId, 0);
            } catch {
              probeOk = false;
            }
            if (!probeOk) {
              // 连续两次都拿不到：大概率后端挂了或任务已过期
              writeStored(storageKey, null);
              setState((s) => ({ ...s, status: "error", error: "连不上后端，任务状态未知", busy: false }));
              return;
            }
            continue;
          }

          if (!aliveRef.current) return;
          applySnapshot(snap);

          if (snap.status === "done" || snap.status === "error" || snap.status === "cancelled") {
            // 终态：清掉 localStorage 槽位，下次进来是干净状态
            writeStored(storageKey, null);
            return;
          }
          await new Promise((r) => setTimeout(r, pollMs));
        }
      } finally {
        pollingRef.current = false;
      }
    },
    [applySnapshot, pollMs, storageKey],
  );

  // 挂载时恢复：localStorage 里还有 job_id 就直接续上。
  useEffect(() => {
    aliveRef.current = true;
    const stored = readStored(storageKey);
    if (stored) {
      cursorRef.current = 0;
      setState((s) => ({ ...s, jobId: stored, status: "running", busy: true }));
      void poll(stored);
    }
    return () => {
      aliveRef.current = false;
      if (timerRef.current) window.clearTimeout(timerRef.current);
    };
  }, [storageKey, poll]);

  const submit = useCallback(async () => {
    // 先把旧的清干净：换标的重跑时不能让上一轮的事件混进来
    writeStored(storageKey, null);
    cursorRef.current = 0;
    setState({
      jobId: null,
      status: "running",
      progress: {},
      result: null,
      error: "",
      elapsed: 0,
      busy: true,
    });
    try {
      const jobId = await handlersRef.current.start();
      writeStored(storageKey, jobId);
      setState((s) => ({ ...s, jobId }));
      void poll(jobId);
      return jobId;
    } catch (e) {
      const message = e instanceof Error ? e.message : String(e);
      setState((s) => ({ ...s, status: "error", error: message, busy: false }));
      throw e;
    }
  }, [poll, storageKey]);

  const cancel = useCallback(async () => {
    const id = state.jobId;
    if (!id) return;
    try {
      await api.cancelJob(id);
    } catch {
      /* 取消失败就让轮询继续，用户还能再点一次 */
    }
  }, [state.jobId]);

  /** 清空本地记录（任务已经消费完，或用户想重来）。 */
  const clear = useCallback(() => {
    writeStored(storageKey, null);
    cursorRef.current = 0;
    setState({
      jobId: null,
      status: "idle",
      progress: {},
      result: null,
      error: "",
      elapsed: 0,
      busy: false,
    });
  }, [storageKey]);

  // 已完成的任务也留在 state 里，但把槽位清掉：
  // 这样刷新页面后是「干净状态」而不是又去拉一个已经消费过的旧结果。
  useEffect(() => {
    if (state.status === "done") writeStored(storageKey, null);
  }, [state.status, storageKey]);

  return { ...state, submit, cancel, clear };
}
