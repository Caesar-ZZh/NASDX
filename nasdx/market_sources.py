"""
Resilient A-share market data sources for NASDX.

The primary Eastmoney K-line endpoint is occasionally unstable behind local
proxy settings. This module keeps the rest of the project on a single normalized
Chinese-column DataFrame contract while trying several AkShare sources.
"""
from __future__ import annotations

from functools import lru_cache
from html.parser import HTMLParser
import json
from typing import Callable, Sequence

import akshare as ak
import pandas as pd
import requests

from nasdx.market_symbols import market_symbol, resolve_exchange


BSE_CODE_MAPPING_URL = "https://www.bse.cn/service/code_mapping.html"


HistFetcher = Callable[[str, str, str, float], pd.DataFrame | None]


def fetch_stock_hist(
    code: str,
    start_date: str,
    end_date: str,
    min_rows: int = 10,
    request_timeout: float = 6.0,
    sources: Sequence[str] = ("tencent_hist_tx", "eastmoney_hist"),
) -> tuple[pd.DataFrame | None, str | None]:
    """Fetch A-share daily K-line data and normalize it to Chinese columns."""
    fetchers = {
        "tencent_hist_tx": _fetch_tencent_hist,
        "eastmoney_hist": _fetch_eastmoney_hist,
    }
    for source in sources:
        fetcher = fetchers.get(source)
        if fetcher is None:
            continue
        try:
            df = fetcher(code, start_date, end_date, request_timeout)
        except Exception:
            continue
        if _is_usable(df, min_rows):
            return df, source
    return None, None


def last_trade_date(df: pd.DataFrame | None) -> str | None:
    """Return the latest K-line date as YYYY-MM-DD."""
    if df is None or df.empty or "日期" not in df.columns:
        return None
    value = df["日期"].iloc[-1]
    try:
        return pd.to_datetime(value).strftime("%Y-%m-%d")
    except Exception:
        return str(value)


class _BseMappingTableParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.rows: list[list[str]] = []
        self._table_depth = 0
        self._row: list[str] | None = None
        self._cell: list[str] | None = None

    def handle_starttag(self, tag: str, attrs) -> None:
        if tag == "table":
            self._table_depth += 1
        elif self._table_depth and tag == "tr":
            self._row = []
        elif self._row is not None and tag in {"td", "th"}:
            self._cell = []

    def handle_data(self, data: str) -> None:
        if self._cell is not None:
            self._cell.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag in {"td", "th"} and self._cell is not None and self._row is not None:
            self._row.append("".join(self._cell).strip())
            self._cell = None
        elif tag == "tr" and self._row is not None:
            if self._row:
                self.rows.append(self._row)
            self._row = None
        elif tag == "table" and self._table_depth:
            self._table_depth -= 1


def _parse_bse_code_mapping(content: bytes) -> dict[str, str]:
    """Parse the official BSE old-to-new security code table."""
    parser = _BseMappingTableParser()
    parser.feed(content.decode("utf-8", errors="replace"))
    for header_index, row in enumerate(parser.rows):
        if "旧代码" not in row or "新代码" not in row:
            continue
        old_index = row.index("旧代码")
        new_index = row.index("新代码")
        mapping: dict[str, str] = {}
        for values in parser.rows[header_index + 1 :]:
            if max(old_index, new_index) >= len(values):
                continue
            old = values[old_index].split(".", 1)[0].strip().zfill(6)
            new = values[new_index].split(".", 1)[0].strip().zfill(6)
            if old.isdigit() and new.isdigit():
                mapping[old] = new
        if mapping:
            return mapping
    raise ValueError("BSE code mapping table not found")


@lru_cache(maxsize=4)
def _get_bse_code_mapping(request_timeout: float = 6.0) -> dict[str, str]:
    response = requests.get(
        BSE_CODE_MAPPING_URL,
        timeout=request_timeout,
        headers={"User-Agent": "Mozilla/5.0 NASDX"},
    )
    response.raise_for_status()
    return _parse_bse_code_mapping(response.content)


def _tencent_history_symbols(code: str, timeout: float) -> list[str]:
    normalized = str(code).strip().lower()
    bare_code = normalized[2:] if normalized[:2] in {"sh", "sz", "bj"} else normalized
    symbols = []
    if resolve_exchange(bare_code) == "BSE" and bare_code.startswith(("4", "8")):
        try:
            current_code = _get_bse_code_mapping(timeout).get(bare_code)
        except Exception:
            current_code = None
        if current_code:
            symbols.append(market_symbol(current_code))
    symbols.append(market_symbol(bare_code))
    return list(dict.fromkeys(symbols))


def _fetch_tencent_hist(code: str, start_date: str, end_date: str, timeout: float) -> pd.DataFrame | None:
    year = start_date[:4]
    for symbol in _tencent_history_symbols(code, timeout):
        response = requests.get(
            "https://proxy.finance.qq.com/ifzqgtimg/appstock/app/newfqkline/get",
            params={
                "_var": f"kline_dayqfq{year}",
                "param": f"{symbol},day,{year}-01-01,{int(year) + 2}-12-31,640,qfq",
                "r": "0.8205512681390605",
            },
            timeout=timeout,
        )
        response.raise_for_status()
        payload = json.loads(response.text.split("=", 1)[-1])
        symbol_payload = payload.get("data", {}).get(symbol, {})
        rows = symbol_payload.get("qfqday") or symbol_payload.get("day") or symbol_payload.get("hfqday") or []
        df = pd.DataFrame(rows)
        if not isinstance(df, pd.DataFrame) or df.empty:
            continue
        df = df.iloc[:, :6]
        df.columns = ["date", "open", "close", "high", "low", "amount"]
        dates = pd.to_datetime(df["date"], errors="coerce")
        start = pd.to_datetime(start_date)
        end = pd.to_datetime(end_date)
        df = df.loc[dates.between(start, end)].reset_index(drop=True)
        normalized = pd.DataFrame(
            {
                "日期": pd.to_datetime(df["date"]).dt.strftime("%Y-%m-%d"),
                "开盘": pd.to_numeric(df["open"], errors="coerce"),
                "收盘": pd.to_numeric(df["close"], errors="coerce"),
                "最高": pd.to_numeric(df["high"], errors="coerce"),
                "最低": pd.to_numeric(df["low"], errors="coerce"),
                "成交量": pd.to_numeric(df["amount"], errors="coerce"),
            }
        )
        return _finalize(normalized)
    return None


def _fetch_eastmoney_hist(code: str, start_date: str, end_date: str, timeout: float) -> pd.DataFrame | None:
    df = ak.stock_zh_a_hist(
        symbol=code,
        period="daily",
        start_date=start_date,
        end_date=end_date,
        adjust="qfq",
        timeout=timeout,
    )
    if not isinstance(df, pd.DataFrame) or df.empty:
        return None
    return _finalize(df.copy())


def _finalize(df: pd.DataFrame) -> pd.DataFrame:
    df = df.dropna(subset=["日期", "收盘"]).copy()
    df["日期"] = pd.to_datetime(df["日期"]).dt.strftime("%Y-%m-%d")
    for column in ("开盘", "收盘", "最高", "最低", "成交量", "成交额", "换手率"):
        if column in df.columns:
            df[column] = pd.to_numeric(df[column], errors="coerce")
    df = df.sort_values("日期").reset_index(drop=True)
    if "涨跌幅" not in df.columns:
        df["涨跌幅"] = df["收盘"].pct_change().fillna(0) * 100
    else:
        df["涨跌幅"] = pd.to_numeric(df["涨跌幅"], errors="coerce").fillna(0)
    return df


def _is_usable(df: pd.DataFrame | None, min_rows: int) -> bool:
    if not isinstance(df, pd.DataFrame) or len(df) < min_rows:
        return False
    required = {"日期", "收盘", "成交量", "涨跌幅"}
    return required.issubset(df.columns)
