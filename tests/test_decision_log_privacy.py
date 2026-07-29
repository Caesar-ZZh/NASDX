# -*- coding: utf-8 -*-
"""#62 决策日志隐私/安全契约测试：opt-in 默认、递归脱敏、有界轮转。"""
import contextlib
import json
import os
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from nasdx import decision_log  # noqa: E402


@contextlib.contextmanager
def env_var(key, value):
    """只设置/还原单个环境变量（避免 patch.dict 还原整个 os.environ
    在本环境因超长变量报 ValueError）。value=None 表示删除。"""
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


class OptInDefaultTest(unittest.TestCase):
    """验收：默认环境不写盘，显式开启才写。"""

    def test_unset_env_disabled(self):
        with env_var("NASDX_DECISION_LOG", None):
            self.assertFalse(decision_log._env_enabled())

    def test_zero_and_false_disabled(self):
        for v in ("0", "false", "False", "no", "off", ""):
            with env_var("NASDX_DECISION_LOG", v):
                self.assertFalse(decision_log._env_enabled(), v)

    def test_truthy_enabled(self):
        for v in ("1", "true", "TRUE", "yes", "on"):
            with env_var("NASDX_DECISION_LOG", v):
                self.assertTrue(decision_log._env_enabled(), v)

    def test_disabled_writes_nothing(self):
        with tempfile.TemporaryDirectory() as tmp, \
             patch.object(decision_log, "get_reports_dir", return_value=tmp), \
             patch.object(decision_log, "_ENABLED", False):
            decision_log.log_decision("a", "b", inputs={"api_key": "sk-secret123456"})
            self.assertFalse(
                os.path.exists(os.path.join(tmp, "decision_log.jsonl")))


class _WriterBase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._p1 = patch.object(decision_log, "get_reports_dir",
                                return_value=self._tmp.name)
        self._p2 = patch.object(decision_log, "_ENABLED", True)
        self._p1.start()
        self._p2.start()

    def tearDown(self):
        self._p2.stop()
        self._p1.stop()
        self._tmp.cleanup()

    def _log_file(self):
        return os.path.join(self._tmp.name, "decision_log.jsonl")

    def _read_entry(self, idx=0):
        with open(self._log_file(), encoding="utf-8") as f:
            lines = f.readlines()
        return json.loads(lines[idx])

    def _raw_text(self):
        with open(self._log_file(), encoding="utf-8") as f:
            return f.read()


class RedactionTest(_WriterBase):
    """验收：嵌套 args/kwargs/output/meta 中的凭据不落盘。"""

    SECRET = "sk-verysecretvalue001"

    def test_nested_secret_keys_redacted(self):
        decision_log.log_decision(
            "a", "b",
            inputs={
                "api_key": self.SECRET,
                "cfg": {"Access_Token": self.SECRET,
                        "PASSWORD": self.SECRET,
                        "headers": [{"Authorization": "Bearer abcdef123456"},
                                    {"Cookie": "sid=xyz"}]},
                "stock_code": "600519",
            },
            output={"secret": self.SECRET, "signal": "buy"},
            meta={"private_key": self.SECRET, "run": 1},
        )
        e = self._read_entry()
        self.assertEqual(e["inputs"]["api_key"], "[REDACTED]")
        self.assertEqual(e["inputs"]["cfg"]["Access_Token"], "[REDACTED]")
        self.assertEqual(e["inputs"]["cfg"]["PASSWORD"], "[REDACTED]")
        self.assertEqual(e["inputs"]["cfg"]["headers"][0]["Authorization"],
                         "[REDACTED]")
        self.assertEqual(e["inputs"]["cfg"]["headers"][1]["Cookie"], "[REDACTED]")
        self.assertEqual(e["output"]["secret"], "[REDACTED]")
        self.assertEqual(e["meta"]["private_key"], "[REDACTED]")
        raw = self._raw_text()
        self.assertNotIn(self.SECRET, raw)
        self.assertNotIn("Bearer abcdef123456", raw)

    def test_secret_patterns_in_string_values_redacted(self):
        decision_log.log_decision(
            "a", "b",
            inputs={"note": "call with api_key=sk-abc12345678 done"},
            output="Authorization: Bearer tok_1234567890abc",
        )
        raw = self._raw_text()
        self.assertNotIn("sk-abc12345678", raw)
        self.assertNotIn("Bearer tok_1234567890abc", raw)
        self.assertIn("[REDACTED]", raw)

    def test_non_sensitive_fields_retained(self):
        decision_log.log_decision(
            "analysis", "finalize",
            inputs={"stock_code": "600519", "mode": "fast"},
            output={"signal": "buy", "bullish_pct": 62.5},
            confidence=0.7,
            meta={"source": "run_analysis"},
        )
        e = self._read_entry()
        self.assertEqual(e["agent"], "analysis")
        self.assertEqual(e["inputs"]["stock_code"], "600519")
        self.assertEqual(e["output"]["signal"], "buy")
        self.assertEqual(e["output"]["bullish_pct"], 62.5)
        self.assertEqual(e["confidence"], 0.7)
        self.assertEqual(e["meta"]["source"], "run_analysis")

    def test_unknown_object_not_reprd(self):
        class Holder:
            def __init__(self):
                self.api_key = "sk-objectsecret9999"

            def __repr__(self):
                return f"Holder(api_key={self.api_key})"

        decision_log.log_decision("a", "b", inputs={"obj": Holder()})
        raw = self._raw_text()
        self.assertNotIn("sk-objectsecret9999", raw)
        e = self._read_entry()
        self.assertIn("Holder", e["inputs"]["obj"])  # 类型摘要

    def test_extra_redact_keys_env(self):
        with env_var("NASDX_DECISION_LOG_REDACT_KEYS", "broker_account"):
            decision_log.log_decision(
                "a", "b", inputs={"broker_account": "A123456", "code": "600519"})
        e = self._read_entry()
        self.assertEqual(e["inputs"]["broker_account"], "[REDACTED]")
        self.assertEqual(e["inputs"]["code"], "600519")

    def test_depth_limit_and_tuple_set(self):
        deep = {"l": [( {"token": "sk-deepsecret1234"}, )]}
        decision_log.log_decision("a", "b", inputs=deep)
        self.assertNotIn("sk-deepsecret1234", self._raw_text())


class DecoratorRedactionTest(_WriterBase):
    def test_decorator_redacts_kwargs_and_result(self):
        @decision_log.decision_logger("t")
        def call(code, api_key=None):
            return {"signal": "buy", "token": "sk-resultsecret001"}

        call("600519", api_key="sk-kwargsecret001")
        raw = self._raw_text()
        self.assertNotIn("sk-kwargsecret001", raw)
        self.assertNotIn("sk-resultsecret001", raw)
        e = self._read_entry()
        self.assertEqual(e["inputs"]["kwargs"]["api_key"], "[REDACTED]")
        self.assertEqual(e["output"]["signal"], "buy")

    def test_decorator_error_message_sanitized(self):
        @decision_log.decision_logger("t")
        def boom():
            raise ValueError("bad api_key=sk-errsecret001")

        with self.assertRaises(ValueError):
            boom()
        raw = self._raw_text()
        self.assertNotIn("sk-errsecret001", raw)
        self.assertIn("ERROR", self._read_entry()["output"])


class RotationTest(_WriterBase):
    """验收：日志保留有界（超限轮转为 .1，仅一份备份）。"""

    def test_rotation_bounded(self):
        with env_var("NASDX_DECISION_LOG_MAX_BYTES", "200"):
            for i in range(20):
                decision_log.log_decision("a", "b", inputs={"i": i, "pad": "x" * 50})
            path = self._log_file()
            backup = path + ".1"
            self.assertTrue(os.path.exists(backup))
            # 总占用有上界：主文件 + 单一备份
            self.assertLessEqual(os.path.getsize(path), 200 + 400)
            self.assertFalse(os.path.exists(path + ".2"))

    def test_invalid_max_bytes_falls_back(self):
        with env_var("NASDX_DECISION_LOG_MAX_BYTES", "abc"):
            self.assertEqual(decision_log._max_bytes(), 5 * 1024 * 1024)


if __name__ == "__main__":
    unittest.main()
