"""CNBC 资讯模块的合约测试（离线 fixture）。"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import List

import pytest

from nasdx.cnbc_news import NewsItem, _is_compliant, fetch_all_cnbc_news, fetch_cnbc_news

FIXTURE_DIR = Path(__file__).parent / "fixtures" / "cnbc"


@pytest.fixture(autouse=True)
def _clear_cache(monkeypatch: pytest.MonkeyPatch):
    import nasdx.cnbc_news as module

    monkeypatch.setattr(module, "_CACHE", {})


def _load_fixture(filename: str) -> str:
    """读取离线 fixture 文件。"""
    fixture_path = FIXTURE_DIR / filename
    if not fixture_path.exists():
        return ""
    return fixture_path.read_text(encoding="utf-8")


class TestCNBCCompliance:
    """测试合规过滤逻辑。"""

    @pytest.mark.parametrize("title,summary,expected", [
        ("股市开盘", "标普500上涨", True),
        ("科技公司新闻", "苹果发布新品", True),
        ("赌博新网站上线", "非法荐股平台", False),
        ("加密货币交易", "杠杆交易风险", False),
        ("市场动态", "内幕交易调查", False),
        ("正常新闻", "市场分析报道", True),
    ])
    def test_is_compliant(self, title: str, summary: str, expected: bool) -> None:
        assert _is_compliant(title, summary) == expected


class TestCNBCFetch:
    """测试 CNBC 抓取逻辑（使用离线 fixture）。"""

    def test_parse_valid_rss(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """测试解析有效的 RSS 内容。"""
        rss_xml = _load_fixture("markets_rss.xml") or (
            "<rss><channel><item><title>Market update</title>"
            "<link>https://example.com/market</link>"
            "<pubDate>Mon, 19 Aug 2024 10:00:00 GMT</pubDate>"
            "<description>Objective market news</description>"
            "</item></channel></rss>"
        )

        monkeypatch.setattr(
            "nasdx.cnbc_news.requests.get",
            lambda *args, **kwargs: type(
                "Resp", (), {"text": rss_xml, "raise_for_status": lambda self: None}
            )(),
        )

        items = fetch_cnbc_news("markets", "Markets+News")
        assert isinstance(items, list)
        assert len(items) == 1
        assert all(isinstance(item, NewsItem) for item in items)

    def test_fetch_all_cnbc_news_structure(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """测试批量获取的新闻结构。"""
        response = type("Resp", (), {"text": "", "raise_for_status": lambda self: None})()
        monkeypatch.setattr("nasdx.cnbc_news.requests.get", lambda *args, **kwargs: response)

        items = fetch_all_cnbc_news()
        assert isinstance(items, list)
        assert all(isinstance(item, NewsItem) for item in items)

    def test_to_dict(self) -> None:
        """测试 NewsItem.to_dict() 序列化。"""
        item = NewsItem(
            title="测试标题",
            link="https://example.com/article",
            published=datetime(2024, 1, 15, 10, 30, 0),
            summary="这是摘要",
            source="CNBC-Markets",
            category="cnbc",
        )

        d = item.to_dict()
        assert d["title"] == "测试标题"
        assert d["link"] == "https://example.com/article"
        assert d["summary"] == "这是摘要"
        assert d["source"] == "CNBC-Markets"
        assert d["category"] == "cnbc"
        assert d["published"] == "2024-01-15T10:30:00"

    def test_no_api_key_hardcoded(self) -> None:
        """确保没有硬编码 API key。"""
        import nasdx.cnbc_news as module
        source = module.__file__
        content = open(source).read()
        assert "api_key" not in content.lower()
        assert "token" not in content.lower() or "user_agent" in content.lower()


class TestCNBCCache:
    def test_empty_result_is_not_cached(self, monkeypatch: pytest.MonkeyPatch) -> None:
        import nasdx.cnbc_news as module

        monkeypatch.setattr(module, "_CACHE", {})
        response = type("Resp", (), {"text": "", "raise_for_status": lambda self: None})()
        monkeypatch.setattr(module.requests, "get", lambda *args, **kwargs: response)
        assert module.fetch_cnbc_news("empty", "Markets") == []
        assert module._CACHE == {}

    def test_nonempty_result_uses_five_minute_cache(self, monkeypatch: pytest.MonkeyPatch) -> None:
        import nasdx.cnbc_news as module

        monkeypatch.setattr(module, "_CACHE", {})
        item = NewsItem("title", "https://example.com", None, "summary", "CNBC", "cnbc")
        calls = []
        monkeypatch.setattr(module, "_parse_rss_content", lambda *_args, **_kwargs: [item])
        response = type("Resp", (), {"text": "rss", "raise_for_status": lambda self: None})()
        monkeypatch.setattr(module.requests, "get", lambda *args, **kwargs: calls.append(1) or response)
        assert module.fetch_cnbc_news("cache", "Markets") == [item]
        assert module.fetch_cnbc_news("cache", "Markets") == [item]
        assert calls == [1]
        assert module._CACHE_TTL_SECONDS == 300
