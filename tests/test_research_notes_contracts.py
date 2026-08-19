"""研究笔记 + 反思审计的合约测试（离线 mock，不联网）。"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path
from unittest.mock import patch

import pytest

# 让 nasdx 能被 import（项目根在 tests/ 上级）
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from nasdx import research_notes as rn
from nasdx import reflection as refl


@pytest.fixture(autouse=True)
def _isolated_db(tmp_path, monkeypatch):
    """每个用例使用临时 SQLite，绝不触碰用户本地笔记。"""
    monkeypatch.setattr(rn, "_DB_FILE", tmp_path / "research_notes.db")
    yield


# ---------- research_notes CRUD ----------

class TestNotesCRUD:
    def test_add_and_get(self):
        out = rn.add("早盘快评", "指数高开 0.5%，量能放大", "复盘", tags=["早盘"])
        assert out["kind"] == "复盘"
        assert out["id"].startswith("N")
        got = rn.get(out["id"])
        assert got["title"] == "早盘快评"
        assert got["content"] == "指数高开 0.5%，量能放大"
        assert got["kind"] == "复盘"
        assert got["tags"] == ["早盘"]

    def test_add_invalid_kind_raises(self):
        with pytest.raises(ValueError, match="kind 必须在"):
            rn.add("x", "y", "未知类型")

    def test_update_fields(self):
        rid = rn.add("t", "c", "要点")["id"]
        up = rn.update(rid, title="new title", content="new content", tags=["a", "b"])
        assert up["title"] == "new title"  # via get
        got = rn.get(rid)
        assert got["content"] == "new content"
        assert got["tags"] == ["a", "b"]
        assert got["updated_at"] > got["created_at"]

    def test_update_missing_raises(self):
        with pytest.raises(KeyError):
            rn.update("NOPE", title="x")

    def test_remove(self):
        rid = rn.add("t", "c", "辩论")["id"]
        assert rn.remove(rid)
        with pytest.raises(KeyError):
            rn.get(rid)
        assert not rn.remove(rid)  # 重复删返回 False

    def test_list_filters(self):
        rn.add("a", "1", "复盘", tags=["指数"])
        rn.add("b", "2", "问AI", tags=["个股"])
        rn.add("c", "3", "复盘", tags=["板块"])
        assert len(rn.list_notes(kind="复盘")) == 2
        assert len(rn.list_notes(kind="问AI")) == 1
        assert len(rn.list_notes(kind="复盘", tag="指数")) == 1
        assert len(rn.list_notes(kind="复盘", tag="板块")) == 1

    def test_list_returns_summaries_without_content(self):
        rn.add("t", "long content here", "要点")
        items = rn.list_notes()
        assert len(items) == 1
        assert "content" not in items[0]

    def test_count(self):
        rn.add("a", "1", "复盘")
        rn.add("b", "2", "复盘")
        rn.add("c", "3", "辩论")
        assert rn.count_notes() == 3
        assert rn.count_notes(kind="复盘") == 2
        assert rn.count_notes(kind="辩论") == 1

    def test_stream_from_file_not_found(self):
        with pytest.raises(FileNotFoundError):
            list(rn.stream_from_file("/tmp/no_such_file_xyz.txt"))


# ---------- reflections ----------

class TestReflections:
    def test_add_and_get_reflection(self):
        nid = rn.add("t", "c", "复盘")["id"]
        ref = rn.add_reflection(nid, "审计结果", source_len=100, truncated=False)
        assert ref["note_id"] == nid
        got = rn.get_reflection(nid)
        assert got is not None
        assert got["full_text"] == "审计结果"
        assert got["source_len"] == 100
        assert got["truncated"] == 0

    def test_get_reflection_none_when_absent(self):
        nid = rn.add("t", "c", "辩论")["id"]
        assert rn.get_reflection(nid) is None

    def test_list_reflections(self):
        nid = rn.add("t", "c", "要点")["id"]
        rn.add_reflection(nid, "r1", 10)
        rn.add_reflection(nid, "r2", 20)
        rows = rn.list_reflections(note_id=nid)
        assert len(rows) == 2
        assert rows[0]["source_len"] == 20  # latest first
        assert "full_text" not in rows[0]  # 列表接口只返回摘要

    def test_remove_cascades_reflections(self):
        nid = rn.add("t", "c", "复盘")["id"]
        rn.add_reflection(nid, "r", 5)
        rn.remove(nid)
        assert rn.get_reflection(nid) is None


# ---------- reflection.run_reflection_stream (mocked LLM) ----------

class TestReflectionStream:
    def _fake_stream_events(self, cfg, messages):
        yield {"type": "delta", "text": "有数据支撑："}
        yield {"type": "delta", "text": "最脆弱一环：X\n"}
        yield {"type": "done", "content": "complete"}

    @patch("nasdx.reflection.llm_client.ask", return_value="有数据支撑：最脆弱一环：X")
    def test_normal_flow(self, m_ask):
        events = list(refl.run_reflection_stream({"model": "d"}, "一段分析"))
        types = [e["type"] for e in events]
        assert "delta" in types
        assert "done" in types
        assert m_ask.call_count == 1

    @patch("nasdx.reflection.llm_client.ask")
    def test_empty_source(self, m_ask):
        events = list(refl.run_reflection_stream({}, ""))
        assert any(e["type"] == "error" for e in events)
        m_ask.assert_not_called()

    @patch("nasdx.reflection.llm_client.ask", return_value="审计完成")
    def test_long_source_truncated(self, m_ask):
        long = "x" * (refl.MAX_SOURCE_CHARS + 100)
        events = list(refl.run_reflection_stream({}, long))
        assert any(e["type"] == "status" for e in events)
        # 调用时传入的 text 应被截断
        call_args = m_ask.call_args.args[0][1]["content"]
        assert len(call_args) <= refl.MAX_SOURCE_CHARS + len("【待审分析】\n\n请开始审计。\n")

    @patch("nasdx.reflection.llm_client.ask", side_effect=RuntimeError("boom"))
    def test_stream_raises_error_event(self, _m_ask):
        events = list(refl.run_reflection_stream({}, "一段"))
        assert any(e["type"] == "error" for e in events)


class TestReflectionSync:
    @patch("nasdx.reflection.run_reflection_stream")
    def test_returns_done(self, m_stream):
        m_stream.return_value = [
            {"type": "delta", "text": "hello"},
            {"type": "done", "content": "done"},
        ]
        out = refl.run_reflection({"model": "d"}, "source")
        assert out["content"] == "hello"
        assert out["truncated"] is False
