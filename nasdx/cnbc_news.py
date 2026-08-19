"""CNBC 资讯源抓取模块。

仅用于客观数据呈现，不提供买卖推荐、预测或个股排名。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from typing import List
from urllib.parse import quote

import requests
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

# CNBC RSS 搜索端点模板
CNBC_SEARCH_URL = "https://search.cnbc.com/search/rssfull?query={query}&path=news&ct={category}"

# 合规过滤关键词（禁止内容）
PROHIBITED_KEYWORDS = {
    "赌博", "赌场", "加密货币交易", "杠杆交易",
    "内幕交易", "操纵市场", "非法荐股",
}


@dataclass
class NewsItem:
    """单条资讯数据结构。"""

    title: str
    link: str
    published: datetime | None
    summary: str
    source: str
    category: str

    def to_dict(self) -> dict:
        return {
            "title": self.title,
            "link": self.link,
            "published": self.published.isoformat() if self.published else None,
            "summary": self.summary,
            "source": self.source,
            "category": self.category,
        }


def fetch_cnbc_news(query: str, category: str, timeout: int = 10) -> List[NewsItem]:
    """从 CNBC 抓取指定主题的 RSS 资讯。

    Args:
        query: 搜索关键词
        category: 分类名称
        timeout: 请求超时秒数

    Returns:
        解析后的 NewsItem 列表
    """
    encoded_query = quote(query)
    encoded_category = quote(category)
    url = CNBC_SEARCH_URL.format(query=encoded_query, category=encoded_category)

    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/120.0.0.0 Safari/537.36"
        )
    }

    try:
        resp = requests.get(url, headers=headers, timeout=timeout)
        resp.raise_for_status()
    except requests.RequestException as exc:
        logger.warning(f"CNBC RSS 请求失败: {exc}")
        return []

    return _parse_rss_content(resp.text, source=f"CNBC-{category}")


def _parse_rss_content(xml_text: str, source: str) -> List[NewsItem]:
    """解析 RSS XML 内容，返回 NewsItem 列表。"""
    soup = BeautifulSoup(xml_text, "xml")
    items = []

    for entry in soup.find_all("item")[:30]:
        title = entry.find("title")
        link = entry.find("link")
        pub_date = entry.find("pubDate")
        description = entry.find("description")

        if not all([title, link]):
            continue

        parsed_date = None
        if pub_date and pub_date.string:
            try:
                parsed_date = datetime.strptime(
                    pub_date.string.strip(), "%a, %d %b %Y %H:%M:%S %Z"
                )
            except ValueError:
                logger.debug(f"日期解析失败: {pub_date.string}")

        summary = description.string if description else ""
        title_text = title.string or ""
        link_text = link.string or ""

        if not _is_compliant(title_text, summary):
            continue

        items.append(
            NewsItem(
                title=title_text,
                link=link_text,
                published=parsed_date,
                summary=summary,
                source=source,
                category="cnbc",
            )
        )

    return sorted(items, key=lambda x: x.published or datetime.min, reverse=True)


def _is_compliant(title: str, summary: str) -> bool:
    """合规过滤：检查是否包含禁止内容关键词。"""
    text = f"{title} {summary}"
    return not any(kw in text for kw in PROHIBITED_KEYWORDS)


def fetch_all_cnbc_news(timeout: int = 10) -> List[NewsItem]:
    """获取所有配置的 CNBC 分类资讯。"""
    queries = [
        ("markets", "Markets+News"),
        ("technology", "Technology"),
        ("earnings", "Earnings"),
    ]

    all_items = []
    for query, category in queries:
        items = fetch_cnbc_news(query, category, timeout)
        all_items.extend(items)
        logger.info(f"CNBC {category} 获取 {len(items)} 条")

    return sorted(all_items, key=lambda x: x.published or datetime.min, reverse=True)
