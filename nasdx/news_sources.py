# -*- coding: utf-8 -*-
"""
新闻 / 媒体证据来源归一化（#69）。

与 ``nasdx.announcement_sources``（法定披露）分工：本模块处理**非法定披露**
的信息面来源——官方媒体、主流财经媒体、券商研究与社交自媒体，
统一映射到来源类型与权威分，并交由 ``nasdx.evidence`` 做转载去重。

原则：

- **来源权威可配置、可审计**：``classify_source_type`` 的映射表公开可读，
  未知来源默认按 ``media`` 而不是静默提升可信度；社交 / 自媒体权威分低于
  买入闸门阈值，因而不能单独触发确定性买入（见 ``evidence.evidence_gate``）。
- **不默认接入任何抓取器**：项目当前没有稳定的授权新闻源，
  ``fetch_news`` 默认返回 ``not_configured``，明确区别于“确实没有新消息”。
  用户可用 :func:`register_news_source` 接入自有来源，
  或用 :func:`normalize_news_items` 把已有抓取结果归一化为证据。
- **转载不重复增强信号**：同一事件的多家转载在
  ``evidence.dedupe_evidence`` 中合并为一条主证据并记录引用来源数量。
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Callable, Iterable, Sequence

from nasdx.announcement_sources import classify_event_type
from nasdx.evidence import (
    CST,
    FETCH_EMPTY,
    FETCH_NOT_CONFIGURED,
    FETCH_OK,
    FETCH_PARSE_FAILED,
    EvidenceItem,
    SourceFetchResult,
    build_evidence_item,
    to_cst,
)

__all__ = [
    "classify_source_type",
    "normalize_news_items",
    "register_news_source",
    "news_source_names",
    "fetch_news",
]


#: 来源名关键词 → 来源类型。顺序敏感，先命中先生效。
_SOURCE_TYPE_KEYWORDS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("regulator", ("证监会", "交易所", "银保监", "国资委", "发改委", "工信部", "财政部")),
    (
        "official_media",
        (
            "新华社", "人民日报", "央视", "中央广播电视", "经济日报", "证券时报",
            "中国证券报", "上海证券报", "证券日报", "新华网", "人民网",
        ),
    ),
    ("research", ("研报", "研究所", "证券研究", "券商", "研究院")),
    ("social", ("雪球", "股吧", "微博", "论坛", "自媒体", "公众号", "贴吧", "知乎")),
    (
        "media",
        ("东方财富", "新浪", "腾讯", "网易", "财联社", "第一财经", "界面", "澎湃", "同花顺"),
    ),
)


def classify_source_type(source_name: Any) -> str:
    """按来源名确定性映射来源类型；未知来源按 ``media`` 处理（不上调可信度）。"""
    text = str(source_name or "")
    for source_type, keywords in _SOURCE_TYPE_KEYWORDS:
        if any(keyword in text for keyword in keywords):
            return source_type
    return "media"


def _relevance(code: str, title: str, name_hint: str) -> float:
    """确定性相关度：命中代码 1.0，命中公司简称 0.9，否则 0.6。"""
    if code and code in title:
        return 1.0
    if name_hint and name_hint in title:
        return 0.9
    return 0.6


def normalize_news_items(
    raw_items: Iterable[Any],
    *,
    code: Any,
    source_name: Any = None,
    name_hint: Any = "",
    now: Any = None,
    fetched_at: Any = None,
) -> SourceFetchResult:
    """把原始新闻记录归一化为 :class:`SourceFetchResult`。

    每条记录需提供 ``title`` 与 ``published_at``（键名兼容
    ``标题`` / ``发布时间`` / ``time`` / ``url``）。缺少标题或时间的记录被跳过；
    全部记录都无法解析时返回 ``parse_failed``（视为格式变化，而不是无消息）。
    """
    moment = to_cst(now) or datetime.now(CST)
    fetched = to_cst(fetched_at) or moment
    code_text = str(code or "").strip()
    hint = str(name_hint or "").strip()
    default_source = str(source_name or "").strip()

    items: list[EvidenceItem] = []
    total = 0
    for raw in raw_items or []:
        total += 1
        if not isinstance(raw, dict):
            continue
        title = str(raw.get("title") or raw.get("标题") or "").strip()
        published = to_cst(
            raw.get("published_at")
            or raw.get("publish_time")
            or raw.get("发布时间")
            or raw.get("time")
        )
        if not title or published is None:
            continue
        item_source = str(raw.get("source") or raw.get("来源") or default_source).strip()
        source_type = str(raw.get("source_type") or "").strip() or classify_source_type(item_source)
        items.append(
            build_evidence_item(
                code=str(raw.get("code") or code_text).strip(),
                title=title,
                published_at=published,
                source_name=item_source or default_source or "unknown",
                source_type=source_type,
                source_url=str(raw.get("url") or raw.get("链接") or "").strip(),
                event_type=str(raw.get("event_type") or "").strip() or classify_event_type(title),
                body=raw.get("body") or raw.get("content") or raw.get("正文") or "",
                summary=raw.get("summary") or "",
                relevance_score=_relevance(code_text, title, hint),
                fetched_at=fetched,
                now=moment,
            )
        )

    resolved_name = default_source or "news"
    resolved_type = classify_source_type(default_source) if default_source else "media"
    if not items:
        status = FETCH_PARSE_FAILED if total else FETCH_EMPTY
        return SourceFetchResult(
            source_name=resolved_name,
            source_type=resolved_type,
            status=status,
            error=f"{total} records missing title/published_at" if total else "",
            pages_fetched=1 if total else 0,
            fetched_at=fetched,
        )
    return SourceFetchResult(
        source_name=resolved_name,
        source_type=resolved_type,
        status=FETCH_OK,
        items=tuple(items),
        error=f"{total - len(items)} records skipped" if total > len(items) else "",
        pages_fetched=1,
        fetched_at=fetched,
    )


@dataclass(frozen=True)
class NewsSource:
    """新闻来源注册项；``fetcher=None`` 表示声明但尚未接入。"""

    name: str
    source_type: str
    fetcher: Callable[..., SourceFetchResult] | None = None
    note: str = ""


_SOURCES: dict[str, NewsSource] = {
    "official_media": NewsSource(
        "official_media", "official_media", None, "官方媒体新闻源未接入，需人工复核"
    ),
    "finance_media": NewsSource(
        "finance_media", "media", None, "财经媒体新闻源未接入，需人工复核"
    ),
}

DEFAULT_NEWS_SOURCES: tuple[str, ...] = ()


def register_news_source(
    name: str,
    source_type: str,
    fetcher: Callable[..., SourceFetchResult] | None,
    note: str = "",
) -> None:
    """注册或覆盖一个新闻来源抓取器。"""
    _SOURCES[str(name)] = NewsSource(str(name), str(source_type), fetcher, str(note))


def news_source_names() -> tuple[str, ...]:
    """返回全部已声明的新闻来源名（含未接入项）。"""
    return tuple(sorted(_SOURCES))


def fetch_news(
    code: Any,
    *,
    sources: Sequence[str] | None = None,
    now: Any = None,
    **kwargs: Any,
) -> list[SourceFetchResult]:
    """按来源名抓取新闻；未接入来源显式返回 ``not_configured``。"""
    moment = to_cst(now) or datetime.now(CST)
    names: Iterable[str] = sources if sources is not None else DEFAULT_NEWS_SOURCES
    results: list[SourceFetchResult] = []
    for name in names:
        source = _SOURCES.get(str(name))
        if source is None:
            results.append(
                SourceFetchResult(
                    source_name=str(name),
                    source_type="media",
                    status=FETCH_NOT_CONFIGURED,
                    error="unknown source",
                    fetched_at=moment,
                )
            )
            continue
        if source.fetcher is None:
            results.append(
                SourceFetchResult(
                    source_name=source.name,
                    source_type=source.source_type,
                    status=FETCH_NOT_CONFIGURED,
                    error=source.note or "source not connected",
                    fetched_at=moment,
                )
            )
            continue
        results.append(source.fetcher(code, now=moment, **kwargs))
    return results
