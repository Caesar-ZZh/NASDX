"""生意社(100ppi.com) 大宗商品行情拉取。

对齐 N4 项。只呈现客观数据（代码/名称/最新价/涨跌幅/成交量等），
不预置标的、不排名、不预测、不给买卖结论（零标的红线）。

接入方式:
- 公开报价列表页: https://www.100ppi.com/syncday.html
- 单个品种页: https://price.100ppi.com/syncday/html/rate/get-{code}.html
- 报价接口 (非官方, 仅作兜底): https://quote.100ppi.com/service/getQuote.html

限流: 串行 ≥1s / 请求, 避免触发反爬。
缓存: 行情级 5min; 日度快照 30min; 空结果不缓存。

凭据: 无。如需调整 User-Agent 可走环境变量 NASDX_COMMODITY_UA。

依赖: requests (项目通用); pathlib; datetime; json; os; time; logging。

本文件仅供技术研究/个人投研使用, 不代表商业授权, 遵守 100ppi.com ToS。
"""

from __future__ import annotations

import json
import logging
import os
import re
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Iterable, Optional

import requests

try:
    import pandas as pd
except ImportError:  # pragma: no cover - 可选依赖
    pd = None  # type: ignore[assignment]

logger = logging.getLogger(__name__)

# ─── 常量 ───────────────────────────────────────────────────────────────────

_BASE_URL = "https://www.100ppi.com"
_LIST_PAGE = f"{_BASE_URL}/syncday.html"
_PRICE_PAGE_TPL = "https://price.100ppi.com/syncday/html/rate/get-{code}.html"
_FALLBACK_API = "https://quote.100ppi.com/service/getQuote.html"

_DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)

UA_HEADER = os.getenv("NASDX_COMMODITY_UA", _DEFAULT_USER_AGENT)

# 串行最低间隔 (秒), 对齐 miaoou 的 em_get 范式
MIN_INTERVAL_SEC = float(os.getenv("NASDX_COMMODITY_MIN_INTERVAL", "1.0"))

# 缓存 TTL (秒)
_TTL_QUOTE = float(os.getenv("NASDX_COMMODITY_TTL_QUOTE", "300"))   # 5min
_TTL_DAYLY = float(os.getenv("NASDX_COMMODITY_TTL_DAYLY", "1800"))  # 30min

# 本地缓存目录 (相对项目根), 可通过环境变量覆盖
_CACHE_DIR_ENV = os.getenv("NASDX_COMMODITY_CACHE_DIR")

# 品类前缀黑名单/白名单 (可选)
# 留空代表不过滤; 非空时只保留在白名单中的前缀
_CATEGORY_WHITELIST_RAW = os.getenv("NASDX_COMMODITY_CATEGORY_WHITELIST", "")
CATEGORY_WHITELIST: list[str] = [
    s.strip().upper()
    for s in _CATEGORY_WHITELIST_RAW.split(",")
    if s.strip()
]

# ─── 基础设施: 缓存 + 限流 ──────────────────────────────────────────────────

_cache_root: Optional[Path] = None
_last_req_at: float = 0.0


def _cache_root_path() -> Path:
    global _cache_root
    if _cache_root is None:
        raw = _CACHE_DIR_ENV or ""
        if raw:
            _cache_root = Path(raw)
        else:
            # 默认放在项目根 / .cache/commodity_100ppi/
            _cache_root = Path(__file__).resolve().parents[2] / ".cache" / "commodity_100ppi"
        _cache_root.mkdir(parents=True, exist_ok=True)
    return _cache_root


def _throttle() -> None:
    """串行限流: 距上一次请求不足 MIN_INTERVAL_SEC 则 sleep 补满。"""
    global _last_req_at
    now = time.monotonic()
    wait = MIN_INTERVAL_SEC - (now - _last_req_at)
    if wait > 0:
        time.sleep(wait)
    _last_req_at = time.monotonic()


def _cache_key(suffix: str) -> Path:
    return _cache_root_path() / f"{suffix}.json"


def _load_cache(suffix: str) -> Optional[Any]:
    p = _cache_root_path() / f"{suffix}.json"
    if not p.exists():
        return None
    try:
        with p.open("r", encoding="utf-8") as f:
            doc = json.load(f)
        if not isinstance(doc, dict) or "ts" not in doc or "data" not in doc:
            return None
        ttl_map = {
            "quote": _TTL_QUOTE,
            "dayly": _TTL_DAYLY,
        }
        ttl = ttl_map.get(suffix, _TTL_QUOTE)
        if (datetime.now() - datetime.fromtimestamp(doc["ts"])).total_seconds() > ttl:
            return None
        return doc["data"]
    except Exception as exc:
        logger.debug("cache load error %r", exc, exc_info=True)
        return None


def _save_cache(suffix: str, data: Any) -> None:
    if data is None:
        return  # 空结果不缓存, 避免雪崩
    p = _cache_root_path() / f"{suffix}.json"
    try:
        p.write_text(
            json.dumps({"ts": datetime.now().timestamp(), "data": data}, ensure_ascii=False, default=str),
            encoding="utf-8",
        )
    except Exception as exc:
        logger.debug("cache save error %r", exc, exc_info=True)


# ─── 网络请求 ───────────────────────────────────────────────────────────────

_session: Optional[requests.Session] = None


def _session() -> requests.Session:
    global _session
    if _session is None:
        _session = requests.Session()
        _session.headers.update({
            "User-Agent": UA_HEADER,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
            "Referer": _BASE_URL,
        })
    return _session


def _get(url: str, *, timeout: tuple[int, int] = (8, 15)) -> requests.Response:
    _throttle()
    resp = _session().get(url, timeout=timeout)
    resp.raise_for_status()
    return resp


# ─── 解析器 ─────────────────────────────────────────────────────────────────

#: 标准化的字段名映射 (下游统一使用)
STANDARD_FIELDS = (
    "code",
    "name",
    "latest",
    "change_pct",
    "prev_close",
    "open",
    "high",
    "low",
    "volume",
    "deal_amount",
    "update_time",
    "raw_source",
)

# 品类前缀与中文名映射 (常见 100ppi 命名惯例)
_PREFIX_NAME_HINT: dict[str, str] = {
    "GLD": "黄金",
    "AG": "白银",
    "CU": "铜",
    "AL": "铝",
    "ZN": "锌",
    "PB": "铅",
    "NI": "镍",
    "SN": "锡",
    "AU": "黄金",
    "RB": "螺纹钢",
    "HC": "热卷",
    "I": "铁矿石",
    "J": "焦炭",
    "JM": "焦煤",
    "PP": "聚丙烯",
    "LH": "生猪",
    "AP": "苹果",
    "CF": "棉花",
    "MA": "甲醇",
    "EG": "乙二醇",
    "TA": "PTA",
    "UR": "尿素",
    "SA": "纯碱",
    "PG": "PG",
    "LU": "低硫燃油",
    "FU": "燃油",
    "BU": "沥青",
    "SP": "纸浆",
    "SC": "原油",
    "LU": "低硫燃油",
    "NR": "橡胶",
    "RU": "橡胶",
    "BU": "沥青",
    "B": "豆油",
    "Y": "豆油",
    "M": "豆粕",
    "C": "玉米",
    "A": "豆一",
    "B": "豆二",
    "JD": "鸡蛋",
    "LH": "生猪",
    "RR": "晚稻",
    "WF": "强麦",
    "AR": "弱麦",
    "CS": "淀粉",
    "CI": "棉纱",
    "SF": "硅铁",
    "SM": "锰硅",
    "AX": "豆油(纸版)",
    "OI": "菜油",
    "P": "棕榈油",
    "FG": "玻璃",
    "TA": "PTA",
    "MA": "甲醇",
    "EG": "乙二醇",
    "UR": "尿素",
    "SA": "纯碱",
    "ZC": "动力煤",
    "JS": "焦炭实盘",
    "RJ": "橡胶国际",
}


def _normalize_code(code: str) -> str:
    """统一转大写, 去空白。"""
    return re.sub(r"\s+", "", code).upper()


def _decode_html_text(resp: requests.Response) -> str:
    """优先用响应charset, 回退 utf-8。"""
    if resp.encoding is None or resp.encoding.lower() in {"iso-8859-1", "latin1"}:
        resp.encoding = resp.apparent_encoding or "utf-8"
    return resp.text


def _extract_from_html(html: str) -> list[dict[str, Any]]:
    """从 100ppi syncday 页面抽取表格数据。

    页面结构: <table id="tablelist"> ... <tr><td>code</td><td>name</td><td>latest</td>...  
    实际以 JS 动态渲染居多, 因此这里优先尝试匹配内嵌 JSON/CSV 片段, 其次解析静态表格。
    """
    items: list[dict[str, Any]] = []

    # 策略1: 尝试找到内嵌的 JSON 数组/对象 (100ppi 部分版本会把数据塞入 script)
    json_blocks = re.findall(r"var\s+data\s*=\s*(\[[^]]*\])", html)
    if not json_blocks:
        json_blocks = re.findall(r"\{[^{}]*\"code\"[^{}]*\}(?:,\s*\{[^{}]*\"code\"[^{}]*\})*", html)
    if json_blocks:
        raw = "[" + ",".join(json_blocks) + "]"
        try:
            arr = json.loads(raw)
            if isinstance(arr, list):
                items = [_clean_row(r) for r in arr if isinstance(r, dict)]
        except Exception as exc:
            logger.debug("inline json parse error %r", exc, exc_info=True)

    # 策略2: 解析 <table> ... <tr> 行
    if not items:
        rows = re.findall(r"<tr[^>]*>(.*?)</tr>", html, flags=re.DOTALL | re.IGNORECASE)
        for row in rows:
            cells = re.findall(r"<td[^>]*>(.*?)</td>", row, flags=re.DOTALL | re.IGNORECASE)
            if len(cells) < 3:
                continue
            items.append(_parse_cells(cells))

    # 去重/清洗
    seen: set[str] = set()
    dedup: list[dict[str, Any]] = []
    for it in items:
        c = _normalize_code(str(it.get("code", "")))
        if not c or c in seen:
            continue
        seen.add(c)
        dedup.append(it)
    return dedup


def _parse_cells(cells: list[str]) -> dict[str, Any]:
    """把 <td> 文本序列映射为标准化字段。"""
    def clean(s: str) -> str:
        return re.sub(r"<[^>]+>", "", s).strip()

    flat = [clean(c) for c in cells]
    # 经验结构: code, name, latest, change_pct(%), prev_close, open, high, low, volume, ...
    row: dict[str, Any] = {
        "code": flat[0] if len(flat) > 0 else "",
        "name": flat[1] if len(flat) > 1 else "",
        "latest": flat[2] if len(flat) > 2 else "",
        "change_pct": flat[3] if len(flat) > 3 else "",
        "prev_close": flat[4] if len(flat) > 4 else "",
        "open": flat[5] if len(flat) > 5 else "",
        "high": flat[6] if len(flat) > 6 else "",
        "low": flat[7] if len(flat) > 7 else "",
        "volume": flat[8] if len(flat) > 8 else "",
        "raw_source": "100ppi_html",
    }
    return _clean_row(row)


def _clean_row(row: dict[str, Any]) -> dict[str, Any]:
    """类型/内容清洗。"""
    out: dict[str, Any] = {}
    for k in STANDARD_FIELDS:
        v = row.get(k, "")
        if k in {"code", "name", "raw_source", "update_time"}:
            out[k] = str(v).strip() if v is not None else ""
        elif k == "change_pct":
            out[k] = _parse_pct(str(v) if v is not None else "")
        elif k in {"latest", "prev_close", "open", "high", "low"}:
            out[k] = _parse_float(str(v) if v is not None else "")
        elif k in {"volume", "deal_amount"}:
            out[k] = _parse_int(str(v) if v is not None else "")
        else:
            out[k] = v
    if not out.get("code"):
        return {}
    # 补充品类提示
    if not out.get("name") or out["name"] == out["code"]:
        hint = _PREFIX_NAME_HINT.get(out["code"], "")
        if hint:
            out["name"] = hint
    out["update_time"] = out.get("update_time") or datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    return out


def _parse_float(s: str) -> Optional[float]:
    s = re.sub(r"[^0-9.\-+]", "", s or "")
    if not s or s in {".", "-", "+"}:
        return None
    try:
        return float(s)
    except Exception:
        return None


def _parse_int(s: str) -> Optional[int]:
    s = re.sub(r"[^0-9\-]", "", s or "")
    if not s:
        return None
    try:
        return int(float(s))
    except Exception:
        return None


def _parse_pct(s: str) -> Optional[float]:
    """解析形如 '2.35%' / '-1.2' / '+0.5%' 的涨跌幅字符串。"""
    s = (s or "").strip()
    s = s.replace("%", "").replace("+", "").strip()
    return _parse_float(s)


def _filter_by_whitelist(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if not CATEGORY_WHITELIST:
        return items
    return [it for it in items if it.get("code", "") in CATEGORY_WHITELIST]


# ─── 主入口函数 ─────────────────────────────────────────────────────────────


def fetch_list(*, cache_suffix: str = "quote", use_cache: bool = True) -> list[dict[str, Any]]:
    """拉取 100ppi 首页列表页 (全品类概览, 每日更新)。

    参数:
        cache_suffix: 缓存键后缀 (quote/dayly)。
        use_cache: 是否读取/写入缓存。

    返回:
        标准化的商品快照列表 (dict)。仅含客观字段, 无标的推荐。
    """
    if use_cache:
        cached = _load_cache(cache_suffix)
        if cached is not None:
            return cached  # type: ignore[return-value]

    resp = _get(_LIST_PAGE)
    html = _decode_html_text(resp)
    items = _extract_from_html(html)
    items = _filter_by_whitelist(items)

    _save_cache(cache_suffix, items)
    logger.info("100ppi list fetched: %d items (suffix=%s)", len(items), cache_suffix)
    return items


def fetch_by_code(code: str, *, cache_suffix: str = "quote") -> Optional[dict[str, Any]]:
    """按品种代码拉取单条行情快照 (走单品页)。

    参数:
        code: 品种代码 (不区分大小写, 例如 'AU', 'rb', 'CU')。
        cache_suffix: 缓存键后缀。

    返回:
        标准化单条记录 dict; 找不到返回 None。
    """
    c = _normalize_code(code)
    if not c:
        return None
    key = f"{cache_suffix}_{c}"
    if cache_suffix:
        cached = _load_cache(key)
        if cached is not None:
            return cached  # type: ignore[return-value]

    url = _PRICE_PAGE_TPL.format(code=c)
    try:
        resp = _get(url)
        html = _decode_html_text(resp)
        items = _extract_from_html(html)
        item: Optional[dict[str, Any]] = None
        for it in items:
            if _normalize_code(str(it.get("code", ""))) == c:
                item = it
                break
        if item is None and items:
            item = items[0]
        if item:
            _save_cache(key, item)
        return item
    except Exception as exc:
        logger.warning("100ppi fetch_by_code(%s) error: %r", c, exc, exc_info=True)
        return None


def fetch_fallback_api() -> list[dict[str, Any]]:
    """兜底: 直接打 quote.100ppi.com 的 JSON 接口 (可能变化, 仅作降级)。

    返回标准化的列表; 失败返回空列表。
    """
    params: dict[str, object] = {
        "type": "1",
        "code": "001",
    }
    try:
        resp = _get(_FALLBACK_API, params=params)
        data = resp.json()
        rows: list[dict[str, Any]] = []
        # 常见结构: {"Result": [...], "State": 1}
        payload = (data or {}).get("Result") or (data or {}).get("result") or []
        if isinstance(payload, list):
            for r in payload:
                if isinstance(r, dict):
                    rows.append(_clean_row(r))
        rows = _filter_by_whitelist(rows)
        return rows
    except Exception as exc:
        logger.debug("fallback api error %r", exc, exc_info=True)
        return []


# ─── Pandas 便利接口 (可选, 依赖 pandas) ───────────────────────────────────


def fetch_list_df(*, cache_suffix: str = "quote", use_cache: bool = True) -> Any:
    """返回列表 (list[dict]), 若 pandas 可用则同时返回 DataFrame。"""
    items = fetch_list(cache_suffix=cache_suffix, use_cache=use_cache)
    if pd is None:
        return items
    return pd.DataFrame(items)


def to_summary(items: Iterable[dict[str, Any]]) -> dict[str, Any]:
    """对列表做聚合统计 (仅客观摘要, 不排名/不推荐)。

    返回:
        {"count": int, "update_time": str, "changes": {"up": int, "down": int, "flat": int},
         "avg_change_pct": float|None, "top_gainers": [...], "losers": [...]}.
        其中 top_gainers/losers 各保留最多 10 条 (仅用于数据展示, 非推荐)。
    """
    rows = list(items)
    up = down = flat = 0
    sums: list[float] = []
    for r in rows:
        p = r.get("change_pct")
        if p is None:
            continue
        if p > 0:
            up += 1
        elif p < 0:
            down += 1
        else:
            flat += 1
        sums.append(p)
    avg = (sum(sums) / len(sums)) if sums else None
    by_pct = sorted(rows, key=lambda r: (r.get("change_pct") or 0.0), reverse=True)
    return {
        "count": len(rows),
        "update_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "changes": {"up": up, "down": down, "flat": flat},
        "avg_change_pct": round(avg, 4) if avg is not None else None,
        "top_gainers": by_pct[:10],
        "losers": by_pct[-10:][::-1],
    }


# ─── CLI 极简入口 (调试用) ─────────────────────────────────────────────────


def main() -> None:
    import argparse

    ap = argparse.ArgumentParser(description="100ppi commodity quote (research only)")
    ap.add_argument("--list", action="store_true", help="fetch full list")
    ap.add_argument("--code", type=str, help="fetch single code")
    ap.add_argument("--json", action="store_true", help="output JSON")
    args = ap.parse_args()

    if args.list:
        items = fetch_list()
        out = {"summary": to_summary(items), "items": items}
        print(json.dumps(out, ensure_ascii=False, indent=2, default=str))
    elif args.code:
        item = fetch_by_code(args.code)
        print(json.dumps(item, ensure_ascii=False, indent=2, default=str) if item else "NOT_FOUND")
    else:
        print(main.__doc__)


if __name__ == "__main__":
    main()
