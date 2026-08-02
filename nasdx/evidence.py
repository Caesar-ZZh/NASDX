# -*- coding: utf-8 -*-
"""
公告 / 新闻证据层与新鲜度契约（#69）。

本模块提供**确定性**的证据对象与新鲜度语义，是盘中建议信息面的唯一真相源：

- ``EvidenceItem``：标题、发布时间、抓取时间、来源、URL、稳定 ID、内容指纹、
  状态六要素齐备。除 ``summary`` 外的所有字段只能来自确定性解析，
  LLM 摘要不得改写来源、时间、URL 与状态（见 :func:`apply_llm_summary`）。
- **三态区分**：``抓取失败(failed)`` / ``解析失败(parse_failed)`` /
  ``确实没有新增公告(empty)`` 是三个不同状态，任何一种都不能被呈现为
  “没有负面消息”。来源未接入时显式记 ``not_configured``。
- **去重**：同一事件的多次抓取与媒体转载合并为一条主证据，
  ``duplicate_count`` / ``duplicate_sources`` 记录引用数量，
  不会重复增强信号；主证据的 ``published_at`` 取全组最早时间，
  因此“旧闻重发”不会伪装成新鲜信息。
- **时区**：所有时间统一为带时区值（Asia/Shanghai，固定 UTC+8，中国自 1991 年
  起无夏令时），并给出盘前 / 盘中 / 盘后 / 非交易时段标记。
- **TTL 按事件类型配置**：财报与盘中快讯不使用同一过期阈值。
- **缓存失效信号**：:func:`cache_invalidation_targets` 输出应失效的研究维度，
  供 #65 定向失效而不是盲目重跑全部 Agent。
- **动作闸门**：:func:`evidence_gate` 在信息面未核验时把买入 / 加仓降级为
  ``wait`` 或 ``review_required``；持仓风险动作可保守放行但必须带依据。
  社交 / 自媒体来源不能单独触发确定性买入。

可配置项（均可审计，读取时即时生效）：

======================================  ==================================
环境变量                                 含义
======================================  ==================================
``NASDX_EVIDENCE_AUTHORITY``            JSON，覆盖来源类型权威分
``NASDX_EVIDENCE_TTL_MINUTES``          JSON，覆盖事件类型过期分钟数
``NASDX_EVIDENCE_MIN_BUY_AUTHORITY``    浮点，支撑买入所需最低权威分（默认 0.7）
======================================  ==================================

本模块不做网络请求；连接器见 ``nasdx.announcement_sources`` 与
``nasdx.news_sources``。``nasdx.external_review`` 的人工复核入口保留，
人工核验结论可通过 :func:`manual_evidence` 录入为一条带来源和时间的证据。
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import unicodedata
from dataclasses import dataclass, field, replace
from datetime import date, datetime, time as dt_time, timedelta, timezone
from typing import Any, Iterable, Mapping, Sequence

__all__ = [
    "CST",
    "EvidenceItem",
    "EvidenceBundle",
    "SourceFetchResult",
    "GateDecision",
    "authority_table",
    "authority_score",
    "event_ttl_minutes",
    "to_cst",
    "market_session",
    "content_fingerprint",
    "make_evidence_id",
    "build_evidence_item",
    "dedupe_evidence",
    "apply_freshness",
    "build_bundle",
    "cache_invalidation_targets",
    "evidence_gate",
    "manual_evidence",
    "apply_llm_summary",
    "merge_llm_summaries",
    "build_evidence_audit",
]


# --------------------------------------------------------------------------
# 常量与可配置项
# --------------------------------------------------------------------------

#: 中国大陆固定时区（1991 年后无夏令时，使用固定偏移避免依赖 tzdata）
CST = timezone(timedelta(hours=8), "Asia/Shanghai")

SOURCE_TYPES = (
    "exchange",
    "regulator",
    "company",
    "official_media",
    "media",
    "research",
    "social",
    "manual",
)

#: 默认来源优先级：交易所/监管/公司披露 > 官方媒体 > 主流财经媒体 > 研究 > 社交
DEFAULT_AUTHORITY_SCORES: dict[str, float] = {
    "exchange": 1.0,
    "regulator": 1.0,
    "company": 0.95,
    "manual": 0.9,
    "official_media": 0.8,
    "media": 0.6,
    "research": 0.55,
    "social": 0.3,
}

#: 视为“权威披露”的来源类型：只有这些来源的抓取状态决定信息面是否已核验
AUTHORITATIVE_SOURCE_TYPES = frozenset({"exchange", "regulator", "company", "manual"})

EVENT_TYPES = (
    "earnings",
    "order",
    "suspension",
    "regulatory",
    "capital_action",
    "industry",
    "other",
)

#: 事件类型 → 过期阈值（分钟）。财报与盘中快讯显式使用不同 TTL。
DEFAULT_EVENT_TTL_MINUTES: dict[str, int] = {
    "earnings": 7 * 24 * 60,
    "order": 5 * 24 * 60,
    "suspension": 24 * 60,
    "regulatory": 10 * 24 * 60,
    "capital_action": 10 * 24 * 60,
    "industry": 3 * 24 * 60,
    "other": 120,
}

# 证据状态
STATUS_VERIFIED = "verified"
STATUS_UNVERIFIED = "unverified"
STATUS_STALE = "stale"
STATUS_FAILED = "failed"
EVIDENCE_STATUSES = (STATUS_VERIFIED, STATUS_UNVERIFIED, STATUS_STALE, STATUS_FAILED)

# 来源抓取状态（三态 + 未接入）
FETCH_OK = "ok"
FETCH_EMPTY = "empty"
FETCH_FAILED = "failed"
FETCH_PARSE_FAILED = "parse_failed"
FETCH_NOT_CONFIGURED = "not_configured"
FETCH_STATUSES = (
    FETCH_OK,
    FETCH_EMPTY,
    FETCH_FAILED,
    FETCH_PARSE_FAILED,
    FETCH_NOT_CONFIGURED,
)

# 信息面整体状态
STATE_VERIFIED_NEW = "verified_new"
STATE_NO_NEW_VERIFIED = "no_new_verified"
STATE_UNVERIFIED = "unverified"
STATE_UNAVAILABLE = "unavailable"

# 动作枚举（与 #67 IntradayDecision 对齐的子集）
RISK_INCREASING_ACTIONS = frozenset({"buy", "add", "buy_first_lot"})
ACTION_WAIT = "wait"
ACTION_REVIEW_REQUIRED = "review_required"

#: 高风险负面事件：即使自动核验通过也应触发人工复核闸门
HIGH_RISK_EVENT_TYPES = frozenset({"suspension", "regulatory"})

#: 事件类型 → 应定向失效的研究维度（#65 分层缓存）
EVENT_CACHE_DIMENSIONS: dict[str, tuple[str, ...]] = {
    "suspension": ("risk", "technical", "synthesis"),
    "regulatory": ("risk", "synthesis"),
    "earnings": ("fundamental", "risk", "synthesis"),
    "order": ("fundamental", "sector", "synthesis"),
    "capital_action": ("fundamental", "risk", "synthesis"),
    "industry": ("sector", "chokepoint", "synthesis"),
    "other": ("synthesis",),
}

_SUMMARY_MAX = 300
_MORNING_OPEN = dt_time(9, 30)
_AFTERNOON_CLOSE = dt_time(15, 0)


def _load_json_env(name: str) -> dict:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except (TypeError, ValueError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def authority_table() -> dict[str, float]:
    """返回来源类型 → 权威分（默认表叠加 ``NASDX_EVIDENCE_AUTHORITY`` 覆盖）。"""
    table = dict(DEFAULT_AUTHORITY_SCORES)
    for key, value in _load_json_env("NASDX_EVIDENCE_AUTHORITY").items():
        try:
            score = float(value)
        except (TypeError, ValueError):
            continue
        if 0.0 <= score <= 1.0:
            table[str(key)] = score
    return table


def authority_score(source_type: str) -> float:
    """返回来源类型权威分；未知来源按最低可信度 0.1 处理（不静默当成可信）。"""
    return authority_table().get(str(source_type or ""), 0.1)


def ttl_table() -> dict[str, int]:
    """返回事件类型 → 过期分钟数（默认表叠加 ``NASDX_EVIDENCE_TTL_MINUTES``）。"""
    table = dict(DEFAULT_EVENT_TTL_MINUTES)
    for key, value in _load_json_env("NASDX_EVIDENCE_TTL_MINUTES").items():
        try:
            minutes = int(value)
        except (TypeError, ValueError):
            continue
        if minutes > 0:
            table[str(key)] = minutes
    return table


def event_ttl_minutes(event_type: str) -> int:
    """返回事件类型的过期阈值（分钟）；未知事件按 ``other`` 处理。"""
    table = ttl_table()
    return table.get(str(event_type or ""), table.get("other", 120))


def min_buy_authority() -> float:
    """支撑“确定性买入”所需的最低来源权威分（默认 0.7，社交/自媒体不达标）。"""
    raw = os.environ.get("NASDX_EVIDENCE_MIN_BUY_AUTHORITY", "").strip()
    if raw:
        try:
            value = float(raw)
        except ValueError:
            return 0.7
        if 0.0 <= value <= 1.0:
            return value
    return 0.7


# --------------------------------------------------------------------------
# 时间与文本归一化
# --------------------------------------------------------------------------

_ISO_TZ_RE = re.compile(r"(Z|[+-]\d{2}:?\d{2})$")


def to_cst(value: Any) -> datetime | None:
    """把任意时间输入归一化为 Asia/Shanghai 带时区 datetime。

    支持 ``datetime``（naive 视为北京时间）、``date``、epoch 秒 / 毫秒、
    ISO8601 字符串（含 ``Z`` 与偏移量）以及 ``YYYY-MM-DD HH:MM:SS`` 形态。
    无法解析时返回 ``None``，绝不猜测。
    """
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, datetime):
        return value.astimezone(CST) if value.tzinfo else value.replace(tzinfo=CST)
    if isinstance(value, date):
        return datetime.combine(value, dt_time(0, 0), tzinfo=CST)
    if isinstance(value, (int, float)):
        seconds = float(value)
        if seconds > 1e11:  # 毫秒时间戳
            seconds /= 1000.0
        try:
            return datetime.fromtimestamp(seconds, tz=timezone.utc).astimezone(CST)
        except (OverflowError, OSError, ValueError):
            return None
    text = str(value).strip()
    if not text:
        return None
    if text.isdigit() and len(text) >= 10:
        return to_cst(int(text))
    candidate = text.replace("/", "-")
    if candidate.endswith("Z"):
        candidate = candidate[:-1] + "+00:00"
    for parser in (_parse_iso, _parse_common):
        parsed = parser(candidate)
        if parsed is not None:
            return parsed.astimezone(CST) if parsed.tzinfo else parsed.replace(tzinfo=CST)
    return None


def _parse_iso(text: str) -> datetime | None:
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        return None


def _parse_common(text: str) -> datetime | None:
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d", "%Y%m%d%H%M%S", "%Y%m%d"):
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue
    return None


def market_session(moment: Any) -> str:
    """返回时间所处的 A 股时段：``pre_market`` / ``intraday`` / ``post_market`` / ``non_trading``。

    仅按自然周与交易时段判断（不含法定节假日日历），周末统一记为
    ``non_trading``，用于区分盘前、盘中与盘后披露，而不是判定是否可交易。
    """
    moment_cst = to_cst(moment)
    if moment_cst is None:
        return "unknown"
    if moment_cst.weekday() >= 5:
        return "non_trading"
    clock = moment_cst.timetz().replace(tzinfo=None)
    if clock < _MORNING_OPEN:
        return "pre_market"
    if clock >= _AFTERNOON_CLOSE:
        return "post_market"
    return "intraday"


_PUNCT_RE = re.compile(r"[\s\u3000\-—－_·、,，.。;；:：!！?？\"'“”‘’()（）\[\]【】<>《》/\\|~]+")


def normalize_title(text: Any) -> str:
    """标题归一化：NFKC + 去空白标点 + 小写，用于稳定指纹与转载识别。"""
    normalized = unicodedata.normalize("NFKC", str(text or ""))
    normalized = _PUNCT_RE.sub("", normalized)
    return normalized.strip().lower()


def _normalize_url(url: Any) -> str:
    text = str(url or "").strip()
    if not text:
        return ""
    text = re.sub(r"^https?://", "", text, flags=re.IGNORECASE)
    text = re.sub(r"^www\.", "", text, flags=re.IGNORECASE)
    return text.rstrip("/").lower()


def _digest(*parts: Any, length: int = 16) -> str:
    payload = "\x1f".join("" if part is None else str(part) for part in parts)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:length]


def content_fingerprint(
    code: str,
    title: Any,
    published_at: datetime | None,
    *,
    announcement_id: Any = None,
    body: Any = None,
) -> str:
    """构造稳定内容指纹：同一事件的多次抓取与媒体转载得到相同值。

    有官方公告 ID 时以 ``(code, announcement_id)`` 为准（最稳定）；
    否则用 ``(code, 归一化标题, 发布日期, 归一化正文摘要)``，
    使同一天不同来源的转载归并为一条，且不受排版差异影响。
    """
    code_text = str(code or "").strip()
    ann_id = str(announcement_id or "").strip()
    if ann_id:
        return _digest("ann", code_text, ann_id)
    day = published_at.date().isoformat() if isinstance(published_at, datetime) else ""
    return _digest("txt", code_text, normalize_title(title), day, normalize_title(body))


def make_evidence_id(fingerprint: str, source_name: Any, source_url: Any) -> str:
    """单条证据的稳定 ID：同一来源同一事件重复抓取得到相同 ID。"""
    return _digest("ev", fingerprint, str(source_name or "").strip().lower(), _normalize_url(source_url))


# --------------------------------------------------------------------------
# 证据对象
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class EvidenceItem:
    """一条可审计的公告 / 新闻证据。

    除 ``summary`` 外的字段均来自确定性解析，不接受 LLM 改写。
    """

    evidence_id: str
    code: str
    title: str
    published_at: datetime
    fetched_at: datetime
    source_name: str
    source_type: str
    source_url: str
    event_type: str
    authority_score: float
    relevance_score: float
    content_fingerprint: str
    status: str
    summary: str = ""
    session: str = "unknown"
    duplicate_count: int = 1
    duplicate_sources: tuple[str, ...] = ()
    latest_seen_at: datetime | None = None
    notes: tuple[str, ...] = ()

    @property
    def is_authoritative(self) -> bool:
        """来源是否属于交易所 / 监管 / 公司披露 / 人工核验。"""
        return self.source_type in AUTHORITATIVE_SOURCE_TYPES

    def expires_at(self) -> datetime:
        """按事件类型 TTL 计算的过期时间（自**首次发布**起算）。"""
        return self.published_at + timedelta(minutes=event_ttl_minutes(self.event_type))

    def is_expired(self, now: datetime | None = None) -> bool:
        moment = to_cst(now) or datetime.now(CST)
        return moment >= self.expires_at()

    def with_summary(self, summary: Any) -> "EvidenceItem":
        """只替换摘要，其余字段原样保留（LLM 唯一被允许影响的字段）。"""
        text = str(summary or "").strip().replace("\r", " ").replace("\n", " ")
        return replace(self, summary=text[:_SUMMARY_MAX])

    def to_dict(self) -> dict[str, Any]:
        return {
            "evidence_id": self.evidence_id,
            "code": self.code,
            "title": self.title,
            "published_at": self.published_at.isoformat(),
            "fetched_at": self.fetched_at.isoformat(),
            "source_name": self.source_name,
            "source_type": self.source_type,
            "source_url": self.source_url,
            "event_type": self.event_type,
            "authority_score": self.authority_score,
            "relevance_score": self.relevance_score,
            "content_fingerprint": self.content_fingerprint,
            "status": self.status,
            "summary": self.summary,
            "session": self.session,
            "duplicate_count": self.duplicate_count,
            "duplicate_sources": list(self.duplicate_sources),
            "latest_seen_at": self.latest_seen_at.isoformat() if self.latest_seen_at else None,
            "notes": list(self.notes),
        }


def build_evidence_item(
    *,
    code: Any,
    title: Any,
    published_at: Any,
    source_name: Any,
    source_type: str,
    source_url: Any = "",
    event_type: str = "other",
    announcement_id: Any = None,
    body: Any = None,
    summary: Any = "",
    relevance_score: float = 1.0,
    fetched_at: Any = None,
    now: Any = None,
) -> EvidenceItem:
    """把一条原始来源记录归一化为 :class:`EvidenceItem`。

    - 发布时间无法解析 → ``status=failed``（不猜测时间，也不当成新消息）；
    - 发布时间晚于抓取时间（未来时间） → ``status=unverified`` 并记 note；
    - 超过事件 TTL → ``status=stale``；
    - 其余情况：权威来源 ``verified``，非权威来源 ``unverified``。
    """
    moment = to_cst(now) or datetime.now(CST)
    fetched = to_cst(fetched_at) or moment
    published = to_cst(published_at)
    source_type_text = str(source_type or "").strip() or "media"
    notes: list[str] = []

    if published is None:
        notes.append("published_at_unparsable")
        published = fetched
        status = STATUS_FAILED
    elif published > fetched + timedelta(minutes=1):
        notes.append("future_dated")
        status = STATUS_UNVERIFIED
    elif source_type_text in AUTHORITATIVE_SOURCE_TYPES:
        status = STATUS_VERIFIED
    else:
        status = STATUS_UNVERIFIED

    fingerprint = content_fingerprint(
        code, title, published, announcement_id=announcement_id, body=body
    )
    item = EvidenceItem(
        evidence_id=make_evidence_id(fingerprint, source_name, source_url),
        code=str(code or "").strip(),
        title=str(title or "").strip(),
        published_at=published,
        fetched_at=fetched,
        source_name=str(source_name or "").strip(),
        source_type=source_type_text,
        source_url=str(source_url or "").strip(),
        event_type=str(event_type or "other").strip() or "other",
        authority_score=authority_score(source_type_text),
        relevance_score=float(relevance_score),
        content_fingerprint=fingerprint,
        status=status,
        summary=str(summary or "").strip()[:_SUMMARY_MAX],
        session=market_session(published),
        latest_seen_at=published,
        notes=tuple(notes),
    )
    if status != STATUS_FAILED and item.is_expired(moment):
        item = replace(item, status=STATUS_STALE, notes=item.notes + ("expired_by_ttl",))
    return item


def dedupe_evidence(items: Iterable[EvidenceItem]) -> list[EvidenceItem]:
    """按内容指纹合并同一事件的重复抓取与媒体转载。

    - 主证据 = 权威分最高者（并列取最早发布、再并列取 ``evidence_id`` 最小），
      结果与输入顺序无关（确定性）；
    - 主证据 ``published_at`` 取全组**最早**时间，因此“旧闻重发”不会
      刷新新鲜度；``latest_seen_at`` 保留最晚一次出现时间；
    - ``duplicate_count`` / ``duplicate_sources`` 记录引用来源数量，
      供上层判断“转载多≠信号强”。
    """
    groups: dict[str, list[EvidenceItem]] = {}
    for item in items:
        groups.setdefault(item.content_fingerprint, []).append(item)

    merged: list[EvidenceItem] = []
    for group in groups.values():
        ordered = sorted(
            group,
            key=lambda item: (-item.authority_score, item.published_at, item.evidence_id),
        )
        primary = ordered[0]
        earliest = min(item.published_at for item in group)
        latest = max(item.published_at for item in group)
        sources = tuple(sorted({item.source_name for item in group if item.source_name}))
        notes = primary.notes
        if len(group) > 1 and latest > earliest:
            notes = notes + ("republished",)
        merged.append(
            replace(
                primary,
                published_at=earliest,
                latest_seen_at=latest,
                session=market_session(earliest),
                duplicate_count=len(group),
                duplicate_sources=sources,
                notes=notes,
            )
        )
    merged.sort(key=lambda item: (-item.published_at.timestamp(), item.evidence_id))
    return merged


def apply_freshness(items: Iterable[EvidenceItem], now: Any = None) -> list[EvidenceItem]:
    """按事件类型 TTL 重新判定过期状态（合并后 published_at 可能提前）。"""
    moment = to_cst(now) or datetime.now(CST)
    refreshed: list[EvidenceItem] = []
    for item in items:
        if item.status == STATUS_FAILED:
            refreshed.append(item)
            continue
        expired = item.is_expired(moment)
        if expired and item.status != STATUS_STALE:
            notes = item.notes if "expired_by_ttl" in item.notes else item.notes + ("expired_by_ttl",)
            refreshed.append(replace(item, status=STATUS_STALE, notes=notes))
        elif not expired and item.status == STATUS_STALE:
            notes = tuple(note for note in item.notes if note != "expired_by_ttl")
            status = STATUS_VERIFIED if item.is_authoritative else STATUS_UNVERIFIED
            refreshed.append(replace(item, status=status, notes=notes))
        else:
            refreshed.append(item)
    return refreshed


# --------------------------------------------------------------------------
# 来源抓取结果与证据包
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class SourceFetchResult:
    """单个来源一次抓取的结果与状态（三态 + 未接入显式区分）。"""

    source_name: str
    source_type: str
    status: str
    items: tuple[EvidenceItem, ...] = ()
    error: str = ""
    pages_fetched: int = 0
    fetched_at: datetime | None = None

    @property
    def is_authoritative(self) -> bool:
        return self.source_type in AUTHORITATIVE_SOURCE_TYPES

    @property
    def succeeded(self) -> bool:
        """抓取链路是否成功（含“确实没有新增公告”）。"""
        return self.status in (FETCH_OK, FETCH_EMPTY)

    def to_dict(self) -> dict[str, Any]:
        return {
            "source_name": self.source_name,
            "source_type": self.source_type,
            "status": self.status,
            "item_count": len(self.items),
            "error": self.error,
            "pages_fetched": self.pages_fetched,
            "fetched_at": self.fetched_at.isoformat() if self.fetched_at else None,
        }


@dataclass(frozen=True)
class EvidenceBundle:
    """一次信息面采集的完整结果：证据 + 每个来源的抓取状态。"""

    items: tuple[EvidenceItem, ...]
    source_results: tuple[SourceFetchResult, ...]
    generated_at: datetime

    def for_code(self, code: Any = None) -> tuple[EvidenceItem, ...]:
        if code is None:
            return self.items
        target = str(code).strip()
        return tuple(item for item in self.items if item.code == target)

    def fresh_verified(self, code: Any = None) -> tuple[EvidenceItem, ...]:
        return tuple(item for item in self.for_code(code) if item.status == STATUS_VERIFIED)

    def source_fetch_status(self) -> dict[str, str]:
        return {result.source_name: result.status for result in self.source_results}

    def latest_verified_evidence_at(self, code: Any = None) -> datetime | None:
        verified = self.fresh_verified(code)
        if not verified:
            return None
        return max(item.published_at for item in verified)

    def state(self, code: Any = None) -> str:
        """信息面整体状态。

        - ``verified_new``：权威来源抓取成功且存在未过期的已核验证据；
        - ``no_new_verified``：权威来源抓取成功但确实没有新增已核验证据；
        - ``unverified``：存在解析失败，或只有低权威 / 过期证据；
        - ``unavailable``：没有任何权威来源成功（失败或未接入）。
        """
        authoritative = [r for r in self.source_results if r.is_authoritative]
        if not authoritative or not any(r.succeeded for r in authoritative):
            return STATE_UNAVAILABLE
        if any(r.status == FETCH_PARSE_FAILED for r in self.source_results):
            return STATE_UNVERIFIED
        if self.fresh_verified(code):
            return STATE_VERIFIED_NEW
        if any(item.status == STATUS_UNVERIFIED for item in self.for_code(code)):
            return STATE_UNVERIFIED
        return STATE_NO_NEW_VERIFIED

    def to_dict(self, code: Any = None) -> dict[str, Any]:
        latest = self.latest_verified_evidence_at(code)
        return {
            "generated_at": self.generated_at.isoformat(),
            "state": self.state(code),
            "latest_verified_evidence_at": latest.isoformat() if latest else None,
            "source_fetch_status": self.source_fetch_status(),
            "items": [item.to_dict() for item in self.for_code(code)],
        }


def build_bundle(
    results: Sequence[SourceFetchResult],
    *,
    now: Any = None,
) -> EvidenceBundle:
    """汇总多来源抓取结果：跨来源去重 + 统一新鲜度判定。"""
    moment = to_cst(now) or datetime.now(CST)
    raw: list[EvidenceItem] = []
    for result in results:
        raw.extend(result.items)
    merged = apply_freshness(dedupe_evidence(raw), moment)
    return EvidenceBundle(
        items=tuple(merged),
        source_results=tuple(results),
        generated_at=moment,
    )


# --------------------------------------------------------------------------
# 缓存失效信号（供 #65 定向失效）
# --------------------------------------------------------------------------


def cache_invalidation_targets(
    items: Iterable[EvidenceItem],
    *,
    since: Any = None,
    now: Any = None,
) -> dict[str, list[str]]:
    """返回 ``{股票代码: [应失效的研究维度]}``。

    只有**未过期且已核验**的权威证据才会触发失效；``since`` 给定时仅考虑
    在该时间之后发布的新事件，避免每轮重复失效同一条旧公告。
    结果按维度名排序，保证确定性。
    """
    threshold = to_cst(since)
    moment = to_cst(now) or datetime.now(CST)
    targets: dict[str, set[str]] = {}
    for item in items:
        if item.status != STATUS_VERIFIED or not item.is_authoritative:
            continue
        if item.is_expired(moment):
            continue
        if threshold is not None and item.published_at <= threshold:
            continue
        dims = EVENT_CACHE_DIMENSIONS.get(item.event_type, EVENT_CACHE_DIMENSIONS["other"])
        targets.setdefault(item.code, set()).update(dims)
    return {code: sorted(dims) for code, dims in sorted(targets.items())}


# --------------------------------------------------------------------------
# 动作闸门
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class GateDecision:
    """信息面闸门结论：是否降级、为什么降级、依据什么状态。"""

    action: str
    original_action: str
    downgraded: bool
    reason: str
    evidence_state: str
    latest_verified_evidence_at: datetime | None
    source_fetch_status: dict[str, str] = field(default_factory=dict)
    blocking_evidence_ids: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "action": self.action,
            "original_action": self.original_action,
            "downgraded": self.downgraded,
            "reason": self.reason,
            "evidence_state": self.evidence_state,
            "latest_verified_evidence_at": (
                self.latest_verified_evidence_at.isoformat()
                if self.latest_verified_evidence_at
                else None
            ),
            "source_fetch_status": dict(self.source_fetch_status),
            "blocking_evidence_ids": list(self.blocking_evidence_ids),
        }


def evidence_gate(
    action: str,
    bundle: EvidenceBundle,
    *,
    code: Any = None,
    supporting_evidence_ids: Sequence[str] | None = None,
    now: Any = None,
) -> GateDecision:
    """信息面未核验时降级买入动作，风险动作保守放行。

    规则（确定性，LLM 不可绕过）：

    1. 存在未过期的高风险负面已核验事件（停复牌 / 监管） → ``review_required``；
    2. 买入 / 加仓类动作：

       - 信息面 ``unavailable``（权威源全部失败或未接入） → ``wait``；
       - 支撑证据全部低于 ``min_buy_authority()``（如仅社交媒体） → ``review_required``，
         并在 ``blocking_evidence_ids`` 中列出不合格证据；
       - 信息面 ``unverified``（解析失败或仅有低权威 / 过期信息） → ``review_required``；

    3. 其余动作（持有 / 减仓 / 止盈 / 退出 / 不追高 / 等待）原样放行，
       但 ``reason`` 必须写明信息面状态与依据。
    """
    moment = to_cst(now) or datetime.now(CST)
    original = str(action or "").strip()
    state = bundle.state(code)
    latest = bundle.latest_verified_evidence_at(code)
    statuses = bundle.source_fetch_status()
    scoped = bundle.for_code(code)

    high_risk = tuple(
        item.evidence_id
        for item in scoped
        if item.status == STATUS_VERIFIED
        and item.event_type in HIGH_RISK_EVENT_TYPES
        and not item.is_expired(moment)
    )
    if high_risk:
        return GateDecision(
            action=ACTION_REVIEW_REQUIRED,
            original_action=original,
            downgraded=original != ACTION_REVIEW_REQUIRED,
            reason="存在已核验的高风险事件（停复牌 / 监管），需人工复核后再执行",
            evidence_state=state,
            latest_verified_evidence_at=latest,
            source_fetch_status=statuses,
            blocking_evidence_ids=high_risk,
        )

    if original not in RISK_INCREASING_ACTIONS:
        return GateDecision(
            action=original,
            original_action=original,
            downgraded=False,
            reason=f"信息面状态 {state}；风险控制类动作在信息未完整核验时仍可保守执行",
            evidence_state=state,
            latest_verified_evidence_at=latest,
            source_fetch_status=statuses,
        )

    if state == STATE_UNAVAILABLE:
        return GateDecision(
            action=ACTION_WAIT,
            original_action=original,
            downgraded=True,
            reason="权威公告来源抓取失败或未接入，信息面未知不等于没有负面消息",
            evidence_state=state,
            latest_verified_evidence_at=latest,
            source_fetch_status=statuses,
        )
    # 先判定“支撑证据权威分不足”，再落到宽泛的 unverified 分支：
    # 两者结论同为 review_required，但前者能明确指出是哪几条证据不够格
    # （blocking_evidence_ids），否则会被 unverified 提前返回吞掉可追溯信息。
    if supporting_evidence_ids:
        wanted = {str(value) for value in supporting_evidence_ids}
        supporting = [item for item in scoped if item.evidence_id in wanted]
        threshold = min_buy_authority()
        if supporting and all(item.authority_score < threshold for item in supporting):
            return GateDecision(
                action=ACTION_REVIEW_REQUIRED,
                original_action=original,
                downgraded=True,
                reason=(
                    f"支撑证据权威分均低于 {threshold:g}（社交 / 自媒体不能单独触发确定性买入）"
                ),
                evidence_state=state,
                latest_verified_evidence_at=latest,
                source_fetch_status=statuses,
                blocking_evidence_ids=tuple(sorted(item.evidence_id for item in supporting)),
            )

    if state == STATE_UNVERIFIED:
        return GateDecision(
            action=ACTION_REVIEW_REQUIRED,
            original_action=original,
            downgraded=True,
            reason="存在解析失败或仅有未核验 / 过期信息，买入动作需人工复核",
            evidence_state=state,
            latest_verified_evidence_at=latest,
            source_fetch_status=statuses,
        )

    return GateDecision(
        action=original,
        original_action=original,
        downgraded=False,
        reason=f"信息面状态 {state}，权威来源核验通过",
        evidence_state=state,
        latest_verified_evidence_at=latest,
        source_fetch_status=statuses,
    )


# --------------------------------------------------------------------------
# 人工证据录入 / LLM 摘要边界 / 审计
# --------------------------------------------------------------------------


def manual_evidence(
    *,
    code: Any,
    title: Any,
    source_name: Any,
    source_url: Any = "",
    published_at: Any = None,
    event_type: str = "other",
    summary: Any = "",
    reviewer: Any = "",
    now: Any = None,
) -> EvidenceItem:
    """把人工复核结论录入为一条带来源与时间的证据（``source_type='manual'``）。

    人工核验被视为权威来源，但仍保留复核人标记，可追溯是谁在什么时间核验。
    """
    moment = to_cst(now) or datetime.now(CST)
    item = build_evidence_item(
        code=code,
        title=title,
        published_at=published_at if published_at is not None else moment,
        source_name=source_name,
        source_type="manual",
        source_url=source_url,
        event_type=event_type,
        summary=summary,
        fetched_at=moment,
        now=moment,
    )
    reviewer_text = str(reviewer or "").strip()
    if reviewer_text:
        item = replace(item, notes=item.notes + (f"reviewer:{reviewer_text}",))
    return item


def apply_llm_summary(item: EvidenceItem, summary: Any) -> EvidenceItem:
    """只允许 LLM 写入 ``summary``；来源、时间、URL、状态、指纹一律不可改。"""
    return item.with_summary(summary)


def merge_llm_summaries(
    items: Iterable[EvidenceItem],
    summaries: Mapping[str, Any],
) -> list[EvidenceItem]:
    """按 ``evidence_id`` 批量写入摘要；未知 ID 一律忽略，不新增证据。"""
    lookup = {str(key): value for key, value in (summaries or {}).items()}
    return [
        apply_llm_summary(item, lookup[item.evidence_id]) if item.evidence_id in lookup else item
        for item in items
    ]


def build_evidence_audit(
    bundle: EvidenceBundle,
    *,
    code: Any = None,
    used_evidence_ids: Sequence[str] | None = None,
    ignored: Mapping[str, str] | Sequence[Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    """构造决策记录中的证据审计块。

    输出四类可区分的信息面结论：已核验并纳入判断、找到但未核验、
    来源未接入需人工复核、本轮无新增已核验证据。
    """
    scoped = bundle.for_code(code)
    known_ids = {item.evidence_id for item in scoped}
    used = [value for value in dict.fromkeys(str(v) for v in (used_evidence_ids or [])) if value in known_ids]

    ignored_records: list[dict[str, str]] = []
    if isinstance(ignored, Mapping):
        ignored_records = [
            {"evidence_id": str(key), "reason": str(value)} for key, value in ignored.items()
        ]
    elif ignored:
        for entry in ignored:
            ignored_records.append(
                {
                    "evidence_id": str(entry.get("evidence_id", "")),
                    "reason": str(entry.get("reason", "")),
                }
            )
    used_set = set(used)
    for item in scoped:
        if item.evidence_id in used_set:
            continue
        if any(record["evidence_id"] == item.evidence_id for record in ignored_records):
            continue
        ignored_records.append(
            {"evidence_id": item.evidence_id, "reason": f"not_used:{item.status}"}
        )

    latest = bundle.latest_verified_evidence_at(code)
    statuses = bundle.source_fetch_status()
    return {
        "evidence_state": bundle.state(code),
        "used_evidence_ids": used,
        "ignored_evidence_ids": sorted(ignored_records, key=lambda record: record["evidence_id"]),
        "latest_verified_evidence_at": latest.isoformat() if latest else None,
        "source_fetch_status": statuses,
        "verified_and_used": len(used),
        "found_but_unverified": sum(
            1 for item in scoped if item.status in (STATUS_UNVERIFIED, STATUS_FAILED)
        ),
        "stale_evidence": sum(1 for item in scoped if item.status == STATUS_STALE),
        "sources_requiring_manual_review": sorted(
            name
            for name, status in statuses.items()
            if status in (FETCH_NOT_CONFIGURED, FETCH_FAILED, FETCH_PARSE_FAILED)
        ),
        "no_new_verified_evidence": bundle.state(code) == STATE_NO_NEW_VERIFIED,
    }
