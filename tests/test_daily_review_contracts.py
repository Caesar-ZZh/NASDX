"""nasdx.daily_review 合同测试：短线情绪聚合 / 缓存行为 / 零个股名。"""

from __future__ import annotations

import json
import time
from unittest import mock

import pytest

import nasdx.daily_review as dr


# ── fixture：东财四池返回 ─────────────────────────────────────────────────

def _pool_zt(count: int, boards_seq: list[int]) -> list[dict]:
    return [{"c": f"{i:06d}", "n": f"stock{i}", "lbc": b, "p": 1000, "zdp": 9.99, "amount": 1e8, "ltsz": 5e9, "hybk": "软件"} for i, b in enumerate(boards_seq[:count])]


def _pool_zb(count: int) -> list[dict]:
    return [{"c": f"zb{i:06d}", "n": f"zbstock{i}"} for i in range(count)]


def _pool_dt(count: int) -> list[dict]:
    return [{"c": f"dt{i:06d}"} for i in range(count)]


def _pool_yzt(count: int) -> list[dict]:
    return [{"c": f"yzt{i:06d}"} for i in range(count)]


@pytest.fixture(autouse=True)
def _clear():
    dr.clear_cache()
    yield
    dr.clear_cache()


# ── 情绪聚合计算 ──────────────────────────────────────────────────────────

def test_emotion_ladder_seal_break_promotion():
    """mock 四池后校验：连板梯队 / 封板率 / 炸板率 / 晋级率是否按公式计算。"""
    # 构造：今天涨停 10 只（其中 4 只 2 板+），炸板 2 只，昨涨停 8 只，跌停 3 只
    zt = _pool_zt(10, [1, 2, 2, 3, 3, 3, 4, 5, 1, 1])  # lbc: 1板×4, 2板×2, 3板×3, 4板×1, 5板×1
    zb = _pool_zb(2)
    dt = _pool_dt(3)
    yzt = _pool_yzt(8)

    expected_lianban = [b for b in [1,2,2,3,3,3,4,5,1,1] if b >= 2]  # 8 只
    expected_tiers = {2: 2, 3: 3, 4: 1, 5: 1, 6: 0, 7: 0, 8: 0, 9: 0, 10: 0}  # min(b,5)
    # 实际 min(b,5): 2->2, 3->3, 4->4, 5->5, 6+->5
    # 2板×2, 3板×3, 4板×1, 5板×1  => tiers: {2:2, 3:3, 4:1, 5:2}  (5和6+合并到5)
    import collections
    from nasdx.daily_review import _num
    lt = [_num(p.get("lbc")) or 1 for p in zt]
    lb = [b for b in lt if b >= 2]
    tiers = collections.Counter(min(b, 5) for b in lb)

    assert len(zb) == 2
    assert len(dt) == 3
    assert len(yzt) == 8

    with mock.patch.object(dr, "_em_zt_pool", side_effect=lambda t, d, s: {
        "getTopicZTPool": zt,
        "getTopicZBPool": zb,
        "getTopicDTPool": dt,
        "getYesterdayZTPool": yzt,
    }.get(t, [])):
        res = dr.get_short_term_emotion()

    assert res["date"] == "2024-01-01"  # mock 中 resolved 为第一回测日
    assert res["zt_count"] == 10
    assert res["dt_count"] == 3
    assert res["zb_count"] == 2
    assert res["max_boards"] == 5
    assert res["lianban_count"] == len(lb)
    assert res["yzt_count"] == 8

    attempts = 10 + 2
    assert res["seal_rate"] == round(10 / attempts, 3)
    assert res["break_rate"] == round(2 / attempts, 3)
    assert res["promotion_rate"] == round(len(lb) / 8, 3)

    # ladder：只保留有家数的档（2/3/4/5），顺序按 boards 升序
    ladder = res["ladder"]
    assert {item["boards"] for item in ladder} == {2, 3, 4, 5}
    ladder_map = {item["boards"]: item for item in ladder}
    assert ladder_map[2]["count"] == 2
    assert ladder_map[3]["count"] == 3
    assert ladder_map[4]["count"] == 1
    assert ladder_map[5]["count"] == 2
    assert all(item["plus"] for item in ladder if item["boards"] >= 5)

    # 零个股名红线：返回字段中不应有 lianban_stocks
    assert "lianban_stocks" not in res


def test_emotion_no_data_returns_empty():
    with mock.patch.object(dr, "_em_zt_pool", return_value=[]):
        res = dr._emotion()
    assert res == {}


# ── 缓存行为 ──────────────────────────────────────────────────────────────

def test_cache_ttl_and_empty_not_cached():
    call_count = 0

    def fn_ok():
        nonlocal call_count
        call_count += 1
        return {"v": call_count}

    def fn_empty():
        nonlocal call_count
        call_count += 1
        return {}

    dr.clear_cache()
    r1 = dr._cached("k_ok", fn_ok)
    r2 = dr._cached("k_ok", fn_ok)
    assert call_count == 1, "命中缓存应只调用一次"
    assert r1 == r2

    call_count = 0
    r3 = dr._cached("k_empty", fn_empty, valid=bool)
    r4 = dr._cached("k_empty", fn_empty, valid=bool)
    assert call_count == 2, "空结果不缓存，下次应重新调用"


def test_cache_clear():
    dr._CACHE["x"] = (0.0, 1)
    dr.clear_cache(["x"])
    assert "x" not in dr._CACHE
    dr.clear_cache()
    assert not dr._CACHE


# ── sentiment / sectors / turnover / global 结构契约 ──────────────────────

def test_sentiment_structure():
    fake_df = mock.MagicMock()
    fake_df.iterrows.return_value = [
        (0, {"item": "上涨", "value": 2500}),
        (1, {"item": "下跌", "value": 1800}),
        (2, {"item": "平盘", "value": 200}),
        (3, {"item": "涨停", "value": 85}),
        (4, {"item": "真实涨停", "value": 72}),
        (5, {"item": "跌停", "value": 5}),
        (6, {"item": "真实跌停", "value": 4}),
        (7, {"item": "活跃度", "value": "高"}),
        (8, {"item": "统计日期", "value": "2024-01-01"}),
    ]
    with mock.patch("nasdx.daily_review.akshare") as m_ak:
        m_ak.stock_market_activity_legu.return_value = fake_df
        res = dr._sentiment()
    assert res["up"] == 2500
    assert res["down"] == 1800
    assert res["breadth"] == "偏强"  # 2500/1800 ≈ 1.39, 在 [1.2, 2.5)
    assert res["speculation"] == "活跃"  # 72 在 [60, 100)


def test_sectors_structure():
    fake_df = mock.MagicMock()
    fake_df.sort_values.return_value = fake_df
    fake_df.iterrows.return_value = [
        (0, {"行业": "半导体", "行业-涨跌幅": 2.1, "净额": 5.2e9, "流入资金": 8e9, "流出资金": 2.8e9, "公司家数": 42}),
    ]
    with mock.patch("nasdx.daily_review.akshare") as m_ak:
        m_ak.stock_fund_flow_industry.return_value = fake_df
        res = dr._sectors()
    assert len(res) == 1
    assert res[0]["name"] == "半导体"
    assert res[0]["net"] == pytest.approx(5.2e9, abs=0.1)


def test_turnover_top_empty_graceful():
    with mock.patch("nasdx.daily_review.nasdx.astock") as m_astock:
        m_astock.market_turnover_rank.side_effect = ImportError("no astock")
        res = dr.get_turnover_top(20)
    assert res["stocks"] == []
    assert "updated" in res


def test_get_overview_combines():
    with mock.patch.object(dr, "_sentiment", return_value={"up": 1000, "down": 900, "breadth": "中性"}), \
         mock.patch.object(dr, "_sectors", return_value=[{"name": "计算机", "net": 1e9}]):
        res = dr.get_overview()
    assert "sentiment" in res
    assert "sectors" in res
    assert "updated" in res
