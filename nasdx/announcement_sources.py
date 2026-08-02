# -*- coding: utf-8 -*-
"""
官方 / 监管公告连接器（#69）。

已接入：**巨潮资讯网（CNINFO）历史公告查询**——沪深北三市上市公司法定披露
的官方指定平台，返回公告 ID、标题、披露时间与原文 URL，全部可确定性解析。

设计要点：

- **分页**：按 ``pageNum`` 逐页拉取，遇到不足一页、``hasMore=false`` 或达到
  ``max_pages`` 即停止；跨页重复公告按 ``announcementId`` 去重。
- **三态区分**：网络 / 超时 / 非 200 → ``failed``；响应不是 JSON、缺少
  ``announcements`` 字段或全部记录无法解析 → ``parse_failed``；
  接口正常返回但没有公告 → ``empty``。三者语义不同，绝不合并为
  “没有负面消息”。
- **未接入来源显式声明**：上交所 / 深交所自有披露接口尚未接入，
  查询时返回 ``not_configured``，交由 ``nasdx.external_review`` 的人工复核入口。
- **可注入**：``session`` 参数接受任何具备 ``post()`` 的对象，
  单元测试无需真实网络即可覆盖分页、超时、格式变化与重复公告。

所有事件类型由标题关键词确定性分类，不经过 LLM。
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Callable, Iterable, Sequence

from nasdx.evidence import (
    CST,
    FETCH_EMPTY,
    FETCH_FAILED,
    FETCH_NOT_CONFIGURED,
    FETCH_OK,
    FETCH_PARSE_FAILED,
    EvidenceItem,
    SourceFetchResult,
    build_evidence_item,
    to_cst,
)

__all__ = [
    "CNINFO_QUERY_URL",
    "classify_event_type",
    "fetch_cninfo_announcements",
    "fetch_announcements",
    "register_announcement_source",
    "announcement_source_names",
]


CNINFO_QUERY_URL = "http://www.cninfo.com.cn/new/hisAnnouncement/query"
CNINFO_PAGE_URL = "http://static.cninfo.com.cn/"

#: 标题关键词 → 事件类型（顺序敏感：先匹配到的优先）
_EVENT_KEYWORDS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("suspension", ("停牌", "复牌", "停复牌", "终止上市", "退市风险")),
    (
        "regulatory",
        ("监管", "问询函", "关注函", "警示函", "处罚", "立案", "调查", "整改", "违规", "纪律处分"),
    ),
    ("earnings", ("业绩", "年度报告", "半年度报告", "季度报告", "快报", "预告", "财务报表")),
    ("order", ("中标", "合同", "订单", "签约", "框架协议", "采购")),
    (
        "capital_action",
        (
            "增发", "配股", "回购", "股权激励", "减持", "增持", "可转债",
            "定向", "分红", "利润分配", "股份变动", "收购", "重组",
        ),
    ),
    ("industry", ("行业", "产能", "投产", "扩产", "价格调整")),
)


def classify_event_type(title: Any) -> str:
    """按标题关键词确定性分类事件类型；无命中返回 ``other``。"""
    text = str(title or "")
    for event_type, keywords in _EVENT_KEYWORDS:
        if any(keyword in text for keyword in keywords):
            return event_type
    return "other"


def _cninfo_column(code: str) -> str:
    """按代码前缀选择巨潮的板块参数（沪 sse / 深 szse / 北 bj）。"""
    text = str(code or "").strip()
    if text.startswith(("60", "68", "51", "58", "11", "9")):
        return "sse"
    if text.startswith(("4", "8", "92")):
        return "bj"
    return "szse"


def _announcement_url(record: dict) -> str:
    adjunct = str(record.get("adjunctUrl") or "").strip()
    if not adjunct:
        return ""
    if adjunct.startswith("http"):
        return adjunct
    return CNINFO_PAGE_URL + adjunct.lstrip("/")


def fetch_cninfo_announcements(
    code: Any,
    *,
    since: Any = None,
    page_size: int = 30,
    max_pages: int = 5,
    timeout: float = 6.0,
    session: Any = None,
    org_id: Any = None,
    now: Any = None,
) -> SourceFetchResult:
    """抓取巨潮资讯网的公司公告并归一化为证据。

    Parameters
    ----------
    code:
        6 位 A 股代码。
    since:
        只保留该时间之后发布的公告（含分页提前终止）。
    page_size / max_pages:
        分页参数；两者共同给出单次抓取的上界，避免无界拉取。
    session:
        任何具备 ``post(url, data=..., timeout=...)`` 的对象；缺省使用
        ``requests``。测试通过注入该参数完全避免真实网络。

    Returns
    -------
    SourceFetchResult
        ``status`` 为 ``ok`` / ``empty`` / ``failed`` / ``parse_failed`` 之一。
    """
    moment = to_cst(now) or datetime.now(CST)
    threshold = to_cst(since)
    code_text = str(code or "").strip()
    stock = f"{code_text},{org_id}" if org_id else code_text

    client = session
    if client is None:
        try:
            import requests  # 延迟导入：无网络场景下也能导入本模块
        except ImportError as exc:  # pragma: no cover - 环境缺依赖
            return SourceFetchResult(
                source_name="cninfo",
                source_type="company",
                status=FETCH_FAILED,
                error=f"requests unavailable: {exc}",
                fetched_at=moment,
            )
        client = requests

    raw_records: list[dict] = []
    seen_ids: set[str] = set()
    pages = 0
    stop = False
    for page in range(1, max(1, int(max_pages)) + 1):
        payload = {
            "stock": stock,
            "tabName": "fulltext",
            "pageSize": int(page_size),
            "pageNum": page,
            "column": _cninfo_column(code_text),
            "category": "",
            "plate": "",
            "seDate": "",
            "searchkey": "",
            "secid": "",
            "sortName": "",
            "sortType": "",
            "isHLtitle": "true",
        }
        try:
            response = client.post(CNINFO_QUERY_URL, data=payload, timeout=timeout)
        except Exception as exc:
            return SourceFetchResult(
                source_name="cninfo",
                source_type="company",
                status=FETCH_FAILED,
                error=f"{type(exc).__name__}: {exc}",
                pages_fetched=pages,
                fetched_at=moment,
            )
        status_code = getattr(response, "status_code", 200)
        if status_code != 200:
            return SourceFetchResult(
                source_name="cninfo",
                source_type="company",
                status=FETCH_FAILED,
                error=f"http {status_code}",
                pages_fetched=pages,
                fetched_at=moment,
            )
        try:
            body = response.json()
        except Exception as exc:
            return SourceFetchResult(
                source_name="cninfo",
                source_type="company",
                status=FETCH_PARSE_FAILED,
                error=f"invalid json: {type(exc).__name__}",
                pages_fetched=pages,
                fetched_at=moment,
            )
        pages += 1
        if not isinstance(body, dict) or "announcements" not in body:
            return SourceFetchResult(
                source_name="cninfo",
                source_type="company",
                status=FETCH_PARSE_FAILED,
                error="missing 'announcements' field",
                pages_fetched=pages,
                fetched_at=moment,
            )
        announcements = body.get("announcements")
        if announcements is None:
            announcements = []
        if not isinstance(announcements, list):
            return SourceFetchResult(
                source_name="cninfo",
                source_type="company",
                status=FETCH_PARSE_FAILED,
                error="'announcements' is not a list",
                pages_fetched=pages,
                fetched_at=moment,
            )

        for record in announcements:
            if not isinstance(record, dict):
                continue
            ann_id = str(record.get("announcementId") or "").strip()
            if ann_id and ann_id in seen_ids:
                continue  # 跨页重复公告
            if ann_id:
                seen_ids.add(ann_id)
            published = to_cst(record.get("announcementTime"))
            if threshold is not None and published is not None and published <= threshold:
                stop = True
                continue
            raw_records.append(record)

        if stop or len(announcements) < int(page_size) or not body.get("hasMore", False):
            break

    if not raw_records:
        return SourceFetchResult(
            source_name="cninfo",
            source_type="company",
            status=FETCH_EMPTY,
            pages_fetched=pages,
            fetched_at=moment,
        )

    items: list[EvidenceItem] = []
    unparsable = 0
    for record in raw_records:
        title = str(record.get("announcementTitle") or "").strip()
        published = to_cst(record.get("announcementTime"))
        if not title or published is None:
            unparsable += 1
            continue
        items.append(
            build_evidence_item(
                code=str(record.get("secCode") or code_text).strip(),
                title=title,
                published_at=published,
                source_name="cninfo",
                source_type="company",
                source_url=_announcement_url(record),
                event_type=classify_event_type(title),
                announcement_id=str(record.get("announcementId") or "").strip(),
                fetched_at=moment,
                now=moment,
            )
        )

    if not items:
        return SourceFetchResult(
            source_name="cninfo",
            source_type="company",
            status=FETCH_PARSE_FAILED,
            error=f"{unparsable} records missing title/announcementTime",
            pages_fetched=pages,
            fetched_at=moment,
        )

    return SourceFetchResult(
        source_name="cninfo",
        source_type="company",
        status=FETCH_OK,
        items=tuple(items),
        error=f"{unparsable} records skipped" if unparsable else "",
        pages_fetched=pages,
        fetched_at=moment,
    )


@dataclass(frozen=True)
class AnnouncementSource:
    """公告来源注册项；``fetcher=None`` 表示声明但尚未接入。"""

    name: str
    source_type: str
    fetcher: Callable[..., SourceFetchResult] | None = None
    note: str = ""


_SOURCES: dict[str, AnnouncementSource] = {
    "cninfo": AnnouncementSource("cninfo", "company", fetch_cninfo_announcements),
    "sse_disclosure": AnnouncementSource(
        "sse_disclosure", "exchange", None, "上交所披露接口未接入，需人工复核"
    ),
    "szse_disclosure": AnnouncementSource(
        "szse_disclosure", "exchange", None, "深交所披露接口未接入，需人工复核"
    ),
}

DEFAULT_ANNOUNCEMENT_SOURCES: tuple[str, ...] = ("cninfo",)


def register_announcement_source(
    name: str,
    source_type: str,
    fetcher: Callable[..., SourceFetchResult] | None,
    note: str = "",
) -> None:
    """注册或覆盖一个公告来源（便于接入交易所 / 监管自有接口）。"""
    _SOURCES[str(name)] = AnnouncementSource(str(name), str(source_type), fetcher, str(note))


def announcement_source_names() -> tuple[str, ...]:
    """返回全部已声明的公告来源名（含未接入项）。"""
    return tuple(sorted(_SOURCES))


def fetch_announcements(
    code: Any,
    *,
    sources: Sequence[str] | None = None,
    now: Any = None,
    **kwargs: Any,
) -> list[SourceFetchResult]:
    """按来源名依次抓取公告；未注册 / 未接入来源返回 ``not_configured``。"""
    moment = to_cst(now) or datetime.now(CST)
    names: Iterable[str] = sources if sources is not None else DEFAULT_ANNOUNCEMENT_SOURCES
    results: list[SourceFetchResult] = []
    for name in names:
        source = _SOURCES.get(str(name))
        if source is None:
            results.append(
                SourceFetchResult(
                    source_name=str(name),
                    source_type="exchange",
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
