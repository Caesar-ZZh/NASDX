# NASDX × miaoousc.xyz 自动对齐 · 定时任务设计说明

> 配套交付：`scripts/auto_align_worker.py`（LLM 驱动修复 worker）、
> `.github/workflows/auto_align.yml`（每 4 小时定时工作流）、
> `MIAOOUSC_NASDX_对齐方案.md`（对齐方案，作上下文）、
> 15 个 `auto-align` issue（#92–#106）。

## 1. 触发周期（Trigger Period）

| 项目 | 设定 |
|---|---|
| 机制 | GitHub Actions `schedule` cron |
| 表达式 | `0 */4 * * *`（UTC 整点，每 4 小时一次） |
| 对应北京时间 | 08:00 / 12:00 / 16:00 / 20:00 / 00:00 / 04:00 |
| 手动触发 | Actions 页面 `Run workflow`，可填 `issue` 号只处理指定项 |
| 并发 | `concurrency.group: auto-align`，串行执行，避免重复开 PR |

每轮**只处理 1 个 issue**（按优先级 P1>P2>P3、再按编号升序），开一个独立 PR，下一轮再处理下一个，保证 PR 粒度可评审、不互相冲突。

## 2. 代码提交分支策略（Branch Strategy）

```
master  ──(切出)──>  fix/auto-align-<issue号>  ──(PR)──>  master
```

- **源分支**：每次从最新 `master` 切出 `fix/auto-align-<N>`（如 `fix/auto-align-92`）。
- **提交内容**：仅包含本次 LLM 生成的受控新文件（`nasdx/`、`tests/` 等），**绝不**触碰
  `CONTEXT.md` / `quant/data.py` / `ths_bridge.py` / `.workbuddy/` / `.audit_state.json`。
- **门禁**：`python -m py_compile` 必须通过（语法）；对应契约测试尽力运行，失败仅告警。
- **合入方式**：worker **只开 PR、不自动合入 master**，需人工评审合并（PR 正文标注 `Closes #<N>`）。
- **重入保护**：若 `fix/auto-align-<N>` 分支已存在，跳过该 issue，避免重复实现。

## 3. issue 与修复代码的关联方式（Association）

- **对齐键**：issue 标题后缀 `[R1]…[R10]`、`[N1]…[N5]` 即 alignment key；正文头含
  `**对齐键**: R1 | **优先级**: P1`，供 worker 解析排序与上下文检索。
- **分支名**：`fix/auto-align-<issue号>`（含 issue 号，唯一可溯）。
- **commit 信息**：`auto-align(<N>): [<键>] <标题>`。
- **PR 标题**：`Auto-align #<N> [<键>] <标题>`；PR 正文 `Closes #<N>` 自动关联并关闭 issue。
- **闭环**：PR 开后，worker 在 issue 评论贴回 PR 链接，再 `gh issue close` 关闭该 issue。
- **上下文来源**：worker 拼接 `MIAOOUSC_NASDX_对齐方案.md`（在仓库内时）+ 逆向源码
  `_reverse_miaoou/stock-analysis-base/`（CI 中缺失则优雅降级到 issue 正文，正文已含
  「参考实现/接入方式/验收」）。

## 4. 依赖与配置（需在仓库 Settings > Secrets 配置）

| Secret | 说明 |
|---|---|
| `LLM_BASE_URL` | OpenAI 兼容端点（如 DeepSeek `https://api.deepseek.com/v1`） |
| `LLM_API_KEY` | LLM API key |
| `LLM_MODEL` | 模型名（默认 `deepseek-chat`） |

未配置这三个 secret 时，worker 直接退出（exit 2），不会误提交空 PR。

## 5. 守护红线（不可逾越）

- 零标的合规：只呈现客观数据，不推荐 / 不预测 / 不排名 / 不暴露个股推荐名单。
- 不硬编码任何 key / token（一律环境变量或用户配置）。
- 不改动既有内核：`backtest/anti_overfit`、`decision_*`、`portfolio_*`、`intraday_copilot`、`evidence`。
- 所有生成代码须通过语法编译；PR 必须经人工评审再合入。

## 6. 本地/WorkBuddy 手动运行

```bash
# 需先 export LLM_BASE_URL / LLM_API_KEY / LLM_MODEL
python scripts/auto_align_worker.py                 # 自动挑最高优先级
python scripts/auto_align_worker.py --issue 92       # 只处理 #92
python scripts/auto_align_worker.py --dry-run       # 只生成不提交
```

## 7. 回滚与止损

- worker 遇失败会在 issue 评论失败原因并**保持 issue 开放**（下一轮重试），且回滚工作区改动。
- PR 未通过评审可直接关闭/删除分支，issue 重新开放，不影响其它 issue。
- 任一 PR 出错均不污染 `master`（仅停留在 feature 分支等待人工裁决）。
