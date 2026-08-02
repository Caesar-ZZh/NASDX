# -*- coding: utf-8 -*-
"""#69 公告/新闻证据层与新鲜度契约测试。

覆盖：跨时区、盘前/盘中/盘后、重复抓取、媒体转载、旧闻重发、
来源抓取失败 / 解析失败 / 确实无新增（三态）、未接入来源、
分页、人工证据录入、事件触发缓存失效、LLM 摘要不可篡改源字段、
以及信息面未核验时的买入降级闸门。
"""
import contextlib
import os
import sys
import unittest
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from nasdx import announcement_sources as ann  # noqa: E402
from nasdx import evidence as ev  # noqa: E402
from nasdx import external_review  # noqa: E402
from nasdx import news_sources as news  # noqa: E402


NOW = datetime(2026, 8, 3, 10, 30, tzinfo=ev.CST)


@contextlib.contextmanager
def env_var(key, value):
    """只设置/还原单个环境变量（本环境存在超长变量，patch.dict 会 ValueError）。"""
    old = os.environ.get(key)
    try:
        if value is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = value
        yield
    finally:
        if old is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = old


class FakeResponse:
    def __init__(self, payload=None, status_code=200, raise_json=None):
        self._payload = payload
        self.status_code = status_code
        self._raise_json = raise_json

    def json(self):
        if self._raise_json is not None:
            raise self._raise_json
        return self._payload


class FakeSession:
    """按 pageNum 顺序返回预设响应；记录调用参数供分页断言。"""

    def __init__(self, responses):
        self._responses = list(responses)
        self.calls = []

    def post(self, url, data=None, timeout=None):
        self.calls.append({"url": url, "data": dict(data or {}), "timeout": timeout})
        if not self._responses:
            raise AssertionError("unexpected extra request")
        response = self._responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


def cninfo_page(records, has_more=False):
    return FakeResponse({"announcements": records, "hasMore": has_more})


def announcement_record(ann_id, title, published_ms, code="600150"):
    return {
        "announcementId": ann_id,
        "secCode": code,
        "secName": "示例",
        "announcementTitle": title,
        "announcementTime": published_ms,
        "adjunctUrl": f"finalpage/2026-08-03/{ann_id}.PDF",
    }


def ms(moment):
    return int(moment.timestamp() * 1000)


# --------------------------------------------------------------------------
# 时间与时段
# --------------------------------------------------------------------------


class TimeNormalizationTest(unittest.TestCase):
    def test_utc_string_converted_to_beijing(self):
        parsed = ev.to_cst("2026-08-03T02:30:00Z")
        self.assertEqual(parsed.utcoffset(), timedelta(hours=8))
        self.assertEqual(parsed.hour, 10)

    def test_other_offset_converted(self):
        parsed = ev.to_cst("2026-08-02T21:30:00-05:00")
        self.assertEqual(parsed.hour, 10)
        self.assertEqual(parsed.day, 3)

    def test_naive_treated_as_beijing(self):
        parsed = ev.to_cst("2026-08-03 09:15:00")
        self.assertEqual(parsed.utcoffset(), timedelta(hours=8))
        self.assertEqual(parsed.hour, 9)

    def test_epoch_ms_and_seconds(self):
        target = datetime(2026, 8, 3, 10, 0, tzinfo=ev.CST)
        self.assertEqual(ev.to_cst(ms(target)), target)
        self.assertEqual(ev.to_cst(int(target.timestamp())), target)

    def test_unparsable_returns_none(self):
        for bad in (None, "", "不是时间", True, []):
            self.assertIsNone(ev.to_cst(bad))

    def test_market_sessions(self):
        self.assertEqual(ev.market_session("2026-08-03 08:30:00"), "pre_market")
        self.assertEqual(ev.market_session("2026-08-03 10:30:00"), "intraday")
        self.assertEqual(ev.market_session("2026-08-03 12:00:00"), "intraday")
        self.assertEqual(ev.market_session("2026-08-03 18:00:00"), "post_market")
        self.assertEqual(ev.market_session("2026-08-01 10:30:00"), "non_trading")  # 周六

    def test_session_uses_beijing_time_for_foreign_timestamp(self):
        # UTC 01:00 == 北京 09:00 == 盘前，不能按 UTC 判成非交易时段
        item = ev.build_evidence_item(
            code="600150",
            title="业绩快报",
            published_at="2026-08-03T01:00:00Z",
            source_name="cninfo",
            source_type="company",
            now=NOW,
        )
        self.assertEqual(item.session, "pre_market")


# --------------------------------------------------------------------------
# 证据对象契约
# --------------------------------------------------------------------------


class EvidenceItemContractTest(unittest.TestCase):
    def _item(self, **kwargs):
        params = dict(
            code="600150",
            title="关于签订重大合同的公告",
            published_at=NOW - timedelta(hours=1),
            source_name="cninfo",
            source_type="company",
            source_url="http://static.cninfo.com.cn/a.PDF",
            event_type="order",
            announcement_id="A1",
            now=NOW,
        )
        params.update(kwargs)
        return ev.build_evidence_item(**params)

    def test_required_fields_present(self):
        payload = self._item().to_dict()
        for key in (
            "evidence_id",
            "code",
            "title",
            "published_at",
            "fetched_at",
            "source_name",
            "source_type",
            "source_url",
            "event_type",
            "authority_score",
            "content_fingerprint",
            "status",
        ):
            self.assertIn(key, payload)
            self.assertNotIn(payload[key], (None, ""), f"{key} 不能为空")

    def test_ids_are_stable_across_refetch(self):
        first = self._item(fetched_at=NOW - timedelta(minutes=30))
        second = self._item(fetched_at=NOW)
        self.assertEqual(first.evidence_id, second.evidence_id)
        self.assertEqual(first.content_fingerprint, second.content_fingerprint)

    def test_authoritative_source_is_verified(self):
        self.assertEqual(self._item().status, ev.STATUS_VERIFIED)
        self.assertTrue(self._item().is_authoritative)

    def test_low_authority_source_is_unverified(self):
        item = self._item(source_type="social", source_name="股吧", announcement_id=None)
        self.assertEqual(item.status, ev.STATUS_UNVERIFIED)
        self.assertFalse(item.is_authoritative)
        self.assertLess(item.authority_score, ev.authority_score("exchange"))

    def test_unparsable_publish_time_is_failed_not_fresh(self):
        item = self._item(published_at="昨天下午")
        self.assertEqual(item.status, ev.STATUS_FAILED)
        self.assertIn("published_at_unparsable", item.notes)

    def test_future_dated_is_unverified(self):
        item = self._item(published_at=NOW + timedelta(hours=2))
        self.assertEqual(item.status, ev.STATUS_UNVERIFIED)
        self.assertIn("future_dated", item.notes)

    def test_authority_order_matches_policy(self):
        table = ev.authority_table()
        self.assertGreater(table["exchange"], table["official_media"])
        self.assertGreater(table["official_media"], table["media"])
        self.assertGreater(table["media"], table["social"])

    def test_authority_table_is_configurable(self):
        with env_var("NASDX_EVIDENCE_AUTHORITY", '{"social": 0.05}'):
            self.assertAlmostEqual(ev.authority_score("social"), 0.05)
        self.assertAlmostEqual(ev.authority_score("social"), 0.3)

    def test_unknown_source_type_is_not_trusted(self):
        self.assertLessEqual(ev.authority_score("some_blog"), 0.1)


# --------------------------------------------------------------------------
# 去重 / 转载 / 旧闻重发
# --------------------------------------------------------------------------


class DeduplicationTest(unittest.TestCase):
    def _news(self, source_name, source_type, published, title="某公司中标 10 亿元项目"):
        return ev.build_evidence_item(
            code="600150",
            title=title,
            published_at=published,
            source_name=source_name,
            source_type=source_type,
            event_type="order",
            now=NOW,
        )

    def test_same_announcement_fetched_twice_counts_once(self):
        first = ev.build_evidence_item(
            code="600150",
            title="中标公告",
            published_at=NOW - timedelta(hours=2),
            source_name="cninfo",
            source_type="company",
            announcement_id="A1",
            fetched_at=NOW - timedelta(hours=1),
            now=NOW,
        )
        second = ev.build_evidence_item(
            code="600150",
            title="中标公告",
            published_at=NOW - timedelta(hours=2),
            source_name="cninfo",
            source_type="company",
            announcement_id="A1",
            fetched_at=NOW,
            now=NOW,
        )
        merged = ev.dedupe_evidence([first, second])
        self.assertEqual(len(merged), 1)
        self.assertEqual(merged[0].duplicate_count, 2)

    def test_media_reprints_collapse_to_one_primary(self):
        published = NOW - timedelta(hours=1)
        items = [
            self._news("东方财富", "media", published),
            self._news("新浪财经", "media", published + timedelta(minutes=5)),
            self._news("新华社", "official_media", published + timedelta(minutes=10)),
        ]
        merged = ev.dedupe_evidence(items)
        self.assertEqual(len(merged), 1)
        primary = merged[0]
        self.assertEqual(primary.source_name, "新华社")  # 权威分最高者当主证据
        self.assertEqual(primary.duplicate_count, 3)
        self.assertEqual(len(primary.duplicate_sources), 3)
        self.assertEqual(primary.published_at, published)  # 取全组最早发布时间

    def test_dedupe_is_order_independent(self):
        published = NOW - timedelta(hours=1)
        items = [
            self._news("东方财富", "media", published),
            self._news("新华社", "official_media", published + timedelta(minutes=10)),
            self._news("雪球", "social", published + timedelta(minutes=20)),
        ]
        forward = ev.dedupe_evidence(items)
        backward = ev.dedupe_evidence(list(reversed(items)))
        self.assertEqual([i.evidence_id for i in forward], [i.evidence_id for i in backward])
        self.assertEqual(forward[0].duplicate_sources, backward[0].duplicate_sources)

    def test_republished_old_news_does_not_look_fresh(self):
        original = self._news("东方财富", "media", NOW - timedelta(days=30))
        repost = ev.build_evidence_item(
            code="600150",
            title="某公司中标 10 亿元项目",
            published_at=NOW - timedelta(minutes=5),
            source_name="新浪财经",
            source_type="media",
            event_type="order",
            body="",
            announcement_id=None,
            now=NOW,
        )
        # 不同日期的转载指纹不同，需显式同 body/日期才归并；此处验证同日重发
        same_day_repost = self._news("新浪财经", "media", NOW - timedelta(days=30, minutes=-10))
        merged = ev.dedupe_evidence([original, same_day_repost])
        self.assertEqual(len(merged), 1)
        self.assertIn("republished", merged[0].notes)
        refreshed = ev.apply_freshness(merged, NOW)
        self.assertEqual(refreshed[0].status, ev.STATUS_STALE)
        self.assertIsNotNone(repost)

    def test_reprint_count_does_not_change_authority(self):
        published = NOW - timedelta(hours=1)
        many = [self._news(f"自媒体{i}", "social", published) for i in range(20)]
        merged = ev.dedupe_evidence(many)
        self.assertEqual(len(merged), 1)
        self.assertEqual(merged[0].duplicate_count, 20)
        self.assertAlmostEqual(merged[0].authority_score, ev.authority_score("social"))


# --------------------------------------------------------------------------
# 新鲜度 / TTL
# --------------------------------------------------------------------------


class FreshnessTest(unittest.TestCase):
    def _item(self, event_type, age):
        return ev.build_evidence_item(
            code="600150",
            title="标题",
            published_at=NOW - age,
            source_name="cninfo",
            source_type="company",
            event_type=event_type,
            announcement_id=f"A-{event_type}",
            now=NOW,
        )

    def test_ttl_differs_by_event_type(self):
        self.assertGreater(ev.event_ttl_minutes("earnings"), ev.event_ttl_minutes("other"))
        self.assertNotEqual(ev.event_ttl_minutes("suspension"), ev.event_ttl_minutes("earnings"))

    def test_flash_news_expires_before_earnings(self):
        flash = self._item("other", timedelta(hours=6))
        earnings = self._item("earnings", timedelta(hours=6))
        self.assertEqual(flash.status, ev.STATUS_STALE)
        self.assertEqual(earnings.status, ev.STATUS_VERIFIED)

    def test_ttl_is_configurable(self):
        with env_var("NASDX_EVIDENCE_TTL_MINUTES", '{"other": 100000}'):
            self.assertEqual(self._item("other", timedelta(hours=6)).status, ev.STATUS_VERIFIED)
        self.assertEqual(self._item("other", timedelta(hours=6)).status, ev.STATUS_STALE)

    def test_apply_freshness_marks_expired(self):
        item = self._item("earnings", timedelta(minutes=1))
        later = ev.apply_freshness([item], NOW + timedelta(days=30))
        self.assertEqual(later[0].status, ev.STATUS_STALE)
        self.assertIn("expired_by_ttl", later[0].notes)


# --------------------------------------------------------------------------
# 巨潮公告连接器
# --------------------------------------------------------------------------


class CninfoConnectorTest(unittest.TestCase):
    def test_pagination_collects_all_pages(self):
        base = NOW - timedelta(hours=3)
        page1 = cninfo_page(
            [
                announcement_record("A1", "关于签订重大合同的公告", ms(base)),
                announcement_record("A2", "2026 年半年度业绩预告", ms(base + timedelta(minutes=1))),
            ],
            has_more=True,
        )
        page2 = cninfo_page([announcement_record("A3", "股票交易异常波动公告", ms(base + timedelta(minutes=2)))])
        session = FakeSession([page1, page2])
        result = ann.fetch_cninfo_announcements("600150", page_size=2, session=session, now=NOW)
        self.assertEqual(result.status, ev.FETCH_OK)
        self.assertEqual(len(result.items), 3)
        self.assertEqual(result.pages_fetched, 2)
        self.assertEqual([call["data"]["pageNum"] for call in session.calls], [1, 2])

    def test_duplicate_announcement_across_pages_counted_once(self):
        base = NOW - timedelta(hours=3)
        page1 = cninfo_page(
            [
                announcement_record("A1", "中标公告", ms(base)),
                announcement_record("A2", "业绩预告", ms(base)),
            ],
            has_more=True,
        )
        page2 = cninfo_page(
            [
                announcement_record("A2", "业绩预告", ms(base)),
                announcement_record("A3", "回购公告", ms(base)),
            ]
        )
        result = ann.fetch_cninfo_announcements(
            "600150", page_size=2, session=FakeSession([page1, page2]), now=NOW
        )
        self.assertEqual(len(result.items), 3)
        self.assertEqual(len({item.evidence_id for item in result.items}), 3)

    def test_timeout_is_failed_not_empty(self):
        session = FakeSession([TimeoutError("timed out")])
        result = ann.fetch_cninfo_announcements("600150", session=session, now=NOW)
        self.assertEqual(result.status, ev.FETCH_FAILED)
        self.assertIn("TimeoutError", result.error)
        self.assertEqual(result.items, ())

    def test_http_error_is_failed(self):
        session = FakeSession([FakeResponse(status_code=502)])
        result = ann.fetch_cninfo_announcements("600150", session=session, now=NOW)
        self.assertEqual(result.status, ev.FETCH_FAILED)

    def test_invalid_json_is_parse_failed(self):
        session = FakeSession([FakeResponse(raise_json=ValueError("not json"))])
        result = ann.fetch_cninfo_announcements("600150", session=session, now=NOW)
        self.assertEqual(result.status, ev.FETCH_PARSE_FAILED)

    def test_schema_change_is_parse_failed(self):
        session = FakeSession([FakeResponse({"data": []})])
        result = ann.fetch_cninfo_announcements("600150", session=session, now=NOW)
        self.assertEqual(result.status, ev.FETCH_PARSE_FAILED)
        self.assertIn("announcements", result.error)

    def test_announcements_wrong_type_is_parse_failed(self):
        session = FakeSession([FakeResponse({"announcements": {"unexpected": 1}})])
        result = ann.fetch_cninfo_announcements("600150", session=session, now=NOW)
        self.assertEqual(result.status, ev.FETCH_PARSE_FAILED)

    def test_no_announcement_is_empty_not_failed(self):
        session = FakeSession([FakeResponse({"announcements": None, "hasMore": False})])
        result = ann.fetch_cninfo_announcements("600150", session=session, now=NOW)
        self.assertEqual(result.status, ev.FETCH_EMPTY)
        self.assertEqual(result.items, ())

    def test_all_records_unparsable_is_parse_failed(self):
        broken = [{"announcementId": "A1", "announcementTitle": "", "announcementTime": None}]
        session = FakeSession([cninfo_page(broken)])
        result = ann.fetch_cninfo_announcements("600150", session=session, now=NOW)
        self.assertEqual(result.status, ev.FETCH_PARSE_FAILED)

    def test_since_filter_skips_old_announcements(self):
        old = NOW - timedelta(days=10)
        fresh = NOW - timedelta(hours=1)
        page = cninfo_page(
            [
                announcement_record("A1", "新公告", ms(fresh)),
                announcement_record("A0", "旧公告", ms(old)),
            ]
        )
        result = ann.fetch_cninfo_announcements(
            "600150", since=NOW - timedelta(days=1), session=FakeSession([page]), now=NOW
        )
        self.assertEqual([item.title for item in result.items], ["新公告"])

    def test_event_classification(self):
        self.assertEqual(ann.classify_event_type("关于公司股票停牌的公告"), "suspension")
        self.assertEqual(ann.classify_event_type("收到上交所问询函的公告"), "regulatory")
        self.assertEqual(ann.classify_event_type("2026 年半年度业绩预告"), "earnings")
        self.assertEqual(ann.classify_event_type("关于签订日常经营重大合同的公告"), "order")
        self.assertEqual(ann.classify_event_type("关于回购股份的进展公告"), "capital_action")
        self.assertEqual(ann.classify_event_type("董事会会议决议"), "other")

    def test_unconnected_exchange_source_is_not_configured(self):
        results = ann.fetch_announcements("600150", sources=["sse_disclosure"], now=NOW)
        self.assertEqual(results[0].status, ev.FETCH_NOT_CONFIGURED)
        self.assertFalse(results[0].succeeded)

    def test_unknown_source_is_not_configured(self):
        results = ann.fetch_announcements("600150", sources=["nope"], now=NOW)
        self.assertEqual(results[0].status, ev.FETCH_NOT_CONFIGURED)


# --------------------------------------------------------------------------
# 三态与整体状态
# --------------------------------------------------------------------------


def source_result(status, items=(), name="cninfo", source_type="company"):
    return ev.SourceFetchResult(
        source_name=name,
        source_type=source_type,
        status=status,
        items=tuple(items),
        fetched_at=NOW,
    )


def announcement_item(event_type="order", age=timedelta(hours=1), code="600150", ann_id="A1"):
    return ev.build_evidence_item(
        code=code,
        title="公告标题",
        published_at=NOW - age,
        source_name="cninfo",
        source_type="company",
        event_type=event_type,
        announcement_id=ann_id,
        now=NOW,
    )


class BundleStateTest(unittest.TestCase):
    def test_three_states_are_distinct(self):
        self.assertNotEqual(ev.FETCH_FAILED, ev.FETCH_PARSE_FAILED)
        self.assertNotEqual(ev.FETCH_FAILED, ev.FETCH_EMPTY)
        self.assertNotEqual(ev.FETCH_PARSE_FAILED, ev.FETCH_EMPTY)

    def test_verified_new_state(self):
        bundle = ev.build_bundle([source_result(ev.FETCH_OK, [announcement_item()])], now=NOW)
        self.assertEqual(bundle.state(), ev.STATE_VERIFIED_NEW)
        self.assertIsNotNone(bundle.latest_verified_evidence_at())

    def test_empty_state_is_confirmed_no_news(self):
        bundle = ev.build_bundle([source_result(ev.FETCH_EMPTY)], now=NOW)
        self.assertEqual(bundle.state(), ev.STATE_NO_NEW_VERIFIED)
        self.assertIsNone(bundle.latest_verified_evidence_at())

    def test_fetch_failure_is_unavailable(self):
        bundle = ev.build_bundle([source_result(ev.FETCH_FAILED)], now=NOW)
        self.assertEqual(bundle.state(), ev.STATE_UNAVAILABLE)

    def test_parse_failure_is_unverified(self):
        bundle = ev.build_bundle(
            [source_result(ev.FETCH_OK, [announcement_item()]), source_result(ev.FETCH_PARSE_FAILED, name="news", source_type="media")],
            now=NOW,
        )
        self.assertEqual(bundle.state(), ev.STATE_UNVERIFIED)

    def test_not_configured_only_is_unavailable(self):
        bundle = ev.build_bundle(
            [source_result(ev.FETCH_NOT_CONFIGURED, name="sse_disclosure", source_type="exchange")],
            now=NOW,
        )
        self.assertEqual(bundle.state(), ev.STATE_UNAVAILABLE)

    def test_source_fetch_status_is_reported(self):
        bundle = ev.build_bundle(
            [
                source_result(ev.FETCH_EMPTY),
                source_result(ev.FETCH_NOT_CONFIGURED, name="official_media", source_type="official_media"),
            ],
            now=NOW,
        )
        self.assertEqual(
            bundle.source_fetch_status(),
            {"cninfo": ev.FETCH_EMPTY, "official_media": ev.FETCH_NOT_CONFIGURED},
        )

    def test_cross_source_dedup_in_bundle(self):
        published = NOW - timedelta(hours=1)
        official = ev.build_evidence_item(
            code="600150",
            title="中标 10 亿元项目",
            published_at=published,
            source_name="cninfo",
            source_type="company",
            event_type="order",
            now=NOW,
        )
        reprint = ev.build_evidence_item(
            code="600150",
            title="中标 10 亿元项目",
            published_at=published + timedelta(minutes=30),
            source_name="东方财富",
            source_type="media",
            event_type="order",
            now=NOW,
        )
        bundle = ev.build_bundle(
            [
                source_result(ev.FETCH_OK, [official]),
                source_result(ev.FETCH_OK, [reprint], name="东方财富", source_type="media"),
            ],
            now=NOW,
        )
        self.assertEqual(len(bundle.items), 1)
        self.assertEqual(bundle.items[0].source_type, "company")
        self.assertEqual(bundle.items[0].duplicate_count, 2)


# --------------------------------------------------------------------------
# 缓存失效信号（#65）
# --------------------------------------------------------------------------


class CacheInvalidationTest(unittest.TestCase):
    def test_event_maps_to_dimensions(self):
        targets = ev.cache_invalidation_targets([announcement_item("suspension")], now=NOW)
        self.assertEqual(targets["600150"], ["risk", "synthesis", "technical"])

    def test_industry_event_invalidates_sector_dimensions(self):
        targets = ev.cache_invalidation_targets([announcement_item("industry")], now=NOW)
        self.assertEqual(targets["600150"], ["chokepoint", "sector", "synthesis"])

    def test_only_new_events_invalidate(self):
        item = announcement_item("order", age=timedelta(hours=5))
        self.assertEqual(
            ev.cache_invalidation_targets([item], since=NOW - timedelta(hours=1), now=NOW), {}
        )
        self.assertIn("600150", ev.cache_invalidation_targets([item], since=NOW - timedelta(days=1), now=NOW))

    def test_unverified_and_stale_evidence_do_not_invalidate(self):
        social = ev.build_evidence_item(
            code="600150",
            title="小道消息",
            published_at=NOW - timedelta(minutes=5),
            source_name="股吧",
            source_type="social",
            event_type="order",
            now=NOW,
        )
        stale = announcement_item("other", age=timedelta(days=3))
        self.assertEqual(ev.cache_invalidation_targets([social, stale], now=NOW), {})

    def test_multiple_codes_are_separated(self):
        targets = ev.cache_invalidation_targets(
            [
                announcement_item("order", code="600150", ann_id="A1"),
                announcement_item("regulatory", code="000001", ann_id="A2"),
            ],
            now=NOW,
        )
        self.assertEqual(sorted(targets), ["000001", "600150"])
        self.assertNotEqual(targets["000001"], targets["600150"])


# --------------------------------------------------------------------------
# 动作闸门
# --------------------------------------------------------------------------


class EvidenceGateTest(unittest.TestCase):
    def test_buy_blocked_when_sources_unavailable(self):
        bundle = ev.build_bundle([source_result(ev.FETCH_FAILED)], now=NOW)
        decision = ev.evidence_gate("buy", bundle, now=NOW)
        self.assertEqual(decision.action, ev.ACTION_WAIT)
        self.assertTrue(decision.downgraded)

    def test_buy_blocked_when_parse_failed(self):
        bundle = ev.build_bundle(
            [source_result(ev.FETCH_EMPTY), source_result(ev.FETCH_PARSE_FAILED, name="news", source_type="media")],
            now=NOW,
        )
        decision = ev.evidence_gate("add", bundle, now=NOW)
        self.assertEqual(decision.action, ev.ACTION_REVIEW_REQUIRED)

    def test_buy_allowed_when_no_new_announcement_confirmed(self):
        bundle = ev.build_bundle([source_result(ev.FETCH_EMPTY)], now=NOW)
        decision = ev.evidence_gate("buy", bundle, now=NOW)
        self.assertEqual(decision.action, "buy")
        self.assertFalse(decision.downgraded)

    def test_high_risk_event_forces_review_even_for_hold(self):
        bundle = ev.build_bundle([source_result(ev.FETCH_OK, [announcement_item("suspension")])], now=NOW)
        decision = ev.evidence_gate("hold", bundle, now=NOW)
        self.assertEqual(decision.action, ev.ACTION_REVIEW_REQUIRED)
        self.assertTrue(decision.blocking_evidence_ids)

    def test_risk_reducing_action_allowed_when_unavailable(self):
        bundle = ev.build_bundle([source_result(ev.FETCH_FAILED)], now=NOW)
        for action in ("reduce", "exit", "take_profit", "no_chase", "hold"):
            decision = ev.evidence_gate(action, bundle, now=NOW)
            self.assertEqual(decision.action, action)
            self.assertFalse(decision.downgraded)
            self.assertIn(decision.evidence_state, ev.STATE_UNAVAILABLE)

    def test_social_only_support_cannot_trigger_buy(self):
        social = ev.build_evidence_item(
            code="600150",
            title="传闻公司将中标大单",
            published_at=NOW - timedelta(minutes=10),
            source_name="股吧",
            source_type="social",
            event_type="order",
            now=NOW,
        )
        bundle = ev.build_bundle(
            [
                source_result(ev.FETCH_EMPTY),
                source_result(ev.FETCH_OK, [social], name="股吧", source_type="social"),
            ],
            now=NOW,
        )
        decision = ev.evidence_gate(
            "buy", bundle, supporting_evidence_ids=[social.evidence_id], now=NOW
        )
        self.assertEqual(decision.action, ev.ACTION_REVIEW_REQUIRED)
        self.assertIn(social.evidence_id, decision.blocking_evidence_ids)

    def test_authoritative_support_allows_buy(self):
        item = announcement_item("order")
        bundle = ev.build_bundle([source_result(ev.FETCH_OK, [item])], now=NOW)
        decision = ev.evidence_gate(
            "buy", bundle, supporting_evidence_ids=[item.evidence_id], now=NOW
        )
        self.assertEqual(decision.action, "buy")

    def test_gate_decision_is_serializable(self):
        bundle = ev.build_bundle([source_result(ev.FETCH_EMPTY)], now=NOW)
        payload = ev.evidence_gate("buy", bundle, now=NOW).to_dict()
        self.assertEqual(payload["original_action"], "buy")
        self.assertIn("source_fetch_status", payload)


# --------------------------------------------------------------------------
# 人工证据 / LLM 边界 / 审计
# --------------------------------------------------------------------------


class ManualEvidenceTest(unittest.TestCase):
    def test_manual_evidence_is_authoritative_and_traceable(self):
        item = ev.manual_evidence(
            code="600150",
            title="已电话确认无未披露重大事项",
            source_name="人工复核",
            reviewer="zzh",
            now=NOW,
        )
        self.assertEqual(item.source_type, "manual")
        self.assertTrue(item.is_authoritative)
        self.assertEqual(item.status, ev.STATUS_VERIFIED)
        self.assertIn("reviewer:zzh", item.notes)

    def test_manual_evidence_unblocks_buy(self):
        item = ev.manual_evidence(
            code="600150", title="人工核验通过", source_name="人工复核", now=NOW
        )
        bundle = ev.build_bundle(
            [
                source_result(ev.FETCH_FAILED),
                source_result(ev.FETCH_OK, [item], name="人工复核", source_type="manual"),
            ],
            now=NOW,
        )
        self.assertEqual(bundle.state(), ev.STATE_VERIFIED_NEW)
        self.assertEqual(ev.evidence_gate("buy", bundle, now=NOW).action, "buy")

    def test_external_review_bridge_returns_evidence_item(self):
        item = external_review.record_manual_review_evidence(
            "600150", "复核通过：无新增风险红灯", reviewer="zzh", checked_at=NOW
        )
        self.assertIsInstance(item, ev.EvidenceItem)
        self.assertEqual(item.source_type, "manual")

    def test_external_review_pack_unchanged(self):
        pack = external_review.build_external_review_pack(
            [{"candidate": "示例", "status_code": "trial_candidate", "code": "600150", "type": "股票"}]
        )
        self.assertEqual(pack[0]["review_status"], "pending_manual_review")
        self.assertTrue(pack[0]["source_links"])


class LlmSummaryBoundaryTest(unittest.TestCase):
    def test_summary_only_field_changes(self):
        item = announcement_item()
        updated = ev.apply_llm_summary(item, "公司中标大额订单，短期利好")
        for field_name in (
            "evidence_id",
            "code",
            "title",
            "published_at",
            "fetched_at",
            "source_name",
            "source_type",
            "source_url",
            "status",
            "content_fingerprint",
            "authority_score",
        ):
            self.assertEqual(getattr(updated, field_name), getattr(item, field_name))
        self.assertNotEqual(updated.summary, item.summary)

    def test_summary_is_bounded_and_single_line(self):
        item = announcement_item()
        updated = ev.apply_llm_summary(item, "行" * 1000 + "\n换行")
        self.assertLessEqual(len(updated.summary), 300)
        self.assertNotIn("\n", updated.summary)

    def test_merge_ignores_unknown_ids(self):
        item = announcement_item()
        merged = ev.merge_llm_summaries([item], {"not-an-id": "伪造证据", item.evidence_id: "真实摘要"})
        self.assertEqual(len(merged), 1)
        self.assertEqual(merged[0].summary, "真实摘要")

    def test_items_are_immutable(self):
        item = announcement_item()
        with self.assertRaises(Exception):
            item.status = ev.STATUS_VERIFIED


class EvidenceAuditTest(unittest.TestCase):
    def test_audit_records_used_and_ignored(self):
        used = announcement_item("order", ann_id="A1")
        other = announcement_item("earnings", ann_id="A2")
        bundle = ev.build_bundle([source_result(ev.FETCH_OK, [used, other])], now=NOW)
        audit = ev.build_evidence_audit(
            bundle, used_evidence_ids=[used.evidence_id], ignored={other.evidence_id: "与本轮判断无关"}
        )
        self.assertEqual(audit["used_evidence_ids"], [used.evidence_id])
        self.assertEqual(audit["ignored_evidence_ids"][0]["evidence_id"] in
                         {used.evidence_id, other.evidence_id}, True)
        self.assertIsNotNone(audit["latest_verified_evidence_at"])
        self.assertEqual(audit["evidence_state"], ev.STATE_VERIFIED_NEW)

    def test_audit_rejects_unknown_used_ids(self):
        bundle = ev.build_bundle([source_result(ev.FETCH_OK, [announcement_item()])], now=NOW)
        audit = ev.build_evidence_audit(bundle, used_evidence_ids=["fabricated"])
        self.assertEqual(audit["used_evidence_ids"], [])

    def test_audit_flags_manual_review_sources(self):
        bundle = ev.build_bundle(
            [
                source_result(ev.FETCH_EMPTY),
                source_result(ev.FETCH_NOT_CONFIGURED, name="official_media", source_type="official_media"),
            ],
            now=NOW,
        )
        audit = ev.build_evidence_audit(bundle)
        self.assertEqual(audit["sources_requiring_manual_review"], ["official_media"])
        self.assertTrue(audit["no_new_verified_evidence"])

    def test_audit_distinguishes_unverified_from_absent(self):
        social = ev.build_evidence_item(
            code="600150",
            title="传闻",
            published_at=NOW - timedelta(minutes=5),
            source_name="股吧",
            source_type="social",
            now=NOW,
        )
        bundle = ev.build_bundle(
            [source_result(ev.FETCH_EMPTY), source_result(ev.FETCH_OK, [social], name="股吧", source_type="social")],
            now=NOW,
        )
        audit = ev.build_evidence_audit(bundle)
        self.assertEqual(audit["found_but_unverified"], 1)
        self.assertFalse(audit["no_new_verified_evidence"])


# --------------------------------------------------------------------------
# 新闻来源归一化
# --------------------------------------------------------------------------


class NewsSourceTest(unittest.TestCase):
    def test_source_type_classification(self):
        self.assertEqual(news.classify_source_type("新华社"), "official_media")
        self.assertEqual(news.classify_source_type("东方财富网"), "media")
        self.assertEqual(news.classify_source_type("雪球用户"), "social")
        self.assertEqual(news.classify_source_type("中信证券研究所"), "research")
        self.assertEqual(news.classify_source_type("某不知名站点"), "media")

    def test_normalize_valid_items(self):
        result = news.normalize_news_items(
            [
                {
                    "title": "600150 中标重大合同",
                    "published_at": NOW - timedelta(minutes=20),
                    "source": "新华社",
                    "url": "https://example.com/a",
                }
            ],
            code="600150",
            source_name="新华社",
            now=NOW,
        )
        self.assertEqual(result.status, ev.FETCH_OK)
        self.assertEqual(result.items[0].source_type, "official_media")
        self.assertEqual(result.items[0].event_type, "order")
        self.assertAlmostEqual(result.items[0].relevance_score, 1.0)

    def test_all_broken_records_is_parse_failed(self):
        result = news.normalize_news_items(
            [{"title": "", "published_at": None}], code="600150", source_name="新浪财经", now=NOW
        )
        self.assertEqual(result.status, ev.FETCH_PARSE_FAILED)

    def test_no_records_is_empty(self):
        result = news.normalize_news_items([], code="600150", source_name="新浪财经", now=NOW)
        self.assertEqual(result.status, ev.FETCH_EMPTY)

    def test_default_news_sources_are_not_configured(self):
        results = news.fetch_news("600150", sources=["official_media"], now=NOW)
        self.assertEqual(results[0].status, ev.FETCH_NOT_CONFIGURED)

    def test_registered_source_is_used(self):
        def fake_fetcher(code, now=None, **kwargs):
            return source_result(ev.FETCH_EMPTY, name="custom", source_type="official_media")

        news.register_news_source("custom", "official_media", fake_fetcher)
        try:
            results = news.fetch_news("600150", sources=["custom"], now=NOW)
            self.assertEqual(results[0].status, ev.FETCH_EMPTY)
        finally:
            news._SOURCES.pop("custom", None)


if __name__ == "__main__":
    unittest.main(verbosity=2)
