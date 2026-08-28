# NASDX 代码库全面评审 — 2026-08-25

> 评审范围：当前 `master`（HEAD = `cebc9d4`，rebrand Vibe-Research→Cosmos）。
> 工作树干净，用户所述"多处修改与缺陷修复"均已提交。
> 方法：git 状态/分叉核对 + 编译 + 密钥扫描 + 重构残留扫描 + 实际跑测试（受管 venv，pytest 9.1.1 / py3.13）。

## 1. 综合结论

| 维度 | 评级 | 一句话 |
|---|---|---|
| 缺陷修复质量 | **A-** | 根因驱动、issue 闭环、配套契约测试、提交信息规范 |
| 测试可运行性 | **C** | 重构后契约测试大面积坏（路径残留），且 CI 不跑 pytest，回归被门禁掩盖 |
| 代码可维护性 | **B-** | 分层清晰，但 6 个文件 >1000 行，变更风险高 |
| 安全/合规 | **A** | 无硬编码密钥，Dependabot/CodeQL/ruleset/SECURITY.md 齐备 |
| 集成健康 | **B** | 本地 master 落后 origin 3 个提交，可干净快进 |

**总评：B-。** 工程纪律在"改 bug"这一层很强，但"仓库重组"这一步留下了被 CI 掩盖的测试回归，是当前最大的技术债。

## 2. 缺陷修复合理性（重点表扬，这是好代码）

抽审了 4 个代表性修复，均为**教科书级根因修复**而非打补丁：

| Issue | 根因 | 修复 | 测试 |
|---|---|---|---|
| #84 盘中新鲜度 | `build_intraday_snapshot` 只判 `age>stale`，未来时间戳算成负龄被误判"已验证" | 4 态新鲜度模型(verified/stale/invalid/unknown)+失败降级+去掉"15:00 猜测" | +4 测试类，定向 200/200 |
| #87 空批次串行回退 | 空批次结果触发重复串行回退，上游故障时把整个池子重新串行化 | `resolve_batch_history` 三态契约，区分"从未执行"与"已穷尽恢复" | 新增 350 行契约 |
| #73 ETF50 批处理 | 量化扫描走单股串行接口 | 迁移到批量 OHLCV API | +464 行契约 |
| #34 刷新/Stocks60 | 扫描在主线程同步等待 | 路由到批量行情层 | +541 行契约 |

共性优点：提交信息写清 root cause / fix / tests / known limitations；每个 fix 配契约测试。这是项目最值得保留的资产。

## 3. 关键回归：仓库重组破坏了契约测试（P0）

提交 `f0585e2`（整理仓库结构 — 脚本入 `scripts/`）把 `scan_stocks_full.py`、`fetch_stock_data.py`、`scan_etf50.py`、`run_final_audit.py`、`run_security_checks.py`、`selector_page.py`、`quant_page.py` 等移入 `scripts/`。
后续 `0450f58`（同步整理后路径）只修了 README/ps1，**漏掉了测试文件里的硬编码根路径**。

实测失败根因全部是：
```
FileNotFoundError: ...\NASDX\scan_stocks_full.py   # 实际在 scripts/ 下
```

受影响测试文件（按 `ROOT / "X.py"` 读已迁移脚本）：

| 测试文件 | 引用但已迁移的脚本 |
|---|---|
| test_scan_throughput_contracts.py | fetch_stock_data / scan_stocks_full / scan_etf50 |
| test_batch_history_resolution_contracts.py | scan_stocks_full |
| test_cloud_sync_contracts.py | scan_and_sync |
| test_delivery_assets_contracts.py | run_final_audit |
| test_desktop_completion_audit_contracts.py | run_desktop_completion_audit |
| test_desktop_doctor_contracts.py | run_desktop_doctor |
| test_desktop_launcher_contracts.py | scan_etf50 / scan_stocks_full / run_investment_workflow |
| test_desktop_packaging_contracts.py | run_final_audit |
| test_desktop_release_check_contracts.py | run_desktop_release_check |
| test_desktop_release_evidence_contracts.py | run_desktop_release_evidence |
| test_secret_scan_contracts.py | run_security_checks / run_final_audit |
| test_security_checks_contracts.py | run_security_checks |
| test_streamlit_state_contracts.py | selector_page / quant_page |

**为什么没被发现？** CI 不跑 `pytest`：
- `final-audit.yml` 跑的是 `python run_final_audit.py`（自带内部检查，不调 pytest）→ 门禁绿 ≠ 测试绿。
- `windows-desktop.yml` 仅跑 2 个契约测试（`test_delivery_assets_contracts`、`test_desktop_release_check_contracts`），而这 2 个现在也因路径坏了。
- 其余 workflow（auto_align/codeql/security）与 pytest 无关。

结论：作者提交 #34/#73/#84/#87 时测试通过（彼时脚本还在根），重组之后**整套契约测试已不可在干净 checkout 下跑绿**，而 CI 看不见。

## 4. 代码质量与可维护性

- **编译**：`nasdx/ quant/ scripts/ server/` 全过，`compileall` 零错误。
- **巨型文件**（变更风险高，建议拆分）：
  - `scripts/run_final_audit.py` 1362 行
  - `nasdx/portfolio_store.py` 1331 行
  - `nasdx/intraday_decision.py` 1181 行
  - `nasdx/evidence.py` 1026 行
  - `quant/backtest.py` 924 行、`nasdx/portfolio.py` 873 行
  - `server/stock/astock.py` 816 / `base_app.py` 682（前端融合带入，属上游 base 代码，改动优先级低）
- **PYTHONPATH 未声明**（P1）：`pyproject.toml` 的 `[tool.pytest.ini_options]` 无 `pythonpath`，也无 `conftest.py`。即使 import 类测试也需手动 `PYTHONPATH=scripts` 才能跑（实测注入后 import-smoke + architecture 6/6 通过）。

## 5. 集成风险

- `git rev-list origin/master...HEAD` = `3 0`：本地 master 落后 origin **3 个提交**（`b2a6547` #129 市场看板收口、`fb8c013` #126 回测超时/图例、`e097972` #125 Cosmos 0.3.0 响应性）。
- `HEAD` 是 `origin/master` 的祖先 → **可干净快进，无冲突**。建议在推送新工作前先 `git pull --ff-only` 同步。
- 存在陈旧 worktree `.worktrees/cosmos-goal-2026-08-23`（远程分支已删 `gone`），以及未跟踪的 `.validation/` 快照副本与 `docs/codex-goal-2026-08-23.md`，属清理项（低风险）。

## 6. 改进建议（按优先级）

| 优先级 | 动作 | 说明 |
|---|---|---|
| **P0** | 修复测试路径引用 | 受影响文件把 `ROOT / "X.py"` 改为 `ROOT / "scripts" / "X.py"`（或定义 `SCRIPTS = ROOT/"scripts"`）。约 13 个文件，机械替换，低风险。 |
| **P0** | 把 `pytest` 接入 CI | 新增/修改 workflow 跑 `pytest tests/`，让契约测试回归可被门禁捕获（否则测试资产持续腐化）。 |
| **P1** | `pyproject` 加 `pythonpath = ["scripts", "."]` | 让 `pytest` 开箱即跑，消除"裸跑失败"的可用性坑。 |
| **P1** | `git pull --ff-only` 同步 origin | 合入 Cosmos 0.3.0 三项修复，避免长期分叉。 |
| **P2** | 拆分巨型文件 | 优先 `run_final_audit.py` / `portfolio_store.py` / `intraday_decision.py`，抽 service/helper 模块降低评审与变更风险。 |
| **P2** | 清理 stale worktree 与 `.validation/` 快照 | `git worktree remove` + 删除未跟踪快照副本。 |

## 7. 实测记录

- 编译：`python -m compileall nasdx quant scripts server` → exit 0。
- 密钥：`grep` 全仓硬编码 key/endpoint → 无命中。
- rebrand 残留：`Vibe-Research` 全仓 0 命中。
- 测试（受管 venv + `PYTHONPATH=scripts`，仅装 pandas/numpy/requests/openpyxl，未装 akshare）：
  - import-smoke + architecture：**6/6 通过**
  - intraday(#84) **41/41 通过**；etf50(#73) **18/18 通过**；backtest_factor 1 通过；analysis_cache 进行中
  - scan_throughput(#34)：**6 失败**（全部 `FileNotFoundError` 旧路径）
  - batch_history(#87)：**13 报错**（全部 `FileNotFoundError` 旧路径）
  - 失败/报错 100% 由"测试引用根级已迁移脚本"导致，与修复逻辑无关。

> 注：未装 akshare，涉及实时数据/网络的部分测试未覆盖；上述失败均非网络/依赖缺失，而是路径硬编码。
