"""GitHub 安全基线契约（Issue #72）。

这些断言锁的是「仓库内可版本化」的那部分安全基线：

* 每个 workflow 都显式声明最小权限，不靠仓库默认值；
* 官方/第三方 Action 一律固定到 40 位 commit SHA（tag 可被重指向，SHA 不能）；
* Dependabot 同时覆盖 pip 与 github-actions，否则 SHA 固定会变成"永不更新"；
* CodeQL 在 PR / 默认分支 push / 定时三种场景都跑；
* SECURITY.md 存在并覆盖漏洞报告与密钥泄露响应流程。

仓库 Settings 侧的开关（Dependabot alerts、ruleset）无法从仓库文件断言，
不在本文件覆盖范围内，见 SECURITY.md 与 Issue #72。
"""

from __future__ import annotations

import json
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW_DIR = ROOT / ".github" / "workflows"
DEPENDABOT = ROOT / ".github" / "dependabot.yml"
CODEQL = WORKFLOW_DIR / "codeql.yml"
RULESET = ROOT / ".github" / "rulesets" / "master-baseline.json"
SECURITY_POLICY = ROOT / "SECURITY.md"
README = ROOT / "README.md"

# `uses: owner/repo@ref` —— 允许 ./local-action 与 docker:// 形式跳过。
USES_RE = re.compile(r"^\s*(?:-\s+)?uses:\s*(?P<ref>\S+)\s*(?P<comment>#.*)?$")
SHA_RE = re.compile(r"^[0-9a-f]{40}$")
# 顶层（零缩进）键，用来判断 permissions 是否在 workflow 根层声明。
TOP_LEVEL_KEY_RE = re.compile(r"^(?P<key>[A-Za-z_][\w-]*):")


def workflow_files() -> list[Path]:
    if not WORKFLOW_DIR.is_dir():
        return []
    return sorted(
        p for p in WORKFLOW_DIR.iterdir() if p.suffix in {".yml", ".yaml"}
    )


def load_yaml(path: Path):
    import yaml  # 延迟导入：没装 PyYAML 时只跑纯文本断言

    return yaml.safe_load(path.read_text(encoding="utf-8"))


def top_level_block(text: str, key: str) -> str | None:
    """抓取顶层 key 的原始文本块（不依赖 PyYAML）。"""
    lines = text.splitlines()
    start = None
    for index, line in enumerate(lines):
        match = TOP_LEVEL_KEY_RE.match(line)
        if match and match.group("key") == key:
            start = index
            break
    if start is None:
        return None
    block = [lines[start]]
    for line in lines[start + 1 :]:
        if line.strip() and not line.startswith((" ", "\t", "#")):
            break
        block.append(line)
    return "\n".join(block)


class WorkflowPermissionContracts(unittest.TestCase):
    def test_workflow_directory_is_present(self):
        self.assertTrue(workflow_files(), "no GitHub workflow files found")

    def test_every_workflow_declares_top_level_permissions(self):
        for path in workflow_files():
            with self.subTest(workflow=path.name):
                text = path.read_text(encoding="utf-8")
                self.assertIsNotNone(
                    top_level_block(text, "permissions"),
                    f"{path.name} 缺少顶层 permissions 声明（不能依赖仓库默认权限）",
                )

    def test_top_level_permissions_are_read_only(self):
        for path in workflow_files():
            with self.subTest(workflow=path.name):
                data = load_yaml(path)
                permissions = data.get("permissions")
                self.assertIsInstance(
                    permissions, dict, f"{path.name} 的 permissions 必须是显式映射"
                )
                self.assertEqual(
                    permissions.get("contents"),
                    "read",
                    f"{path.name} 顶层不应授予 contents 写权限",
                )
                for scope, value in permissions.items():
                    self.assertEqual(
                        value,
                        "read",
                        f"{path.name} 顶层 {scope} 应为 read，写权限只能下放到具体 job",
                    )

    def test_job_level_write_permissions_are_allowlisted(self):
        """job 级写权限只允许 CodeQL 的 security-events。"""
        allowed = {("codeql.yml", "security-events")}
        for path in workflow_files():
            data = load_yaml(path)
            for job_name, job in (data.get("jobs") or {}).items():
                permissions = (job or {}).get("permissions")
                if not isinstance(permissions, dict):
                    continue
                for scope, value in permissions.items():
                    if value == "read" or value == "none":
                        continue
                    with self.subTest(workflow=path.name, job=job_name, scope=scope):
                        self.assertIn(
                            (path.name, scope),
                            allowed,
                            f"{path.name}:{job_name} 授予了未登记的写权限 {scope}={value}",
                        )


class ActionPinningContracts(unittest.TestCase):
    def _action_refs(self, path: Path) -> list[tuple[str, str | None]]:
        refs: list[tuple[str, str | None]] = []
        for line in path.read_text(encoding="utf-8").splitlines():
            match = USES_RE.match(line)
            if not match:
                continue
            ref = match.group("ref").strip("'\"")
            if ref.startswith(("./", "docker://")):
                continue
            refs.append((ref, match.group("comment")))
        return refs

    def test_every_action_is_pinned_to_commit_sha(self):
        found_any = False
        for path in workflow_files():
            for ref, _comment in self._action_refs(path):
                found_any = True
                with self.subTest(workflow=path.name, ref=ref):
                    self.assertIn("@", ref, f"{ref} 未指定版本")
                    pinned = ref.rsplit("@", 1)[1]
                    self.assertRegex(
                        pinned,
                        SHA_RE,
                        f"{path.name} 的 {ref} 必须固定到 40 位 commit SHA，tag 可被重指向",
                    )
        self.assertTrue(found_any, "未解析到任何 action 引用，正则可能失效")

    def test_pinned_actions_carry_version_comment(self):
        for path in workflow_files():
            for ref, comment in self._action_refs(path):
                with self.subTest(workflow=path.name, ref=ref):
                    self.assertIsNotNone(
                        comment,
                        f"{path.name} 的 {ref} 缺少 `# vX.Y.Z` 版本注释，Dependabot 依赖它做升级",
                    )
                    self.assertRegex(
                        comment or "",
                        r"#\s*v?\d+(\.\d+)*",
                        f"{path.name} 的 {ref} 版本注释格式不可识别",
                    )

    def test_checkout_steps_do_not_persist_credentials(self):
        for path in workflow_files():
            text = path.read_text(encoding="utf-8")
            if "actions/checkout@" not in text:
                continue
            with self.subTest(workflow=path.name):
                self.assertIn(
                    "persist-credentials: false",
                    text,
                    f"{path.name} 的 checkout 应关闭凭证持久化，避免后续步骤复用 GITHUB_TOKEN",
                )


class DependabotContracts(unittest.TestCase):
    def test_dependabot_config_exists(self):
        self.assertTrue(DEPENDABOT.is_file(), ".github/dependabot.yml is missing")

    def test_dependabot_covers_pip_and_actions(self):
        data = load_yaml(DEPENDABOT)
        self.assertEqual(data.get("version"), 2)
        ecosystems = {
            entry.get("package-ecosystem") for entry in data.get("updates") or []
        }
        self.assertIn(
            "github-actions",
            ecosystems,
            "Action 已固定 SHA，必须由 Dependabot 驱动升级，否则永远停在旧版本",
        )
        self.assertIn("pip", ecosystems)

    def test_dependabot_updates_have_schedule(self):
        data = load_yaml(DEPENDABOT)
        for entry in data.get("updates") or []:
            with self.subTest(ecosystem=entry.get("package-ecosystem")):
                schedule = entry.get("schedule") or {}
                self.assertIn(
                    schedule.get("interval"),
                    {"daily", "weekly", "monthly"},
                    "每个 update 条目都要有明确的 schedule.interval",
                )

    def test_toolchain_pins_are_not_auto_bumped(self):
        """pip/uv 版本由 run_dependency_lock_check.py --enforce-toolchain 锁定。"""
        data = load_yaml(DEPENDABOT)
        pip_entries = [
            entry
            for entry in data.get("updates") or []
            if entry.get("package-ecosystem") == "pip"
        ]
        self.assertTrue(pip_entries)
        ignored = {
            rule.get("dependency-name")
            for entry in pip_entries
            for rule in entry.get("ignore") or []
        }
        self.assertIn("pip", ignored)
        self.assertIn("uv", ignored)


class CodeQlWorkflowContracts(unittest.TestCase):
    def test_codeql_workflow_exists(self):
        self.assertTrue(CODEQL.is_file(), ".github/workflows/codeql.yml is missing")

    def test_codeql_runs_on_pr_push_and_schedule(self):
        data = load_yaml(CODEQL)
        # PyYAML 会把裸 `on:` 解析成布尔 True（YAML 1.1），两种键都兜住。
        triggers = data.get("on") if "on" in data else data.get(True)
        self.assertIsInstance(triggers, dict, "codeql.yml 的触发器解析失败")
        self.assertIn("pull_request", triggers)
        self.assertIn("push", triggers)
        self.assertIn("schedule", triggers)
        push_branches = (triggers.get("push") or {}).get("branches") or []
        self.assertIn("master", push_branches)
        self.assertTrue(
            (triggers.get("schedule") or [{}])[0].get("cron"),
            "定时扫描必须给出 cron 表达式",
        )

    def test_codeql_job_requests_security_events_write(self):
        data = load_yaml(CODEQL)
        jobs = data.get("jobs") or {}
        self.assertTrue(jobs, "codeql.yml 没有 job")
        for job_name, job in jobs.items():
            with self.subTest(job=job_name):
                permissions = (job or {}).get("permissions") or {}
                self.assertEqual(permissions.get("security-events"), "write")
                self.assertEqual(permissions.get("contents"), "read")

    def test_codeql_analyzes_python(self):
        text = CODEQL.read_text(encoding="utf-8")
        self.assertIn("github/codeql-action/init@", text)
        self.assertIn("github/codeql-action/analyze@", text)
        data = load_yaml(CODEQL)
        languages = set()
        for job in (data.get("jobs") or {}).values():
            matrix = ((job or {}).get("strategy") or {}).get("matrix") or {}
            languages.update(matrix.get("language") or [])
        self.assertIn("python", languages)


class MasterRulesetContracts(unittest.TestCase):
    """ruleset 的真实生效状态在 GitHub 侧，这里只锁「可导入的声明文件」本身。"""

    def test_ruleset_declaration_exists_and_parses(self):
        self.assertTrue(RULESET.is_file(), ".github/rulesets/master-baseline.json is missing")
        json.loads(RULESET.read_text(encoding="utf-8"))

    def test_ruleset_targets_master_and_blocks_history_rewrite(self):
        data = json.loads(RULESET.read_text(encoding="utf-8"))
        self.assertEqual(data.get("target"), "branch")
        self.assertEqual(data.get("enforcement"), "active")
        include = ((data.get("conditions") or {}).get("ref_name") or {}).get("include")
        self.assertEqual(include, ["refs/heads/master"])
        rule_types = {rule.get("type") for rule in data.get("rules") or []}
        self.assertIn("deletion", rule_types)
        self.assertIn(
            "non_fast_forward",
            rule_types,
            "必须阻断 force-push，否则历史可被静默重写",
        )

    def test_pr_only_rules_are_documented_but_not_silently_enabled(self):
        """切换到 PR-only 会阻断直推 master，属 owner 决定，不能由自动化偷偷打开。"""
        data = json.loads(RULESET.read_text(encoding="utf-8"))
        optional = data.get("_optional_rules_requiring_owner_decision") or []
        optional_types = {rule.get("type") for rule in optional}
        self.assertIn("pull_request", optional_types)
        self.assertIn("required_status_checks", optional_types)
        active_types = {rule.get("type") for rule in data.get("rules") or []}
        self.assertFalse(
            active_types & optional_types,
            "同一条规则不能同时出现在生效列表和待决策列表",
        )

    def test_required_checks_reference_real_workflow_jobs(self):
        data = json.loads(RULESET.read_text(encoding="utf-8"))
        contexts: set[str] = set()
        for rule in data.get("_optional_rules_requiring_owner_decision") or []:
            if rule.get("type") != "required_status_checks":
                continue
            for check in (rule.get("parameters") or {}).get(
                "required_status_checks"
            ) or []:
                contexts.add(check["context"])
        self.assertTrue(contexts, "待决策的 required_status_checks 为空")

        job_names: set[str] = set()
        for path in workflow_files():
            data_wf = load_yaml(path)
            for job_name, job in (data_wf.get("jobs") or {}).items():
                job_names.add(job_name)
                display = (job or {}).get("name")
                if isinstance(display, str):
                    # `Analyze (${{ matrix.language }})` -> `Analyze (python)`
                    job_names.add(
                        re.sub(
                            r"\$\{\{\s*matrix\.language\s*\}\}", "python", display
                        )
                    )
        for context in contexts:
            with self.subTest(context=context):
                self.assertIn(
                    context,
                    job_names,
                    f"required check `{context}` 没有对应的 workflow job，规则会永远处于 pending",
                )


class SecurityPolicyContracts(unittest.TestCase):
    def test_security_policy_exists(self):
        self.assertTrue(SECURITY_POLICY.is_file(), "SECURITY.md is missing")

    def test_security_policy_covers_reporting_and_leak_response(self):
        text = SECURITY_POLICY.read_text(encoding="utf-8")
        for marker in [
            "security/advisories/new",
            "NASDX_API_KEY",
            "run_security_checks.py --skip-optional --history",
            "codeql.yml",
            "dependabot.yml",
        ]:
            with self.subTest(marker=marker):
                self.assertIn(marker, text)
        self.assertIn("吊销", text, "密钥泄露流程必须先要求服务端吊销")

    def test_security_policy_has_no_literal_credentials(self):
        text = SECURITY_POLICY.read_text(encoding="utf-8")
        # 只允许脱敏占位，不允许出现可用长度的真实前缀。
        self.assertIsNone(
            re.search(r"sk-[A-Za-z0-9]{16,}", text),
            "SECURITY.md 中出现疑似真实凭证",
        )

    def test_readme_links_security_policy(self):
        text = README.read_text(encoding="utf-8")
        self.assertIn("SECURITY.md", text, "README 应指向 SECURITY.md")


if __name__ == "__main__":
    unittest.main()
