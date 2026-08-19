"""NASDX 市场研究驾驶舱的 Streamlit 页面组件。

页面复用现有 Python/Streamlit 运行链，不引入 React、Node 或前端构建步骤。
个股榜单按产品红线替换为市场级成交额聚合；产业链只展示环节 taxonomy，
不预置证券代码或名称。
"""

from __future__ import annotations

import math
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Mapping, Optional


BEIJING = timezone(timedelta(hours=8))
CACHE_TTL_SECONDS = 300.0
FLASH_NEWS_URL = "https://api-one-wscn.awtmt.com/apiv1/content/lives"

_CACHE: dict[str, tuple[float, Any]] = {}

_DOMESTIC_INDICES = (
    ("sh000001", "上证指数"),
    ("sz399001", "深证成指"),
    ("sz399006", "创业板指"),
    ("sh000300", "沪深300"),
)

_INDUSTRY_CHAINS = (
    {
        "theme": "人工智能",
        "segments": (
            ("上游", "算力芯片、服务器、光通信"),
            ("中游", "数据中心、模型平台、开发工具"),
            ("下游", "企业软件、智能终端、行业应用"),
        ),
    },
    {
        "theme": "半导体",
        "segments": (
            ("上游", "材料、设备、EDA"),
            ("中游", "设计、制造、封装测试"),
            ("下游", "消费电子、汽车电子、工业控制"),
        ),
    },
    {
        "theme": "新能源",
        "segments": (
            ("上游", "关键矿物、硅料、基础材料"),
            ("中游", "电池、组件、储能系统"),
            ("下游", "电站、电网、交通电动化"),
        ),
    },
    {
        "theme": "创新药",
        "segments": (
            ("上游", "靶点研究、试剂、临床前服务"),
            ("中游", "临床开发、生产、质量管理"),
            ("下游", "商业化、医疗服务、支付"),
        ),
    },
)

PANEL_ORDER = (
    "domestic_indices",
    "global_indices",
    "market_breadth",
    "sector_funds",
    "turnover",
    "flash_news",
    "commodities",
    "treasury_curve",
    "industry_chains",
)

PANEL_LABELS = {
    "domestic_indices": "A 股关键指数",
    "global_indices": "全球关键指数",
    "market_breadth": "市场宽度",
    "sector_funds": "板块资金",
    "turnover": "成交额集中度",
    "flash_news": "7×24 快讯",
    "commodities": "大宗商品概览",
    "treasury_curve": "美债收益率曲线",
    "industry_chains": "产业链全景",
}


def _monotonic() -> float:
    return time.monotonic()


def _updated_at() -> str:
    return datetime.now(BEIJING).strftime("%Y-%m-%d %H:%M:%S")


def _cached(key: str, loader: Callable[[], Any]) -> Any:
    now = _monotonic()
    hit = _CACHE.get(key)
    if hit is not None and now - hit[0] < CACHE_TTL_SECONDS:
        return hit[1]
    value = loader()
    if value:
        _CACHE[key] = (now, value)
    return value


def clear_dashboard_cache() -> None:
    _CACHE.clear()


def _number(value: Any) -> Optional[float]:
    if isinstance(value, bool):
        return None
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def _load_domestic_indices() -> list[dict[str, Any]]:
    def load() -> list[dict[str, Any]]:
        from nasdx.fast_market import fetch_tencent_quotes

        quotes = fetch_tencent_quotes(
            [code for code, _name in _DOMESTIC_INDICES],
            request_timeout=4.0,
            max_workers=1,
        )
        result: list[dict[str, Any]] = []
        for qualified_code, label in _DOMESTIC_INDICES:
            bare_code = qualified_code[2:]
            quote = quotes.get(bare_code)
            if not isinstance(quote, Mapping):
                continue
            result.append(
                {
                    "key": qualified_code,
                    "name": label,
                    "price": _number(quote.get("close")),
                    "change_pct": _number(quote.get("change_pct")),
                    "data_as_of": str(quote.get("quote_time") or ""),
                }
            )
        return result

    return _cached("dashboard:domestic_indices", load)


def _load_global_indices() -> list[dict[str, Any]]:
    from nasdx.global_market import global_indices

    return global_indices()


def _load_market_breadth() -> dict[str, Any]:
    from nasdx.daily_review import get_overview

    overview = get_overview()
    return dict(overview.get("sentiment") or {}) if isinstance(overview, Mapping) else {}


def _load_sector_funds() -> list[dict[str, Any]]:
    from nasdx.daily_review import get_overview

    overview = get_overview()
    sectors = overview.get("sectors") if isinstance(overview, Mapping) else None
    return [dict(item) for item in sectors if isinstance(item, Mapping)][:12] if isinstance(sectors, list) else []


def _load_turnover() -> dict[str, Any]:
    from nasdx.daily_review import get_turnover_top

    return get_turnover_top(20)


def _parse_flash_news(payload: Mapping[str, Any], limit: int) -> list[dict[str, Any]]:
    data = payload.get("data")
    if isinstance(data, Mapping):
        raw_items = data.get("items") or data.get("lives") or data.get("list") or []
    elif isinstance(data, list):
        raw_items = data
    else:
        raw_items = []
    result: list[dict[str, Any]] = []
    for item in raw_items:
        if not isinstance(item, Mapping):
            continue
        title = str(
            item.get("title")
            or item.get("content_text")
            or item.get("content")
            or ""
        ).strip()
        if not title:
            continue
        timestamp = (
            item.get("display_time")
            or item.get("created_at")
            or item.get("time")
            or item.get("published_at")
        )
        source = str(item.get("source") or item.get("author") or "华尔街见闻").strip()
        result.append(
            {
                "id": str(item.get("id") or item.get("uri") or len(result)),
                "published_at": str(timestamp or ""),
                "title": title[:240],
                "source": source[:80],
            }
        )
        if len(result) >= limit:
            break
    return result


def _load_flash_news(limit: int = 30) -> list[dict[str, Any]]:
    def load() -> list[dict[str, Any]]:
        import requests

        try:
            response = requests.get(
                FLASH_NEWS_URL,
                params={"channel": "global-channel", "limit": limit},
                headers={"User-Agent": "Mozilla/5.0 NASDX"},
                timeout=4.0,
            )
            response.raise_for_status()
            payload = response.json()
        except Exception:
            return []
        return _parse_flash_news(payload, limit) if isinstance(payload, Mapping) else []

    return _cached(f"dashboard:flash_news:{limit}", load)


def _load_commodities() -> dict[str, Any]:
    from nasdx.commodity_100ppi import fetch_list

    rows = fetch_list()
    changes = [
        value
        for item in rows
        if isinstance(item, Mapping)
        for value in [_number(item.get("change_pct"))]
        if value is not None
    ]
    return {
        "count": len(rows),
        "up": sum(1 for value in changes if value > 0),
        "down": sum(1 for value in changes if value < 0),
        "flat": sum(1 for value in changes if value == 0),
        "average_change_pct": (
            round(sum(changes) / len(changes), 4) if changes else None
        ),
    }


def _load_treasury_curve() -> list[dict[str, Any]]:
    from nasdx.overseas_sources import treasury_yield_curve

    rows = treasury_yield_curve()
    return [
        {
            "effective_date": str(item.get("effective_date") or ""),
            "term": str(item.get("term_to_maturity") or ""),
            "rate": _number(item.get("rate")),
        }
        for item in rows
        if isinstance(item, Mapping)
    ]


def _load_industry_chains() -> list[dict[str, str]]:
    return [
        {"theme": chain["theme"], "stage": stage, "scope": scope}
        for chain in _INDUSTRY_CHAINS
        for stage, scope in chain["segments"]
    ]


DEFAULT_LOADERS: dict[str, Callable[[], Any]] = {
    "domestic_indices": _load_domestic_indices,
    "global_indices": _load_global_indices,
    "market_breadth": _load_market_breadth,
    "sector_funds": _load_sector_funds,
    "turnover": _load_turnover,
    "flash_news": _load_flash_news,
    "commodities": _load_commodities,
    "treasury_curve": _load_treasury_curve,
    "industry_chains": _load_industry_chains,
}


def _panel_result(name: str, loader: Callable[[], Any]) -> dict[str, Any]:
    try:
        data = loader()
    except ModuleNotFoundError as exc:
        return {
            "label": PANEL_LABELS[name],
            "status": "dependency_pending",
            "data": None,
            "detail": str(exc.name or "optional module"),
        }
    except Exception as exc:
        return {
            "label": PANEL_LABELS[name],
            "status": "unavailable",
            "data": None,
            "detail": type(exc).__name__,
        }
    if not data:
        return {
            "label": PANEL_LABELS[name],
            "status": "unavailable",
            "data": None,
            "detail": "empty_source_result",
        }
    return {
        "label": PANEL_LABELS[name],
        "status": "ready",
        "data": data,
        "detail": "",
    }


def build_dashboard_snapshot(
    loaders: Optional[Mapping[str, Callable[[], Any]]] = None,
) -> dict[str, Any]:
    """并行装配一屏式快照，单个面板失败不阻断其余区域。"""
    resolved = dict(DEFAULT_LOADERS if loaders is None else loaders)
    results: dict[str, dict[str, Any]] = {}
    with ThreadPoolExecutor(max_workers=min(6, max(1, len(PANEL_ORDER)))) as executor:
        futures = {
            name: executor.submit(_panel_result, name, resolved[name])
            for name in PANEL_ORDER
            if name in resolved
        }
        for name in PANEL_ORDER:
            future = futures.get(name)
            if future is None:
                results[name] = {
                    "label": PANEL_LABELS[name],
                    "status": "dependency_pending",
                    "data": None,
                    "detail": "loader_not_configured",
                }
            else:
                results[name] = future.result()
    ready = sum(1 for panel in results.values() if panel["status"] == "ready")
    return {
        "schema_version": "nasdx_market_dashboard.v1",
        "status": "complete" if ready == len(PANEL_ORDER) else "partial",
        "updated": _updated_at(),
        "ready_panels": ready,
        "total_panels": len(PANEL_ORDER),
        "panels": results,
        "compliance": {
            "individual_rankings": False,
            "recommendations": False,
            "predictions": False,
        },
    }


_DASHBOARD_CSS = """
<style>
.block-container {max-width: 1680px; padding-top: 1.2rem; padding-bottom: 2rem;}
[data-testid="stMetric"] {
  background: linear-gradient(145deg, rgba(16,30,50,.92), rgba(8,18,32,.96));
  border: 1px solid rgba(56,189,248,.18); border-radius: 12px; padding: .75rem;
}
[data-testid="stDataFrame"] {border: 1px solid rgba(148,163,184,.14); border-radius: 10px;}
@media (max-width: 720px) {
  .block-container {padding-left: .7rem; padding-right: .7rem;}
}
</style>
"""


def _panel_data(snapshot: Mapping[str, Any], name: str) -> Any:
    panels = snapshot.get("panels")
    panel = panels.get(name) if isinstance(panels, Mapping) else None
    return panel.get("data") if isinstance(panel, Mapping) else None


def _show_panel_state(st: Any, snapshot: Mapping[str, Any], name: str) -> bool:
    panels = snapshot.get("panels")
    panel = panels.get(name) if isinstance(panels, Mapping) else None
    if isinstance(panel, Mapping) and panel.get("status") == "ready":
        return True
    status = panel.get("status") if isinstance(panel, Mapping) else "unavailable"
    st.info(f"{PANEL_LABELS[name]}：{status}")
    return False


def _render_index_metrics(st: Any, items: list[dict[str, Any]]) -> None:
    columns = st.columns(min(5, max(1, len(items))))
    for column, item in zip(columns, items):
        price = item.get("price")
        change = item.get("change_pct")
        column.metric(
            str(item.get("name") or item.get("key") or "指数"),
            "—" if price is None else f"{float(price):,.2f}",
            None if change is None else f"{float(change):+.2f}%",
        )


def render_market_dashboard(
    snapshot: Optional[Mapping[str, Any]] = None,
    *,
    st_module: Any = None,
) -> dict[str, Any]:
    """渲染 Streamlit 大屏组件；返回实际使用的快照便于测试和嵌入。"""
    if st_module is None:
        import streamlit as st_module
    st = st_module
    st.markdown(_DASHBOARD_CSS, unsafe_allow_html=True)
    st.title("市场研究驾驶舱")
    st.caption("客观市场数据同屏 · 不含个股榜单、投资建议或价格预测")

    if st.button("刷新大屏", use_container_width=False):
        clear_dashboard_cache()
        snapshot = None
    resolved = dict(snapshot) if isinstance(snapshot, Mapping) else build_dashboard_snapshot()
    st.caption(
        f"数据时间 {resolved.get('updated', '')} · "
        f"可用面板 {resolved.get('ready_panels', 0)}/{resolved.get('total_panels', 0)}"
    )

    st.subheader("关键指数")
    domestic = _panel_data(resolved, "domestic_indices")
    if _show_panel_state(st, resolved, "domestic_indices") and isinstance(domestic, list):
        _render_index_metrics(st, domestic)
    global_items = _panel_data(resolved, "global_indices")
    if _show_panel_state(st, resolved, "global_indices") and isinstance(global_items, list):
        _render_index_metrics(st, global_items)

    left, right = st.columns(2, gap="medium")
    with left:
        st.subheader("市场宽度与成交额")
        breadth = _panel_data(resolved, "market_breadth")
        if _show_panel_state(st, resolved, "market_breadth") and isinstance(breadth, Mapping):
            metric_cols = st.columns(3)
            metric_cols[0].metric("上涨家数", breadth.get("up", "—"))
            metric_cols[1].metric("下跌家数", breadth.get("down", "—"))
            metric_cols[2].metric("宽度状态", breadth.get("breadth", "—"))
        turnover = _panel_data(resolved, "turnover")
        if _show_panel_state(st, resolved, "turnover") and isinstance(turnover, Mapping):
            metric_cols = st.columns(3)
            metric_cols[0].metric("聚合样本", turnover.get("sample_size", "—"))
            metric_cols[1].metric("成交额合计", turnover.get("total_amount", "—"))
            share = turnover.get("market_amount_share")
            metric_cols[2].metric(
                "全市场占比",
                "—" if share is None else f"{float(share):.2%}",
            )
    with right:
        st.subheader("板块资金")
        sectors = _panel_data(resolved, "sector_funds")
        if _show_panel_state(st, resolved, "sector_funds") and isinstance(sectors, list):
            st.dataframe(sectors, use_container_width=True, hide_index=True)

    left, right = st.columns(2, gap="medium")
    with left:
        st.subheader("7×24 快讯")
        flashes = _panel_data(resolved, "flash_news")
        if _show_panel_state(st, resolved, "flash_news") and isinstance(flashes, list):
            for item in flashes[:15]:
                st.write(
                    f"{item.get('published_at', '')} · {item.get('source', '')} · "
                    f"{item.get('title', '')}"
                )
    with right:
        st.subheader("美债收益率曲线")
        curve = _panel_data(resolved, "treasury_curve")
        if _show_panel_state(st, resolved, "treasury_curve") and isinstance(curve, list):
            st.dataframe(curve, use_container_width=True, hide_index=True)

    left, right = st.columns(2, gap="medium")
    with left:
        st.subheader("大宗商品概览")
        commodities = _panel_data(resolved, "commodities")
        if _show_panel_state(st, resolved, "commodities") and isinstance(commodities, Mapping):
            metric_cols = st.columns(4)
            metric_cols[0].metric("覆盖品类", commodities.get("count", "—"))
            metric_cols[1].metric("上涨", commodities.get("up", "—"))
            metric_cols[2].metric("下跌", commodities.get("down", "—"))
            average = commodities.get("average_change_pct")
            metric_cols[3].metric(
                "平均变动",
                "—" if average is None else f"{float(average):+.2f}%",
            )
    with right:
        st.subheader("产业链全景")
        chains = _panel_data(resolved, "industry_chains")
        if _show_panel_state(st, resolved, "industry_chains") and isinstance(chains, list):
            st.dataframe(chains, use_container_width=True, hide_index=True)

    return resolved


def main() -> None:
    import streamlit as st

    st.set_page_config(
        page_title="NASDX 市场研究驾驶舱",
        page_icon="🌐",
        layout="wide",
    )
    render_market_dashboard(st_module=st)


if __name__ == "__main__":
    main()
