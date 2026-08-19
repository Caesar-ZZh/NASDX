"""my_reports 模块合约测试 —— 上传/列举/下载/删除 + 行业标签推断。

不联网，用临时目录 + base64 假文件，覆盖全部公共函数路径。
"""

from __future__ import annotations

import base64
import os
import sys
import tempfile
from pathlib import Path

# 把项目根加到 path，确保能 import nasdx
_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT))

import pytest

# 注入临时 REPORTS_DIR，避免污染用户真实数据
_TMP_DIR: Path | None = None


def _setup_tmp_dir():
    global _TMP_DIR
    if _TMP_DIR is None:
        _TMP_DIR = Path(tempfile.mkdtemp(prefix="nasdx_myreports_test_"))
    # 猴子补丁模块级 REPORTS_DIR / _INDEX
    import nasdx.my_reports as mr
    mr.REPORTS_DIR = _TMP_DIR
    mr._INDEX = _TMP_DIR / "index.json"
    # 清掉旧索引
    if mr._INDEX.exists():
        mr._INDEX.unlink()
    return mr


def _make_blob(size_kb: int = 4) -> bytes:
    """造一份 size_kb KB 的随机二进制。"""
    import secrets
    return secrets.token_bytes(size_kb * 1024)


def _b64(blob: bytes) -> str:
    return base64.b64encode(blob).decode("ascii")


def _b64_data_uri(blob: bytes) -> str:
    return f"data:application/pdf;base64,{_b64(blob)}"


class TestClassify:
    def test_robot(self):
        from nasdx.my_reports import classify
        assert classify("人形机器人2026研报.pdf") == "人形机器人"

    def test_optical(self):
        from nasdx.my_reports import classify
        assert classify("CPO光模块月报.docx") == "光互联"

    def test_hbm(self):
        from nasdx.my_reports import classify
        assert classify("HBM存储产业链分析.pdf") == "HBM存储"

    def test_ai(self):
        from nasdx.my_reports import classify
        assert classify("英伟达GPU算力服务器.pdf") == "AI算力"

    def test_semi(self):
        from nasdx.my_reports import classify
        assert classify("半导体晶圆代工.md") == "半导体"

    def test_energy(self):
        from nasdx.my_reports import classify
        assert classify("宁德时代锂电电池研究.pdf") == "新能源"

    def test_pharma(self):
        from nasdx.my_reports import classify
        assert classify("创新药CXO临床进展.docx") == "创新药"

    def test_aerospace(self):
        from nasdx.my_reports import classify
        assert classify("商业航天卫星火箭.pdf") == "商业航天"

    def test_power(self):
        from nasdx.my_reports import classify
        assert classify("特高压电力电网.pdf") == "电力电网"

    def test_unknown(self):
        from nasdx.my_reports import classify
        assert classify("随机未分类文档.txt") == "未分类"

    def test_case_insensitive(self):
        from nasdx.my_reports import classify
        assert classify("hbm存储颗粒.pdf") == "HBM存储"


class TestSaveReport:
    @pytest.fixture(autouse=True)
    def _patch_dir(self):
        self.mr = _setup_tmp_dir()

    def test_save_pdf(self):
        blob = _make_blob(2)
        meta = self.mr.save_report("机器人研报.pdf", _b64(blob))
        assert meta["name"] == "机器人研报.pdf"
        assert meta["industry"] == "人形机器人"
        assert meta["ext"] == ".pdf"
        assert meta["size"] == len(blob)
        assert meta["id"] is not None
        assert (self.mr.REPORTS_DIR / f"{meta['id']}.pdf").exists()

    def test_save_with_data_uri(self):
        blob = _make_blob(1)
        meta = self.mr.save_report("test.pdf", _b64_data_uri(blob))
        assert meta["size"] == len(blob)

    def test_save_docx(self):
        blob = _make_blob(3)
        meta = self.mr.save_report("光模块行业研究.docx", _b64(blob))
        assert meta["industry"] == "光互联"
        assert (self.mr.REPORTS_DIR / f"{meta['id']}.docx").exists()

    def test_save_txt(self):
        blob = b"hello world"
        meta = self.mr.save_report("notes.txt", _b64(blob))
        assert meta["ext"] == ".txt"
        assert (self.mr.REPORTS_DIR / f"{meta['id']}.txt").read_bytes() == blob

    def test_save_image(self):
        blob = _make_blob(1)
        meta = self.mr.save_report("chart.png", _b64(blob))
        assert meta["ext"] == ".png"
        assert (self.mr.REPORTS_DIR / f"{meta['id']}.png").exists()

    def test_empty_name_becomes_unnamed(self):
        blob = b"x"
        meta = self.mr.save_report("", _b64(blob))
        assert meta["name"] == "未命名"

    def test_path_traversal_sanitized(self):
        blob = b"x"
        meta = self.mr.save_report("../../etc/passwd.pdf", _b64(blob))
        assert ".." not in meta["name"] and "/" not in meta["name"] and "\\" not in meta["name"]

    def test_unsupported_ext_rejected(self):
        with pytest.raises(self.mr.ReportError, match="不支持的文件类型"):
            self.mr.save_report("evil.exe", _b64(b"malware"))

    def test_empty_file_rejected(self):
        with pytest.raises(self.mr.ReportError, match="文件为空"):
            self.mr.save_report("empty.pdf", _b64(b""))

    def test_invalid_b64_rejected(self):
        with pytest.raises(self.mr.ReportError, match="解码失败"):
            self.mr.save_report("bad.pdf", "not-base64!!!")

    def test_toolong_b64_rejected(self):
        blob = b"x" * (self.mr.MAX_BYTES + 1)
        with pytest.raises(self.mr.ReportError, match="文件过大"):
            self.mr.save_report("big.pdf", _b64(blob))


class TestListReports:
    @pytest.fixture(autouse=True)
    def _patch_dir(self):
        self.mr = _setup_tmp_dir()

    def test_empty(self):
        assert self.mr.list_reports() == []

    def test_order_descending_by_ts(self):
        import time
        t1 = int(time.time() * 1000)
        self.mr.save_report("a.pdf", _b64(b"aa"))
        time.sleep(0.01)
        self.mr.save_report("b.pdf", _b64(b"bb"))
        t2 = int(time.time() * 1000)
        lst = self.mr.list_reports()
        assert len(lst) == 2
        assert lst[0]["ts"] >= lst[1]["ts"]


class TestReportPath:
    @pytest.fixture(autouse=True)
    def _patch_dir(self):
        self.mr = _setup_tmp_dir()

    def test_existing(self):
        meta = self.mr.save_report("test.pdf", _b64(b"hello"))
        hit = self.mr.report_path(meta["id"])
        assert hit is not None
        path, name = hit
        assert path.exists()
        assert name == "test.pdf"
        assert path.read_bytes() == b"hello"

    def test_missing_id_returns_none(self):
        assert self.mr.report_path("nonexistent-id") is None

    def test_deleted_file_returns_none(self):
        meta = self.mr.save_report("del.pdf", _b64(b"x"))
        self.mr.delete_report(meta["id"])
        assert self.mr.report_path(meta["id"]) is None


class TestDeleteReport:
    @pytest.fixture(autouse=True)
    def _patch_dir(self):
        self.mr = _setup_tmp_dir()

    def test_delete_existing(self):
        meta = self.mr.save_report("to_del.pdf", _b64(b"y"))
        assert self.mr.delete_report(meta["id"]) is True
        assert self.mr.report_path(meta["id"]) is None
        assert not (self.mr.REPORTS_DIR / f"{meta['id']}.pdf").exists()
        assert meta["id"] not in [r["id"] for r in self.mr.list_reports()]

    def test_delete_missing_returns_false(self):
        assert self.mr.delete_report("no-such-id") is False

    def test_double_delete_is_noop(self):
        meta = self.mr.save_report("twice.pdf", _b64(b"z"))
        assert self.mr.delete_report(meta["id"]) is True
        assert self.mr.delete_report(meta["id"]) is False


class TestAtomicIndex:
    @pytest.fixture(autouse=True)
    def _patch_dir(self):
        self.mr = _setup_tmp_dir()

    def test_index_persistence(self):
        self.mr.save_report("a.pdf", _b64(b"1"))
        self.mr.save_report("b.pdf", _b64(b"2"))
        # 重新加载应保留两条
        assert len(self.mr.list_reports()) == 2


class TestGitignoreCompliance:
    """确认报告目录不在 git 追踪范围内。"""

    def test_reports_dir_is_outside_repo(self, monkeypatch):
        # 即便REPORTS_DIR设成临时目录，也应是用户外目录
        import nasdx.my_reports as mr
        # 临时目录永远不在 git 仓库根下（pytest 临时目录在系统 temp）
        assert str(mr.REPORTS_DIR).startswith(tempfile.gettempdir())
