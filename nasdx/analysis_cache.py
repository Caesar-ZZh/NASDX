"""Auditable analysis snapshots and layered cache invalidation (#65).

The multi-agent pipeline (Research -> Battle -> Synthesis) costs 14+ LLM calls
per run. Re-running everything for an intraday refresh is slow, expensive and
introduces conclusion drift on unchanged inputs. This module provides the cache
substrate that makes an incremental path possible **without** silently reusing
stale conclusions.

Design contract (``nasdx_analysis_cache.v1``)
---------------------------------------------
* **Identity (hard key, file name).** Fields that must never be mixed inside one
  snapshot file: stock code, provider, model, prompt/schema version, agent
  config version and the cache schema version. Any change yields a different
  file, so an old snapshot can never be served for a new model or prompt.
* **Invalidation inputs (soft, per dimension).** Fields that change constantly
  and only invalidate the dimensions that actually depend on them:
  ``price_fingerprint``, ``sector_fingerprint``, ``fundamental_fingerprint``,
  ``risk_profile``, ``portfolio_snapshot_hash`` and ``trading_day``.
* **Per-dimension TTL.** Fast dimensions expire in minutes, slow industry logic
  lives a trading day. TTLs are overridable per dimension through
  ``NASDX_ANALYSIS_TTL_<DIMENSION>`` (seconds).
* **Fail-open to a full run.** A missing, unreadable, corrupt, foreign-identity
  or schema-mismatched snapshot is a *miss with a reason*, never a silent reuse.

Nothing in this module calls an LLM or the network; it is pure bookkeeping so
the invalidation rules stay unit-testable.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import re
import tempfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, Mapping, Optional, Sequence, Tuple

from nasdx.paths import get_analysis_cache_dir

CACHE_CONTRACT = "nasdx_analysis_cache.v1"
CACHE_SCHEMA_VERSION = "1"
#: Bump when the structured-output contract sent to research agents changes.
PROMPT_SCHEMA_VERSION = "research_structured.v1"
#: Bump when the agent roster / their configuration semantics change.
AGENT_CONFIG_VERSION = "agents.v1"

DEPTH_FULL = "full"
DEPTH_INTRADAY = "intraday"
DEPTH_REFRESH = "refresh"
DEPTHS: Tuple[str, ...] = (DEPTH_FULL, DEPTH_INTRADAY, DEPTH_REFRESH)

RESEARCH_DIMENSIONS: Tuple[str, ...] = (
    "technical",
    "fund_flow",
    "risk",
    "sector",
    "chokepoint",
)

#: Dimensions an intraday pass is allowed to recompute (fast, market driven).
INTRADAY_REFRESHABLE: Tuple[str, ...] = ("technical", "fund_flow", "risk", "sector")

MISS_MISSING = "missing"
MISS_UNREADABLE = "unreadable"
MISS_CORRUPT = "corrupt"
MISS_SCHEMA = "schema_version_mismatch"
MISS_IDENTITY = "identity_mismatch"
HIT = "hit"


class AnalysisCacheError(RuntimeError):
    """Raised when a snapshot cannot be persisted."""


@dataclass(frozen=True)
class DimensionPolicy:
    """Freshness rule for one research dimension."""

    name: str
    ttl_seconds: float
    inputs: Tuple[str, ...]


DIMENSION_POLICIES: Dict[str, DimensionPolicy] = {
    "technical": DimensionPolicy("technical", 300.0, ("price_fingerprint", "trading_day")),
    "fund_flow": DimensionPolicy("fund_flow", 300.0, ("price_fingerprint", "trading_day")),
    "risk": DimensionPolicy(
        "risk",
        900.0,
        ("price_fingerprint", "risk_profile", "portfolio_snapshot_hash", "trading_day"),
    ),
    "sector": DimensionPolicy("sector", 1800.0, ("sector_fingerprint", "trading_day")),
    "chokepoint": DimensionPolicy(
        "chokepoint", 14400.0, ("fundamental_fingerprint", "trading_day")
    ),
}

INVALIDATION_INPUT_KEYS: Tuple[str, ...] = (
    "price_fingerprint",
    "sector_fingerprint",
    "fundamental_fingerprint",
    "risk_profile",
    "portfolio_snapshot_hash",
    "trading_day",
)

_PRICE_FIELDS = (
    "price",
    "current_price",
    "close",
    "open",
    "high",
    "low",
    "pre_close",
    "change",
    "change_pct",
    "pct_chg",
    "volume",
    "amount",
    "turnover",
    "turnover_rate",
    "bid",
    "ask",
    "ma5",
    "ma10",
    "ma20",
    "ma60",
    "macd",
    "rsi",
    "kdj_k",
    "kdj_d",
    "boll_upper",
    "boll_lower",
    "main_net_inflow",
    "super_large_net",
    "large_net",
    "fund_flow",
    "indicators",
)

_FUNDAMENTAL_FIELDS = (
    "name",
    "industry",
    "industry_name",
    "concepts",
    "tags",
    "pe",
    "pe_ttm",
    "pb",
    "market_cap",
    "total_market_cap",
    "circulating_market_cap",
    "total_share",
    "float_share",
    "listing_date",
    "chokepoint",
    "supply_chain",
    "business",
)

_SECTOR_FIELDS = (
    "sector_name",
    "sector",
    "sector_signal",
    "sector_change_pct",
    "sector_rank",
    "sector_strength",
    "board",
)


# ---------------------------------------------------------------------------
# fingerprints
# ---------------------------------------------------------------------------
def _stable_json(payload: Any) -> str:
    def _default(value: Any) -> Any:
        if isinstance(value, float):
            return "nan" if math.isnan(value) else str(value)
        return str(value)

    return json.dumps(payload, ensure_ascii=False, sort_keys=True, default=_default)


def _normalize_scalar(value: Any) -> Any:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int,)):
        return value
    if isinstance(value, float):
        if math.isnan(value):
            return "nan"
        if math.isinf(value):
            return "inf" if value > 0 else "-inf"
        # 抹掉浮点尾差，避免同一份行情因序列化差异反复失效。
        return round(value, 6)
    if isinstance(value, Mapping):
        return {str(k): _normalize_scalar(v) for k, v in sorted(value.items(), key=lambda kv: str(kv[0]))}
    if isinstance(value, (list, tuple)):
        return [_normalize_scalar(v) for v in value]
    if value is None:
        return None
    return str(value)


def _digest(prefix: str, payload: Any) -> str:
    raw = _stable_json({"prefix": prefix, "payload": _normalize_scalar(payload)})
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]


def _subset(stock_data: Mapping[str, Any] | None, fields: Sequence[str]) -> Dict[str, Any]:
    if not isinstance(stock_data, Mapping):
        return {}
    return {key: stock_data.get(key) for key in fields if key in stock_data}


def price_fingerprint(stock_data: Mapping[str, Any] | None) -> str:
    """Fast-moving market state (prices, volume, technical indicators)."""
    return _digest("price", _subset(stock_data, _PRICE_FIELDS))


def fundamental_fingerprint(stock_data: Mapping[str, Any] | None) -> str:
    """Slow-moving facts (name, industry, valuation scale, supply chain)."""
    return _digest("fundamental", _subset(stock_data, _FUNDAMENTAL_FIELDS))


def sector_fingerprint(stock_data: Mapping[str, Any] | None) -> str:
    """Sector attribution and sector-level strength."""
    return _digest("sector", _subset(stock_data, _SECTOR_FIELDS))


def build_invalidation_inputs(
    stock_data: Mapping[str, Any] | None,
    *,
    risk_profile: str = "balanced",
    portfolio_snapshot_hash: str = "",
    trading_day: str = "",
) -> Dict[str, str]:
    """Return the per-dimension invalidation inputs for one analysis request."""
    return {
        "price_fingerprint": price_fingerprint(stock_data),
        "sector_fingerprint": sector_fingerprint(stock_data),
        "fundamental_fingerprint": fundamental_fingerprint(stock_data),
        "risk_profile": str(risk_profile or "balanced"),
        "portfolio_snapshot_hash": str(portfolio_snapshot_hash or ""),
        "trading_day": str(trading_day or ""),
    }


# ---------------------------------------------------------------------------
# identity
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class CacheIdentity:
    """Hard cache key: fields that must never share one snapshot file."""

    stock_code: str
    provider: str
    model: str
    prompt_version: str = PROMPT_SCHEMA_VERSION
    agent_config_version: str = AGENT_CONFIG_VERSION
    cache_schema_version: str = CACHE_SCHEMA_VERSION

    def to_dict(self) -> Dict[str, str]:
        return {
            "stock_code": self.stock_code,
            "provider": self.provider,
            "model": self.model,
            "prompt_version": self.prompt_version,
            "agent_config_version": self.agent_config_version,
            "cache_schema_version": self.cache_schema_version,
        }

    @property
    def key(self) -> str:
        return _digest("identity", self.to_dict())


def _safe_code(stock_code: str) -> str:
    cleaned = re.sub(r"[^0-9A-Za-z_.-]", "_", str(stock_code or "unknown"))
    return cleaned[:32] or "unknown"


def build_identity(
    stock_code: str,
    *,
    provider: str | None = None,
    model: str | None = None,
    prompt_version: str = PROMPT_SCHEMA_VERSION,
    agent_config_version: str = AGENT_CONFIG_VERSION,
    environ: Mapping[str, str] | None = None,
) -> CacheIdentity:
    """Build the cache identity, resolving provider/model from the environment."""
    env = os.environ if environ is None else environ
    resolved_provider = provider if provider is not None else (
        env.get("NASDX_PROVIDER") or env.get("NASDX_BASE_URL") or "default"
    )
    resolved_model = model if model is not None else (env.get("NASDX_MODEL") or "unknown")
    return CacheIdentity(
        stock_code=str(stock_code),
        provider=str(resolved_provider).strip().lower(),
        model=str(resolved_model).strip(),
        prompt_version=str(prompt_version),
        agent_config_version=str(agent_config_version),
        cache_schema_version=CACHE_SCHEMA_VERSION,
    )


# ---------------------------------------------------------------------------
# snapshot
# ---------------------------------------------------------------------------
@dataclass
class AnalysisSnapshot:
    """One cached analysis run, fully auditable."""

    identity: CacheIdentity
    created_at: str
    data_as_of: str = ""
    inputs: Dict[str, str] = field(default_factory=dict)
    dimensions: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    battle: Dict[str, Any] = field(default_factory=dict)
    synthesis: Dict[str, Any] = field(default_factory=dict)
    stats: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "contract": CACHE_CONTRACT,
            "cache_schema_version": self.identity.cache_schema_version,
            "identity": self.identity.to_dict(),
            "identity_key": self.identity.key,
            "created_at": self.created_at,
            "data_as_of": self.data_as_of,
            "inputs": dict(self.inputs),
            "dimensions": {k: dict(v) for k, v in self.dimensions.items()},
            "battle": dict(self.battle),
            "synthesis": dict(self.synthesis),
            "stats": dict(self.stats),
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "AnalysisSnapshot":
        identity_payload = payload.get("identity")
        if not isinstance(identity_payload, Mapping):
            raise ValueError("snapshot identity missing")
        identity = CacheIdentity(
            stock_code=str(identity_payload.get("stock_code", "")),
            provider=str(identity_payload.get("provider", "")),
            model=str(identity_payload.get("model", "")),
            prompt_version=str(identity_payload.get("prompt_version", "")),
            agent_config_version=str(identity_payload.get("agent_config_version", "")),
            cache_schema_version=str(identity_payload.get("cache_schema_version", "")),
        )
        dimensions_payload = payload.get("dimensions")
        if dimensions_payload is not None and not isinstance(dimensions_payload, Mapping):
            raise ValueError("snapshot dimensions must be a mapping")
        return cls(
            identity=identity,
            created_at=str(payload.get("created_at", "")),
            data_as_of=str(payload.get("data_as_of", "")),
            inputs={
                str(k): str(v)
                for k, v in dict(payload.get("inputs") or {}).items()
            },
            dimensions={
                str(k): dict(v)
                for k, v in dict(dimensions_payload or {}).items()
                if isinstance(v, Mapping)
            },
            battle=dict(payload.get("battle") or {}),
            synthesis=dict(payload.get("synthesis") or {}),
            stats=dict(payload.get("stats") or {}),
        )


def utc_now_iso(now: datetime | None = None) -> str:
    moment = now or datetime.now(timezone.utc)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(timezone.utc).isoformat(timespec="seconds")


def parse_iso(value: Any) -> Optional[datetime]:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def snapshot_path(identity: CacheIdentity, cache_dir: str | Path | None = None) -> Path:
    base = Path(cache_dir).expanduser() if cache_dir else get_analysis_cache_dir()
    return base / _safe_code(identity.stock_code) / f"{identity.key}.json"


def save_snapshot(
    snapshot: AnalysisSnapshot, cache_dir: str | Path | None = None
) -> Path:
    """Persist a snapshot atomically; never raise into the analysis pipeline."""
    path = snapshot_path(snapshot.identity, cache_dir)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(snapshot.to_dict(), ensure_ascii=False, indent=2)
        with tempfile.NamedTemporaryFile(
            "w", encoding="utf-8", dir=str(path.parent), delete=False, suffix=".tmp"
        ) as handle:
            handle.write(payload)
            temp_path = Path(handle.name)
        os.replace(temp_path, path)
    except OSError as exc:  # pragma: no cover - filesystem specific
        raise AnalysisCacheError(f"无法写入分析快照 {path}: {exc}") from exc
    return path


def load_snapshot(
    identity: CacheIdentity, cache_dir: str | Path | None = None
) -> Tuple[Optional[AnalysisSnapshot], str]:
    """Load a snapshot. Returns ``(snapshot, reason)``; reason is ``hit`` on success."""
    path = snapshot_path(identity, cache_dir)
    if not path.exists():
        return None, MISS_MISSING
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError:
        return None, MISS_UNREADABLE
    try:
        payload = json.loads(raw)
    except (json.JSONDecodeError, ValueError):
        return None, MISS_CORRUPT
    if not isinstance(payload, Mapping):
        return None, MISS_CORRUPT
    if str(payload.get("contract")) != CACHE_CONTRACT:
        return None, MISS_SCHEMA
    if str(payload.get("cache_schema_version")) != identity.cache_schema_version:
        return None, MISS_SCHEMA
    try:
        snapshot = AnalysisSnapshot.from_dict(payload)
    except (ValueError, TypeError):
        return None, MISS_CORRUPT
    if snapshot.identity != identity:
        return None, MISS_IDENTITY
    return snapshot, HIT


def clear_snapshots(stock_code: str | None = None, cache_dir: str | Path | None = None) -> int:
    """Delete cached snapshots; returns the number of removed files."""
    base = Path(cache_dir).expanduser() if cache_dir else get_analysis_cache_dir()
    if not base.exists():
        return 0
    target = base / _safe_code(stock_code) if stock_code else base
    if not target.exists():
        return 0
    removed = 0
    for path in sorted(target.rglob("*.json")):
        try:
            path.unlink()
            removed += 1
        except OSError:  # pragma: no cover - filesystem specific
            continue
    return removed


# ---------------------------------------------------------------------------
# reuse planning
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class DimensionDecision:
    """Per-dimension reuse verdict with an explicit, auditable reason."""

    dimension: str
    reuse: bool
    reason: str
    refreshed_at: str = ""
    age_seconds: Optional[float] = None
    ttl_seconds: Optional[float] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "dimension": self.dimension,
            "reuse": self.reuse,
            "reason": self.reason,
            "refreshed_at": self.refreshed_at,
            "age_seconds": self.age_seconds,
            "ttl_seconds": self.ttl_seconds,
        }


@dataclass(frozen=True)
class ReusePlan:
    """Which dimensions may be reused and why the others must be recomputed."""

    snapshot_available: bool
    snapshot_reason: str
    decisions: Dict[str, DimensionDecision]

    @property
    def hit_dimensions(self) -> Tuple[str, ...]:
        return tuple(name for name, d in self.decisions.items() if d.reuse)

    @property
    def miss_dimensions(self) -> Tuple[str, ...]:
        return tuple(name for name, d in self.decisions.items() if not d.reuse)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "snapshot_available": self.snapshot_available,
            "snapshot_reason": self.snapshot_reason,
            "hit_dimensions": list(self.hit_dimensions),
            "miss_dimensions": list(self.miss_dimensions),
            "decisions": {name: d.to_dict() for name, d in self.decisions.items()},
        }


def resolve_ttl(dimension: str, environ: Mapping[str, str] | None = None) -> float:
    """Return the TTL for a dimension, honoring ``NASDX_ANALYSIS_TTL_<DIM>``."""
    policy = DIMENSION_POLICIES.get(dimension)
    default = policy.ttl_seconds if policy else 0.0
    env = os.environ if environ is None else environ
    raw = env.get(f"NASDX_ANALYSIS_TTL_{dimension.upper()}", "")
    if not raw:
        return default
    try:
        value = float(str(raw).strip())
    except (TypeError, ValueError):
        return default
    if not math.isfinite(value) or value < 0:
        return default
    return value


def plan_reuse(
    snapshot: Optional[AnalysisSnapshot],
    inputs: Mapping[str, str],
    *,
    snapshot_reason: str = MISS_MISSING,
    dimensions: Iterable[str] = RESEARCH_DIMENSIONS,
    now: datetime | None = None,
    environ: Mapping[str, str] | None = None,
) -> ReusePlan:
    """Decide, dimension by dimension, whether the cached result is still valid."""
    moment = now or datetime.now(timezone.utc)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    decisions: Dict[str, DimensionDecision] = {}

    for dimension in dimensions:
        ttl = resolve_ttl(dimension, environ=environ)
        if snapshot is None:
            decisions[dimension] = DimensionDecision(
                dimension, False, f"no_snapshot:{snapshot_reason}", ttl_seconds=ttl
            )
            continue

        cached = snapshot.dimensions.get(dimension)
        if not isinstance(cached, Mapping) or not isinstance(cached.get("result"), Mapping):
            decisions[dimension] = DimensionDecision(
                dimension, False, "missing_dimension", ttl_seconds=ttl
            )
            continue

        refreshed_at = str(cached.get("refreshed_at", ""))
        refreshed_dt = parse_iso(refreshed_at)
        if refreshed_dt is None:
            decisions[dimension] = DimensionDecision(
                dimension, False, "missing_refreshed_at", refreshed_at, ttl_seconds=ttl
            )
            continue

        age = (moment - refreshed_dt).total_seconds()
        if age < 0:
            # 时钟回拨或人为篡改：宁可重算也不复用"未来"结论。
            decisions[dimension] = DimensionDecision(
                dimension, False, "future_timestamp", refreshed_at, age, ttl
            )
            continue

        policy = DIMENSION_POLICIES.get(dimension)
        watched = policy.inputs if policy else INVALIDATION_INPUT_KEYS
        cached_inputs = cached.get("inputs")
        cached_inputs = cached_inputs if isinstance(cached_inputs, Mapping) else {}
        changed = [
            key
            for key in watched
            if str(cached_inputs.get(key, "")) != str(inputs.get(key, ""))
        ]
        if changed:
            decisions[dimension] = DimensionDecision(
                dimension, False, f"input_changed:{','.join(sorted(changed))}", refreshed_at, age, ttl
            )
            continue

        if ttl <= 0 or age > ttl:
            decisions[dimension] = DimensionDecision(
                dimension, False, "ttl_expired", refreshed_at, age, ttl
            )
            continue

        decisions[dimension] = DimensionDecision(dimension, True, HIT, refreshed_at, age, ttl)

    return ReusePlan(
        snapshot_available=snapshot is not None,
        snapshot_reason=snapshot_reason if snapshot is None else HIT,
        decisions=decisions,
    )


def dimension_payload(
    result: Any, inputs: Mapping[str, str], *, now: datetime | None = None
) -> Dict[str, Any]:
    """Serialize one research result plus the inputs it was computed from."""
    policy_inputs = INVALIDATION_INPUT_KEYS
    return {
        "refreshed_at": utc_now_iso(now),
        "inputs": {key: str(inputs.get(key, "")) for key in policy_inputs},
        "result": _result_to_dict(result),
    }


def _result_to_dict(result: Any) -> Dict[str, Any]:
    if isinstance(result, Mapping):
        return dict(result)
    dumper = getattr(result, "model_dump", None) or getattr(result, "dict", None)
    if callable(dumper):
        return dict(dumper())
    raise TypeError(f"无法序列化分析结果：{type(result).__name__}")


def normalize_depth(depth: Any) -> str:
    """Validate an execution depth, raising ``ValueError`` on unknown values."""
    value = str(depth or DEPTH_FULL).strip().lower()
    if value not in DEPTHS:
        raise ValueError(f"未知分析深度 depth={depth!r}，可选 {list(DEPTHS)}")
    return value
