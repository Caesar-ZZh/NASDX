"""nasdx.commodity_100ppi 合约测试 (离线 fixture, 不联网)。

验收:
- 解析器在 fixture 下能输出标准化字段;
- 限流/缓存路径存在且可被调用;
- 零标的红线: to_summary / fetch_list 不返回任何买卖建议字段。
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from unittest.mock import patch

import pytest

# 假设仓库根在 tests/../, nasdx 包同级; 若运行路径不同请按需调整 sys.path。
# 这里用相对导入, 依赖 pytest discover 在仓库根下执行。

# ─── fixture HTML ───────────────────────────────────────────────────────────

_FIXTURE_TABLE_HTML = """
<!doctype html>
<html><body>
<table id="tablelist">
<tr><td>code</td><td>name</td><td>latest</td><td>change_pct(%)</td><td>prev_close</td><td>open</td><td>high</td><td>low</td><td>volume</td></tr>
<tr><td>AU</td><td>黄金</td><td>485.20</td><td>+1.25%</td><td>479.20</td><td>480.00</td><td>486.50</td><td>479.80</td><td>12000</td></tr>
<tr><td>CU</td><td>铜</td><td>71230</td><td>-0.42</td><td>71530</td><td>71500</td><td>71800</td><td>71000</td><td>8800</td></tr>
<tr><td>RB</td><td>螺纹钢</td><td>3562</td><td>+0.87%</td><td>3531</td><td>3535</td><td>3570</td><td>3530</td><td>22300</td></tr>
</table>
</body></html>
"""

_FIXTURE_JSON_HTML = """
<script>var data=[{"code":"AG","name":"白银","latest":"6820","change_pct":"+0.95%","prev_close":"6756","open":"6760","high":"6850","low":"6750","volume":"5400"},{"code":"ZN","name":"锌","latest":"22150","change_pct":"-1.10","prev_close":"22400","open":"22380","high":"22420","low":"22100","volume":"3100"}];</script>
"""

_VALID_ITEM = {
    "code": "AU",
    "name": "黄金",
    "latest": 485.20,
    "change_pct": 1.25,
    "prev_close": 479.20,
    "open": 480.00,
    "high": 486.50,
    "low": 479.80,
    "volume": 12000,
    "raw_source": "100ppi_html",
    "update_time": pytest.lazy_fixture if False else "2026-07-01 10:00:00",  # type: ignore[attr-defined]
}


# ─── 导入被测试模块 ─────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def _zero_cache_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """强制使用 tmp_path 作为缓存目录, 并重置模块级状态。"""
    import nasdx.commodity_100ppi as m
    monkeypatch.setattr(m, "_cache_root", None)
    monkeypatch.setattr(m, "_last_req_at", 0.0)
    monkeypatch.setattr(m, "MIN_INTERVAL_SEC", 0.0)
    monkeypatch.setattr(m, "CATEGORY_WHITELIST", [])
    monkeypatch.setattr(m, "_cache_root_path", lambda: tmp_path)
    monkeypatch.setenv("NASDX_COMMODITY_CACHE_DIR", str(tmp_path))
    monkeypatch.setenv("NASDX_COMMODITY_CATEGORY_WHITELIST", "")


def test_parse_cells_standard_fields() -> None:
    import nasdx.commodity_100ppi as m
    cells = ["AU", "黄金", "485.20", "+1.25%", "479.20", "480.00", "486.50", "479.80", "12000"]
    row = m._parse_cells(cells)
    assert row["code"] == "AU"
    assert row["name"] == "黄金"
    assert row["latest"] == 485.20
    assert row["change_pct"] == 1.25
    assert row["prev_close"] == 479.20
    assert row["open"] == 480.00
    assert row["high"] == 486.50
    assert row["low"] == 479.80
    assert row["volume"] == 12000
    assert row["raw_source"] == "100ppi_html"
    assert row["update_time"] != ""


def test_parse_mixed_pct_formats() -> None:
    import nasdx.commodity_100ppi as m
    assert m._parse_pct("+1.25%") == 1.25
    assert m._parse_pct("-0.42") == -0.42
    assert m._parse_pct("0") == 0.0
    assert m._parse_pct("") is None
    assert m._parse_pct("abc") is None


def test_extract_from_html_table() -> None:
    import nasdx.commodity_100ppi as m
    items = m._extract_from_html(_FIXTURE_TABLE_HTML)
    assert len(items) >= 3
    codes = [it["code"] for it in items]
    assert "AU" in codes
    assert "CU" in codes
    assert "RB" in codes
    for it in items:
        assert "latest" in it
        assert "change_pct" in it


def test_extract_from_html_inline_json() -> None:
    import nasdx.commodity_100ppi as m
    items = m._extract_from_html(_FIXTURE_JSON_HTML)
    assert len(items) >= 2
    codes = [it["code"] for it in items]
    assert "AG" in codes
    assert "ZN" in codes


def test_dedup_and_whitespace_code() -> None:
    import nasdx.commodity_100ppi as m
    html = '<table><tr><td> au </td><td>x</td><td>1</td><td>0</td><td>0</td><td>0</td><td>0</td><td>0</td><td>0</td></tr>'
    html += '<tr><td>AU</td><td>y</td><td>2</td><td>0</td><td>0</td><td>0</td><td>0</td><td>0</td><td>0</td></tr></table>'
    items = m._extract_from_html(html)
    assert len(items) == 1
    assert items[0]["code"] == "AU"


def test_normalize_code() -> None:
    import nasdx.commodity_100ppi as m
    assert m._normalize_code("  rb  ") == "RB"
    assert m._normalize_code("Cu") == "CU"


def test_prefix_hint_fills_name() -> None:
    import nasdx.commodity_100ppi as m
    row = {"code": "HG", "name": "", "latest": "", "change_pct": "", "prev_close": "", "open": "", "high": "", "low": "", "volume": "", "raw_source": ""}
    out = m._clean_row(row)
    # HG 不在 hint 字典, 保持空 name; 但 AU 会补充
    row2 = {"code": "AU", "name": "", "latest": "", "change_pct": "", "prev_close": "", "open": "", "high": "", "low": "", "volume": "", "raw_source": ""}
    out2 = m._clean_row(row2)
    assert out2["name"] == "黄金"


def test_throttle_respects_interval() -> None:
    import nasdx.commodity_100ppi as m
    m.MIN_INTERVAL_SEC = 0.05
    m._last_req_at = 0.0
    t0 = time.monotonic()
    m._throttle()
    t1 = time.monotonic()
    # 第一次调用没有上一次请求，应立即通过。
    assert t1 - t0 < 0.04
    m._last_req_at = time.monotonic()
    t0 = time.monotonic()
    m._throttle()
    t1 = time.monotonic()
    # 第二次调用应补齐到 interval
    assert t1 - t0 >= 0.04


def test_cache_roundtrip(tmp_path: Path) -> None:
    import nasdx.commodity_100ppi as m
    m._cache_root = None
    # 强制重新取 path
    with patch.object(m, "_cache_root_path", return_value=tmp_path):
        data = [{"code": "AU", "name": "黄金", "latest": 1.0, "change_pct": 0.0,
                 "prev_close": 0.0, "open": 0.0, "high": 0.0, "low": 0.0, "volume": 0,
                 "raw_source": "test", "update_time": "now"}]
        m._save_cache("quote", data)
        loaded = m._load_cache("quote")
        assert loaded == data


def test_cache_miss_after_ttl(tmp_path: Path) -> None:
    import nasdx.commodity_100ppi as m
    from datetime import datetime, timedelta
    m._cache_root = None
    with patch.object(m, "_cache_root_path", return_value=tmp_path):
        old_ts = datetime.now() - timedelta(seconds=10000)
        p = tmp_path / "quote.json"
        p.write_text(json.dumps({"ts": old_ts.timestamp(), "data": []}), encoding="utf-8")
        assert m._load_cache("quote") is None


def test_cache_no_save_on_none() -> None:
    import nasdx.commodity_100ppi as m
    m._save_cache("q", None)
    # 不应报错, 且不生成文件 (由调用方保证)


def test_cache_no_save_on_empty_list(tmp_path: Path) -> None:
    import nasdx.commodity_100ppi as m
    with patch.object(m, "_cache_root_path", return_value=tmp_path):
        m._save_cache("empty", [])
        assert not (tmp_path / "empty.json").exists()


def test_filter_by_whitelist() -> None:
    import nasdx.commodity_100ppi as m
    m.CATEGORY_WHITELIST = ["AU", "CU"]
    items = [{"code": "AU"}, {"code": "RB"}, {"code": "CU"}]
    out = m._filter_by_whitelist(items)
    assert len(out) == 2
    assert out[0]["code"] == "AU"
    assert out[1]["code"] == "CU"


def test_to_summary_zero_recommendation() -> None:
    import nasdx.commodity_100ppi as m
    items = [
        {"code": "A", "name": "a", "latest": 10, "change_pct": 2.0, "prev_close": 9.8,
         "open": 9.9, "high": 10.1, "low": 9.8, "volume": 100, "raw_source": "t", "update_time": "t"},
        {"code": "B", "name": "b", "latest": 20, "change_pct": -1.0, "prev_close": 20.2,
         "open": 20.2, "high": 20.3, "low": 20.0, "volume": 200, "raw_source": "t", "update_time": "t"},
        {"code": "C", "name": "c", "latest": 30, "change_pct": 0.0, "prev_close": 30.0,
         "open": 30.0, "high": 30.0, "low": 30.0, "volume": 0, "raw_source": "t", "update_time": "t"},
    ]
    summary = m.to_summary(items)
    assert summary["count"] == 3
    assert summary["changes"]["up"] == 1
    assert summary["changes"]["down"] == 1
    assert summary["changes"]["flat"] == 1
    assert summary["avg_change_pct"] == pytest.approx(0.3333, abs=0.01)
    # 零标的红线: 摘要中不能出现 "buy"/"sell"/"recommend"/"pick"/"rank" 等推荐语义字段
    flat = json.dumps(summary, ensure_ascii=False)
    forbidden = ["buy", "sell", "recommend", "pick", "rank"]
    for kw in forbidden:
        assert kw not in flat.lower(), f"forbidden keyword '{kw}' found in summary"


def test_fetch_list_network_mocked() -> None:
    import nasdx.commodity_100ppi as m
    fake_resp = type("Resp", (), {"encoding": "utf-8", "apparent_encoding": "utf-8", "text": _FIXTURE_TABLE_HTML, "raise_for_status": lambda self: None})()
    fake_session = type("Sess", (), {"get": lambda *a, **k: fake_resp})()
    with patch.object(m, "_session", return_value=fake_session):
        items = m.fetch_list(use_cache=False)
    assert len(items) >= 3
    assert any(it["code"] == "AU" for it in items)


def test_fetch_by_code_network_mocked() -> None:
    import nasdx.commodity_100ppi as m
    fake_resp = type("Resp", (), {"encoding": "utf-8", "apparent_encoding": "utf-8", "text": _FIXTURE_TABLE_HTML, "raise_for_status": lambda self: None})()
    fake_session = type("Sess", (), {"get": lambda *a, **k: fake_resp})()
    with patch.object(m, "_session", return_value=fake_session):
        item = m.fetch_by_code("cu")
    assert item is not None
    assert item["code"] == "CU"


def test_fetch_by_code_empty() -> None:
    import nasdx.commodity_100ppi as m
    fake_resp = type("Resp", (), {"encoding": "utf-8", "apparent_encoding": "utf-8", "text": "<html></html>", "raise_for_status": lambda self: None})()
    fake_session = type("Sess", (), {"get": lambda *a, **k: fake_resp})()
    with patch.object(m, "_session", return_value=fake_session):
        item = m.fetch_by_code("ZZZZ")
    assert item is None


def test_fetch_fallback_api_structure() -> None:
    import nasdx.commodity_100ppi as m
    fake_resp = type("Resp", (), {
        "raise_for_status": lambda self: None,
        "json": lambda self: {"Result": [{"code": "X", "name": "x", "latest": "1", "change_pct": "0"}], "State": 1}
    })()
    fake_session = type("Sess", (), {"get": lambda *a, **k: fake_resp})()
    with patch.object(m, "_session", return_value=fake_session):
        rows = m.fetch_fallback_api()
    assert len(rows) == 1
    assert rows[0]["code"] == "X"


def test_illegal_code_rejected() -> None:
    import nasdx.commodity_100ppi as m
    assert m.fetch_by_code("") is None
    assert m.fetch_by_code("   ") is None


def test_main_cli_list_mocked(capsys: pytest.Capsys) -> None:
    import nasdx.commodity_100ppi as m
    fake_resp = type("Resp", (), {"encoding": "utf-8", "apparent_encoding": "utf-8", "text": _FIXTURE_TABLE_HTML, "raise_for_status": lambda self: None})()
    fake_session = type("Sess", (), {"get": lambda *a, **k: fake_resp})()
    with patch.object(m, "_session", return_value=fake_session):
        with patch("sys.argv", ["commodity_100ppi", "--list", "--json"]):
            m.main()
    out = capsys.readouterr().out
    parsed = json.loads(out)
    assert "summary" in parsed
    assert "items" in parsed


def test_summary_keys_are_safe() -> None:
    import nasdx.commodity_100ppi as m
    items: list[dict[str, object]] = []
    s = m.to_summary(items)
    assert set(s.keys()) == {"count", "update_time", "changes", "avg_change_pct"}
