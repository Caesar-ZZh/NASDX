# -*- coding: utf-8 -*-
"""Issue #61：audit_loop harness 的 Issue/PR 生命周期正确性（全部 mock GitHub/git）。

验收覆盖：
- 创建 PR 不关闭 Issue（open/draft PR 下 Issue 保持开放）；
- 仅当修复合并且可达默认分支后才关闭 Issue、标记 fixed；
- PR 未合并即关闭 → Issue 保持/重开，finding 可重试；
- git commit/push、PR 创建失败不得标记 fixed；
- 旧 schema（fixed=PR 已开）迁移后可被重新验证；
- Issue→PR 关联在状态与评论中保持可见。
"""
from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

import audit_loop  # noqa: E402  (tools/audit_loop.py)


def make_state(**over):
    state = audit_loop._default_state()
    state.update(over)
    return state


def finding(fid="F0", file="quant/backtest.py", test="tests/x.py"):
    return {
        "id": fid, "module": "quant-backtest", "severity": "P1",
        "category": "defect", "title": "示例缺陷", "description": "d",
        "expected": "e", "actual": "a", "repro": "r", "suggestion": "s",
        "file": file, "test": test,
    }


class LifecycleBase(unittest.TestCase):
    def setUp(self):
        # 隔离持久化与外部命令
        patches = [
            mock.patch.object(audit_loop, "save_state", lambda s: None),
            mock.patch.object(audit_loop, "gh_issue_comment"),
            mock.patch.object(audit_loop, "gh_issue_close"),
            mock.patch.object(audit_loop, "gh_issue_reopen"),
        ]
        self.mocks = {}
        for p in patches:
            m = p.start()
            self.addCleanup(p.stop)
        # 保留可断言的引用
        self.comment = audit_loop.gh_issue_comment
        self.close = audit_loop.gh_issue_close
        self.reopen = audit_loop.gh_issue_reopen


class TestPhaseFixDoesNotCloseIssue(LifecycleBase):
    """验收 1/2：创建 fix PR 不关闭 Issue；open/draft PR 下 Issue 保持开放。"""

    def _run_phase_fix(self, pr_number):
        state = make_state(findings=[finding()], issues={"F0": 51})
        with mock.patch.object(audit_loop, "_generate_patch", return_value="diff --git x"), \
             mock.patch.object(audit_loop, "_apply_and_test", return_value=True), \
             mock.patch.object(audit_loop, "gh_pr_create", return_value=pr_number), \
             mock.patch.object(Path, "exists", return_value=True):
            audit_loop.phase_fix(state, max_fix=3)
        return state

    def test_pr_opened_keeps_issue_open(self):
        state = self._run_phase_fix(pr_number=53)
        self.close.assert_not_called()
        self.reopen.assert_not_called()
        # 状态区分：PR 已开 != 已修复
        self.assertEqual(state["prs"], {"F0": 53})
        self.assertEqual(state["fixed"], {})
        # Issue→PR 关联可见（评论提及 PR 号）
        self.comment.assert_called_once()
        args = self.comment.call_args[0]
        self.assertEqual(args[0], 51)
        self.assertIn("#53", args[1])
        self.assertNotIn("Closes", args[1])

    def test_pr_create_failure_marks_nothing(self):
        state = self._run_phase_fix(pr_number=None)
        self.assertEqual(state["prs"], {})
        self.assertEqual(state["fixed"], {})
        self.close.assert_not_called()

    def test_apply_and_test_failure_marks_nothing(self):
        state = make_state(findings=[finding()], issues={"F0": 51})
        with mock.patch.object(audit_loop, "_generate_patch", return_value="diff --git x"), \
             mock.patch.object(audit_loop, "_apply_and_test", return_value=False), \
             mock.patch.object(audit_loop, "gh_pr_create") as prc, \
             mock.patch.object(Path, "exists", return_value=True):
            audit_loop.phase_fix(state, max_fix=3)
        prc.assert_not_called()
        self.assertEqual(state["prs"], {})
        self.assertEqual(state["fixed"], {})

    def test_pending_pr_not_refixed(self):
        """已有开放 PR 的 finding 不重复走修复（等待 verify），保持可审计。"""
        state = make_state(findings=[finding()], issues={"F0": 51}, prs={"F0": 53})
        with mock.patch.object(audit_loop, "_generate_patch") as gen:
            audit_loop.phase_fix(state, max_fix=3)
        gen.assert_not_called()
        self.assertEqual(state["prs"], {"F0": 53})


class TestPhaseVerify(LifecycleBase):
    """验收 3/4/5：合并+默认分支可达才关闭；未合并关闭可重试。"""

    def test_merged_and_reachable_closes_issue(self):
        state = make_state(issues={"F0": 51}, prs={"F0": 53})
        with mock.patch.object(audit_loop, "gh_pr_view", return_value={
                 "state": "MERGED", "mergedAt": "2026-07-29T00:00:00Z",
                 "mergeCommitSha": "abc1234567"}), \
             mock.patch.object(audit_loop, "commit_reachable_on_default", return_value=True), \
             mock.patch.object(audit_loop, "gh_issue_state", return_value="OPEN"):
            audit_loop.phase_verify(state)
        self.assertEqual(state["fixed"], {"F0": 53})
        self.assertEqual(state["prs"], {})
        self.close.assert_called_once_with(51)

    def test_merged_but_not_reachable_stays_pending(self):
        state = make_state(issues={"F0": 51}, prs={"F0": 53})
        with mock.patch.object(audit_loop, "gh_pr_view", return_value={
                 "state": "MERGED", "mergedAt": "x", "mergeCommitSha": "abc"}), \
             mock.patch.object(audit_loop, "commit_reachable_on_default", return_value=False):
            audit_loop.phase_verify(state)
        self.assertEqual(state["fixed"], {})
        self.assertEqual(state["prs"], {"F0": 53})
        self.close.assert_not_called()

    def test_open_pr_leaves_issue_open(self):
        state = make_state(issues={"F0": 51}, prs={"F0": 53})
        with mock.patch.object(audit_loop, "gh_pr_view", return_value={
                 "state": "OPEN", "mergedAt": None, "mergeCommitSha": None}):
            audit_loop.phase_verify(state)
        self.assertEqual(state["fixed"], {})
        self.assertEqual(state["prs"], {"F0": 53})
        self.close.assert_not_called()

    def test_closed_unmerged_reopens_issue_and_is_retryable(self):
        state = make_state(findings=[finding()], issues={"F0": 51}, prs={"F0": 53})
        with mock.patch.object(audit_loop, "gh_pr_view", return_value={
                 "state": "CLOSED", "mergedAt": None, "mergeCommitSha": None}), \
             mock.patch.object(audit_loop, "gh_issue_state", return_value="CLOSED"):
            audit_loop.phase_verify(state)
        self.assertEqual(state["prs"], {})       # 可重试
        self.assertEqual(state["fixed"], {})
        self.reopen.assert_called_once_with(51)
        # 重试路径：phase_fix 可再次处理该 finding
        with mock.patch.object(audit_loop, "_generate_patch", return_value="diff --git x"), \
             mock.patch.object(audit_loop, "_apply_and_test", return_value=True), \
             mock.patch.object(audit_loop, "gh_pr_create", return_value=88), \
             mock.patch.object(Path, "exists", return_value=True):
            audit_loop.phase_fix(state, max_fix=3)
        self.assertEqual(state["prs"], {"F0": 88})

    def test_closed_unmerged_issue_already_open_not_reopened(self):
        state = make_state(issues={"F0": 51}, prs={"F0": 53})
        with mock.patch.object(audit_loop, "gh_pr_view", return_value={
                 "state": "CLOSED", "mergedAt": None, "mergeCommitSha": None}), \
             mock.patch.object(audit_loop, "gh_issue_state", return_value="OPEN"):
            audit_loop.phase_verify(state)
        self.reopen.assert_not_called()
        self.assertEqual(state["prs"], {})

    def test_pr_view_failure_keeps_state(self):
        state = make_state(issues={"F0": 51}, prs={"F0": 53})
        with mock.patch.object(audit_loop, "gh_pr_view", return_value=None):
            audit_loop.phase_verify(state)
        self.assertEqual(state["prs"], {"F0": 53})
        self.assertEqual(state["fixed"], {})
        self.close.assert_not_called()


class TestApplyAndTestReturnCodes(unittest.TestCase):
    """验收 6：git commit/push 失败不能声称修复分支已发布。"""

    def _run(self, fail_on=None):
        """fail_on: git 子命令名（'commit'/'push'/'add'/'checkout'/'apply'）。"""
        def fake_git(args, timeout=120):
            rc = 1 if (fail_on and args[0] == fail_on) else 0
            return mock.Mock(returncode=rc, stdout="", stderr=f"boom {args[0]}")

        ok_proc = mock.Mock(returncode=0, stdout="", stderr="")
        with mock.patch.object(audit_loop, "_git", side_effect=fake_git), \
             mock.patch.object(audit_loop.subprocess, "run", return_value=ok_proc), \
             mock.patch.object(Path, "write_text"), \
             mock.patch.object(Path, "unlink"):
            return audit_loop._apply_and_test(
                "fix/audit-F0", audit_loop.ROOT / "quant" / "backtest.py",
                "diff --git x", "")

    def test_push_failure_returns_false(self):
        self.assertFalse(self._run(fail_on="push"))

    def test_commit_failure_returns_false(self):
        self.assertFalse(self._run(fail_on="commit"))

    def test_add_failure_returns_false(self):
        self.assertFalse(self._run(fail_on="add"))

    def test_checkout_failure_returns_false(self):
        self.assertFalse(self._run(fail_on="checkout"))

    def test_all_success_returns_true(self):
        self.assertTrue(self._run(fail_on=None))


class TestStateMigration(unittest.TestCase):
    """验收 7：旧 schema 的 fixed（实为 PR 已开）迁移后可重新验证/重试。"""

    def test_legacy_fixed_demoted_to_prs(self):
        legacy = {"executed": {}, "findings": [], "issues": {"F0": 51},
                  "fixed": {"F0": 53}, "skipped_modules": []}
        state = audit_loop._migrate_state(legacy)
        self.assertEqual(state["prs"], {"F0": 53})
        self.assertEqual(state["fixed"], {})

    def test_new_schema_untouched(self):
        fresh = audit_loop._default_state()
        fresh["prs"]["F1"] = 60
        fresh["fixed"]["F0"] = 50
        state = audit_loop._migrate_state(fresh)
        self.assertEqual(state["prs"], {"F1": 60})
        self.assertEqual(state["fixed"], {"F0": 50})

    def test_load_state_migrates_from_disk(self):
        legacy = {"executed": {}, "findings": [], "issues": {"F0": 51},
                  "fixed": {"F0": 53}, "skipped_modules": []}
        with mock.patch.object(audit_loop, "STATE_PATH") as sp:
            sp.exists.return_value = True
            sp.read_text.return_value = json.dumps(legacy)
            state = audit_loop.load_state()
        self.assertEqual(state["prs"], {"F0": 53})
        self.assertEqual(state["fixed"], {})


class TestCommitReachability(unittest.TestCase):
    def test_empty_sha_false(self):
        self.assertFalse(audit_loop.commit_reachable_on_default(""))

    def test_fetch_failure_false(self):
        with mock.patch.object(audit_loop, "_git",
                               return_value=mock.Mock(returncode=1, stderr="net down")):
            self.assertFalse(audit_loop.commit_reachable_on_default("abc"))

    def test_reachable_true(self):
        with mock.patch.object(audit_loop, "_git",
                               return_value=mock.Mock(returncode=0, stderr="")):
            self.assertTrue(audit_loop.commit_reachable_on_default("abc"))

    def test_not_ancestor_false(self):
        calls = []

        def fake_git(args, timeout=120):
            calls.append(args[0])
            rc = 1 if args[0] == "merge-base" else 0
            return mock.Mock(returncode=rc, stderr="")

        with mock.patch.object(audit_loop, "_git", side_effect=fake_git):
            self.assertFalse(audit_loop.commit_reachable_on_default("abc"))
        self.assertIn("fetch", calls)


class TestReportShowsLinkage(unittest.TestCase):
    """验收 8：Issue→PR 关联在报告中保持可见（区分 PR 待合并 / 已验证）。"""

    def test_report_rows(self):
        state = audit_loop._default_state()
        state["findings"] = [finding("F0"), finding("F1")]
        state["issues"] = {"F0": 51, "F1": 52}
        state["prs"] = {"F0": 53}
        state["fixed"] = {"F1": 54}
        state["executed"] = {}
        with mock.patch.object(audit_loop, "REPORT_DIR", audit_loop.ROOT / ".workbuddy"), \
             mock.patch.object(Path, "write_text") as wt, \
             mock.patch.object(Path, "mkdir"):
            audit_loop.write_report(state)
        text = wt.call_args[0][0]
        self.assertIn("PR 待合并（Issue 开放）", text)
        self.assertIn("已合并验证并关闭", text)
        self.assertIn("#53", text)
        self.assertIn("#54", text)


if __name__ == "__main__":
    unittest.main(verbosity=2)
