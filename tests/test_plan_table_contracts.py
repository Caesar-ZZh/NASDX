import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class PlanTableContractsTest(unittest.TestCase):
    def test_candidate_table_escapes_values_and_keeps_required_markers(self):
        from nasdx.ui.plan_tables import candidate_table

        rendered = candidate_table(
            [
                {
                    "code": '<script>alert("code")</script>',
                    "name": '<img src=x onerror="alert(1)">',
                    "asset_type": "stock",
                    "adjusted_score": 88,
                    "signal": "bullish",
                    "action": "review",
                    "reason": "A&B",
                }
            ]
        )

        self.assertIn('class="n-card plan-table"', rendered)
        self.assertIn("<thead><tr>", rendered)
        self.assertIn("<tbody><tr>", rendered)
        self.assertIn("代码", rendered)
        self.assertIn("理由", rendered)
        self.assertIn("&lt;script&gt;", rendered)
        self.assertIn("&lt;img", rendered)
        self.assertIn("A&amp;B", rendered)
        self.assertNotIn("<script>", rendered)
        self.assertNotIn("<img", rendered)

    def test_external_review_table_allows_only_safe_links(self):
        from nasdx.ui.plan_tables import external_review_table

        rendered = external_review_table(
            [
                {
                    "candidate": '<b>600000</b>',
                    "review_gate": "公告复核",
                    "must_pass_before": "买入前",
                    "required_checks": ['<script>alert("check")</script>'],
                    "source_links": [
                        {"label": "公告", "url": "https://example.com/a", "usage": "核对公告"},
                        {"label": "危险", "url": "javascript:alert(1)", "usage": "不应链接"},
                    ],
                    "failure_action": "放弃",
                }
            ]
        )

        self.assertIn('href="https://example.com/a"', rendered)
        self.assertNotIn("javascript:", rendered)
        self.assertIn("危险", rendered)
        self.assertIn("&lt;b&gt;600000&lt;/b&gt;", rendered)
        self.assertIn("&lt;script&gt;", rendered)
        self.assertNotIn("<script>", rendered)

    def test_all_builders_preserve_empty_states(self):
        from nasdx.ui.plan_tables import (
            account_review_table,
            audit_table,
            brief_playbook_table,
            candidate_table,
            execution_queue_table,
            external_review_table,
            position_sizing_table,
            recommendation_review_table,
            scenario_table,
            tracker_change_table,
        )

        expected = {
            candidate_table: "暂无候选",
            scenario_table: "暂无情景",
            brief_playbook_table: "暂无候选剧本",
            audit_table: "暂无候选证据核查",
            execution_queue_table: "暂无执行队列",
            external_review_table: "暂无外部复核包",
            position_sizing_table: "暂无仓位换算候选",
            account_review_table: "暂无真实持仓",
            tracker_change_table: "暂无状态变化",
            recommendation_review_table: "暂无建议结果复盘",
        }

        for builder, message in expected.items():
            with self.subTest(builder=builder.__name__):
                rendered = builder([])
                self.assertIn('class="n-card"', rendered)
                self.assertIn(message, rendered)

    def test_rich_cells_escape_text_before_adding_trusted_markup(self):
        from nasdx.ui.plan_tables import (
            account_review_table,
            audit_table,
            brief_playbook_table,
            execution_queue_table,
            position_sizing_table,
            recommendation_review_table,
            tracker_change_table,
        )

        hostile = '<svg onload="alert(1)">'
        cases = [
            (brief_playbook_table, {"candidate": hostile, "deep_signal": "bullish"}),
            (audit_table, {"candidate": hostile, "audit_status": "观察等待", "deep_signal": "neutral"}),
            (execution_queue_table, {"stage": "盘前", "target": hostile, "command": hostile}),
            (position_sizing_table, {"candidate": hostile, "audit_status": "观察等待"}),
            (account_review_table, {"code": hostile, "route_status": "watch"}),
            (tracker_change_table, {"candidate": hostile, "changes": [{"field": hostile, "from": "A", "to": "B"}]}),
            (recommendation_review_table, {"candidate": hostile, "review_status": "signal_continues"}),
        ]

        for builder, item in cases:
            with self.subTest(builder=builder.__name__):
                rendered = builder([item])
                self.assertIn("&lt;svg", rendered)
                self.assertNotIn("<svg", rendered)
                self.assertIn('class="n-card plan-table"', rendered)

    def test_app_imports_plan_tables_instead_of_defining_them_inline(self):
        source = (ROOT / "app.py").read_text(encoding="utf-8")

        self.assertIn("from nasdx.ui.plan_tables import (", source)
        self.assertIn("candidate_table as _table", source)
        self.assertIn("recommendation_review_table as _recommendation_review_table", source)
        for name in [
            "_table",
            "_scenario_table",
            "_brief_playbook_table",
            "_audit_table",
            "_execution_queue_table",
            "_external_review_table",
            "_position_sizing_table",
            "_account_review_table",
            "_tracker_change_table",
            "_recommendation_review_table",
        ]:
            self.assertNotIn(f"        def {name}(", source)

    def test_final_audit_covers_the_plan_table_module_boundary(self):
        from scripts.run_final_audit import check_streamlit_markers

        detail = check_streamlit_markers()

        self.assertIn("10 个独立表格 helper", detail)


if __name__ == "__main__":
    unittest.main()
