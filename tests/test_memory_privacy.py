# -*- coding: utf-8 -*-
"""#63 决策记忆隐私/生命周期契约测试。

验收覆盖：
- 默认关闭：不设 NASDX_MEMORY_ENABLED 时不产生 JSONL / SQLite 持久化
  （包括显式 db_path 调用与仅设 NASDX_MEMORY_DB 的情况）；
- 显式开启 JSONL / 显式开启 SQLite 均正常写入与召回；
- 开启后再关闭：不再写入新记录，且 recall 不回灌历史摘要；
- 摘要有界 + 脱敏（凭据形态抹除 / NASDX_MEMORY_SUMMARY_MAX 截断 / 0=不留正文）；
- 保留上限 NASDX_MEMORY_MAX_RECORDS（JSONL 与 SQLite）；
- clear_memory() 一键清除（含关闭状态下清除历史遗留）；
- memory_status() 诊断字段。
"""
import contextlib
import json
import os
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from nasdx import memory  # noqa: E402


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


class _Base(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._patches = [
            patch.object(memory, "get_reports_dir", return_value=self._tmp.name),
            patch.object(memory, "MEMORY_DB", ""),
        ]
        for p in self._patches:
            p.start()

    def tearDown(self):
        for p in self._patches:
            p.stop()
        self._tmp.cleanup()

    def _jsonl(self):
        return os.path.join(self._tmp.name, "decision_memory.jsonl")

    def _lines(self):
        if not os.path.exists(self._jsonl()):
            return []
        with open(self._jsonl(), "r", encoding="utf-8") as f:
            return [json.loads(ln) for ln in f if ln.strip()]

    def _sqlite_count(self, db):
        if not os.path.exists(db):
            return 0
        conn = sqlite3.connect(db)
        try:
            return conn.execute("SELECT COUNT(*) FROM decisions").fetchone()[0]
        except sqlite3.OperationalError:
            return 0
        finally:
            conn.close()


class DefaultDisabledTest(_Base):
    """验收：默认安装/运行不产生任何决策记忆持久化。"""

    def test_unset_env_writes_nothing_jsonl(self):
        with env_var("NASDX_MEMORY_ENABLED", None):
            rec = memory.record_decision("600519", "2026-07-30", "buy", 0.8, "摘要")
            self.assertFalse(os.path.exists(self._jsonl()))
            self.assertEqual(rec.stock_code, "600519")  # 内存对象仍可用

    def test_falsy_values_disabled(self):
        for v in ("0", "false", "no", "off", ""):
            with env_var("NASDX_MEMORY_ENABLED", v):
                memory.record_decision("600519", "2026-07-30", "buy", 0.8, "摘要")
                self.assertFalse(os.path.exists(self._jsonl()), v)

    def test_disabled_blocks_explicit_db_path(self):
        db = os.path.join(self._tmp.name, "mem.db")
        with env_var("NASDX_MEMORY_ENABLED", None):
            memory.record_decision("600519", "2026-07-30", "buy", 0.8, "摘要", db_path=db)
            self.assertEqual(self._sqlite_count(db), 0)

    def test_memory_db_alone_does_not_enable(self):
        """仅设 NASDX_MEMORY_DB（无 NASDX_MEMORY_ENABLED）不得开启持久化。"""
        db = os.path.join(self._tmp.name, "envdb.db")
        with env_var("NASDX_MEMORY_ENABLED", None), \
             env_var("NASDX_MEMORY_DB", db), \
             patch.object(memory, "MEMORY_DB", db):
            memory.record_decision("600519", "2026-07-30", "buy", 0.8, "摘要")
            self.assertEqual(self._sqlite_count(db), 0)
            self.assertFalse(os.path.exists(self._jsonl()))

    def test_disabled_recall_returns_empty_even_if_file_exists(self):
        """关闭时 recall 不回灌历史摘要（数据边界收口）。"""
        with open(self._jsonl(), "w", encoding="utf-8") as f:
            f.write(json.dumps({"stock_code": "600519", "summary": "旧摘要"}) + "\n")
        with env_var("NASDX_MEMORY_ENABLED", None):
            self.assertEqual(memory.recall_for_reflection(), [])
            self.assertEqual(memory.recall_for_reflection("600519"), [])


class ExplicitEnableTest(_Base):
    """验收：显式开启后 JSONL / SQLite 正常工作；再关闭即停写。"""

    def test_enabled_jsonl_roundtrip(self):
        with env_var("NASDX_MEMORY_ENABLED", "1"):
            memory.record_decision("600519", "2026-07-30", "buy", 0.8, "摘要A")
            memory.record_decision("000001", "2026-07-30", "hold", 0.5, "摘要B")
            self.assertEqual(len(self._lines()), 2)
            recs = memory.recall_for_reflection("600519")
            self.assertEqual(len(recs), 1)
            self.assertEqual(recs[0]["signal"], "buy")

    def test_enabled_sqlite_roundtrip(self):
        db = os.path.join(self._tmp.name, "mem.db")
        with env_var("NASDX_MEMORY_ENABLED", "true"):
            memory.record_decision("600519", "2026-07-30", "sell", 0.9, "s",
                                   db_path=db, benchmark=1.5, outcome=-0.2)
            self.assertEqual(self._sqlite_count(db), 1)
            recs = memory.recall_for_reflection("600519", db_path=db)
            self.assertEqual(len(recs), 1)
            self.assertAlmostEqual(recs[0]["benchmark"], 1.5)

    def test_disabled_after_enabled_stops_writing(self):
        with env_var("NASDX_MEMORY_ENABLED", "1"):
            memory.record_decision("600519", "2026-07-30", "buy", 0.8, "第一条")
        self.assertEqual(len(self._lines()), 1)
        with env_var("NASDX_MEMORY_ENABLED", "0"):
            memory.record_decision("600519", "2026-07-30", "sell", 0.6, "第二条")
            self.assertEqual(len(self._lines()), 1)  # 无新增
            self.assertEqual(memory.recall_for_reflection(), [])


class SummaryBoundedRedactedTest(_Base):
    """验收：自由文本摘要默认有界且脱敏。"""

    def test_summary_truncated_to_default_200(self):
        with env_var("NASDX_MEMORY_ENABLED", "1"):
            memory.record_decision("600519", "2026-07-30", "buy", 0.8, "x" * 1000)
            stored = self._lines()[0]["summary"]
            self.assertLessEqual(len(stored), 200 + len("...(truncated)"))
            self.assertIn("...(truncated)", stored)

    def test_summary_credentials_redacted(self):
        with env_var("NASDX_MEMORY_ENABLED", "1"):
            memory.record_decision(
                "600519", "2026-07-30", "buy", 0.8,
                "结论看多 api_key=sk-abcdef1234567890 以及 Bearer AbCdEf123456789",
            )
            stored = self._lines()[0]["summary"]
            self.assertNotIn("sk-abcdef1234567890", stored)
            self.assertNotIn("AbCdEf123456789", stored)
            self.assertIn("[REDACTED]", stored)

    def test_summary_max_env_override(self):
        with env_var("NASDX_MEMORY_ENABLED", "1"), \
             env_var("NASDX_MEMORY_SUMMARY_MAX", "10"):
            memory.record_decision("600519", "2026-07-30", "buy", 0.8, "一二三四五六七八九十超出")
            stored = self._lines()[0]["summary"]
            self.assertTrue(stored.startswith("一二三四五六七八九十"))
            self.assertIn("...(truncated)", stored)

    def test_summary_max_zero_stores_no_body(self):
        with env_var("NASDX_MEMORY_ENABLED", "1"), \
             env_var("NASDX_MEMORY_SUMMARY_MAX", "0"):
            memory.record_decision("600519", "2026-07-30", "buy", 0.8, "机密内容")
            self.assertEqual(self._lines()[0]["summary"], "")


class RetentionTest(_Base):
    """验收：保留量受 NASDX_MEMORY_MAX_RECORDS 约束。"""

    def test_jsonl_prunes_to_max_records(self):
        with env_var("NASDX_MEMORY_ENABLED", "1"), \
             env_var("NASDX_MEMORY_MAX_RECORDS", "5"):
            for i in range(9):
                memory.record_decision(f"60051{i}", "2026-07-30", "buy", 0.5, f"r{i}")
            lines = self._lines()
            self.assertEqual(len(lines), 5)
            self.assertEqual(lines[0]["stock_code"], "600514")  # 最旧的被淘汰
            self.assertEqual(lines[-1]["stock_code"], "600518")

    def test_sqlite_prunes_to_max_records(self):
        db = os.path.join(self._tmp.name, "mem.db")
        with env_var("NASDX_MEMORY_ENABLED", "1"), \
             env_var("NASDX_MEMORY_MAX_RECORDS", "3"):
            for i in range(7):
                memory.record_decision(f"00000{i}", "2026-07-30", "hold", 0.5, "s",
                                       db_path=db)
            self.assertEqual(self._sqlite_count(db), 3)
            recs = memory.recall_for_reflection(limit=10, db_path=db)
            self.assertEqual({r["stock_code"] for r in recs},
                             {"000004", "000005", "000006"})

    def test_invalid_max_records_falls_back_to_default(self):
        with env_var("NASDX_MEMORY_ENABLED", "1"), \
             env_var("NASDX_MEMORY_MAX_RECORDS", "abc"):
            memory.record_decision("600519", "2026-07-30", "buy", 0.8, "s")
            self.assertEqual(len(self._lines()), 1)  # 不崩溃，默认 500 生效


class ClearAndStatusTest(_Base):
    """验收：可清除、可诊断。"""

    def test_clear_jsonl(self):
        with env_var("NASDX_MEMORY_ENABLED", "1"):
            memory.record_decision("600519", "2026-07-30", "buy", 0.8, "a")
            memory.record_decision("000001", "2026-07-30", "hold", 0.5, "b")
        n = memory.clear_memory()
        self.assertEqual(n, 2)
        self.assertFalse(os.path.exists(self._jsonl()))
        self.assertEqual(memory.clear_memory(), 0)  # 幂等

    def test_clear_works_while_disabled(self):
        """关闭状态也能清除历史遗留数据。"""
        with env_var("NASDX_MEMORY_ENABLED", "1"):
            memory.record_decision("600519", "2026-07-30", "buy", 0.8, "a")
        with env_var("NASDX_MEMORY_ENABLED", None):
            self.assertEqual(memory.clear_memory(), 1)
            self.assertFalse(os.path.exists(self._jsonl()))

    def test_clear_sqlite(self):
        db = os.path.join(self._tmp.name, "mem.db")
        with env_var("NASDX_MEMORY_ENABLED", "1"):
            memory.record_decision("600519", "2026-07-30", "buy", 0.8, "a", db_path=db)
        self.assertEqual(memory.clear_memory(db_path=db), 1)
        self.assertEqual(self._sqlite_count(db), 0)

    def test_status_fields(self):
        with env_var("NASDX_MEMORY_ENABLED", "1"):
            memory.record_decision("600519", "2026-07-30", "buy", 0.8, "a")
            st = memory.memory_status()
            self.assertTrue(st["enabled"])
            self.assertEqual(st["backend"], "jsonl")
            self.assertEqual(st["record_count"], 1)
            self.assertEqual(st["max_records"], 500)
            self.assertEqual(st["summary_max_chars"], 200)
        with env_var("NASDX_MEMORY_ENABLED", None):
            self.assertFalse(memory.memory_status()["enabled"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
