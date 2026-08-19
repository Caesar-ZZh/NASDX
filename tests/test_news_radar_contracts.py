"""news_radar 模块契约测试 —— 离线验证（不联网）。

覆盖：
1. JSON 源清单解析
2. 合规红线过滤
3. 时间解析
4. 赛道分组骨架
5. 缓存读写
6. evidence 衔接（有/无两层）
"""

from __future__ import annotations

import json
import os
import shutil
import tempfile
from datetime import datetime, timedelta, timezone
from unittest.mock import patch, MagicMock

import pytest

# ── Fixtures ──────────────────────────────────────────────────────────────────

SAMPLE_SOURCE = {
    "name": "测试源",
    "url": "http://example.com/rss.xml",
    "hint": "macro",
    "authority": 0.9,
}

SAMPLE_FEED = """
<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0">
  <channel>
    <title>测试频道</title>
    <item>
      <title>正常新闻标题</title>
      <link>http://example.com/1</link>
      <pubDate>Mon, 14 Jul 2025 10:00:00 GMT</pubDate>
      <description>这是一条正常的财经新闻。</description>
    </item>
    <item>
      <title>BTC 暴跌，比特币预测：十倍行情来了</title>
      <link>http://example.com/2</link>
      <pubDate>Mon, 14 Jul 2025 11:00:00 GMT</pubDate>
      <description>加密货币分析：BTC 必涨。</description>
    </item>
    <item>
      <title>央行降准 50BP，释放长期资金约 1 万亿元</title>
      <link>http://example.com/3</link>
      <pubDate>Sun, 13 Jul 2025 09:00:00 GMT</pubDate>
      <description>央行宣布下调存款准备金率 0.5 个百分点。</description>
    </item>
    <item>
      <title>六合彩开奖结果：特码 18</title>
      <link>http://example.com/4</link>
      <pubDate>Sun, 13 Jul 2025 08:00:00 GMT</pubDate>
      <description>彩票预测与开奖。</description>
    </item>
  </channel>
</rss>
"""


def _make_fake_response(body: str):
    resp = MagicMock()
    resp.read.return_value = body.encode("utf-8")
    resp.__enter__ = MagicMock(return_value=resp)
    resp.__exit__ = MagicMock(return_value=False)
    return resp


@pytest.fixture
def tmp_src_file(tmp_path):
    """创建临时 news_sources.json。"""
    cfg = {
        "fetch": {"recent_days": 7, "per_source": 10},
        "redline_keywords": ["btc", "六合彩", "彩票预测", "加密", "色情", "AV"],
        "evidence_weights": {"authority": 0.6, "freshness": 0.4},
        "industries": [
            {"key": "macro", "name": "宏观政策", "accent": "#3b82f6"},
            {"key": "tech", "name": "科技前沿", "accent": "#10b981"},
        ],
        "sources": [
            {"name": "新华社", "url": "http://xh.com/rss", "hint": "macro", "authority": 0.95},
            {"name": "量子位", "url": "http://qb.com/rss", "hint": "tech", "authority": 0.72},
        ],
    }
    f = tmp_path / "news_sources.json"
    f.write_text(json.dumps(cfg, ensure_ascii=False), encoding="utf-8")
    return f


@pytest.fixture
def env_setup(tmp_path, monkeypatch):
    """将 nasdx 临时路径挂到 sys.path。"""
    pkg = tmp_path / "nasdx"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("", encoding="utf-8")
    (pkg / "news_radar.py").write_text("", encoding="utf-8")  # 占位
    tmp_path.joinpath("news_sources.json").write_text(
        json.dumps({
            "fetch": {"recent_days": 7, "per_source": 10},
            "redline_keywords": ["btc", "六合彩", "彩票预测", "加密", "色情", "AV"],
            "industries": [{"key": "macro", "name": "宏观政策", "accent": "#3b82f6"}],
            "sources": [SAMPLE_SOURCE],
        }),
        encoding="utf-8",
    )
    monkeypatch.syspath_prepend(tmp_path)
    return tmp_path


# ── 1. 基础工具函数 ───────────────────────────────────────────────────────────


def test_strip_html():
    from nasdx.news_radar import _strip_html
    assert _strip_html("<b>你好</b> <i>世界</i>") == "你好 世界"
    assert _strip_html(None) == ""


def test_local_qname():
    from nasdx.news_radar import _local
    assert _local("{http://www.w3.org/1999/xhtml}title") == "title"
    assert _local("item") == "item"


def test_parse_dt_iso():
    from nasdx.news_radar import _parse_dt
    dt = _parse_dt("2025-07-14T10:00:00+00:00")
    assert dt is not None
    assert dt.year == 2025
    assert dt.month == 7
    assert dt.day == 14


def test_parse_dt_email():
    from nasdx.news_radar import _parse_dt
    dt = _parse_dt("Mon, 14 Jul 2025 10:00:00 GMT")
    assert dt is not None


def test_parse_dt_invalid():
    from nasdx.news_radar import _parse_dt
    assert _parse_dt("") is None
    assert _parse_dt("not-a-date") is None


def test_is_compliant_pass():
    from nasdx.news_radar import _is_compliant
    assert _is_compliant("央行降准释放流动性", ["btc", "六合彩"]) is True


def test_is_compliant_fail_crypto():
    from nasdx.news_radar import _is_compliant
    assert _is_compliant("比特币 BTC 暴涨", ["btc", "比特币"]) is False


def test_is_compliant_fail_gambling():
    from nasdx.news_radar import _is_compliant
    assert _is_compliant("六合彩开奖结果", ["六合彩"]) is False


def test_is_compliant_case_insensitive():
    from nasdx.news_radar import _is_compliant
    assert _is_compliant("Bitcoin 暴跌", ["btc"]) is False


# ── 2. RSS 抓取 + 过滤 ────────────────────────────────────────────────────────


def test_fetch_source_success(env_setup, monkeypatch):
    from nasdx.news_radar import _fetch_source
    dt_future = datetime.now(timezone.utc) + timedelta(days=1)
    feed = SAMPLE_FEED.replace("Mon, 14 Jul 2025", dt_future.strftime("%a, %d %b %Y"))
    monkeypatch.setattr(
        "nasdx.news_radar.urllib.request.urlopen",
        lambda req, timeout=None: _make_fake_response(feed),
    )
    result = _fetch_source(SAMPLE_SOURCE, per=10, cutoff=None, redline=[])
    assert result is not None
    assert len(result) == 2  # 仅 2 条通过过滤
    titles = [r["title"] for r in result]
    assert "正常新闻标题" in titles
    assert "央行降准" in titles


def test_fetch_source_blocked_by_redline(env_setup, monkeypatch):
    from nasdx.news_radar import _fetch_source
    monkeypatch.setattr(
        "nasdx.news_radar.urllib.request.urlopen",
        lambda req, timeout=None: _make_fake_response(SAMPLE_FEED),
    )
    result = _fetch_source(SAMPLE_SOURCE, per=10, cutoff=None, redline=["btc", "六合彩"])
    assert result is not None
    assert len(result) == 2  # 过滤掉 BTC 和六合彩两条
    titles = [r["title"] for r in result]
    assert not any("BTC" in t or "六合彩" in t for t in titles)


def test_fetch_source_time_cutoff(env_setup, monkeypatch):
    from nasdx.news_radar import _fetch_source
    monkeypatch.setattr(
        "nasdx.news_radar.urllib.request.urlopen",
        lambda req, timeout=None: _make_fake_response(SAMPLE_FEED),
    )
    # 设置 cutoff 在未来 → 所有条目都被过滤
    future = datetime.now(timezone.utc) + timedelta(days=1)
    result = _fetch_source(SAMPLE_SOURCE, per=10, cutoff=future, redline=[])
    assert result is not None
    assert len(result) == 0


def test_fetch_source_network_error(env_setup):
    from nasdx.news_radar import _fetch_source
    import urllib.error
    with patch("nasdx.news_radar.urllib.request.urlopen", side_effect=urllib.error.URLError("fail")):
        result = _fetch_source(SAMPLE_SOURCE, per=10, cutoff=None, redline=[])
    assert result is None


def test_fetch_source_per_limit(env_setup, monkeypatch):
    from nasdx.news_radar import _fetch_source
    multi_item_feed = SAMPLE_FEED.replace(
        "</rss>",
        '<item><title>A</title><link>http://a.com</link></item>'
        '<item><title>B</title><link>http://b.com</link></item>'
        '</rss>',
    )
    monkeypatch.setattr(
        "nasdx.news_radar.urllib.request.urlopen",
        lambda req, timeout=None: _make_fake_response(multi_item_feed),
    )
    result = _fetch_source(SAMPLE_SOURCE, per=2, cutoff=None, redline=[])
    assert result is not None
    assert len(result) <= 2


# ── 3. 骨架 & 缓存 ────────────────────────────────────────────────────────────


def test_skeleton(env_setup, monkeypatch):
    from nasdx.news_radar import skeleton, get_radar
    cached = get_radar(force=False)
    assert cached["generated_at"] is None
    assert len(cached["industries"]) == 1
    assert cached["industries"][0]["key"] == "macro"
    assert cached["industries"][0]["items"] == []


def test_cache_roundtrip(env_setup, monkeypatch, tmp_path):
    from nasdx.news_radar import fetch_radar, load_cache, get_radar
    cache_dir = tmp_path / ".cache"
    cache_file = cache_dir / "radar.json"
    monkeypatch.setattr("nasdx.news_radar.CACHE_DIR", str(cache_dir))
    monkeypatch.setattr("nasdx.news_radar.CACHE_FILE", str(cache_file))
    monkeypatch.setattr(
        "nasdx.news_radar.urllib.request.urlopen",
        lambda req, timeout=None: _make_fake_response(SAMPLE_FEED),
    )
    data = fetch_radar()
    assert data["generated_at"] is not None
    assert data["stats"]["total_sources"] == 1
    assert data["stats"]["total_items"] == 2
    loaded = load_cache()
    assert loaded is not None
    assert loaded["stats"]["total_items"] == 2


def test_load_cache_missing(env_setup, monkeypatch):
    from nasdx.news_radar import load_cache
    import tempfile
    fake = os.path.join(tempfile.gettempdir(), "nonexistent_radar.json")
    monkeypatch.setattr("nasdx.news_radar.CACHE_FILE", fake)
    assert load_cache() is None


# ── 4. 赛道分组 & 综合得分 ─────────────────────────────────────────────────────


def test_merge_evidence_with_weights():
    from nasdx.news_radar import _merge_evidence
    items = [
        {"title": "A", "authority": 0.9, "freshness": 1.0},
        {"title": "B", "authority": 0.5, "freshness": 0.8},
    ]
    merged = _merge_evidence(items, {"authority": 0.6, "freshness": 0.4})
    # A: 0.9*0.6 + 1.0*0.4 = 0.94
    # B: 0.5*0.6 + 0.8*0.4 = 0.62
    assert merged[0]["composite_score"] == pytest.approx(0.94, abs=0.01)
    assert merged[1]["composite_score"] == pytest.approx(0.62, abs=0.01)
    # 按分数降序
    assert merged[0]["composite_score"] >= merged[1]["composite_score"]


def test_merge_evidence_no_weights():
    from nasdx.news_radar import _merge_evidence
    items = [{"title": "A"}]
    result = _merge_evidence(items, {})
    assert result[0]["composite_score"] == 0.0


# ── 5. get_radar 入口 ─────────────────────────────────────────────────────────


def test_get_radar_force(env_setup, monkeypatch, tmp_path):
    from nasdx.news_radar import get_radar
    cache_dir = tmp_path / ".cache"
    cache_file = cache_dir / "radar.json"
    monkeypatch.setattr("nasdx.news_radar.CACHE_DIR", str(cache_dir))
    monkeypatch.setattr("nasdx.news_radar.CACHE_FILE", str(cache_file))
    monkeypatch.setattr(
        "nasdx.news_radar.urllib.request.urlopen",
        lambda req, timeout=None: _make_fake_response(SAMPLE_FEED),
    )
    data = get_radar(force=True)
    assert data["stats"]["total_items"] == 2
    # 第二次调用走缓存
    data2 = get_radar(force=False)
    assert data2["stats"]["total_items"] == 2


def test_get_radar_returns_skeleton_when_no_cache(env_setup, monkeypatch):
    from nasdx.news_radar import get_radar
    import os
    fake_cache = os.path.join(tempfile.gettempdir(), "no_cache_radar.json")
    monkeypatch.setattr("nasdx.news_radar.CACHE_FILE", fake_cache)
    data = get_radar(force=False)
    assert data["generated_at"] is None
    assert data["industries"][0]["items"] == []


# ── 6. 行业摘要 ───────────────────────────────────────────────────────────────

def test_get_industry_summary(env_setup, monkeypatch, tmp_path):
    from nasdx.news_radar import get_industry_summary, fetch_radar
    cache_dir = tmp_path / ".cache"
    cache_file = cache_dir / "radar.json"
    monkeypatch.setattr("nasdx.news_radar.CACHE_DIR", str(cache_dir))
    monkeypatch.setattr("nasdx.news_radar.CACHE_FILE", str(cache_file))
    monkeypatch.setattr(
        "nasdx.news_radar.urllib.request.urlopen",
        lambda req, timeout=None: _make_fake_response(SAMPLE_FEED),
    )
    fetch_radar()
    summary = get_industry_summary()
    assert len(summary) == 1
    s = summary[0]
    assert s["key"] == "macro"
    assert s["items_count"] == 2
    assert s["latest_time"] is not None


# ── 7. 原子写缓存 ─────────────────────────────────────────────────────────────

def test_cache_atomic_write(env_setup, monkeypatch, tmp_path):
    from nasdx.news_radar import fetch_radar
    cache_dir = tmp_path / ".cache"
    cache_file = cache_dir / "radar.json"
    monkeypatch.setattr("nasdx.news_radar.CACHE_DIR", str(cache_dir))
    monkeypatch.setattr("nasdx.news_radar.CACHE_FILE", str(cache_file))
    monkeypatch.setattr(
        "nasdx.news_radar.urllib.request.urlopen",
        lambda req, timeout=None: _make_fake_response(SAMPLE_FEED),
    )
    fetch_radar()
    # 检查 cache 文件存在且非空
    assert cache_file.exists()
    assert cache_file.stat().st_size > 0
    # tmp 文件不应残留
    assert not (cache_file.parent / (cache_file.name + ".tmp")).exists()


# ── 8. 源清单校验 ─────────────────────────────────────────────────────────────

def test_src_json_structure(env_setup):
    import json
    import os
    src = os.path.join(os.path.dirname(__import__("nasdx").__file__), "news_sources.json")
    if not os.path.exists(src):
        pytest.skip("news_sources.json 未落地")
    with open(src, encoding="utf-8") as f:
        cfg = json.load(f)
    assert "industries" in cfg
    assert "sources" in cfg
    assert len(cfg["industries"]) == 12
    assert len(cfg["sources"]) == 108
    for ind in cfg["industries"]:
        assert "key" in ind
        assert "name" in ind
    for src in cfg["sources"]:
        assert "name" in src
        assert "url" in src
        assert "hint" in src
