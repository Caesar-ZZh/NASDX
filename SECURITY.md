# 安全策略 / Security Policy

NASDX 是一个本地运行的 A 股量化分析工具。它不托管服务、不代持资金、不下单交易，
但它会在本机处理 **LLM API Key、持仓账本和决策记录**，这三类数据都属于敏感信息。

本文件说明：怎么报漏洞、密钥泄露怎么处置、仓库侧有哪些自动门禁。

---

## 1. 报告漏洞

**不要用公开 Issue 报告未公开的安全漏洞。**

| 渠道 | 用途 |
|---|---|
| [GitHub Security Advisory](https://github.com/Caesar-ZZh/NASDX/security/advisories/new) | 首选。私有、可协同修复、可申请 CVE |
| 仓库 owner 私信 | Advisory 不可用时的备用渠道 |

报告时请尽量包含：受影响文件/函数、复现步骤或 PoC、影响范围（本地读写 / 凭证泄露 / 远程触发）、
以及你验证过的版本或 commit SHA。

**请勿在报告中粘贴真实 API Key、账户号、完整持仓或对账截图。**
需要举例时请用脱敏值（例如 `sk-****`）。

响应节奏（尽力而为，非商业 SLA）：

| 阶段 | 目标 |
|---|---|
| 首次确认收到 | 3 个自然日内 |
| 影响评估与定级 | 7 个自然日内 |
| 高危修复发布 | 评估完成后 14 个自然日内 |
| 公开披露 | 修复合入默认分支后，与报告者协商时间 |

## 2. 支持范围

| 范围 | 是否受支持 |
|---|---|
| `master` 默认分支最新提交 | ✅ |
| 历史 tag / 旧 release | ❌ 请升级到最新 `master` |
| fork 仓库的自有改动 | ❌ 由 fork 维护者负责 |

**不属于安全漏洞**（会被关闭，请走普通 Issue）：投资建议不准、回测收益不及预期、
第三方数据源（AkShare / mootdx / 巨潮）自身返回错误数据、需要本机管理员权限才能触发的问题。

## 3. 密钥泄露响应流程

密钥一旦进入 Git 历史，**删除文件并不能让它失效**。按以下顺序处理，顺序不能颠倒：

1. **服务端吊销/轮换**（第一优先级，且不可省略）
   到对应 LLM 供应商后台立即吊销旧 Key 并生成新 Key。历史清理无法让已被复制的凭证失效。
2. **换用环境变量注入**
   新 Key 只通过 `NASDX_API_KEY` 环境变量或网页会话输入提供，**不写入任何被 Git 跟踪的文件**。
   参见 README「配置 LLM」。
3. **确认当前树干净**
   ```powershell
   python -B run_security_checks.py --skip-optional
   ```
4. **确认全历史干净**
   ```powershell
   python -B run_security_checks.py --skip-optional --history
   ```
   该命令扫描所有 ref 可达的 blob，覆盖「提交后又删掉」的情况。
5. **需要重写历史时**（破坏性操作，必须 owner 显式授权）
   用 `git filter-repo --replace-text` 或 BFG 清理，并提前协调所有远端分支、tag 和现有 clone。
   重写不能替代第 1 步。
6. **记录**
   在对应 Issue 说明「已吊销 + 日期」，不要粘贴任何 Key 片段或账单截图。

历史处置先例见 Issue #12。

## 4. 本仓库的自动门禁

| 门禁 | 位置 | 作用 |
|---|---|---|
| Secret scanning + Push protection | GitHub 仓库设置 | 拦截推送中的已知供应商凭证格式 |
| 自研多供应商扫描 | `nasdx/secret_scan.py`，`.github/workflows/security.yml` | 当前树 + `--history` 全历史，覆盖多家 LLM/云厂商 |
| gitleaks（固定版本 + SHA-256 校验） | `.github/workflows/security.yml` | 独立第二意见，豁免项按 finding 精确限定在 `.gitleaksignore` |
| CodeQL SAST | `.github/workflows/codeql.yml` | Python 代码缺陷扫描，PR / push / 每周定时 |
| Dependabot | `.github/dependabot.yml` + 仓库 Settings | 依赖与 Actions 漏洞告警和升级 PR |
| Windows 发布检查 | `.github/workflows/windows-desktop.yml` | 依赖锁、桌面发布 gate、交付契约 |
| `master` 分支 ruleset | GitHub 仓库设置，声明见 `.github/rulesets/master-baseline.json` | 禁止删除分支与 force-push |

`master-baseline.json` 里还写了两条**默认未启用**的规则（必需状态检查 + 至少 1 次 review）。
它们会让直推 `master` 被拒绝、所有变更必须走 PR，是否切换属于仓库 owner 的决定；
需要时把它们移入 `rules` 数组并重新导入即可。

所有 workflow 均显式声明 `permissions: contents: read`（CodeQL 额外需要 `security-events: write`），
且官方 Action 全部固定到 commit SHA。fork 发起的 PR 在 GitHub 侧默认拿不到 secrets 与写权限。

本地提交前自查：

```powershell
python -B run_security_checks.py --skip-optional
pre-commit run --all-files
```

## 5. 用户侧数据处置建议

| 数据 | 默认行为 | 控制方式 |
|---|---|---|
| LLM API Key | 只读环境变量，无硬编码默认值 | `NASDX_API_KEY` |
| 决策日志 | **默认不落盘**（opt-in），落盘时递归脱敏 + 轮转 | `NASDX_DECISION_LOG` |
| 决策记忆 | **默认不落盘**（opt-in），摘要脱敏 + 截断 + 条数淘汰 | `NASDX_MEMORY_ENABLED` |
| 持仓账本 | 本地 SQLite，不含账号字段，自由文本脱敏 | `nasdx_portfolio*.db`（已 gitignore） |
| 决策记录 | 本地 SQLite | `nasdx_decisions*.db`（已 gitignore） |

以上 `*.db` 均已在 `.gitignore` 中，**不要手动 `git add -f`**。
