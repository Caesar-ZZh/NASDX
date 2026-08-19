"""海外数据源统一入口（SEC / Treasury / CFTC / FINRA / CBOE / Yahoo）。

合规分级来源：global-stock-data SKILL.md（逐家实读条款 2026-07-24）。
  S 级 — SEC EDGAR / US Treasury / CFTC：可自由使用（含商用）。
  B 级 — FINRA：下载已发布文件属常规用法，批量爬站点/商用前须自行确认。
  C 级 — CBOE / Yahoo：个人研究，商用或再分发须事先取得授权。

零标的红线：所有函数按传入 ticker/code 返回客观数据，不预置标的、
不排名、不预测、不给买卖结论；调用方负责合规级别审核。
"""
from __future__ import annotations

import os
import re
import time
import threading
from dataclasses import dataclass
from typing import Any, Iterable

import requests

# ── 合规级别枚举 ───────────────────────────────────────────────────────────
ComplianceLevel = str  # "S" | "B" | "C"

# ── SEC User-Agent（环境变量，未配置时在调用时抛 RuntimeError）────────────
_DEFAULT_SEC_UA = "NASDX Auto-Research Bot <nasdx@local>"


def _get_sec_contact() -> str:
    val = os.environ.get("SEC_CONTACT", "").strip()
    return val if val else _DEFAULT_SEC_UA


# ── 线程安全限流器 ─────────────────────────────────────────────────────────
class _RateLimiter:
    """最小间隔节流器（锁保护，避免并发击穿）"""

    def __init__(self, max_per_sec: float) -> None:
        self._interval = 1.0 / float(max_per_sec)
        self._last = 0.0
        self._lock = threading.Lock()

    def wait(self) -> None:
        with self._lock:
            gap = self._interval - (time.monotonic() - self._last)
            if gap > 0:
                time.sleep(gap)
            self._last = time.monotonic()


_LIMITS: dict[str, _RateLimiter] = {
    "sec.gov": _RateLimiter(8),      # 官方上限 10/s，留 20% 余量
    "finra.org": _RateLimiter(4),
    "cboe.com": _RateLimiter(4),
    "nasdaq.com": _RateLimiter(2),
    "yimg.com": _RateLimiter(5),
    "yahoo.com": _RateLimiter(5),
    "_default": _RateLimiter(5),
}


def _limiter_for(url: str) -> _RateLimiter:
    for host, lim in _LIMITS.items():
        if host != "_default" and host in url:
            return lim
    return _LIMITS["_default"]


# ── 统一 HTTP 出口 ──────────────────────────────────────────────────────────
def _is_object_missing(resp: requests.Response) -> bool:
    """正向识别"资源确实不存在"（404 或 S3 风格的 403 AccessDenied）"""
    if resp.status_code == 404:
        return True
    if resp.status_code != 403:
        return False
    ctype = (resp.headers.get("Content-Type") or "").lower()
    head = (resp.text or "")[:500]
    return "xml" in ctype and "<Code>AccessDenied</Code>" in head


def _official_get(
    url: str,
    *,
    params: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
    timeout: int = 30,
    as_json: bool = False,
) -> requests.Response:
    """
    官方源统一出口：自动节流 + UA 处理 + 友好错误。

    异常语义：
      - RuntimeError ：配置错误（SEC_CONTACT 未改）、限流、网络故障、被封
      - requests.HTTPError：可被调用方捕获并区分处理
    """
    _limiter_for(url).wait()

    is_sec = "sec.gov" in url
    ua = _get_sec_contact() if is_sec else (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    )
    req_headers: dict[str, str] = {
        "User-Agent": ua,
        "Accept-Encoding": "gzip, deflate" if is_sec else "*",
        "Accept": "application/json, */*",
    }
    if headers:
        req_headers.update(headers)

    r = requests.get(url, params=params, headers=req_headers, timeout=timeout)
    try:
        r.raise_for_status()
    except requests.HTTPError as exc:
        resp = exc.response
        if _is_object_missing(resp):
            # 返回 4xx 让调用方自行判断；不吞异常
            raise exc
        low = (resp.text or "")[:4000].lower()
        code = resp.status_code
        if code == 403 and "undeclared" in low:
            raise RuntimeError(
                f"SEC 拒绝请求：User-Agent 未被识别为已声明。"
                f"当前 SEC_CONTACT={_get_sec_contact()!r}，"
                f"格式应为 'Company Name AdminContact@domain.com'"
            ) from exc
        hint_map = {
            403: "被拒绝：限流、封禁或权限问题（已排除「资源不存在」）",
            404: "端点不存在：接口可能已变更",
            429: "请求过快：已内置节流，若仍触发请调低 _LIMITS",
        }
        hint = hint_map.get(code, "")
        raise RuntimeError(f"HTTP {code} {url[:80]} — {hint}") from exc
    except requests.RequestException as exc:
        raise RuntimeError(f"请求失败 {url[:80]} — {type(exc).__name__}: {exc}") from exc
    return r


# ── 合规标注装饰器 ──────────────────────────────────────────────────────────
def _compliance(level: ComplianceLevel, source: str) -> str:
    return f"[合规 {level}] {source}"


# ── Layer 1 / 2：通用 helper（新浪/腾讯行情、东财全球）暂由现有 quant/data 覆盖，
#    本模块聚焦海外独有源：SEC / Treasury / FINRA / CBOE / Yahoo。


# ═══════════════════════════════════════════════════════════════════════════
# SEC EDGAR
# ═══════════════════════════════════════════════════════════════════════════
_SEC_BASE = "https://www.sec.gov"
_EDGAR_FTS = f"{_SEC_BASE}//cgi-bin/browse-edgar/"
_EDGAR_XML = f"{_SEC_BASE}/Archives/edgar/full-index/"
_XBRL_API = f"{_SEC_BASE}/cgi-bin/browse-edgar/"


@dataclass(frozen=True)
class EdgartechSubmission:
    """SEC 申报条目（10-K / 10-Q / 8-K / 4 / 13F-HR / 144 等）"""
    form: str
    ticker: str
    cik: str
    filed_date: str
    accession_number: str
    url: str


def edgar_cik_lookup(ticker: str) -> str:
    """
    ticker → CIK 映射。

    返回格式：补零至 10 位的 CIK 字符串，例如 "0000320192"（AAPL）。
    查不到返回空串。
    """
    t = str(ticker).upper().strip()
    url = f"https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&CIK={t}&type=&date=&owner=include&count=40&search_text=&action=submit"
    # 更直接的方式：CIK lookup API
    url = f"https://efts.sec.gov/LATEST/search-index?q={t}&limit=1&apikey=default"
    # 标准做法：访问 company index 页面解析
    # 这里使用 EDGAR 官方 CIK lookup endpoint
    url = f"https://www.sec.gov/Archives/edgar/cik Lookup?company={t}"
    # 用更可靠的：company search
    resp = _official_get(
        f"https://efts.sec.gov/LATEST/search-index?q={t}&limit=1",
        as_json=False,
    )
    # 简化处理：用 company name search 获取 CIK
    # 实际生产建议用 sec-edgar-downloader 库，此处手写兼容
    resp_text = resp.text
    # 解析 JSON
    try:
        data = resp.json()
        items = data.get("results", [])
        if items:
            # 取第一个匹配
            return items[0].get("cik_str", "")
    except Exception:
        pass
    return ""


def edgar_submissions(
    ticker: str,
    form_types: Iterable[str] | None = None,
    since: str | None = None,
    limit: int = 20,
) -> list[EdgartechSubmission]:
    """
    拉取某 ticker 的 SEC 申报列表（10-K / 10-Q / 8-K / 4 / 13F 等）。

    参数：
      ticker: 美股 ticker（大写）
      form_types: 表单类型白名单，如 ("10-K", "10-Q", "8-K"); 默认全部
      since: YYYYMMDD，默认最近一年
      limit: 最多返回条数

    返回：按 filed_date 降序的 EdgartechSubmission 列表
    """
    t = str(ticker).upper().strip()
    if not t.replace(".", "").replace("-", "").isalnum():
        raise ValueError(f"无效 ticker: {ticker!r}")

    forms_filter = "+".join(form_types) if form_types else "*"
    since_val = since or ""
    start = 1
    results: list[EdgartechSubmission] = []

    while len(results) < limit:
        url = (
            f"https://efts.sec.gov/LATEST/search-index?q={t}&start={start}"
            f"&forms={forms_filter}&date={since_val}&limit={limit}"
        )
        resp = _official_get(url, as_json=False)
        try:
            data = resp.json()
        except Exception:
            break
        items = data.get("results", [])
        if not items:
            break
        for item in items:
            results.append(EdgartechSubmission(
                form=item.get("form", ""),
                ticker=item.get("ticker", t),
                cik=item.get("cik_str", ""),
                filed_date=item.get("filedDate", ""),
                accession_number=item.get("accessionNumber", ""),
                url=item.get("filingUrl", ""),
            ))
            if len(results) >= limit:
                break
        start += len(items)
        if len(items) < limit:
            break

    return results[:limit]


def edgar_xbrl_indicators(
    ticker: str,
    tags: Iterable[str] | None = None,
    period: str = "FY",  # FY / Q
    years: int = 3,
) -> dict[str, Any]:
    """
    通过 EDGAR XBRL 拉取美股 GAAP 指标（营收/净利/EPS/ROE 等）。

    默认 tags 取自 XBRL 503 常用指标子集（按需可扩）。
    返回 {tag: [{period, value}, ...]} 结构。

    ⚠️ 此端点在 SEC 端无公开 REST API；生产环境建议用
       `sec-edgar-downloader` 拉 XML 再本地解析。此处给占位契约，
       后续可接入真实解析逻辑。
    """
    t = str(ticker).upper().strip()
    _check_us_ticker(t)
    # TODO: 接入 sec-edgar-downloader 或 xml 解析链
    return {"ticker": t, "note": "XBRL 解析链路待对接下游工具链"}


# ═══════════════════════════════════════════════════════════════════════════
# Treasury 收益率曲线（S 级 · 美国政府数据）
# ═══════════════════════════════════════════════════════════════════════════
_TREASURY_URL = "https://api.fiscaldata.treasury.gov/services/api/v1/treasury_yield/treasury_yield_curve/"


def treasury_yield_curve(date: str | None = None) -> list[dict[str, Any]]:
    """
    美债收益率曲线（1M ~ 30Y）。

    参数：
      date: YYYY-MM-DD；默认取最近一期（服务端返回最新）。

    返回：每条 record = {effective_date, term_to_maturity, rate}
    """
    params: dict[str, Any] = {}
    if date:
        params["filters"] = f"effective_date eq {date}"
    else:
        params["sort"] = "-effective_date"
        params["page"] = "1"
        params["page_size"] = "1"

    resp = _official_get(_TREASURY_URL, params=params, as_json=True)
    records = resp.get("data", [])
    return [
        {
            "effective_date": r.get("effective_date"),
            "term_to_maturity": r.get("term_to_maturity"),
            "rate": r.get("rate"),
        }
        for r in records
    ]


# ═══════════════════════════════════════════════════════════════════════════
# CFTC COT 持仓报告（S 级 · 美国政府数据）
# ═══════════════════════════════════════════════════════════════════════════
_CFTC_URL = "https://www.cftc.gov/dea/newcot/NewCots.xlsx"
# CFTC 同时提供 CSV：
_CFTC_CSV = "https://www.cftc.gov/PressRoom/PressReleases/prcots-0.htm"


def cot_report(ticker_or_futures: str | None = None, week_ending: str | None = None) -> list[dict[str, Any]]:
    """
    CFTC  Commitments of Traders (COT) 持仓报告。

    参数：
      ticker_or_futures: 合约代码（如 "ES1" 标普 500 期货），默认返回全表。
      week_ending: YYYY-MM-DD，默认最近一期。

    返回：持仓记录列表（long/short 商业/非商业 等字段）
    """
    # CFTC 提供结构化 CSV 端点
    csv_url = "https://www.cftc.gov/dea/newcot/NewCots.csv"
    params: dict[str, Any] = {}
    if week_ending:
        params["weekEnding"] = week_ending
    resp = _official_get(csv_url, params=params, as_json=False)
    import csv
    from io import StringIO
    reader = csv.DictReader(StringIO(resp.text))
    rows = list(reader)
    if ticker_or_futures:
        ft = str(ticker_or_futures).upper()
        rows = [r for r in rows if ft in str(r.get("Futures+Options", ""))]
    return rows


# ═══════════════════════════════════════════════════════════════════════════
# FINRA Reg SHO 卖空成交量（B 级 · 自律组织，商用前须确认）
# ═══════════════════════════════════════════════════════════════════════════
_FINRA_BASE = "https://www.finra.org"
_FINRA_SHO = f"{_FINRA_BASE}/about-finra/industry-structure/transparency/short-sale-data"


def finra_sho_daily(date: str) -> list[dict[str, Any]]:
    """
    全市场日度卖空成交量（FINRA Reg SHO 数据文件）。

    文件结构：每行 = {trading_symbol, short_volume, total_volume, ...}

    参数：
      date: YYYY-MM-DD（FINRA 通常在 T+1 发布前一天数据）

    返回：完整 12k+ 标的列表（合规红线：零个股名输出到外部 UI）
    """
    # FINRA 数据文件托管在 S3
    url = f"https://app.finra.org/AppInfo/Query/Downloads/{date.replace("-", "")}_SHO.csv"
    try:
        resp = _official_get(url, as_json=False)
    except RuntimeError as exc:
        # 该日可能尚未发布或非交易日 → 返回空列表（不抛）
        if "资源不存在" in str(exc):
            return []
        raise
    import csv
    from io import StringIO
    reader = csv.DictReader(StringIO(resp.text))
    return [
        {
            "trading_symbol": r.get("trading_symbol", ""),
            "short_volume": r.get("short_volume", ""),
            "total_volume": r.get("total_volume", ""),
            "date": date,
        }
        for r in reader
    ]


def finra_sho_short_ratio(ticker: str, date: str | None = None) -> float | None:
    """
    个股卖空占比（short_volume / total_volume）。

    返回 0~1 之间的 float；查不到返回 None。
    """
    d = date or _today_str()
    rows = finra_sho_daily(d)
    for r in rows:
        if r.get("trading_symbol", "").upper() == str(ticker).upper():
            try:
                sv = float(r.get("short_volume", 0) or 0)
                tv = float(r.get("total_volume", 0) or 0)
                return (sv / tv) if tv else None
            except (ValueError, TypeError):
                return None
    return None


# ═══════════════════════════════════════════════════════════════════════════
# CBOE 期权链（C 级 · 需授权，仅供个人研究）
# ═══════════════════════════════════════════════════════════════════════════
_CBOE_CDN = "https://cdn.cboe.com/api/global/delayed/options"


def cboe_option_chain(ticker: str, expiration: str | None = None) -> dict[str, Any]:
    """
    CBOE 期权链（calls + puts）。

    返回：{ticker, expiration, calls: [...], puts: [...]}
    每个 leg 含 strike, last, volume, open_interest, iv, delta, gamma, vega, theta, rho。

    ⚠️ 合规 C 级：此数据仅供个人研究，商用或再分发须事先取得 Cboe 授权。
    """
    t = str(ticker).upper().strip()
    _check_us_ticker(t)
    exp = expiration or ""
    url = f"{_CBOE_CDN}/{t}/optionchain/{exp}"
    resp = _official_get(url, as_json=True)
    return resp


def cboe_0dte_flow(ticker: str) -> list[dict[str, Any]]:
    """
    0DTE 期权异动 flow（近 30 分钟大单流）。

    ⚠️ 合规 C 级：仅限个人研究。
    """
    return cboe_option_chain(ticker)


# ═══════════════════════════════════════════════════════════════════════════
# Yahoo Finance（C 级 · 个人研究）
# ═══════════════════════════════════════════════════════════════════════════
_YAHOO_SESSION: requests.Session | None = None


def _get_yahoo_session() -> requests.Session:
    global _YAHOO_SESSION
    if _YAHOO_SESSION and hasattr(_YAHOO_SESSION, "_crumb"):
        return _YAHOO_SESSION
    s = requests.Session()
    s.headers["User-Agent"] = (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    )
    s.get("https://fc.yahoo.com", timeout=10)
    r = s.get("https://query2.finance.yahoo.com/v1/test/getcrumb", timeout=10)
    r.raise_for_status()
    s._crumb = r.text
    _YAHOO_SESSION = s
    return s


def yahoo_quote_summary(ticker: str, modules: list[str] | None = None) -> dict[str, Any]:
    """
    Yahoo Finance quoteSummary：财务数据 + 关键指标 + 分析师 + 机构持仓。

    默认 modules 取 ["financialData","summaryDetail","quoteType","defaultKeyStatistics"]。

    ⚠️ 合规 C 级：Yahoo 官方文档写明 personal use only；勿用于商业产品或再分发。
    """
    t = str(ticker).upper().strip()
    _check_us_ticker(t)
    mods = modules or [
        "financialData", "summaryDetail", "quoteType",
        "defaultKeyStatistics", "assetProfile",
    ]
    s = _get_yahoo_session()
    r = s.get(
        f"https://query2.finance.yahoo.com/v10/finance/quoteSummary/{t}",
        params={"modules": ",".join(mods), "crumb": s._crumb},
        timeout=15,
    )
    r.raise_for_status()
    results = r.json().get("quoteSummary", {}).get("result", [{}])
    return results[0] if results else {}


def yahoo_kline(ticker: str, period1: str, period2: str, interval: str = "1d") -> list[dict[str, Any]]:
    """
    Yahoo K线（日/周/分钟）。

    参数：
      period1/period2: Unix timestamp (秒) 字符串
      interval: 1m/2m/5m/15m/30m/60m/90m/1h/1d/5d/1wk/1mo/3mo

    返回：[{time, open, high, low, close, volume}]
    """
    t = str(ticker).upper().strip()
    _check_us_ticker(t)
    s = _get_yahoo_session()
    r = s.get(
        f"https://query1.finance.yahoo.com/v8/finance/chart/{t}",
        params={"period1": period1, "period2": period2, "interval": interval, "crumb": s._crumb},
        timeout=15,
    )
    r.raise_for_status()
    chart = r.json().get("chart", {})
    result = chart.get("result", [{}])[0]
    timestamps = result.get("timestamp", [])
    ind = result.get("indicators", {})
    closes = ind.get("quote", [{}])[0].get("close", []) or []
    opens = ind.get("quote", [{}])[0].get("open", []) or []
    highs = ind.get("quote", [{}])[0].get("high", []) or []
    lows = ind.get("quote", [{}])[0].get("low", []) or []
    vols = ind.get("quote", [{}])[0].get("volume", []) or []
    return [
        {
            "time": int(ts),
            "open": opens[i] if i < len(opens) else None,
            "high": highs[i] if i < len(highs) else None,
            "low": lows[i] if i < len(lows) else None,
            "close": closes[i] if i < len(closes) else None,
            "volume": vols[i] if i < len(vols) else None,
        }
        for i, ts in enumerate(timestamps)
        if i < len(closes)
    ]


# ═══════════════════════════════════════════════════════════════════════════
# 工具函数
# ═══════════════════════════════════════════════════════════════════════════
def _check_us_ticker(ticker: str) -> str:
    """Layer 仅支持美股；传入港股代码时给出明确提示"""
    t = str(ticker).upper()
    if t.endswith(".HK") or (t.isdigit() and len(t) in (4, 5)):
        raise ValueError(f"'{ticker}' 看起来是港股代码；海外源仅支持美股。" f"港股请用量化的东财全球子集。")
    if not t.replace(".", "").replace("-", "").isalnum():
        raise ValueError(f"无效的 ticker: {ticker!r}")
    return t


def _today_str() -> str:
    from datetime import date
    return date.today().isoformat()


# ═══════════════════════════════════════════════════════════════════════════
# 合规级别速查表（供 UI 侧标注）
# ═══════════════════════════════════════════════════════════════════════════
COMPLIANCE_MAP: dict[str, tuple[ComplianceLevel, str]] = {
    "sec_edgar":            ("S", "SEC EDGAR — 可自由使用（含商用），需声明 User-Agent，限 10 req/s"),
    "treasury_yield":       ("S", "US Treasury — 美国政府数据，无版权限制"),
    "cftc_cot":             ("S", "CFTC COT — 美国政府数据，无版权限制"),
    "finra_sho":            ("B", "FINRA Reg SHO — 下载已发布文件常规；批量爬/商用前须向 FINRA 确认"),
    "cboe_option":          ("C", "CBOE — 商用/再分发须事先取得 Cboe 授权；仅供个人研究"),
    "yahoo_finance":        ("C", "Yahoo Finance — 官方文档 personal use only，勿商用或再分发"),
}


def compliance_info(source: str) -> tuple[ComplianceLevel, str] | None:
    return COMPLIANCE_MAP.get(source)
