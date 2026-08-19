"""CNBC 资讯模块的合约测试（离线 fixture）。"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import List

import pytest

from nasdx.cnbc_news import NewsItem, _is_compliant, fetch_all_cnbc_news, fetch_cnbc_news

FIXTURE_DIR = Path(__file__).parent / "fixtures" / "cnbc"


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
        rss_xml = _load_fixture("markets_rss.xml")
        if not rss_xml:
            pytest.skip("缺少 fixture: fixtures/cnbc/markets_rss.xml")

        monkeypatch.setattr(
            "nasdx.cnbc_news.requests.get",
            lambda *args, **kwargs: type("Resp", (), {"text": rss_xml})(),
        )

        items = fetch_cnbc_news("markets", "Markets+News")
        assert isinstance(items, list)
        assert all(isinstance(item, NewsItem) for item in items)

    def test_fetch_all_cnbc_news_structure(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """测试批量获取的新闻结构。"""
        monkeypatch.setattr(
            "nasdx.cnbc_news.requests.get",
            lambda *args, **kwargs: type("Resp", (), {"text": ""})(),
        )

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


class TestNewsSourcesConfig:
    """测试 news_sources.json 配置。"""

    def test_cnbc_source_exists(self) -> None:
        """验证 news_sources.json 包含 CNBC 配置。"""
        config_path = Path(__file__).parent.parent / "nasdx" / "news_sources.json"
        if not config_path.exists():
            pytest.skip("news_sources.json 不存在")

        with open(config_path, encoding="utf-8") as f:
            config = json.load(f)

        categories = config.get("categories", [])
        cnbc_category = next((c for c in categories if c.get("name") == "cnbc"), None)
        assert cnbc_category is not None, "news_sources.json 中缺少 CNBC 类别配置"
        assert len(cnbc_category.get("sources", [])) > 0, "CNBC 类别下无源配置"

    def test_cnbc_source_fields(self) -> None:
        """验证 CNBC 源字段完整性。"""
        config_path = Path(__file__).parent.parent / "nasdx" / "news_sources.json"
        if not config_path.exists():
            pytest.skip("news_sources.json 不存在")

        with open(config_path, encoding="utf-8") as f:
            config = json.load(f)

        categories = config.get("categories", [])
        cnbc_category = next((c for c in categories if c.get("name") == "cnbc"), None)
        assert cnbc_category is not None

        for source in cnbc_category.get("sources", []):
            assert "name" in source, "源配置缺少 name 字段"
            assert "url" in source, "源配置缺少 url 字段"
            assert source["url"].startswith("https://"), f"URL 不合法: {source['url']}"
