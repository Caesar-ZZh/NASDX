"""
数据加载器 — 读取 fetch_stock_data.py 生成的 JSON 文件
并为各 Agent 提供结构化数据视图
"""
import json
from typing import Any, Dict, List, Optional

from nasdx.paths import get_market_data_dir


def load_latest_data() -> Dict[str, Any]:
    """加载最新的 stock_data_YYYYMMDD.json"""
    files = sorted(get_market_data_dir().glob("stock_data_*.json"))
    if not files:
        raise FileNotFoundError("未找到股票数据文件，请先运行 fetch_stock_data.py")
    with open(files[-1], "r", encoding="utf-8") as f:
        return json.load(f)


def _enrich_indicators_from_fund_flow(stock: Dict[str, Any]) -> Dict[str, Any]:
    """若 indicators 为空，从资金流向数据补全收盘价和涨跌幅"""
    indicators = stock.get("indicators", {})
    fund_flow = stock.get("fund_flow", [])
    if not indicators and fund_flow:
        last = fund_flow[-1]
        indicators = {
            "close": last.get("收盘价", 0),
            "change_pct": last.get("涨跌幅", 0),
        }
        stock["indicators"] = indicators
        stock["_indicators_from_fund_flow"] = True
    return stock


def get_stock_data(data: Dict[str, Any], stock_code: str) -> Optional[Dict[str, Any]]:
    """从完整数据中提取单只股票的数据"""
    for sector in data.get("sectors", []):
        for stock in sector.get("stocks", []):
            if stock.get("code") == stock_code:
                stock["sector_name"] = sector.get("name", "未知板块")
                return _enrich_indicators_from_fund_flow(stock)
        for etf in sector.get("etfs", []):
            if etf.get("code") == stock_code:
                etf["sector_name"] = sector.get("name", "未知板块")
                return _enrich_indicators_from_fund_flow(etf)
    return None


def get_sector_data(data: Dict[str, Any], sector_name: str) -> Optional[Dict[str, Any]]:
    """获取整个板块的数据"""
    for sector in data.get("sectors", []):
        if sector.get("name") == sector_name:
            return sector
    return None


def get_market_overview(data: Dict[str, Any]) -> Dict[str, Any]:
    """获取大盘概览"""
    return data.get("market_overview", {})


def _get(ind: Dict, *keys, default=None):
    """兼容新旧字段名，按顺序尝试"""
    for k in keys:
        if ind.get(k) is not None:
            return ind[k]
    return default


def format_indicators(indicators: Dict[str, Any]) -> str:
    """将技术指标格式化为可读文本（兼容新旧字段名）"""
    if not indicators or indicators.get("error"):
        return "暂无技术指标数据"
    lines = []
    # 兼容旧字段名 current_price/price_change_pct/rsi14/macd_dif/macd_dea
    mapping = {
        "close":      (("close", "current_price"),       "最新收盘价", ".2f"),
        "change_pct": (("change_pct","price_change_pct"),"涨跌幅",    ".2f"),
        "ma5":        (("ma5",),   "MA5",    ".2f"),
        "ma10":       (("ma10",),  "MA10",   ".2f"),
        "ma20":       (("ma20",),  "MA20",   ".2f"),
        "ma60":       (("ma60",),  "MA60",   ".2f"),
        "macd_bar":   (("macd_bar",),         "MACD柱",  ".4f"),
        "dif":        (("dif","macd_dif"),     "DIF",     ".4f"),
        "dea":        (("dea","macd_dea"),     "DEA",     ".4f"),
        "rsi":        (("rsi","rsi14"),        "RSI14",   ".1f"),
        "boll_upper": (("boll_upper",),        "布林上轨", ".2f"),
        "boll_lower": (("boll_lower",),        "布林下轨", ".2f"),
        "vol_ratio":  (("vol_ratio",),         "量比",    ".2f"),
        "up_days_20": (("up_days_20",),        "20日上涨天数", "d"),
    }
    for _key, (keys, label, fmt) in mapping.items():
        val = _get(indicators, *keys)
        if val is None:
            continue
        try:
            if fmt.endswith("%"):
                lines.append(f"  {label}: {val:.2f}%")
            elif fmt == "d":
                lines.append(f"  {label}: {int(val)}天")
            else:
                lines.append(f"  {label}: {val:{fmt}}")
        except Exception:
            lines.append(f"  {label}: {val}")
    return "\n".join(lines) if lines else "暂无有效指标"


def format_fund_flow(fund_flow: List[Dict], days: int = 5) -> str:
    """格式化近N日资金流向"""
    if not fund_flow:
        return "暂无资金流向数据（科创板/ETF 不支持）"
    recent = fund_flow[-days:]
    lines = []
    for row in recent:
        date = row.get("日期", "?")
        main_net = row.get("主力净流入-净额", 0)
        main_pct = row.get("主力净流入-净占比", 0)
        close = row.get("收盘价", 0)
        chg = row.get("涨跌幅", 0)
        sign = "↑" if main_net > 0 else "↓"
        lines.append(
            f"  {date} 收盘{close:.2f} 涨跌{chg:+.2f}% "
            f"主力{sign}{abs(main_net)/1e8:.2f}亿({main_pct:+.1f}%)"
        )
    return "\n".join(lines)


def format_kline_summary(indicators: Dict[str, Any]) -> str:
    """生成 K 线摘要（供 Battle 环境使用，兼容新旧字段名）"""
    close     = _get(indicators, "close", "current_price") or 0
    ma5       = _get(indicators, "ma5")
    ma20      = _get(indicators, "ma20")
    rsi       = _get(indicators, "rsi", "rsi14")
    macd      = _get(indicators, "macd_bar") or 0
    vol_ratio = _get(indicators, "vol_ratio") or 1

    parts = [f"收盘价{close:.2f}"]
    if ma5 and ma20:
        trend = "多头" if ma5 > ma20 else "空头"
        parts.append(f"均线{trend}排列(MA5={ma5:.2f}/MA20={ma20:.2f})")
    if rsi:
        if rsi > 70:
            parts.append(f"RSI={rsi:.0f}超买")
        elif rsi < 30:
            parts.append(f"RSI={rsi:.0f}超卖")
        else:
            parts.append(f"RSI={rsi:.0f}中性")
    if macd > 0:
        parts.append(f"MACD金叉({macd:+.4f})")
    else:
        parts.append(f"MACD死叉({macd:+.4f})")
    parts.append(f"量比{vol_ratio:.2f}")
    return "，".join(parts)
