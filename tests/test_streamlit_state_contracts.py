import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class StreamlitStateContractsTest(unittest.TestCase):
    def test_app_does_not_write_llm_config_to_process_environment(self):
        source = (ROOT / "app.py").read_text(encoding="utf-8")

        self.assertNotIn('os.environ["NASDX_API_KEY"]', source)
        self.assertNotIn('os.environ["NASDX_BASE_URL"]', source)
        self.assertNotIn('os.environ["NASDX_MODEL"]', source)
        self.assertNotIn("LLMClient._instance = None", source)

    def test_app_uses_public_llm_defaults_without_private_proxy_preset(self):
        app_source = (ROOT / "app.py").read_text(encoding="utf-8")
        llm_source = (ROOT / "nasdx" / "llm.py").read_text(encoding="utf-8")
        combined = app_source + "\n" + llm_source

        self.assertIn('"deepseek-chat"', combined)
        self.assertNotIn("deepseek-v4-pro", combined)
        self.assertNotIn("newapi.ecdigit.cn", app_source)
        self.assertNotIn("Claude 中转", app_source)

    def test_background_threads_are_not_stored_in_session_state(self):
        source = (ROOT / "app.py").read_text(encoding="utf-8")

        forbidden = [
            '"thread":None',
            "st.session_state.thread",
            '"etf50_scan_thread"',
            "st.session_state[\"etf50_scan_thread\"]",
        ]
        for marker in forbidden:
            self.assertNotIn(marker, source)

        self.assertIn("from nasdx.ui_tasks import", source)
        self.assertIn("task_id", source)

    def test_task_registry_survives_streamlit_script_reruns(self):
        source = (ROOT / "app.py").read_text(encoding="utf-8")

        self.assertNotIn("RUNNING_TASKS = {}", source)
        self.assertIn("register_task as _register_task", source)
        self.assertIn("task_alive as _task_alive", source)
        self.assertIn("set_task_result as _set_task_result", source)
        self.assertIn("take_task_result as _take_task_result", source)

    def test_final_audit_checks_persistent_task_registry_module(self):
        source = (ROOT / "scripts" / "run_final_audit.py").read_text(encoding="utf-8")

        self.assertNotIn('"RUNNING_TASKS",', source)
        self.assertIn('ROOT / "nasdx" / "ui_tasks.py"', source)
        self.assertIn('"_TASKS"', source)

    def test_analysis_logs_are_unique_per_task(self):
        source = (ROOT / "app.py").read_text(encoding="utf-8")

        self.assertNotIn('ROOT / f"nasdx_log_{code}.txt"', source)
        self.assertIn("nasdx_log_{code}_{task_id}.txt", source)

    def test_stocks60_scan_uses_background_task_state(self):
        source = (ROOT / "app.py").read_text(encoding="utf-8")

        self.assertIn('"stocks60_scan_task_id"', source)
        self.assertIn('_new_task_id("stocks60_scan")', source)
        self.assertIn("_register_task(task_id, thread)", source)
        self.assertNotIn('with st.spinner("扫描中，约 5 分钟...")', source)

    def test_selector_scan_exposes_limit_timeout_without_session_thread(self):
        source = (ROOT / "scripts" / "selector_page.py").read_text(encoding="utf-8")

        self.assertIn('"--limit"', source)
        self.assertIn('"selector_timeout"', source)
        self.assertIn('"selector_scan_task_id"', source)
        self.assertNotIn('"selector_scan_thread"', source)

    def test_scan_actions_surface_subprocess_results_without_start_rerun(self):
        app_source = (ROOT / "app.py").read_text(encoding="utf-8")
        selector_source = (ROOT / "scripts" / "selector_page.py").read_text(encoding="utf-8")

        self.assertIn('"etf50_scan_result"', app_source)
        self.assertIn('"stocks60_scan_result"', app_source)
        self.assertIn('"selector_scan_result"', selector_source)
        self.assertIn("completed.returncode", app_source)
        self.assertIn("completed.returncode", selector_source)
        self.assertNotIn(
            'st.session_state["etf50_scan_start"] = time.time()\n            st.rerun()',
            app_source,
        )
        self.assertNotIn(
            'st.session_state["stocks60_scan_start"] = time.time()\n            st.rerun()',
            app_source,
        )
        self.assertNotIn(
            'st.session_state["selector_scan_start"] = time.time()\n            st.rerun()',
            selector_source,
        )

    def test_report_history_page_is_first_class_route(self):
        source = (ROOT / "app.py").read_text(encoding="utf-8")

        self.assertIn('"history"', source)
        self.assertIn("报告历史", source)
        self.assertIn("list_report_history", source)

    def test_navigation_uses_callbacks_without_explicit_rerun(self):
        app_source = (ROOT / "app.py").read_text(encoding="utf-8")
        selector_source = (ROOT / "scripts" / "selector_page.py").read_text(encoding="utf-8")
        route_start = app_source.index("def _nav_to")
        route_end = app_source.index("# ═", route_start)
        route_source = app_source[route_start:route_end]

        self.assertNotIn("st.rerun()", route_source)
        self.assertIn("on_click=_nav_to", app_source)
        self.assertIn('"navigate": _nav_to', app_source)
        self.assertIn("on_click=navigate", selector_source)
        self.assertNotIn('st.query_params["page"] = "deep"', selector_source)

    def test_deep_analysis_poll_does_not_block_streamlit_thread(self):
        source = (ROOT / "app.py").read_text(encoding="utf-8")

        self.assertNotIn("time.sleep(3); st.rerun()", source)
        self.assertNotIn("_schedule_refresh", source)
        self.assertIn("@st.fragment(run_every=3)", source)

    def test_background_task_polling_preserves_streamlit_session(self):
        app_source = (ROOT / "app.py").read_text(encoding="utf-8")
        selector_source = (ROOT / "scripts" / "selector_page.py").read_text(encoding="utf-8")
        combined = app_source + "\n" + selector_source

        self.assertNotIn("window.parent.location.reload()", combined)
        self.assertGreaterEqual(app_source.count("@st.fragment(run_every=3)"), 3)
        self.assertIn("@st.fragment(run_every=3)", selector_source)
        self.assertIn("on_click=_start_selector_scan", selector_source)

    def test_agnes_preset_uses_environment_config_without_embedded_key(self):
        source = (ROOT / "app.py").read_text(encoding="utf-8")

        self.assertIn(
            '"Agnes AI": ("https://apihub.agnes-ai.com/v1", ["agnes-2.0-flash"])',
            source,
        )
        self.assertIn("def _preset_for_config", source)
        self.assertIn('"api_preset":_preset_for_config(', source)
        self.assertIn("timeout=30", source)
        self.assertIn("max_retries=0", source)
        self.assertNotRegex(source, r"sk-[A-Za-z0-9_-]{20,}")

    def test_home_recent_reports_use_cached_loader(self):
        source = (ROOT / "app.py").read_text(encoding="utf-8")
        home_start = source.index('if pg == "home":')
        home_end = source.index('elif pg == "plan":', home_start)
        home_source = source[home_start:home_end]

        self.assertIn("all_r = load_recent_reports()", home_source)
        self.assertNotIn('glob("report_*.json")', home_source)

    def test_quick_stock_picker_does_not_render_one_button_per_symbol(self):
        source = (ROOT / "app.py").read_text(encoding="utf-8")
        picker_start = source.index("# 股票快速选择")
        picker_end = source.index("# API 配置", picker_start)
        picker_source = source[picker_start:picker_end]

        self.assertIn('key="quick_sector"', picker_source)
        self.assertIn('key="quick_stock"', picker_source)
        self.assertIn('key="quick_open"', picker_source)
        self.assertNotIn('key=f"q_{sector}_{code}"', picker_source)

    def test_quant_page_defers_dataframe_imports_until_chart_rendering(self):
        source = (ROOT / "scripts" / "quant_page.py").read_text(encoding="utf-8")
        render_start = source.index("def render_quant_page")
        startup_end = source.index("# ── 启动时", render_start)
        startup_source = source[render_start:startup_end]

        self.assertNotIn("import pandas as pd", startup_source)
        self.assertNotIn("import numpy as np", source)
        self.assertGreaterEqual(source.count("import pandas as pd"), 2)


if __name__ == "__main__":
    unittest.main()
