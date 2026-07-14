"""Bounded, low-latency market data helpers for interactive workflows."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from io import BytesIO
import json
import os
from pathlib import Path
import time
from typing import Callable, Iterable, Sequence

import pandas as pd
import requests

from nasdx.market_sources import fetch_stock_hist, fetch_tdxrs_histories
from nasdx.market_symbols import market_symbol, resolve_exchange


TENCENT_QUOTE_URL = "https://qt.gtimg.cn/q="
SSE_LIST_URL = "https://query.sse.com.cn/sseQuery/commonQuery.do"
SZSE_LIST_URL = "https://www.szse.cn/api/report/ShowReport"
BSE_LIST_URL = "https://www.bse.cn/nqxxController/nqxxCnzq.do"
LISTING_CACHE_SCHEMA = "nasdx.a_share_listings.v2"
BSE_JSONP_CALLBACK = "nasdxBseCallback"
_LAST_LISTING_COVERAGE: dict = {}


def parse_tencent_quotes(payload: str) -> dict[str, dict]:
    quotes: dict[str, dict] = {}
    for line in str(payload or "").splitlines():
        if '="' not in line:
            continue
        raw = line.split('="', 1)[1].rsplit('"', 1)[0]
        fields = raw.split("~")
        if len(fields) < 39:
            continue
        code = fields[2].strip()
        try:
            close = float(fields[3])
            change_pct = float(fields[32])
            amount = float(fields[37]) * 10_000
            turnover = float(fields[38] or 0)
        except (TypeError, ValueError):
            continue
        if not code or close <= 0:
            continue
        quotes[code] = {
            "code": code,
            "name": fields[1].strip() or code,
            "close": close,
            "change_pct": change_pct,
            "amount": amount,
            "turnover": turnover,
            "high": _float_or_none(fields[33]),
            "low": _float_or_none(fields[34]),
            "quote_time": fields[30].strip(),
            "data_source": "tencent_quote",
        }
    return quotes


def fetch_tencent_quotes(
    codes: Iterable[str],
    *,
    request_timeout: float = 4.0,
    chunk_size: int = 500,
    max_workers: int = 12,
) -> dict[str, dict]:
    symbols = list(dict.fromkeys(market_symbol(code) for code in codes if str(code).strip()))
    chunks = [symbols[index : index + chunk_size] for index in range(0, len(symbols), chunk_size)]

    def fetch_chunk(chunk: list[str], timeout: float) -> dict[str, dict]:
        response = requests.get(
            TENCENT_QUOTE_URL + ",".join(chunk),
            timeout=timeout,
            headers={"User-Agent": "Mozilla/5.0 NASDX"},
        )
        response.raise_for_status()
        response.encoding = "gbk"
        return parse_tencent_quotes(response.text)

    def run_chunks(pending: list[list[str]], timeout: float, workers: int) -> dict[str, dict]:
        batch_quotes: dict[str, dict] = {}
        if not pending:
            return batch_quotes
        with ThreadPoolExecutor(max_workers=min(workers, len(pending))) as executor:
            futures = [executor.submit(fetch_chunk, chunk, timeout) for chunk in pending]
            for future in as_completed(futures):
                try:
                    batch_quotes.update(future.result())
                except Exception:
                    continue
        return batch_quotes

    quotes = run_chunks(chunks, request_timeout, max_workers)
    missing = [symbol for symbol in symbols if symbol[2:] not in quotes]
    if missing:
        retry_size = min(chunk_size, 100)
        retry_chunks = [missing[index : index + retry_size] for index in range(0, len(missing), retry_size)]
        quotes.update(run_chunks(retry_chunks, max(6.0, request_timeout * 2), max(1, max_workers // 2)))
    return quotes


def fetch_histories(
    codes: Sequence[str],
    start_date: str,
    end_date: str,
    *,
    request_timeout: float = 4.0,
    max_workers: int = 20,
    min_rows: int = 20,
    sources: Sequence[str] = ("tdxrs", "tencent_hist_tx"),
    hist_fetcher: Callable = fetch_stock_hist,
    batch_hist_fetcher: Callable | None = fetch_tdxrs_histories,
    use_disk_cache: bool = True,
    cache_dir: Path | None = None,
    cache_ttl_seconds: float = 600.0,
) -> dict[str, tuple[pd.DataFrame | None, str | None]]:
    unique_codes = list(dict.fromkeys(str(code).strip() for code in codes if str(code).strip()))
    history_cache_dir = cache_dir or (
        Path(os.environ.get("LOCALAPPDATA", Path.home())) / "NASDX" / "market_cache"
    )

    fallback_sources = tuple(source for source in sources if source != "tdxrs")

    def fetch_one(code: str, timeout: float):
        return hist_fetcher(
            code,
            start_date,
            end_date,
            min_rows,
            timeout,
            fallback_sources,
        )

    def run_batch(pending: list[str], timeout: float, workers: int):
        batch_results: dict[str, tuple[pd.DataFrame | None, str | None]] = {}
        if not pending:
            return batch_results
        with ThreadPoolExecutor(max_workers=min(workers, len(pending))) as executor:
            future_codes = {executor.submit(fetch_one, code, timeout): code for code in pending}
            for future in as_completed(future_codes):
                code = future_codes[future]
                try:
                    batch_results[code] = future.result()
                except Exception:
                    batch_results[code] = (None, None)
        return batch_results

    results: dict[str, tuple[pd.DataFrame | None, str | None]] = {}
    pending = unique_codes
    if use_disk_cache:
        pending = []
        for code in unique_codes:
            cached = _read_history_cache(
                history_cache_dir / f"{market_symbol(code)}_{start_date}_{end_date}.json",
                cache_ttl_seconds,
            )
            if cached is None:
                pending.append(code)
            else:
                results[code] = cached

    batch_enabled = (
        "tdxrs" in sources
        and batch_hist_fetcher is not None
        and (hist_fetcher is fetch_stock_hist or batch_hist_fetcher is not fetch_tdxrs_histories)
    )
    batch_pending = [code for code in pending if resolve_exchange(code) != "BSE"] if batch_enabled else []
    early_fallback = [code for code in pending if code not in batch_pending] if batch_enabled else []
    early_fallback_attempted: set[str] = set()
    if batch_enabled and pending:
        with ThreadPoolExecutor(max_workers=2) as orchestrator:
            batch_future = (
                orchestrator.submit(
                    batch_hist_fetcher,
                    batch_pending,
                    start_date,
                    end_date,
                    min_rows,
                )
                if batch_pending
                else None
            )
            fallback_future = (
                orchestrator.submit(run_batch, early_fallback, request_timeout, max_workers)
                if early_fallback and fallback_sources
                else None
            )
            if batch_future is not None:
                try:
                    results.update(batch_future.result())
                except Exception:
                    pass
            if fallback_future is not None:
                early_fallback_attempted.update(early_fallback)
                results.update(fallback_future.result())

    fallback_pending = [code for code in pending if results.get(code, (None, None))[0] is None]
    first_attempt_pending = [code for code in fallback_pending if code not in early_fallback_attempted]
    fetched = run_batch(first_attempt_pending, request_timeout, max_workers) if fallback_sources else {}
    results.update(fetched)
    missing = [code for code in fallback_pending if results.get(code, (None, None))[0] is None]
    if missing:
        results.update(run_batch(missing, max(6.0, request_timeout * 2), max(1, max_workers // 2)))
    for code in pending:
        results.setdefault(code, (None, None))
    if use_disk_cache:
        for code in pending:
            frame, source = results.get(code, (None, None))
            if frame is not None and source:
                _write_history_cache(
                    history_cache_dir / f"{market_symbol(code)}_{start_date}_{end_date}.json",
                    frame,
                    source,
                )
    return results


def _read_history_cache(
    path: Path,
    ttl_seconds: float,
) -> tuple[pd.DataFrame, str] | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        if time.time() - float(payload["cached_at"]) > ttl_seconds:
            return None
        frame = pd.DataFrame(payload["records"])
        source = str(payload["source"])
        if frame.empty or not source:
            return None
        return frame, source
    except (KeyError, OSError, TypeError, ValueError):
        return None


def _write_history_cache(path: Path, frame: pd.DataFrame, source: str) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "cached_at": time.time(),
            "source": source,
            "records": json.loads(frame.to_json(orient="records", date_format="iso")),
        }
        serialized = json.dumps(payload, ensure_ascii=False)
        temp_path = path.with_name(f"{path.name}.{os.getpid()}.tmp")
        temp_path.write_text(serialized, encoding="utf-8")
        try:
            temp_path.replace(path)
        except OSError:
            path.write_text(serialized, encoding="utf-8")
            temp_path.unlink(missing_ok=True)
    except (OSError, TypeError, ValueError):
        pass


def get_listing_coverage() -> dict:
    return json.loads(json.dumps(_LAST_LISTING_COVERAGE)) if _LAST_LISTING_COVERAGE else {}


def _parse_bse_page(payload: str) -> dict:
    text = str(payload or "").strip().rstrip(";")
    prefix = f"{BSE_JSONP_CALLBACK}("
    if not text.startswith(prefix) or not text.endswith(")"):
        raise ValueError("invalid BSE JSONP response")
    decoded = json.loads(text[len(prefix) : -1])
    if not isinstance(decoded, list) or not decoded or not isinstance(decoded[0], dict):
        raise ValueError("invalid BSE listing payload")
    page = decoded[0]
    listings = []
    for row in page.get("content", []):
        code = str(row.get("xxzqdm", "")).strip()
        if not code:
            continue
        listings.append(
            {
                "code": code,
                "name": str(row.get("xxzqjc", code)).strip(),
                "sector": str(row.get("xxhyzl", "北交所")).strip() or "北交所",
                "exchange": "BSE",
            }
        )
    return {
        "listings": listings,
        "total_elements": int(page.get("totalElements", len(listings)) or len(listings)),
        "total_pages": max(1, int(page.get("totalPages", 1) or 1)),
    }


def _fetch_bse_page(page: int, request_timeout: float) -> dict:
    response = requests.get(
        BSE_LIST_URL,
        params={
            "page": page,
            "typejb": "T",
            "xxfcbj[]": "2",
            "xxzqdm": "",
            "sortfield": "xxzqdm",
            "sorttype": "asc",
            "callback": BSE_JSONP_CALLBACK,
        },
        timeout=request_timeout,
        headers={
            "Referer": "https://www.bse.cn/nq/listedcompany.html",
            "User-Agent": "Mozilla/5.0 NASDX",
        },
    )
    response.raise_for_status()
    response.encoding = "utf-8"
    return _parse_bse_page(response.text)


def _fetch_bse_listings(request_timeout: float, max_workers: int = 4) -> tuple[list[dict], bool]:
    try:
        first = _fetch_bse_page(0, request_timeout)
    except Exception:
        return [], False
    listings = list(first["listings"])
    pages = list(range(1, first["total_pages"]))
    complete = True
    if pages:
        with ThreadPoolExecutor(max_workers=min(max_workers, len(pages))) as executor:
            futures = [executor.submit(_fetch_bse_page, page, request_timeout) for page in pages]
            for future in as_completed(futures):
                try:
                    listings.extend(future.result()["listings"])
                except Exception:
                    complete = False
    unique = {item["code"]: item for item in listings}
    complete = complete and len(unique) >= first["total_elements"]
    return list(unique.values()), complete


def _build_listing_coverage(listings: Sequence[dict], source_status: dict[str, bool]) -> dict:
    counts = {"SSE": 0, "SZSE": 0, "BSE": 0}
    for item in listings:
        exchange = str(item.get("exchange") or resolve_exchange(item.get("code", ""))).upper()
        if exchange in counts:
            counts[exchange] += 1
    sources = {exchange: bool(source_status.get(exchange, False)) for exchange in counts}
    unavailable = [exchange for exchange, available in sources.items() if not available]
    return {
        "complete": not unavailable,
        "counts": counts,
        "sources": sources,
        "unavailable_exchanges": unavailable,
    }


def load_a_share_listings(request_timeout: float = 8.0) -> list[dict]:
    global _LAST_LISTING_COVERAGE
    cache_path = Path(os.environ.get("LOCALAPPDATA", Path.home())) / "NASDX" / "a_share_listings.json"
    try:
        if cache_path.exists() and time.time() - cache_path.stat().st_mtime < 7 * 86400:
            cached = json.loads(cache_path.read_text(encoding="utf-8"))
            if (
                isinstance(cached, dict)
                and cached.get("schema") == LISTING_CACHE_SCHEMA
                and isinstance(cached.get("listings"), list)
                and len(cached["listings"]) > 1000
            ):
                _LAST_LISTING_COVERAGE = dict(cached.get("coverage") or {})
                return cached["listings"]
    except (OSError, ValueError):
        pass

    listings: dict[str, dict] = {}
    source_status = {"SSE": False, "SZSE": False, "BSE": False}
    headers = {
        "Referer": "https://www.sse.com.cn/assortment/stock/list/share/",
        "User-Agent": "Mozilla/5.0 NASDX",
    }
    sse_successes = 0
    for stock_type in ("1", "8"):
        try:
            response = requests.get(
                SSE_LIST_URL,
                params={
                    "STOCK_TYPE": stock_type,
                    "REG_PROVINCE": "",
                    "CSRC_CODE": "",
                    "STOCK_CODE": "",
                    "sqlId": "COMMON_SSE_CP_GPJCTPZ_GPLB_GP_L",
                    "COMPANY_STATUS": "2,4,5,7,8",
                    "type": "inParams",
                    "isPagination": "true",
                    "pageHelp.beginPage": "1",
                    "pageHelp.pageSize": "10000",
                    "pageHelp.pageNo": "1",
                    "pageHelp.endPage": "1",
                },
                headers=headers,
                timeout=request_timeout,
            )
            response.raise_for_status()
            sse_successes += 1
            for row in response.json().get("result", []):
                code = str(row.get("A_STOCK_CODE", "")).strip()
                if code:
                    listings[code] = {
                        "code": code,
                        "name": str(row.get("SEC_NAME_CN", code)).strip(),
                        "sector": "科创板" if stock_type == "8" else "沪市主板",
                        "exchange": "SSE",
                    }
        except Exception:
            continue
    source_status["SSE"] = sse_successes == 2

    try:
        response = requests.get(
            SZSE_LIST_URL,
            params={"SHOWTYPE": "xlsx", "CATALOGID": "1110", "TABKEY": "tab1"},
            timeout=request_timeout,
            headers={"User-Agent": "Mozilla/5.0 NASDX"},
        )
        response.raise_for_status()
        frame = pd.read_excel(BytesIO(response.content))
        source_status["SZSE"] = True
        for _, row in frame.iterrows():
            raw_code = str(row.get("A股代码", "")).split(".", 1)[0]
            code = raw_code.zfill(6) if raw_code.isdigit() else ""
            if code:
                listings[code] = {
                    "code": code,
                    "name": str(row.get("A股简称", code)).strip(),
                    "sector": str(row.get("所属行业", "深市A股")).strip() or "深市A股",
                    "exchange": "SZSE",
                }
    except Exception:
        pass

    bse_listings, bse_complete = _fetch_bse_listings(request_timeout)
    for item in bse_listings:
        listings[item["code"]] = item
    source_status["BSE"] = bse_complete

    result = list(listings.values())
    coverage = _build_listing_coverage(result, source_status)
    _LAST_LISTING_COVERAGE = coverage
    if len(result) > 1000 and coverage["complete"]:
        try:
            cache_path.parent.mkdir(parents=True, exist_ok=True)
            temp_path = cache_path.with_suffix(".tmp")
            serialized = json.dumps(
                {"schema": LISTING_CACHE_SCHEMA, "listings": result, "coverage": coverage},
                ensure_ascii=False,
            )
            temp_path.write_text(serialized, encoding="utf-8")
            try:
                temp_path.replace(cache_path)
            except OSError:
                cache_path.write_text(serialized, encoding="utf-8")
                temp_path.unlink(missing_ok=True)
        except OSError:
            pass
    return result


def _float_or_none(value: object) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
