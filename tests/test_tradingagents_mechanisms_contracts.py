"""TradingAgents 借鉴机制契约测试（#55-#59）。

覆盖：
- #55 Provider 工厂：resolve/apply/quick_think、切换实际生效（含惰性单例刷新与
  FALLBACK_MODELS 随 provider 重算）、base_url 合法性校验
- #56 事实校验层：约束注入幂等、数值抽取、diff 容差与零值边界
- #57 多空对抗提炼：transcript 解析、str/list 输入、空输入降级
- #58 决策记忆层：JSONL 与 SQLite 双通道、缺失文件降级、prompt 格式化
- #59 统一决策日志：写入结构、开关关闭、装饰器异常透传
"""
import json
import os
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

import nasdx.llm as llm_mod
from nasdx.llm import (
    LLMClient,
    PROVIDERS,
    apply_provider,
    llm,
    resolve_provider,
    set_quick_think,
)
from nasdx import debate_review, decision_log, fact_check, memory

_PROVIDER_ENV_KEYS = ("NASDX_PROVIDER", "NASDX_BASE_URL", "NASDX_MODEL", "NASDX_API_KEY",
                      "NASDX_FALLBACK_MODELS")


class ProviderFactoryContractsTest(unittest.TestCase):
    """#55 Provider 工厂"""

    def setUp(self):
        self._saved_env = {k: os.environ.get(k) for k in _PROVIDER_ENV_KEYS}
        self._saved_instance = LLMClient._instance
        self._saved_lazy = llm._client
        LLMClient._instance = None
        llm._client = None
        for k in _PROVIDER_ENV_KEYS:
            os.environ.pop(k, None)

    def tearDown(self):
        for k, v in self._saved_env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        LLMClient._instance = self._saved_instance
        llm._client = self._saved_lazy

    def test_resolve_provider_known_and_unknown(self):
        self.assertIs(resolve_provider("deepseek"), PROVIDERS["deepseek"])
        self.assertIsNone(resolve_provider(""))
        with self.assertRaises(ValueError):
            resolve_provider("not-a-provider")

    def test_resolve_provider_from_env(self):
        os.environ["NASDX_PROVIDER"] = "Qwen"
        self.assertIs(resolve_provider(), PROVIDERS["qwen"])

    def test_apply_provider_sets_env_and_resets_singleton(self):
        LLMClient._instance = object()  # 假装已有实例
        apply_provider("qwen")
        self.assertEqual(os.environ["NASDX_PROVIDER"], "qwen")
        self.assertEqual(os.environ["NASDX_BASE_URL"], PROVIDERS["qwen"]["base_url"])
        self.assertEqual(os.environ["NASDX_MODEL"], "qwen-plus")
        self.assertIsNone(LLMClient._instance)

    def test_apply_provider_noop_without_name(self):
        sentinel = object()
        LLMClient._instance = sentinel
        apply_provider(None)  # 无 provider 名 + 无 env：保持原状
        self.assertIs(LLMClient._instance, sentinel)

    def test_new_client_picks_up_provider_after_apply(self):
        apply_provider("qwen")
        client = LLMClient()
        self.assertEqual(client.base_url, PROVIDERS["qwen"]["base_url"])
        self.assertEqual(client.model, "qwen-plus")

    def test_lazy_wrapper_refreshes_after_apply_provider(self):
        """核心回归：模块级 llm 缓存旧实例导致 provider 切换静默失效。"""
        apply_provider("deepseek")
        first = llm._get_client()
        self.assertEqual(first.model, "deepseek-chat")
        apply_provider("qwen")
        second = llm._get_client()
        self.assertIsNot(second, first)
        self.assertEqual(second.model, "qwen-plus")
        self.assertEqual(second.base_url, PROVIDERS["qwen"]["base_url"])

    def test_fallback_models_recomputed_per_provider(self):
        """切到 qwen 后不得再回退 deepseek 模型（invalid_request 风险）。"""
        apply_provider("qwen")
        client = LLMClient()
        self.assertNotIn("deepseek-reasoner", client.FALLBACK_MODELS)
        self.assertNotIn("deepseek-chat", client.FALLBACK_MODELS)
        LLMClient._instance = None
        apply_provider("deepseek")
        client2 = LLMClient()
        self.assertIn("deepseek-reasoner", client2.FALLBACK_MODELS)

    def test_set_quick_think_after_explicit_apply(self):
        apply_provider("qwen")
        set_quick_think(True)
        self.assertEqual(os.environ["NASDX_MODEL"], "qwen-turbo")
        set_quick_think(False)
        self.assertEqual(os.environ["NASDX_MODEL"], "qwen-plus")

    def test_set_quick_think_noop_without_provider(self):
        os.environ["NASDX_MODEL"] = "keep-me"
        set_quick_think(True)
        self.assertEqual(os.environ["NASDX_MODEL"], "keep-me")

    def test_validate_provider_base_url_rejects_bad_scheme(self):
        with self.assertRaises(ValueError):
            llm_mod._validate_provider_base_url("file:///etc/passwd")
        with self.assertRaises(ValueError):
            llm_mod._validate_provider_base_url("not-a-url")
        llm_mod._validate_provider_base_url("https://api.example.com/v1")  # 不抛

    def test_all_declared_providers_have_valid_urls_and_models(self):
        for name, prov in PROVIDERS.items():
            llm_mod._validate_provider_base_url(prov["base_url"])
            self.assertTrue(prov["default_model"], name)
            self.assertTrue(prov["quick_model"], name)
            self.assertIn("needs_key", prov, name)

    def test_no_hardcoded_api_key_in_providers(self):
        blob = json.dumps(PROVIDERS).lower()
        for marker in ("sk-", "api_key", "apikey", "token"):
            self.assertNotIn(marker, blob)


class FactCheckContractsTest(unittest.TestCase):
    """#56 quant 事实校验层"""

    def test_enforce_constraints_idempotent(self):
        base = "你是分析师。"
        once = fact_check.enforce_quant_constraints(base)
        twice = fact_check.enforce_quant_constraints(once)
        self.assertEqual(once, twice)
        self.assertIn("不得自行计算", once)

    def test_extract_numeric_claims(self):
        text = "该股 PE 为 12.5，涨跌幅 3.2%，收盘 10.88 元"
        claims = fact_check.extract_numeric_claims(text)
        self.assertEqual(claims.get("pe"), 12.5)
        self.assertEqual(claims.get("涨跌幅"), 3.2)
        self.assertEqual(claims.get("收盘"), 10.88)

    def test_extract_accepts_list_input(self):
        claims = fact_check.extract_numeric_claims(["PE 8.0", "其他"])
        self.assertEqual(claims.get("pe"), 8.0)

    def test_diff_claims_tolerance(self):
        truth = {"pe": 10.0}
        self.assertEqual(fact_check.diff_claims({"pe": 10.4}, truth, 0.05), [])
        warns = fact_check.diff_claims({"pe": 12.0}, truth, 0.05)
        self.assertEqual(len(warns), 1)

    def test_diff_claims_zero_truth(self):
        warns = fact_check.diff_claims({"涨跌幅": 1.0}, {"涨跌幅": 0.0})
        self.assertEqual(len(warns), 1)
        self.assertEqual(fact_check.diff_claims({"涨跌幅": 0.0}, {"涨跌幅": 0.0}), [])

    def test_check_consistency_no_claims_or_no_truth(self):
        self.assertEqual(fact_check.check_consistency("没有数字的纯文本", {"pe": 10}), [])
        self.assertEqual(fact_check.check_consistency("PE 99", {}), [])


class DebateReviewContractsTest(unittest.TestCase):
    """#57 Bull/Bear 多空对抗提炼"""

    def test_empty_transcript_degrades(self):
        out = debate_review.summarize_counter_argument("")
        self.assertFalse(out["available"])
        self.assertEqual(debate_review.format_counter_argument_block(out), "")

    def test_bull_bear_classification(self):
        transcript = "技术面看多，均线支撑有效\n基本面利空，业绩下跌压力大\n中性观察"
        out = debate_review.summarize_counter_argument(transcript, {"bullish_pct": 40})
        self.assertTrue(out["available"])
        self.assertTrue(any("利空" in p for p in out["bear_points"]))
        self.assertTrue(any("看多" in p for p in out["bull_points"]))
        self.assertEqual(out["vote_bullish_pct"], 40)
        block = debate_review.format_counter_argument_block(out)
        self.assertIn("反方观点", block)

    def test_list_transcript_supported(self):
        out = debate_review.summarize_counter_argument(["主力卖出，压力位明显", "看多信号"])
        self.assertTrue(out["available"])
        self.assertTrue(out["bear_points"])


class MemoryContractsTest(unittest.TestCase):
    """#58 决策记忆层"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()

    def tearDown(self):
        self._tmp.cleanup()

    def test_jsonl_record_and_recall(self):
        with patch.object(memory, "get_reports_dir", return_value=self._tmp.name), \
             patch.object(memory, "MEMORY_DB", ""):
            memory.record_decision("600519", "2026-07-27", "buy", 0.8, "测试摘要")
            memory.record_decision("000001", "2026-07-27", "hold", 0.5, "另一条")
            recs = memory.recall_for_reflection("600519")
            self.assertEqual(len(recs), 1)
            self.assertEqual(recs[0]["signal"], "buy")
            self.assertEqual(len(memory.recall_for_reflection()), 2)

    def test_recall_missing_file_returns_empty(self):
        with patch.object(memory, "get_reports_dir", return_value=self._tmp.name), \
             patch.object(memory, "MEMORY_DB", ""):
            self.assertEqual(memory.recall_for_reflection("999999"), [])

    def test_sqlite_record_and_recall(self):
        db = os.path.join(self._tmp.name, "mem.db")
        memory.record_decision("600519", "2026-07-27", "sell", 0.9, "sqlite 路径",
                               db_path=db, benchmark=1.5, outcome=-0.2)
        recs = memory.recall_for_reflection("600519", db_path=db)
        self.assertEqual(len(recs), 1)
        self.assertEqual(recs[0]["signal"], "sell")
        self.assertAlmostEqual(recs[0]["benchmark"], 1.5)
        conn = sqlite3.connect(db)
        try:
            n = conn.execute("SELECT COUNT(*) FROM decisions").fetchone()[0]
        finally:
            conn.close()  # Windows 下必须显式关闭，否则临时目录清理报文件占用
        self.assertEqual(n, 1)

    def test_format_memory_prompt(self):
        self.assertEqual(memory.format_memory_prompt([]), "")
        text = memory.format_memory_prompt([
            {"date": "2026-07-27", "stock_code": "600519", "signal": "buy",
             "confidence": 0.8, "summary": "s", "benchmark": 1.0, "outcome": 2.0},
        ])
        self.assertIn("历史决策记忆", text)
        self.assertIn("600519", text)


class DecisionLogContractsTest(unittest.TestCase):
    """#59 统一决策日志"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()

    def tearDown(self):
        self._tmp.cleanup()

    def _log_file(self):
        return os.path.join(self._tmp.name, "decision_log.jsonl")

    def test_log_decision_writes_structured_entry(self):
        with patch.object(decision_log, "get_reports_dir", return_value=self._tmp.name), \
             patch.object(decision_log, "_ENABLED", True):
            decision_log.log_decision("analysis", "finalize",
                                      inputs={"code": "600519"},
                                      output={"signal": "buy"}, confidence=0.7)
        with open(self._log_file(), encoding="utf-8") as f:
            entry = json.loads(f.readline())
        self.assertEqual(entry["agent"], "analysis")
        self.assertEqual(entry["action"], "finalize")
        self.assertEqual(entry["confidence"], 0.7)
        self.assertIn("ts", entry)

    def test_disabled_flag_skips_write(self):
        with patch.object(decision_log, "get_reports_dir", return_value=self._tmp.name), \
             patch.object(decision_log, "_ENABLED", False):
            decision_log.log_decision("analysis", "finalize")
        self.assertFalse(os.path.exists(self._log_file()))

    def test_decorator_logs_and_reraises(self):
        with patch.object(decision_log, "get_reports_dir", return_value=self._tmp.name), \
             patch.object(decision_log, "_ENABLED", True):
            @decision_log.decision_logger("t")
            def boom():
                raise ValueError("x")

            with self.assertRaises(ValueError):
                boom()
        with open(self._log_file(), encoding="utf-8") as f:
            entry = json.loads(f.readline())
        self.assertIn("ERROR", str(entry["output"]))


if __name__ == "__main__":
    unittest.main()
