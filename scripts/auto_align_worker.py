#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""NASDX × miaoousc.xyz 自动对齐 worker。

设计要点（定时任务调用本脚本）：
- 触发周期：由调用方决定。GitHub Actions 用 `cron: "0 */4 * * *"`（每 4 小时）；
  本地/WorkBuddy 亦可 `python scripts/auto_align_worker.py` 手动触发。
- 分支策略：每处理一个 issue，从 origin/master 切出 `fix/auto-align-<issue号>`，
  仅提交本次新增/修改的受控文件，推送后向 master 开 PR，PR 合并前不自动合入。
- issue 与修复代码关联：
    * issue 标题后缀 `[R1]..[N5]` = 对齐键（alignment key）；
    * 分支名 `fix/auto-align-<n>`、commit/PR 标题均带 issue 号与对齐键；
    * PR 正文含 `Closes #<n>` 自动关联，关闭 issue 时评论贴回 PR 链接。
- 守护红线：绝不触碰主工作树的 CONTEXT.md / quant/data.py / ths_bridge.py /
  .workbuddy/ / .audit_state.json；不硬编码任何 key；零标的（不推荐/不预测/不排名）。
- 隔离机制：每个 issue 在独立 git worktree（系统临时目录）中完成生成、编译、提交、
  推送，主工作树的未提交改动（用户的本地修改）全程不受影响。

仅依赖 Python 标准库（urllib 调 OpenAI 兼容 LLM，subprocess 调 gh/git）。
"""
import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REPO = os.environ.get("GITHUB_REPO", "Caesar-ZZh/NASDX")
SPEC_FILE = os.path.join(ROOT, "MIAOOUSC_NASDX_对齐方案.md")
REF_ROOT = os.path.join(ROOT, "_reverse_miaoou", "stock-analysis-base")

# 受保护文件：任何情况下都不得被本脚本修改或删除
FORBIDDEN_FILES = {"CONTEXT.md", "quant/data.py", "ths_bridge.py", ".audit_state.json"}
FORBIDDEN_PREFIXES = (".workbuddy/", ".github/", "dist/", "__pycache__/", ".git/")
# 仅允许在以下目录内写新文件，防止越界改动项目其它部分
ALLOWED_PREFIXES = ("nasdx/", "quant/", "tests/", "desktop/", "docs/")

PRIORITY_ORDER = {"P1": 0, "P2": 1, "P3": 2, "P4": 3}


# ----------------------------- 基础工具 -----------------------------
def run(cmd, check=True, capture=True, cwd=ROOT):
    r = subprocess.run(cmd, cwd=cwd, capture_output=capture, text=True)
    if check and r.returncode != 0:
        raise RuntimeError(f"命令失败: {' '.join(cmd)}\n{r.stderr}")
    return r


def gh(*args):
    cmd = ["gh", *args]
    r = run(cmd, check=True)
    return r.stdout.strip()


def log(msg):
    print(f"[auto-align] {msg}", flush=True)


def _load_llm_from_config():
    """从项目 config.toml 的 [llm] 段兜底读取凭据（不提交、本地生效）。"""
    cfg_path = os.path.join(ROOT, "config.toml")
    if not os.path.isfile(cfg_path):
        return {}
    try:
        import tomllib
        with open(cfg_path, "rb") as f:
            data = tomllib.load(f)
        llm = data.get("llm", {}) or {}
        return {
            "base_url": llm.get("base_url"),
            "api_key": llm.get("api_key"),
            "model": llm.get("model"),
        }
    except Exception:
        return {}


def resolve_llm_creds():
    """凭据解析优先级（与项目 desktop/config.py 一致）：
    LLM_* 环境变量 > NASDX_* 环境变量 > 项目配置（NASDX_CONFIG_FILE / %APPDATA%/NASDX/config.toml / 仓库 config.toml 的 [llm] 段）。

    这样 WorkBuddy 本机自动化可直接复用项目既有 DeepSeek 配置，无需另配 secret。
    """
    base = os.environ.get("LLM_BASE_URL") or os.environ.get("NASDX_BASE_URL")
    key = os.environ.get("LLM_API_KEY") or os.environ.get("NASDX_API_KEY")
    model = (os.environ.get("LLM_MODEL") or os.environ.get("NASDX_MODEL")
             or "agnes-2.5-flash")
    # 复用项目自带配置解析器（最权威，覆盖 APPDATA / 显式路径 / 仓库根）
    try:
        import importlib.util
        import pathlib
        spec = importlib.util.spec_from_file_location(
            "nasdx_desktop_config", os.path.join(ROOT, "desktop", "config.py"))
        mod = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = mod  # 必须注册，否则 dataclass 导入期 introspect 失败
        spec.loader.exec_module(mod)
        cfg = mod.load_desktop_config(pathlib.Path(ROOT))
        vals = getattr(cfg, "values", None) or {}
        base = base or vals.get("NASDX_BASE_URL")
        key = key or vals.get("NASDX_API_KEY")
        model = model or vals.get("NASDX_MODEL") or "deepseek-chat"
    except Exception as e:
        log(f"（注）项目配置解析不可用，回退到仓库根 config.toml: {e}")
    # 兜底：仓库根 config.toml [llm]
    if not (base and key):
        c = _load_llm_from_config()
        base = base or c.get("base_url")
        key = key or c.get("api_key")
        model = model or c.get("model") or "deepseek-chat"
    return base, key, model


# ----------------------------- LLM 调用 -----------------------------
def call_llm(system, user, model, base_url, api_key, timeout=600):
    url = base_url.rstrip("/") + "/chat/completions"
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "temperature": 0.2,
        # 推理模型会把大量 token 消耗在 reasoning_content 上，
        # 留给最终 content（代码 JSON）的预算必须足够大，否则 JSON 会被截断。
        "max_tokens": 24000,
    }
    # 尽量要求 JSON 输出（部分 OpenAI 兼容端点支持）
    try:
        payload["response_format"] = {"type": "json_object"}
    except Exception:
        pass
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=data, method="POST")
    req.add_header("Content-Type", "application/json")
    req.add_header("Authorization", f"Bearer {api_key}")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        obj = json.loads(resp.read().decode("utf-8"))
    content = obj["choices"][0]["message"]["content"]
    return _extract_json(content)


def _extract_json(text):
    try:
        return json.loads(text)
    except Exception:
        m = re.search(r"\{.*\}", text, re.DOTALL)
        if m:
            return json.loads(m.group(0))
        raise


# ----------------------------- issue 读取与排序 -----------------------------
def get_open_issues():
    out = gh("issue", "list", "--repo", REPO, "--label", "auto-align",
             "--state", "open", "--json", "number,title,body")
    return json.loads(out)


def priority_of(body):
    m = re.search(r"\*\*优先级\*\*:\s*(P\d)", body or "")
    return m.group(1) if m else "P3"


def key_of(title):
    m = re.search(r"\[([A-Z]\d+)\]\s*$", title or "")
    return m.group(1) if m else "?"


def sort_issues(issues):
    def sort_key(it):
        p = PRIORITY_ORDER.get(priority_of(it.get("body", "")), 9)
        return (p, it["number"])
    return sorted(issues, key=sort_key)


# ----------------------------- 上下文构建 -----------------------------
def build_context(issue):
    """把对齐方案 + 逆向参考源码（本地可用时）拼进上下文；缺失时优雅降级到 issue 正文。"""
    parts = []
    if os.path.isfile(SPEC_FILE):
        with open(SPEC_FILE, "r", encoding="utf-8") as f:
            parts.append("### 对齐方案（MIAOOUSC_NASDX_对齐方案.md）\n" + f.read())
    else:
        parts.append("（对齐方案文件不在仓库内，已跳过；以 issue 正文为准。）")

    refs = re.findall(r"_reverse_miaoou/stock-analysis-base/[^\s`\)]+", issue.get("body", ""))
    seen = set()
    for ref in refs:
        path = os.path.join(ROOT, ref)
        if path in seen or not os.path.isfile(path):
            continue
        seen.add(path)
        with open(path, "r", encoding="utf-8", errors="ignore") as f:
            code = f.read()
        if len(code) > 14000:
            code = code[:14000] + "\n...（已截断）"
        parts.append(f"### 逆向参考源码：{ref}\n```python\n{code}\n```")

    if not seen:
        parts.append("（本地逆向源码不可用，已跳过；以 issue 正文中的「参考实现/接入方式」为准。）")
    return "\n\n".join(parts)


# ----------------------------- 文件落盘与守护 -----------------------------
def validate_paths(files):
    for item in files:
        path = item.get("path", "")
        if not path:
            raise ValueError(f"空路径: {item}")
        norm = path.replace("\\", "/")
        if norm in FORBIDDEN_FILES or norm.split("/")[-1] in FORBIDDEN_FILES:
            raise ValueError(f"禁止修改受保护文件: {norm}")
        if any(norm.startswith(p) for p in FORBIDDEN_PREFIXES):
            raise ValueError(f"禁止写入受保护目录: {norm}")
        if "/" in norm:
            prefix = norm.rsplit("/", 1)[0] + "/"
            if not any(prefix.startswith(p) for p in ALLOWED_PREFIXES):
                raise ValueError(
                    f"只允许在 {ALLOWED_PREFIXES} 内写文件，越界: {norm}")
        else:
            if not norm.endswith(".py"):
                raise ValueError(f"根目录只允许新增 .py 文件，越界: {norm}")
    return True


def apply_files(files, base_dir):
    written = []
    for item in files:
        path = os.path.join(base_dir, item["path"])
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(item["content"])
        written.append(item["path"])
        log(f"写入 {item['path']} ({len(item['content'])} 字符)")
    return written


def compile_gate(paths, base_dir):
    py_files = [p for p in paths if p.endswith(".py")]
    if not py_files:
        return True
    for p in py_files:
        run([sys.executable, "-m", "py_compile", os.path.join(base_dir, p)], check=True)
    log(f"编译门禁通过：{len(py_files)} 个 .py 文件语法正确")
    return True


def run_contract_tests(written, base_dir):
    """尽力运行本轮新增的契约测试；缺失或失败仅告警，不阻断 PR。

    注意：以「本轮实际写入的测试文件」为准，而非用对齐键去硬匹配文件名
    （模型生成的测试名形如 test_<模块>_contracts.py，不含 R1/N1 等键）。
    """
    if not os.path.isdir(os.path.join(base_dir, "tests")):
        log("未找到 tests 目录（可选），跳过契约测试。")
        return
    matches = [p for p in written
               if p.startswith("tests/") and p.endswith("_contracts.py")]
    if not matches:
        log("本轮未生成契约测试（可选），跳过。")
        return
    for m in matches:
        r = run([sys.executable, "-m", "pytest", m, "-q"], check=False, cwd=base_dir)
        if r.returncode != 0:
            log(f"⚠ 契约测试未通过（PR 需人工评审）: {m}")


# ----------------------------- git worktree 隔离 -----------------------------
def branch_exists(branch):
    r = run(["git", "ls-remote", "--exit-code", "--heads", "origin", branch], check=False)
    return r.returncode == 0


def _worktree_dir(issue_number):
    return os.path.join(tempfile.gettempdir(), f"nasdx_aa_{issue_number}")


def prepare_worktree(issue_number):
    """从 origin/master 起一个独立 worktree，主工作树的未提交改动不受影响。"""
    branch = f"fix/auto-align-{issue_number}"
    wt = _worktree_dir(issue_number)
    run(["git", "worktree", "remove", wt, "--force"], check=False)
    run(["git", "branch", "-D", branch], check=False)
    # 拉取最新远端 master 作为基底；失败则退回到本地 master
    r = run(["git", "fetch", "origin", "master"], check=False)
    base = "origin/master" if r.returncode == 0 else "master"
    run(["git", "worktree", "add", wt, "-b", branch, base], check=True)
    log(f"已创建隔离 worktree: {wt} (基底 {base})")
    return wt, branch


def cleanup_worktree(issue_number):
    wt = _worktree_dir(issue_number)
    branch = f"fix/auto-align-{issue_number}"
    run(["git", "worktree", "remove", wt, "--force"], check=False)
    run(["git", "branch", "-D", branch], check=False)


# ----------------------------- git / PR / issue -----------------------------
def git_commit_push(issue_number, key, title, files, wt_dir):
    branch = f"fix/auto-align-{issue_number}"
    if branch_exists(branch):
        raise RuntimeError(f"分支 {branch} 已存在，跳过以免重复实现")
    run(["git", "add", "--", *files], cwd=wt_dir)
    # 防止误带受保护/构建产物
    run(["git", "reset", "HEAD", ".workbuddy", ".audit_state.json"], cwd=wt_dir, check=False)
    msg = f"auto-align({issue_number}): [{key}] {title}"
    run(["git", "commit", "-m", msg], cwd=wt_dir)
    run(["git", "push", "-u", "origin", branch], cwd=wt_dir)
    return branch


def open_pr(issue_number, key, title, branch):
    pr_title = f"Auto-align #{issue_number} [{key}] {title}"
    body = (
        f"本 PR 由 4 小时定时任务依据 miaoousc.xyz 逆向对齐方案**自动生成**，"
        f"用于修复 #{issue_number}（对齐键 [{key}]）。\n\n"
        f"Closes #{issue_number}\n\n"
        f"⚠️ 自动生成代码，**需人工评审后再合并**。门禁已通过语法编译；"
        f"契约测试与业务正确性请在评审中确认。\n\n"
        f"关联键：[{key}]  |  分支策略：fix/auto-align-{issue_number} → master"
    )
    tmp = os.path.join(ROOT, "_pr_body.tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(body)
    out = gh("pr", "create", "--repo", REPO, "--base", "master", "--head", branch,
             "--title", pr_title, "--body-file", tmp)
    os.remove(tmp)
    return out  # gh pr create 输出 PR URL


def close_issue(issue_number, pr_url):
    comment = (
        f"自动化修复已提交并开 PR：{pr_url}\n"
        f"本 issue 由 4 小时定时任务依据对齐方案自动实现并关闭。"
        f"PR 需人工评审合并，请审核代码与契约测试后再合入 master。"
    )
    gh("issue", "comment", str(issue_number), "--repo", REPO, "--body", comment)
    gh("issue", "close", str(issue_number), "--repo", REPO)
    log(f"已关闭 issue #{issue_number}，关联 PR: {pr_url}")


def post_failure(issue_number, err):
    comment = (
        f"⚠️ 自动修复未能完成：{str(err)[:500]}\n"
        f"本 issue 保持开放，下一轮定时任务将重试。如属设计外情况请手动处理。"
    )
    gh("issue", "comment", str(issue_number), "--repo", REPO, "--body", comment)
    log(f"已在 issue #{issue_number} 贴失败说明")


# ----------------------------- 主流程 -----------------------------
SYSTEM_PROMPT = """你是 NASDX（A股量化研究项目）的高级 Python 工程师。你的任务：根据给定 GitHub issue 与参考材料，生成**可直接落盘**的实现代码。

硬性规则：
1. 只产出代码，不修改项目既有内核（backtest/anti_overfit、decision_*、portfolio_*、intraday_copilot、evidence 等保持不动）。
2. 不硬编码任何 API key / token；凭据一律走环境变量或用户配置。
3. 严守「零标的」合规红线：只呈现客观数据（行情、估值分位、资金流、资讯），绝不给出买卖推荐、预测、个股排名或选股建议。
4. 新增模块优先放在 `nasdx/` 下；测试放在 `tests/`，文件名 `test_<模块>_contracts.py`，不联网（用 mock/fixture）。
5. 代码须通过 `python -m py_compile`，且尽量可被现有项目 import（复用 quant/data.py 的请求/缓存范式、nasdx/llm.py 的 LLM 层）。
6. 输出**仅**一个 JSON 对象，不要任何额外说明文字：
{
  "summary": "本次实现的简短中文说明",
  "files": [{"path": "nasdx/xxx.py", "content": "<完整文件内容>"}],
  "tests": [{"path": "tests/test_xxx_contracts.py", "content": "<完整文件内容>"}]
}
files 与 tests 至少其一非空；新增文件必须给完整内容（不要 diff）。"""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--issue", type=int, default=None, help="指定处理某个 issue 号")
    ap.add_argument("--max-issues", type=int, default=1, help="本轮最多处理几个 issue")
    ap.add_argument("--dry-run", action="store_true", help="只生成不提交/不关 issue")
    args = ap.parse_args()

    base_url, api_key, model = resolve_llm_creds()
    if not (base_url and api_key):
        log("缺少 LLM 凭据（LLM_* / NASDX_* 环境变量或 config.toml [llm]），无法生成代码。")
        sys.exit(2)

    issues = get_open_issues()
    if args.issue:
        issues = [i for i in issues if i["number"] == args.issue]
    issues = sort_issues(issues)
    if not issues:
        log("没有开放的 auto-align issue，退出。")
        return

    processed = 0
    for issue in issues:
        if processed >= args.max_issues:
            break
        num = issue["number"]
        title = issue["title"]
        key = key_of(title)
        log(f"处理 issue #{num} [{key}] {title}")
        try:
            if branch_exists(f"fix/auto-align-{num}"):
                log(f"分支已存在，跳过 #{num}")
                continue
            wt, branch = prepare_worktree(num)
            try:
                context = build_context(issue)
                user_prompt = (
                    f"# Issue 标题\n{title}\n\n"
                    f"# Issue 正文\n{issue.get('body','')}\n\n"
                    f"# 参考上下文（对齐方案 + 逆向源码）\n{context}\n\n"
                    f"请依据以上内容实现该 issue，遵守 SYSTEM 中的全部规则，"
                    f"输出仅 JSON（files/tests）。"
                )
                result = call_llm(SYSTEM_PROMPT, user_prompt, model, base_url, api_key)
                files = result.get("files", []) or []
                tests = result.get("tests", []) or []
                if not files and not tests:
                    raise ValueError("LLM 未返回任何文件")
                all_items = files + tests
                validate_paths(all_items)
                written = apply_files(all_items, wt)
                compile_gate(written, wt)
                run_contract_tests(written, wt)
                if args.dry_run:
                    log(f"[dry-run] 已在隔离 worktree 生成 {len(written)} 个文件，"
                        f"未提交/未关 issue，正在清理 worktree。")
                    cleanup_worktree(num)
                    continue
                git_commit_push(num, key, title, written, wt)
                pr_url = open_pr(num, key, title, branch)
                close_issue(num, pr_url)
                cleanup_worktree(num)
                processed += 1
            except Exception as e:
                # 异常时丢弃 worktree（连同其中生成的文件），主工作树不受影响
                log(f"处理 #{num} 失败: {e}")
                if not args.dry_run:
                    post_failure(num, e)
                else:
                    log("[dry-run] 不向 GitHub 写任何评论/状态变更")
                cleanup_worktree(num)
        except Exception as e:
            log(f"准备 worktree 失败 #{num}: {e}")

    log(f"本轮完成 {processed} 个 issue。")


if __name__ == "__main__":
    main()
