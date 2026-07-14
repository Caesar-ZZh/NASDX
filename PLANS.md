# NASDX Windows Desktop Application Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:subagent-driven-development` or `superpowers:executing-plans` to implement this plan task-by-task after the user explicitly asks to proceed. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn the existing NASDX A-share quant analysis and multi-agent investment research system into a Windows desktop application without rewriting the current Streamlit app or removing existing CLI workflows.

**Architecture:** Keep `app.py` and the existing CLI scripts as the canonical application surface. Add a thin Windows desktop launcher/shell that starts the existing Streamlit server locally, opens it in a desktop WebView or browser fallback, manages ports/process lifecycle, and packages the current Python runtime predictably.

**Tech Stack:** Python, Streamlit, AkShare, pandas/numpy, OpenAI-compatible LLM client, SQLite, Windows PowerShell/batch, optional `pywebview`/WebView2, portable venv packaging first, installer packaging later.

---

## Phase 1 Dependency Plan

Do not install every possible desktop/product tool at once. Phase 1 adds only the low-risk development baseline: `pytest`, `ruff`, and `pre-commit`. Other tools stay evaluated and are introduced only when the phase actually needs them; security scanners are wired through an optional wrapper before becoming required dependencies.

| Group | Tool | Plan decision | Why |
|---|---|---|---|
| Development quality | `ruff` | Add in Phase 1 | Fast lint baseline for syntax-level blockers without style churn; expand rules after existing scripts are cleaned deliberately. |
| Development quality | `pre-commit` | Add in Phase 1 as optional local hooks | Gives contributors one command to run ruff and focused tests before commits; uses local hooks to avoid pinning remote hook repos. |
| Testing | `pytest` | Add in Phase 1 | Runs existing `unittest` tests and new pytest-compatible tests with better filtering and reporting. |
| Testing | GitHub Actions | Added in Milestone 8.10 | Runs the local desktop release gate on `windows-latest` without requiring market snapshots or installer execution. |
| Streamlit UI enhancement | `streamlit-extras` | Defer | UI polish only; not needed for desktop MVP. |
| Streamlit UI enhancement | `streamlit-option-menu` | Defer | Current sidebar navigation works and is tested through app markers. |
| Streamlit UI enhancement | `streamlit-aggrid` | Defer | Table UX may help later, but it adds frontend dependency risk. |
| Financial charts | `plotly` | Defer | Good interactive chart candidate, but not required for Phase 1 hygiene. |
| Financial charts | `streamlit-echarts` / `pyecharts` | Defer | Potential later dashboard enhancement; avoid adding now. |
| Financial charts | `mplfinance` | Defer | Useful for candlestick views after chart requirements are clear. |
| Desktop launcher | `pywebview` | Defer to Phase 3 | Preferred first desktop wrapper candidate, but only after local launcher boundary is ready. |
| Windows packaging | `pyinstaller` | Optional launcher-only path added in Milestone 8.26 | Use only on packaging machines to freeze `desktop\exe_launcher.py`; do not add to runtime deps or bundle the analytics app into one file. |
| Config/security | `pydantic-settings` | Defer to Phase 4 | Good fit for validated local settings once config layer is introduced. |
| Config/security | `python-dotenv` | Defer to Phase 4 | Can help local env loading, but `.env` must remain untracked. |
| Config/security | `platformdirs` | Defer to Phase 4 | Good fit for Windows user config/cache paths after launcher exists. |
| Security checks | `pip-audit` | Optional via `run_security_checks.py` | Useful for dependency vulnerability scanning, but not a default dependency because it can add network/runtime friction. |
| Security checks | `bandit` | Optional via `run_security_checks.py` | Useful Python security lint; keep optional until findings can be triaged without blocking desktop work. |
| Security checks | `detect-secrets` | Optional via `run_security_checks.py` | Useful full secret scanner; default gate uses a lightweight local key scan first to avoid noisy baselines. |

Phase 1 files:

- `requirements-dev.txt`: development install entry, chaining `requirements_nasdx.txt`.
- `pyproject.toml`: conservative pytest and ruff config.
- `.pre-commit-config.yaml`: optional local hooks for ruff and focused core tests.
- `requirements_nasdx.txt`: keep the already-used `mootdx` fallback declared and pin `tenacity<9` for compatibility.

---

## 1. Current Repository Structure

| Path | Role | Notes for desktop plan |
|---|---|---|
| `app.py` | Main Streamlit UI and route controller | Do not rewrite. Keep as the web UI loaded by desktop shell. |
| `position_page.py` | Holding/position advisor UI used by `quant_page.py` | Preserve module boundary. |
| `quant_page.py` | Quant engine Streamlit page | Hosts tabs for position advisor, ETF quant, factor analysis, backtest, optimization, overfit diagnosis, confidence training. |
| `confidence_page.py` | Confidence training Streamlit page | Produces/reads `models/signal_confidence.json`; generated model file should not be treated as source. |
| `ths_page.py` / `ths_bridge.py` | Tonghuashun connection, realtime quote, dry-run/live trading bridge | Desktop shell must default to safe launch; never auto-trigger live trading. |
| `nasdx/` | Core research package | Multi-agent analysis, portfolio roadmap, final brief, data quality, history, reports. |
| `nasdx/agents/` | Expert agents | `technical`, `fund_flow`, `risk`, `sector`, `chokepoint`, `synthesis`. |
| `nasdx/environments/` | Research and battle environments | Research phase supports concurrent agent execution. |
| `quant/` | Quant/factor/backtest subsystem | Keep algorithm modules reusable from Streamlit and CLI. |
| `tests/` | Contract tests | Use as desktop regression base; add launcher/packaging contract tests later. |
| `docs/` | Project docs | Update only when desktop run mode or packaging becomes real. |
| `static/style.css` | Streamlit CSS | Preserve visual style for desktop shell. |
| `.streamlit/config.toml` | Local Streamlit server/theme config | Treat as local runtime config; do not commit real local secrets/config. |
| `requirements_nasdx.txt` | Current install manifest | Keep versionable; current audit checks it is not ignored. |
| `config.example.toml` | Safe sample config | Preserve as template. |
| `config.toml`, `.env` | Local secret/config files | Must not be committed. |
| `reports/` | Generated Markdown/JSON/HTML/ZIP research outputs | Must not be committed. |
| `models/` | Generated training artifacts | Do not commit generated model outputs such as `signal_confidence.json`. |
| `.claude/agents/` | Local subagent templates | Useful for workflow, not part of desktop runtime. |
| `启动网页.bat` | Current Windows Streamlit launcher | Preserve; desktop launcher should coexist with it. |

## 2. Existing Entry Points and Commands

| Entry point | Command | Existing purpose |
|---|---|---|
| Streamlit UI | `python -m streamlit run app.py --server.port 8501 --server.address 127.0.0.1` | Main web app. |
| Windows batch UI | `启动网页.bat` | Starts Streamlit on `127.0.0.1:8501`. |
| Data refresh | `python fetch_stock_data.py` | Writes `stock_data_YYYYMMDD.json`. |
| ETF scan | `python scan_etf50.py` | Writes ETF50 reports under `reports/`. |
| Stock scan | `python scan_stocks_full.py` | Writes stocks60 reports under `reports/`. |
| Single-name analysis | `python run_analysis.py 603501 --mode auto --risk-profile balanced` | LLM or rules fallback single stock report. |
| Rules-only analysis | `python run_analysis.py 603501 --mode rules --risk-profile balanced` | No API key path. |
| Full research workflow | `python run_investment_workflow.py 603501 --workflow full --rounds 1 --risk-profile conservative` | Refresh, scans, analysis, final brief. |
| Portfolio plan | `python run_portfolio_plan.py --risk-profile balanced` | Builds portfolio roadmap from local artifacts. |
| Investment brief | `python run_investment_brief.py --risk-profile balanced` | Builds final investment brief. |
| Position sizing | `python run_position_sizing.py --capital 100000 --current-etf 10000 --current-stock 5000` | Temporary sizing calculation; no account data persistence. |
| Recommendation tracker | `python run_recommendation_tracker.py --print` | Compares current and prior briefs. |
| Recommendation review | `python run_recommendation_review.py --print` | Reviews prior recommendations using current data. |
| Account review | `python run_account_review.py --ledger trades.csv --capital 100000 --print` | Derived account review from user CSV. |
| Snapshot export | `python run_review_snapshot.py --risk-profile balanced` | Exports review ZIP package. |
| Final audit | `python -B run_final_audit.py` | Delivery contract audit. |
| Readiness aggregate | `python -B run_product_readiness.py` | Runs unit tests plus final audit. |
| Optional LLM smoke | `python -B run_product_readiness.py --llm-smoke` | Requires `NASDX_API_KEY` in environment. |
| Deploy helper | `python scan_and_sync.py` | Git deploy branch sync; do not run from desktop app automatically. |

## 3. Existing Streamlit UI Structure

| UI area | Current implementation | Desktop implication |
|---|---|---|
| Navigation | `app.py` sidebar buttons plus `st.query_params["page"]` | Desktop shell should load URLs like `/?page=plan` without changing routing. |
| Pages | `home`, `plan`, `etf50`, `stocks60`, `deep`, `quant`, `ths` | Preserve page keys and URLs. |
| Home | Summary, shortcuts, latest ETF scan, recent reports | Desktop should open to existing home by default. |
| Plan | Portfolio route, final brief, tracker, recommendation review, position sizing, account review upload, snapshot export | High-value desktop default candidate after MVP, but do not alter first. |
| ETF50 | Runs `scan_etf50.py` in background thread and reloads cached report | Keep subprocess behavior. |
| Stocks60 | Runs `scan_stocks_full.py` synchronously with timeout | Preserve CLI script call. |
| Deep analysis | Builds env-only LLM config, spawns `run_investment_workflow.py`, reads unique log file | Important safety boundary: API config stays in child env, not global files. |
| Quant | Delegates to `quant_page.render_quant_page(st)` | Existing tabbed quant workflow should stay inside Streamlit. |
| Quant tabs | Holding advisor, ETF50 full quant, factor analysis, VnPy backtest, parameter optimization, overfit diagnosis, confidence training | Desktop shell should not recreate these controls. |
| THS | Delegates to `ths_page.render_ths_page(st, ROOT)` | Must keep live trading behind existing explicit UI actions. |
| Background state | `RUNNING_TASKS` process table, `task_id` in `session_state` | Preserve; tests assert threads are not stored directly in `session_state`. |
| Secrets | Sidebar API key/base/model are kept in session and passed to child process env | Do not write API keys to `config.toml`, `.streamlit/config.toml`, logs, or reports. |

## 4. Existing Data Layer

| Layer | Files | Contract |
|---|---|---|
| Market data refresh | `fetch_stock_data.py` | Produces `stock_data_YYYYMMDD.json` with `sectors`, `stocks`, `etfs`, `indicators`, fund-flow fields. |
| A-share K-line fallback | `nasdx/market_sources.py` | Tries Tencent, Sina, Eastmoney and normalizes to Chinese columns such as `日期`, `收盘`, `成交量`, `涨跌幅`. |
| Research data loading | `nasdx/data_loader.py` | Loads latest `stock_data_*.json`, finds stock/ETF by code, formats indicators/fund flow/K-line summaries. |
| Quant data loading | `quant/data.py` | Returns English OHLCV DataFrame columns for quant modules; AkShare first, mootdx fallback. |
| Data quality gates | `nasdx/data_quality.py` | Used by reports, portfolio, final brief, and audits to cap/stop actions when data is stale or low coverage. |
| Reports directory | `reports/*.json`, `reports/*.md`, `reports/*.html`, `reports/snapshots/*.zip` | Runtime artifacts only; desktop app should read/write here by default but packaging must exclude them. |
| SQLite history | `nasdx/history_store.py`, `nasdx_history.db` | Stores artifact/report/scan/pool history. DB path can be overridden with `NASDX_HISTORY_DB`. |
| Account CSV | `nasdx/account_review.py` | Parses uploaded or provided CSV; derived review can be saved, raw ledger should not be committed. |
| Config | environment variables, `config.example.toml` | Real secrets must stay outside repo. |

## 5. Existing Quant, Backtest, and Factor Modules

| Module | Purpose | Preserve/refactor stance |
|---|---|---|
| `quant/data.py` | Unified OHLCV fetch with retry and fallback | Preserve; do not duplicate in desktop shell. |
| `quant/factors.py` | Alpha158-like factor calculation and ranking | Preserve factor contract. |
| `quant/backtest.py` | Lightweight pandas backtester plus momentum/mean-reversion/factor-rank strategies | Preserve; desktop shell only invokes through UI/CLI. |
| `quant/etf50_quant.py` | Full ETF50 quant analysis, factor score, backtest, JSON output | Preserve delayed imports and output path behavior. |
| `quant/portfolio.py` | Mean-variance, risk parity, equal-weight, portfolio metrics | Preserve as algorithm module. |
| `quant/signal_engine.py` | Technical/factor/trend/volume/AI signal aggregation | Preserve signal schema. |
| `quant/anti_overfit.py` | Walk-forward, IC/ICIR, robustness, SignalVoter | Preserve for confidence and overfit workflows. |
| `quant/confidence_trainer.py` | Historical signal calibration, writes model artifact | Preserve logic; later isolate generated model path if packaging needs it. |
| `quant/position_advisor.py` | Holding analysis, realtime/history data, factor/risk/advice | Preserve; desktop should not auto-run without user action. |
| `quant/vnpy_bridge.py`, `quant/rl_strategy.py`, `quant/ml_model.py` | Optional advanced integrations | Treat as optional dependencies in packaging. |

## 6. Existing Generated Files and Files That Must Not Be Committed

| Pattern/path | Why not commit | Current status or action |
|---|---|---|
| `config.toml`, `.env`, `.streamlit/config.toml` | Local config and possible secrets | Keep ignored. Use `config.example.toml` for docs. |
| `reports/`, `reports/snapshots/` | Generated investment reports, JSON, HTML, ZIP | Keep ignored and excluded from packages by default. |
| `stock_data_*.json` | Daily market snapshots | Keep ignored. |
| `nasdx_history.db` | Local SQLite runtime history | Keep ignored. |
| `*_log*.txt`, `nasdx_log_*.txt`, `fetch_log.txt`, `streamlit.log` | Runtime logs may contain paths or API errors | Keep ignored. |
| `batch_etf_log.txt`, `etf50_quant_log.txt`, `nasdx_out.txt`, `pip_*.txt` | Generated logs/install captures | Keep ignored. |
| `models/signal_confidence.json` | Generated confidence-training model | Add ignore later if desktop work creates it. |
| `focus_ind.json`, `stocks.json.bak` | Runtime scratch/backup | Keep ignored. |
| `__pycache__/`, `*.pyc`, `*.pyo` | Python cache | Keep ignored. |
| `.claude/scheduled_tasks.lock`, `scheduled_tasks.lock` | Local automation lock | Keep ignored. |
| `dist/`, `build/`, `.venv/`, `venv/`, `*.egg-info/` | Packaging/build/runtime outputs | Keep ignored. |
| API keys in any file | Security risk | Never write real `sk-*` keys to repo, reports, logs, prompts, or config. |

`requirements_nasdx.txt` is intentionally versionable and should stay committed.

## 7. What Should Be Preserved

- Preserve `app.py` as the Streamlit entry point; no full rewrite.
- Preserve all current CLI scripts and their command-line arguments.
- Preserve report filenames and latest aliases such as `portfolio_plan_latest.json`, `investment_brief_latest.json`, `account_review_latest.json`.
- Preserve `reports/` as the runtime artifact directory unless a later compatibility layer adds an override.
- Preserve `nasdx_history.db` default behavior and `NASDX_HISTORY_DB` override.
- Preserve environment-variable based secrets: `NASDX_API_KEY`, `NASDX_BASE_URL`, `NASDX_MODEL`.
- Preserve rule-based fallback when no API key exists.
- Preserve current Streamlit page keys and `?page=` deep links.
- Preserve `RUNNING_TASKS`/`task_id` state boundary in `app.py`.
- Preserve final audit and unit-test workflow as the release gate.
- Preserve Tonghuashun live trading as explicit opt-in UI action only.

## 8. What Should Be Refactored Later

| Refactor | Why | Timing |
|---|---|---|
| Add desktop launcher module | Desktop app needs lifecycle, port, process, WebView/browser handling | First implementation milestone. |
| Add a small command builder for Streamlit launch | Avoid duplicating `streamlit run app.py` flags across `.bat`, launcher, tests | After launcher MVP. |
| Extract desktop-safe path helpers | Packaging may run from a bundled directory; source root and writable data root must be explicit | Before portable packaging. |
| Add generated-artifact ignore rules for `models/signal_confidence.json` and desktop build outputs | Prevent accidental commits | Packaging milestone. |
| Wrap root scripts that parse at import time, especially `run_analysis.py` | Improves tests and launcher reuse while preserving CLI behavior | Later, not in first desktop shell step. |
| Extract repeated Streamlit table rendering helpers from `app.py` | Makes UI easier to maintain | Later; no UI framework migration. |
| Separate optional THS/easytrader/pytdx features from required desktop startup | Avoid failed optional imports breaking app launch | Packaging hardening milestone. |
| Add desktop health endpoint or readiness probe | Launcher needs to know when Streamlit is ready | Launcher verification milestone. |

## 9. Recommended Windows Desktop Strategy

Use a thin desktop shell around the existing Streamlit application.

| Option | Verdict | Reason |
|---|---|---|
| Thin Python launcher + local Streamlit + WebView2/pywebview | Recommended | Minimal rewrite, keeps Python data/quant stack intact, fastest path to Windows desktop feel. |
| Existing `.bat` only | Keep as fallback | Works now, but lacks process lifecycle, port selection, desktop window, packaging discipline. |
| Electron/Tauri rewrite | Do not do now | Would force UI migration and IPC redesign; conflicts with no full rewrite. |
| Native Qt/PySide rewrite | Do not do now | Rebuilds Streamlit UI and table workflows; high risk. |
| Full PyInstaller one-file of all dependencies | Avoid for first package | Streamlit/AkShare/pandas/browser assets are heavy and brittle in one-file mode. |

Recommended launcher behavior:

- Find a free localhost port, defaulting to `8501` when available.
- Start `python -m streamlit run app.py --server.address 127.0.0.1 --server.port <port> --server.headless true`.
- Set `PYTHONIOENCODING=utf-8`.
- Pass through `NASDX_API_KEY`, `NASDX_BASE_URL`, `NASDX_MODEL`, `NASDX_HISTORY_DB` from the parent environment.
- Wait for `http://127.0.0.1:<port>` readiness.
- Open WebView2/pywebview when installed; fallback to the default browser.
- On desktop window close, terminate only the Streamlit child process it started.
- Never run scans, trading, deploy sync, or LLM smoke automatically at startup.

## 10. Recommended Packaging Strategy

| Stage | Package shape | Notes |
|---|---|---|
| Developer MVP | Source checkout + `.venv` + `desktop/launcher.py` | Fastest validation, no installer. |
| Portable Windows folder | `dist/NASDX-Desktop/` containing source, `.venv`, launcher exe/script, README, sample config | Best first user package. Easier than one-file. |
| Launcher executable | PyInstaller one-dir or one-file for launcher only | Launcher starts the portable venv Python; do not bundle all analytics into the exe at first. |
| Offline install support | Optional `wheelhouse/` built from `requirements_nasdx.txt` and `requirements_desktop.txt` | Useful because AkShare/pandas installs can be slow or network-sensitive. |
| Installer | Inno Setup or WiX after portable folder is stable | Add Start Menu shortcut, uninstall, WebView2 prerequisite note. |

Packaging exclusions:

- Exclude `reports/`, `stock_data_*.json`, `nasdx_history.db`, `*.log`, `*_log*.txt`, `pip_*.txt`, `config.toml`, `.env`, `.streamlit/config.toml`, `__pycache__/`, `.git/`.
- Include `config.example.toml`, `requirements_nasdx.txt`, `README.md`, `PLANS.md`, `static/style.css`, source `.py` files, `etf50_pool.json`, `stocks.json`.
- Decide later whether to include `.claude/agents/`; it is workflow documentation, not runtime.

## 11. Testing Strategy

| Layer | Command | Expected use |
|---|---|---|
| Unit contracts | `python -B -m unittest discover -s tests` | Run after each code milestone. |
| Final delivery audit | `python -B run_final_audit.py` | Main release gate; avoids `.pyc` write issues. |
| Readiness aggregate | `python -B run_product_readiness.py` | Unit tests plus final audit. |
| Workflow dry-run | `python -B run_investment_workflow.py 603501 --workflow analysis-only --dry-run` | Confirms CLI command construction without generating artifacts. |
| Streamlit startup | `python -m streamlit run app.py --server.port 8501 --server.address 127.0.0.1` | Manual smoke for current UI. |
| Desktop launcher dry-run | `python -B desktop\launcher.py --dry-run` | Add during desktop milestone. |
| Desktop launcher smoke | `python -B desktop\launcher.py --headless-smoke --timeout 30` | Add during desktop milestone; start/stop child process cleanly. |
| Packaging contract | `python -B -m unittest tests.test_desktop_packaging_contracts -v` | Add during packaging milestone. |
| Full package smoke | `dist\NASDX-Desktop\NASDX.exe --headless-smoke --timeout 30` | Add after portable packaging exists. |

Do not require `NASDX_API_KEY` for normal desktop release tests. Keep LLM smoke optional and explicitly skipped when the key is absent.

## 12. Step-by-Step Milestones

### Milestone 0: Repository Plan Only

**Goal:** Create the repository-aware desktop migration plan without changing application logic.

**Files likely to change:**

- `PLANS.md`

**Implementation steps:**

- [x] Read current root context files: `AGENTS.md`, `CONTEXT.md`, and `README.md`; no `lessons.md` or `ARCHITECTURE.md` exists in this checkout.
- [x] Inspect current root scripts, Streamlit pages, `nasdx/`, `quant/`, `tests/`, `.gitignore`, generated artifacts, and existing Windows launcher.
- [x] Write this plan to `PLANS.md`.

**Verification command:**

```powershell
Get-Content -Raw -Encoding UTF8 PLANS.md
git diff -- PLANS.md
```

**Rollback risk:** Low. Delete or revert `PLANS.md` only.

**What not to touch:** `app.py`, `nasdx/`, `quant/`, root CLI scripts, `.streamlit/config.toml`, `reports/`, `nasdx_history.db`, API config.

### Milestone 1: Desktop Boundary Audit

**Goal:** Confirm exact runtime assumptions before adding a launcher.

**Files likely to change:**

- `PLANS.md` only if findings require plan correction.
- No application source changes expected.

**Implementation steps:**

- [x] Run unit contracts to establish baseline.
- [x] Run final audit to establish baseline.
- [x] Start the existing Streamlit app manually and confirm `home`, `plan`, `deep`, `quant`, and `ths` routes load.
- [x] Record startup command, port, environment variables, and observed generated files.
- [x] Confirm worktree status shows no unexpected runtime artifacts.

**Audit evidence from 2026-06-23:**

- Baseline tests: `python -m pytest tests` passed with 24 tests.
- Lint baseline: `python -m ruff check --no-cache .` passed.
- Final audit: `python -B run_final_audit.py` passed with 21 checks and 0 failures.
- Streamlit command: `python -m streamlit run app.py --server.port 8501 --server.address 127.0.0.1 --server.headless true --browser.gatherUsageStats false`.
- Port: `127.0.0.1:8501` was free before launch.
- Routes checked by HTTP readiness: `/`, `/?page=plan`, `/?page=deep`, `/?page=quant`, `/?page=ths`; each returned HTTP 200.
- Log check: no `Traceback`, `Exception`, or `ModuleNotFoundError` marker in the Streamlit stdout/stderr logs.
- Runtime artifacts: Streamlit audit logs were written to the Windows temp directory, not the repository.
- Startup environment assumption for launcher: set `PYTHONIOENCODING=utf-8`; preserve parent `NASDX_API_KEY`, `NASDX_BASE_URL`, `NASDX_MODEL`, and `NASDX_HISTORY_DB` when present.

**Verification command:**

```powershell
python -B -m unittest discover -s tests
python -B run_final_audit.py
python -m streamlit run app.py --server.port 8501 --server.address 127.0.0.1 --server.headless true
```

**Rollback risk:** Low. This is mostly read-only plus local runtime artifacts.

**What not to touch:** Do not edit `app.py`; do not run `scan_and_sync.py`; do not commit generated reports/logs/config.

### Milestone 2: Add Thin Desktop Launcher MVP

**Goal:** Add a small launcher that starts existing Streamlit locally and opens it without changing UI logic.

**Files likely to change:**

- Create: `desktop/__init__.py`
- Create: `desktop/launcher.py`
- Create: `desktop/runtime.py`
- Create: `tests/test_desktop_launcher_contracts.py`
- Modify only if needed: `.gitignore`

**Implementation steps:**

- [x] Add `desktop/runtime.py` with helpers for project root detection, free-port selection, Streamlit command construction, readiness polling, and child shutdown.
- [x] Add `desktop/launcher.py` CLI flags: `--port`, `--host`, `--page`, `--browser`, `--no-browser`, `--dry-run`, `--headless-smoke`, `--timeout`.
- [x] Launch `app.py` by subprocess using `sys.executable -m streamlit run`, not by importing `app.py`.
- [x] Open `http://127.0.0.1:<port>/?page=<page>` in browser fallback.
- [x] Keep environment pass-through explicit and UTF-8 safe.
- [x] Add contract tests that assert the launcher command includes `app.py`, localhost binding, no report generation, no `config.toml` write, and no CLI deletion/removal.

**Audit evidence from 2026-06-23:**

- Contract tests: `python -B -m unittest tests.test_desktop_launcher_contracts -v` passed with 7 tests.
- Dry-run: `python -B desktop\launcher.py --dry-run --page plan` printed the Streamlit command and URL without starting services.
- Headless smoke: `python -B desktop\launcher.py --headless-smoke --timeout 30 --no-browser` started Streamlit, reached readiness on `http://127.0.0.1:8501/`, and stopped the child process.
- Safety boundary: launcher command only starts `app.py`; it does not call `scan_etf50.py`, `scan_stocks_full.py`, `run_investment_workflow.py`, `scan_and_sync.py`, or THS actions.
- Secrets boundary: launcher passes through parent environment keys and does not write `.env` or `config.toml`.

**Verification command:**

```powershell
python -B -m unittest tests.test_desktop_launcher_contracts -v
python -B desktop\launcher.py --dry-run --page plan
python -B desktop\launcher.py --headless-smoke --timeout 30 --no-browser
python -B run_final_audit.py
```

**Rollback risk:** Low to medium. New launcher files can be removed without affecting current app.

**What not to touch:** Do not rewrite `app.py`; do not migrate UI frameworks; do not remove `启动网页.bat` or any `run_*.py` script; do not write secrets.

### Milestone 3: Add Optional WebView Desktop Shell

**Goal:** Give the app a desktop-window feel while still serving the existing Streamlit UI.

**Files likely to change:**

- Create: `desktop/webview_shell.py`
- Modify: `desktop/launcher.py`
- Modify: `requirements_desktop.txt`
- Modify: `tests/test_desktop_launcher_contracts.py`

**Implementation steps:**

- [x] Add optional dependency `pywebview` to `requirements_desktop.txt`.
- [x] In `desktop/webview_shell.py`, open the launcher URL in a WebView window when `pywebview` and WebView2 are available.
- [x] Keep browser fallback as default-safe behavior when WebView fails.
- [x] Ensure closing the WebView terminates only the child Streamlit process.
- [x] Add tests that WebView import failure falls back without crashing.

**Audit evidence from 2026-06-23:**

- Optional dependency is isolated in `requirements_desktop.txt`; normal runtime and dev installs do not require `pywebview`.
- Launcher exposes `--webview` and `--window-title`; default behavior remains browser fallback.
- `desktop/webview_shell.py` returns `False` when WebView is unavailable, so `desktop/launcher.py` can keep the Streamlit child alive and fall back to browser.
- Contract tests: `python -B -m unittest tests.test_desktop_launcher_contracts -v` passed with 11 tests.
- Headless smoke remains independent of WebView: `python -B desktop\launcher.py --headless-smoke --timeout 30 --no-browser`.

**Verification command:**

```powershell
python -B -m unittest tests.test_desktop_launcher_contracts -v
python -B desktop\launcher.py --headless-smoke --timeout 30 --no-browser
python -B desktop\launcher.py --page plan
```

**Rollback risk:** Medium. WebView startup can vary by Windows machine; fallback must remain intact.

**What not to touch:** Do not change Streamlit page routing; do not change API key handling; do not auto-run THS live trading.

### Milestone 4: Desktop Runtime Path Hardening

**Goal:** Make desktop startup reliable from a portable folder while preserving current source-tree defaults.

**Files likely to change:**

- Create: `desktop/paths.py`
- Modify: `desktop/runtime.py`
- Modify only if necessary: `nasdx/history_store.py`
- Modify only if necessary: launcher tests

**Implementation steps:**

- [x] Add path resolver that distinguishes application root from writable runtime directory.
- [x] Keep current defaults when running from the repository.
- [x] Support `NASDX_HISTORY_DB` for packaged SQLite location instead of hardcoding the repo path.
- [x] If reports relocation is needed, add a compatibility env var in the launcher first; only touch data modules after proving the need.
- [x] Ensure packaged mode never writes generated files into the installed read-only program directory unless that directory is the chosen portable workspace.

**Audit evidence from 2026-06-23:**

- Added `desktop/paths.py` with app-root detection, source-checkout detection, runtime-dir resolution, and desktop environment construction.
- Source checkout default remains the repository root when `.git` exists.
- `NASDX_RUNTIME_DIR` overrides the writable runtime directory for portable/user-data scenarios.
- `NASDX_HISTORY_DB` defaults to `<runtime_dir>\nasdx_history.db` when the parent environment does not set it; an explicit parent value is preserved.
- `NASDX_REPORTS_DIR` is set as a compatibility variable for future report relocation, but current report structure is not changed.
- `nasdx/history_store.py` already honors `NASDX_HISTORY_DB`, so no business module change was required.
- `python -B desktop\launcher.py --dry-run --page plan` now prints non-secret runtime paths for auditability.

**Verification command:**

```powershell
python -B -m unittest discover -s tests
python -B run_final_audit.py
python -B desktop\launcher.py --headless-smoke --timeout 30 --no-browser
```

**Rollback risk:** Medium. Path changes can break report/history lookup if overdone.

**What not to touch:** Do not rename report files; do not move `reports/` by default; do not change JSON schemas.

### Milestone 4.5: Safe Local Desktop Configuration

**Goal:** Let the Windows desktop launcher read local user settings without writing secrets to Git-tracked files or changing Streamlit business logic.

**Files likely to change:**

- Create: `desktop/config.py`
- Modify: `desktop/paths.py`
- Modify: `desktop/launcher.py`
- Modify: `config.example.toml`
- Modify: `tests/test_desktop_launcher_contracts.py`
- Modify: `README.md`

**Implementation steps:**

- [x] Add a TOML reader for `%APPDATA%\NASDX\config.toml`, with `NASDX_CONFIG_FILE` override.
- [x] Map allowed `[llm]` fields to `NASDX_API_KEY`, `NASDX_BASE_URL`, and `NASDX_MODEL`.
- [x] Map allowed `[paths]` fields to `NASDX_RUNTIME_DIR`, `NASDX_HISTORY_DB`, and `NASDX_REPORTS_DIR`.
- [x] Preserve explicit parent environment variables over config-file values.
- [x] Keep config read-only: do not create or write `config.toml`.
- [x] Ignore placeholder API keys and do not print config values in `--dry-run`.
- [x] Keep `app.py` and CLI scripts unchanged; Streamlit still receives configuration through environment variables.

**Audit evidence from 2026-06-24:**

- Added `desktop/config.py` with TOML parsing, safe config discovery, placeholder filtering, base URL validation, and path expansion relative to the config file.
- `desktop/paths.py` now applies config values before runtime defaults while preserving parent environment values.
- `desktop/launcher.py --dry-run` reports `config_file`, `config_exists`, and `config_loaded_keys` only; it does not print API key values.
- `config.example.toml` no longer contains an `sk-` placeholder and documents `%APPDATA%\NASDX\config.toml` plus `NASDX_CONFIG_FILE`.
- Added launcher contract tests for missing config, explicit config, parent env priority, invalid config, placeholder API keys, and dry-run redaction.

**Verification command:**

```powershell
python -B -m unittest tests.test_desktop_launcher_contracts -v
python -B desktop\launcher.py --dry-run --page plan
python -B run_final_audit.py
```

**Rollback risk:** Low to medium. The launcher now reads config if present, but parent environment variables still win and no application logic is changed.

**What not to touch:** Do not write `config.toml`; do not read or print API key values in logs/dry-run; do not change `app.py` config widgets.

### Milestone 5: Packaging Skeleton and Ignore Rules

**Goal:** Add repeatable Windows packaging scripts without producing commit-worthy build artifacts.

**Files likely to change:**

- Create: `requirements_desktop.txt`
- Create: `packaging/windows/build_portable.ps1`
- Create: `packaging/windows/NASDX-Desktop.iss` or `packaging/windows/README.md`
- Create: `tests/test_desktop_packaging_contracts.py`
- Modify: `.gitignore`
- Modify: `README.md` only after packaging command is real

**Implementation steps:**

- [x] Add `requirements_desktop.txt` for launcher-only dependencies.
- [x] Add PowerShell build script that creates `dist/NASDX-Desktop/`, installs dependencies into a local venv, copies source files, and excludes generated/runtime files.
- [x] Add ignore rules for `dist/`, `build/`, `wheelhouse/`, generated desktop logs, and `models/signal_confidence.json`.
- [x] Add packaging tests that assert excluded files are not copied.
- [x] Keep generated package output out of git.

**Audit evidence from 2026-06-23:**

- Added `packaging/windows/build_portable.ps1` with allowlist copying for source/runtime files and explicit exclusion patterns for reports, market snapshots, local DB, secrets, logs, caches, and build output.
- Added `packaging/windows/README.md` documenting the portable-folder-first packaging strategy.
- Added `tests/test_desktop_packaging_contracts.py` to verify ignore rules and package contents with `-SkipDependencyInstall`.
- `.gitignore` now excludes `wheelhouse/`, generated desktop logs, `*.spec`, and `models/signal_confidence.json` while keeping dependency manifests versionable.
- Package launcher script `启动NASDX桌面.bat` is generated inside the package output and uses the existing desktop launcher.
- Final audit ignores `dist/`, `build/`, `wheelhouse/`, and cache directories so generated package copies do not pollute source-level checks.

**Verification command:**

```powershell
python -B -m unittest tests.test_desktop_packaging_contracts -v
powershell -ExecutionPolicy Bypass -File packaging\windows\build_portable.ps1 -SkipDependencyInstall
git status --short --ignored
```

**Rollback risk:** Medium. Bad ignore rules can hide source files; review `git check-ignore -v` for changed patterns.

**What not to touch:** Do not commit `dist/`, `.venv`, reports, logs, caches, `config.toml`, or API keys.

### Milestone 6: Portable Package Smoke Test

**Goal:** Prove the packaged portable folder can start and stop the app on Windows.

**Files likely to change:**

- Modify: `packaging/windows/build_portable.ps1`
- Modify: `desktop/launcher.py`
- Modify: `tests/test_desktop_packaging_contracts.py`
- Create: `packaging/windows/smoke_portable.ps1`
- Create: `packaging/windows/constraints-win.txt`
- Create: `packaging/windows/build_wheelhouse.ps1`

**Implementation steps:**

- [x] Build the portable package into `dist/NASDX-Desktop/` with `-SkipDependencyInstall`.
- [x] Run launcher smoke from inside the package directory using the active Python environment.
- [x] Confirm `static/style.css` is packaged and non-empty.
- [x] Confirm `/?page=plan` returns HTTP 200 during `--headless-smoke`.
- [x] Confirm package shutdown leaves no orphan Streamlit child process.
- [x] Confirm generated files remain under a configured package smoke runtime and are cleaned after smoke.
- [x] Complete dependency-contained package build with `.venv` inside `dist/NASDX-Desktop/`.

**Audit evidence from 2026-06-23:**

- Added `packaging/windows/smoke_portable.ps1` to verify package root detection, configured runtime path, static CSS presence, `--headless-smoke --page plan`, and orphan Streamlit process cleanup.
- `desktop/launcher.py` now probes the requested app URL during `--headless-smoke`, not only Streamlit health.
- `packaging/windows/build_portable.ps1` now checks native command exit codes, builds package `.venv` in a temp directory before moving it, gives pip explicit timeout/retry settings, and keeps `pywebview` behind `-IncludeWebView`.
- `build_portable.ps1` now forces `pip install --no-user` to ignore this machine's global `install.user=yes` pip config, uses `constraints-win.txt`, and can install from `-WheelhouseDir`.
- Added `packaging/windows/build_wheelhouse.ps1` for a reproducible ignored `wheelhouse/nasdx-win-py311` path.
- Verified `powershell -ExecutionPolicy Bypass -File packaging\windows\build_portable.ps1 -SkipDependencyInstall`.
- Verified `powershell -ExecutionPolicy Bypass -File packaging\windows\smoke_portable.ps1 -PackageDir dist\NASDX-Desktop -Timeout 45`.
- Verified full dependency build with `.venv` by running `powershell -ExecutionPolicy Bypass -File packaging\windows\build_portable.ps1 -PipTimeout 30 -PipRetries 1`.
- Verified package smoke using the generated `.venv` with `powershell -ExecutionPolicy Bypass -File packaging\windows\smoke_portable.ps1 -PackageDir dist\NASDX-Desktop -Timeout 60`.
- Verified package dry-run command uses `dist\NASDX-Desktop\.venv\Scripts\python.exe`.
- `-OnlyBinary` remains optional and should not be the default because AkShare's `jsonpath` dependency may need a locally built pure-Python wheel.

**Verification command:**

```powershell
powershell -ExecutionPolicy Bypass -File packaging\windows\build_portable.ps1 -SkipDependencyInstall
powershell -ExecutionPolicy Bypass -File packaging\windows\smoke_portable.ps1 -PackageDir dist\NASDX-Desktop -Timeout 45
powershell -ExecutionPolicy Bypass -File packaging\windows\build_portable.ps1 -PipTimeout 30 -PipRetries 1
powershell -ExecutionPolicy Bypass -File packaging\windows\smoke_portable.ps1 -PackageDir dist\NASDX-Desktop -Timeout 60
python -B run_final_audit.py
```

**Rollback risk:** Medium. Packaging script can create many ignored files; cleanup should remove only `dist/NASDX-Desktop/`.

**What not to touch:** Do not delete user reports outside the package output; do not alter current repository runtime files; do not make `pywebview` a default dependency.

### Milestone 7: Documentation Update for Real Desktop Use

**Goal:** Document the new desktop startup and packaging path after it exists.

**Files likely to change:**

- Modify: `README.md`
- Modify: `docs/SUBAGENT_WORKFLOW.md` only if workflow changes
- Create or modify: `docs/WINDOWS_DESKTOP.md`
- Modify: `CHANGELOG.md`
- Modify: `CONTEXT.md`

**Implementation steps:**

- [x] Add desktop startup command and portable package instructions.
- [x] Keep existing Streamlit and CLI commands in README.
- [x] Document that API keys are environment-only, local-user-config only, or user-entered in Streamlit session.
- [x] Document generated files and exclusions.
- [x] Update `CONTEXT.md` with the current desktop milestone and key decisions.
- [x] Add a documentation contract test so the desktop guide cannot silently omit key commands or safety rules.

**Audit evidence from 2026-06-24:**

- Added `docs/WINDOWS_DESKTOP.md` with Windows setup, launcher commands, safe local config, portable package, optional WebView, wheelhouse path, verification, troubleshooting, and do-not-commit rules.
- Linked the guide from `README.md` and `packaging/windows/README.md`.
- Updated `packaging/windows/build_portable.ps1` so the `docs/` folder, including `WINDOWS_DESKTOP.md`, is included in the portable package.
- Added `tests/test_delivery_assets_contracts.py` coverage for the guide, including launcher dry-run, `%APPDATA%\NASDX\config.toml`, `NASDX_CONFIG_FILE`, package build/smoke commands, wheelhouse path, final audit, and no `sk-` token examples.
- Existing Streamlit and CLI README commands remain documented.

**Verification command:**

```powershell
python -B -m unittest tests.test_delivery_assets_contracts -v
python -B run_final_audit.py
python -B run_product_readiness.py
```

**Rollback risk:** Low. Documentation can be reverted independently.

**What not to touch:** Do not remove existing CLI docs; do not publish real local paths, API keys, account ledgers, or report contents.

### Milestone 8: Installer Layer After Portable Folder Is Stable

**Goal:** Create a user-friendly installer only after portable packaging works.

**Files likely to change:**

- Create or modify: `packaging/windows/NASDX-Desktop.iss`
- Create or modify: `packaging/windows/installer_assets/`
- Modify: `packaging/windows/build_portable.ps1`
- Modify: `docs/WINDOWS_DESKTOP.md`

**Implementation steps:**

- [x] Add Inno Setup configuration that wraps the already-tested portable folder.
- [x] Add Start Menu and Desktop shortcuts to launcher, not directly to `app.py`.
- [x] Document WebView2 requirement and browser fallback.
- [x] Ensure uninstall does not delete user runtime reports/history unless explicitly configured by the user.
- [x] Keep installer output under ignored `dist/` or `build/`.

**Audit evidence from 2026-06-24:**

- Added `packaging/windows/NASDX-Desktop.iss` as a thin Inno Setup wrapper around `dist/NASDX-Desktop`.
- Installer shortcuts point to `启动NASDX桌面.bat`; the script does not target `app.py` directly.
- Installer output is configured under ignored `dist/installer`.
- Installer source excludes `reports/`, `stock_data_*.json`, `nasdx_history.db`, `config.toml`, `.env`, logs, cache folders, `wheelhouse/`, `dist/`, `build/`, and `models/signal_confidence.json`.
- Documentation now covers the Inno Setup compile command, WebView2 note, browser fallback, and uninstall boundary.
- Contract tests check the installer script and documentation without running an installer on the local machine.

**Verification command:**

```powershell
powershell -ExecutionPolicy Bypass -File packaging\windows\build_portable.ps1 -SkipDependencyInstall
python -m pytest tests/test_desktop_packaging_contracts.py tests/test_delivery_assets_contracts.py
python -B run_product_readiness.py
```

**Rollback risk:** Medium to high. Installers affect local machine state; test in a disposable Windows profile or VM first.

**What not to touch:** Do not make installer depend on committed reports, logs, caches, `config.toml`, or account data.

### Milestone 8.5: Desktop Control Panel MVP

**Goal:** Give normal Windows users a simple desktop control surface with Start, Stop, Open App, Settings, Logs, and Data Refresh while preserving the existing Streamlit UI and CLI scripts.

**Files likely to change:**

- Create: `desktop/control.py`
- Create: `desktop/control_panel.py`
- Modify: `packaging/windows/build_portable.ps1`
- Modify: `packaging/windows/smoke_portable.ps1`
- Modify: `docs/WINDOWS_DESKTOP.md`
- Modify: `README.md`
- Modify: `tests/test_desktop_control_contracts.py`
- Modify: `tests/test_desktop_packaging_contracts.py`

**Implementation steps:**

- [x] Add a standard-library Tkinter control panel that stays outside `app.py`.
- [x] Reuse `desktop.runtime` and `desktop.paths` for Streamlit process startup, runtime paths, and safe config handling.
- [x] Provide buttons for Start, Stop, Open App, Settings, Logs, and Data Refresh.
- [x] Make Settings open or create user-local config only; do not write repo `config.toml`.
- [x] Make Logs open a runtime `desktop_logs` folder.
- [x] Make Data Refresh call only the existing `fetch_stock_data.py` CLI; do not auto-run scans, THS, trading, or deploy sync.
- [x] Update the portable package batch so normal users land on the control panel, with fallback to direct launcher.
- [x] Add dry-run and contract tests so the control panel can be verified without opening a GUI.

**Audit evidence from 2026-06-24:**

- Added `desktop/control.py` with testable control actions and process/log/config helpers.
- Added `desktop/control_panel.py` with Tkinter UI and `--dry-run` metadata output.
- `启动NASDX桌面.bat` generated by `build_portable.ps1` now opens the control panel first and falls back to `desktop\launcher.py --webview --page plan` if needed.
- `smoke_portable.ps1` checks `desktop\control_panel.py --dry-run` and confirms required actions including Start and Data Refresh.
- Documentation now describes the control panel and keeps direct launcher commands for development and smoke tests.

**Verification command:**

```powershell
python -m pytest tests/test_desktop_control_contracts.py tests/test_desktop_launcher_contracts.py tests/test_desktop_packaging_contracts.py tests/test_delivery_assets_contracts.py
python -B desktop\control_panel.py --dry-run --page plan
powershell -ExecutionPolicy Bypass -File packaging\windows\build_portable.ps1 -SkipDependencyInstall
powershell -ExecutionPolicy Bypass -File packaging\windows\smoke_portable.ps1 -PackageDir dist\NASDX-Desktop -Timeout 60
python -B run_product_readiness.py
```

**Rollback risk:** Medium. It changes the packaged user entry point, but the original direct launcher remains as fallback.

**What not to touch:** Do not rewrite `app.py`; do not migrate UI frameworks; do not remove CLI scripts; do not auto-run scans, THS live trading, deploy sync, or LLM workflows from the control panel.

### Milestone 8.6: Installer Build Wrapper

**Goal:** Make installer compilation repeatable without asking developers to remember raw Inno Setup commands, while still avoiding any automatic installer execution.

**Files likely to change:**

- Create: `packaging/windows/build_installer.ps1`
- Modify: `packaging/windows/NASDX-Desktop.iss`
- Modify: `packaging/windows/README.md`
- Modify: `docs/WINDOWS_DESKTOP.md`
- Modify: `README.md`
- Modify: `tests/test_desktop_packaging_contracts.py`

**Implementation steps:**

- [x] Add a PowerShell wrapper that can build or validate installer inputs.
- [x] Let the wrapper build the portable package first unless `-SkipPortableBuild` is supplied.
- [x] Add `-SkipCompile` so machines without Inno Setup can still validate package paths and required files.
- [x] Locate `iscc` from `PATH`, common Inno Setup 6 locations, or explicit `-IsccPath`.
- [x] Pass portable and installer output paths into `NASDX-Desktop.iss` via preprocessor defines.
- [x] Verify expected installer output under ignored `dist/installer/` after compile.
- [x] Keep the script compile-only; it must not run or install the generated setup executable.

**Audit evidence from 2026-06-24:**

- Added `packaging/windows/build_installer.ps1` with `-SkipPortableBuild`, `-SkipCompile`, `-IsccPath`, `-PackageDir`, and `-InstallerOutputDir`.
- Updated `NASDX-Desktop.iss` so `PortableDir` and `InstallerOutputDir` can be overridden by the build script.
- Added packaging contract tests for the wrapper and a non-compiling validation run.
- Documentation now points users to `build_installer.ps1` first, while still keeping the portable-folder-first model.
- Local machine did not have Inno Setup compiler available, so this milestone verified `-SkipCompile` only and did not create or run an installer.

**Verification command:**

```powershell
powershell -ExecutionPolicy Bypass -File packaging\windows\build_portable.ps1 -SkipDependencyInstall
powershell -ExecutionPolicy Bypass -File packaging\windows\build_installer.ps1 -SkipPortableBuild -SkipCompile
python -m pytest tests/test_desktop_packaging_contracts.py tests/test_delivery_assets_contracts.py
python -B run_product_readiness.py
```

**Rollback risk:** Low to medium. The wrapper is additive and does not alter runtime behavior; risk is mainly incorrect installer path handling.

**What not to touch:** Do not run the installer; do not install to the local machine; do not package reports, logs, caches, `config.toml`, `.env`, or account data.

### Milestone 8.7: Desktop Release Check Aggregator

**Goal:** Provide one local command that verifies the Windows desktop package path before handoff, without installing or running a generated installer.

**Files likely to change:**

- Create: `run_desktop_release_check.py`
- Create: `tests/test_desktop_release_check_contracts.py`
- Modify: `.pre-commit-config.yaml`
- Modify: `README.md`
- Modify: `docs/WINDOWS_DESKTOP.md`
- Modify: `tests/test_delivery_assets_contracts.py`
- Modify: `CONTEXT.md`

**Implementation steps:**

- [x] Add a release-check script that runs ruff, desktop contracts, portable build, portable smoke, installer input validation, and final audit.
- [x] Default to `-SkipDependencyInstall` and installer `-SkipCompile` so the check is fast and non-invasive.
- [x] Use `dist\NASDX-Desktop-check` for the default fast package, so release checks do not overwrite a dependency-contained `dist\NASDX-Desktop` artifact.
- [x] Add explicit `--full-package` and `--compile-installer` flags for real package/installer build machines.
- [x] Ensure the script never runs or installs the generated setup executable.
- [x] Add contract tests for default command composition and explicit full/compile options.
- [x] Add the release-check contracts to pre-commit.

**Audit evidence from 2026-06-24:**

- Added `run_desktop_release_check.py` with labels `ruff`, `desktop_contracts`, `portable_package`, `portable_smoke`, `installer_inputs`, and `final_audit`.
- Default release check validates installer inputs through `build_installer.ps1 -SkipPortableBuild -SkipCompile`.
- `--full-package --compile-installer` is explicit and still only compiles; the script does not run `NASDX-Desktop-Setup.exe`.
- Default release check now uses `dist\NASDX-Desktop-check`; `dist\NASDX-Desktop` is reserved for explicit full-package/release artifact work.
- Documentation now lists `python -B run_desktop_release_check.py` as the local desktop release gate.

**Verification command:**

```powershell
python -m pytest tests/test_desktop_release_check_contracts.py tests/test_delivery_assets_contracts.py
python -B run_desktop_release_check.py
python -B run_product_readiness.py
```

**Rollback risk:** Low. The release check is additive and only orchestrates existing commands.

**What not to touch:** Do not run installer executables; do not make installer compile the default unless explicitly requested; do not weaken `run_product_readiness.py`.

### Milestone 8.8: Final Audit Covers Desktop Delivery

**Goal:** Ensure the main `run_final_audit.py` gate checks both investment workflows and Windows desktop delivery assets.

**Files likely to change:**

- Modify: `run_final_audit.py`
- Modify: `tests/test_delivery_assets_contracts.py`
- Modify: `README.md`
- Modify: `docs/WINDOWS_DESKTOP.md`
- Modify: `CONTEXT.md`

**Implementation steps:**

- [x] Add a final-audit check for desktop launcher, control panel, runtime/config helpers, WebView fallback, portable packaging, installer wrapper, installer build script, desktop guide, and release check script.
- [x] Check desktop guide markers for Start/Stop/Open App/Settings/Logs/Data Refresh, safe config, portable package, installer validation, release gate, and do-not-commit rules.
- [x] Check source markers for `--headless-smoke`, `fetch_stock_data.py`, `-SkipCompile`, installer non-run wording, control-panel package entry, and generated model exclusion.
- [x] Check Git ignore coverage for `dist/`, installer outputs, wheelhouse, desktop logs, and generated model artifacts.
- [x] Update docs so `run_final_audit.py` is clearly a desktop delivery gate too.

**Audit evidence from 2026-06-24:**

- Added `check_desktop_delivery_assets()` to `run_final_audit.py`.
- Final audit now reports `桌面交付资产` between dependency and market-data checks.
- Delivery contract tests assert the new final-audit check and release-check markers.

**Verification command:**

```powershell
python -m pytest tests/test_delivery_assets_contracts.py
python -B run_final_audit.py
python -B run_desktop_release_check.py
python -B run_product_readiness.py
```

**Rollback risk:** Low. The change only strengthens audit coverage and does not change runtime behavior.

**What not to touch:** Do not rewrite Streamlit UI; do not require installer compile by default; do not make final audit depend on network or a live API key.

### Milestone 8.9: Installed App Smoke Script

**Goal:** Provide a repeatable command to verify a real installed NASDX Desktop directory in a disposable Windows profile or VM, without installing anything from the script itself.

**Files likely to change:**

- Create: `packaging/windows/smoke_installed.ps1`
- Modify: `packaging/windows/build_portable.ps1`
- Modify: `run_desktop_release_check.py`
- Modify: `run_final_audit.py`
- Modify: `tests/test_desktop_packaging_contracts.py`
- Modify: `tests/test_desktop_release_check_contracts.py`
- Modify: `README.md`
- Modify: `docs/WINDOWS_DESKTOP.md`
- Modify: `packaging/windows/README.md`
- Modify: `CONTEXT.md`

**Implementation steps:**

- [x] Add an installed-layout smoke script that validates an existing install directory, defaulting to `%LOCALAPPDATA%\Programs\NASDX Desktop`.
- [x] Check required installed files including `desktop\control_panel.py`, `desktop\launcher.py`, `static\style.css`, and `启动NASDX桌面.bat`.
- [x] Reject runtime/user artifacts in the installed app directory such as `config.toml`, `.env`, `nasdx_history.db`, and `reports`.
- [x] Run launcher and control-panel dry-runs plus a headless `?page=plan` smoke with runtime paths redirected to a temporary directory.
- [x] Optionally check Start Menu shortcut with `-CheckShortcuts` for real installer VM tests.
- [x] Include the installed smoke script in portable packages and the desktop release check aggregator.
- [x] Add final audit, packaging contract, and documentation markers for the installed smoke path.

**Audit evidence from 2026-06-24:**

- Added `packaging/windows/smoke_installed.ps1`.
- `run_desktop_release_check.py` now runs `installed_layout_smoke` against `dist\NASDX-Desktop`.
- `build_portable.ps1` copies `packaging/windows/smoke_installed.ps1` into package output.
- Documentation now gives both simulated installed-layout smoke and real installed-directory smoke commands.

**Verification command:**

```powershell
powershell -ExecutionPolicy Bypass -File packaging\windows\build_portable.ps1 -SkipDependencyInstall
powershell -ExecutionPolicy Bypass -File packaging\windows\smoke_installed.ps1 -InstallDir dist\NASDX-Desktop -Timeout 60
python -B run_desktop_release_check.py
python -B run_product_readiness.py
```

**Rollback risk:** Low to medium. The script is additive, but release check now spends extra time on an installed-layout smoke.

**What not to touch:** Do not run or install `NASDX-Desktop-Setup.exe`; do not write runtime reports/history/config into the installed application directory.

### Milestone 8.10: Windows Desktop CI Skeleton

**Goal:** Add a lightweight Windows CI gate for the desktop package path without requiring local market snapshots, generated reports, or real installer execution.

**Files likely to change:**

- Create: `.github/workflows/windows-desktop.yml`
- Create: `tests/test_desktop_ci_contracts.py`
- Modify: `.pre-commit-config.yaml`
- Modify: `run_final_audit.py`
- Modify: `tests/test_delivery_assets_contracts.py`
- Modify: `README.md`
- Modify: `docs/WINDOWS_DESKTOP.md`
- Modify: `CONTEXT.md`

**Implementation steps:**

- [x] Add a `windows-latest` GitHub Actions workflow with Python 3.11.
- [x] Install `requirements-dev.txt` only; do not introduce new dependencies.
- [x] Run `python -B run_desktop_release_check.py --skip-final-audit --fail-fast` so CI checks desktop contracts, portable package, portable smoke, installed-layout smoke, and installer inputs.
- [x] Run delivery asset contracts after the desktop release check.
- [x] Do not compile or run `NASDX-Desktop-Setup.exe` in CI.
- [x] Add contract tests and final-audit markers so workflow safety rules stay visible.

**Audit evidence from 2026-06-24:**

- Added `.github/workflows/windows-desktop.yml`.
- Added `tests/test_desktop_ci_contracts.py`.
- Final audit now checks the Windows desktop workflow file and required safe command markers.
- Documentation explains that CI skips final audit because fresh checkouts do not include local market snapshots or generated reports.

**Verification command:**

```powershell
python -m pytest tests/test_desktop_ci_contracts.py tests/test_delivery_assets_contracts.py
python -B run_final_audit.py
python -B run_desktop_release_check.py
```

**Rollback risk:** Low. CI is additive, but it may need runtime tuning on GitHub-hosted Windows if package install/network time is slow.

**What not to touch:** Do not put secrets in workflow YAML; do not run installer executables; do not make CI depend on generated reports, `stock_data_*.json`, `.env`, or `config.toml`.

### Milestone 8.11: Lightweight Security Check Gate

**Goal:** Add a default-safe security command for desktop release handoff without making `pip-audit`, `bandit`, or `detect-secrets` required dependencies.

**Files likely to change:**

- Create: `run_security_checks.py`
- Create: `tests/test_security_checks_contracts.py`
- Modify: `run_desktop_release_check.py`
- Modify: `run_final_audit.py`
- Modify: `tests/test_desktop_release_check_contracts.py`
- Modify: `tests/test_delivery_assets_contracts.py`
- Modify: `.pre-commit-config.yaml`
- Modify: `README.md`
- Modify: `docs/WINDOWS_DESKTOP.md`
- Modify: `CONTEXT.md`

**Implementation steps:**

- [x] Add a lightweight secret scan over versionable text files using `git ls-files --exclude-standard` where available.
- [x] Keep generated outputs, reports, caches, local configs, logs, and packaged artifacts out of the scan path through existing ignore rules.
- [x] Expose `pip-audit`, `bandit`, and `detect-secrets` as optional checks behind `--run-optional`.
- [x] Run `run_security_checks.py --skip-optional` inside `run_desktop_release_check.py`.
- [x] Add contract coverage for default skip behavior and generated-directory exclusions.
- [x] Document the default and optional security commands.

**Verification command:**

```powershell
python -B run_security_checks.py --skip-optional
python -m pytest tests/test_security_checks_contracts.py tests/test_desktop_release_check_contracts.py tests/test_delivery_assets_contracts.py
python -B run_desktop_release_check.py
python -B run_product_readiness.py
```

**Rollback risk:** Low. The default path is additive, does not install tools, and does not touch runtime application logic.

**What not to touch:** Do not commit scanner baselines with local secrets; do not make optional security tools mandatory yet; do not scan or package reports, logs, cache files, `.env`, `config.toml`, or installer outputs.

### Milestone 8.12: Installer Roundtrip Smoke Path

**Goal:** Provide a safe, repeatable way to validate the real Inno Setup installer through install, installed smoke, and uninstall in a disposable Windows profile or VM.

**Files likely to change:**

- Create: `packaging/windows/smoke_installer_roundtrip.ps1`
- Modify: `packaging/windows/build_portable.ps1`
- Modify: `packaging/windows/README.md`
- Modify: `run_final_audit.py`
- Modify: `tests/test_desktop_packaging_contracts.py`
- Modify: `tests/test_delivery_assets_contracts.py`
- Modify: `README.md`
- Modify: `docs/WINDOWS_DESKTOP.md`
- Modify: `CONTEXT.md`

**Implementation steps:**

- [x] Add a roundtrip smoke script that is plan-only by default and requires explicit `-AllowInstall` to run the installer.
- [x] Default the install directory to a temporary `nasdx-installer-roundtrip-*` directory and reject high-risk paths.
- [x] Install silently with Inno Setup arguments, call `smoke_installed.ps1`, then run `unins*.exe` unless `-KeepInstalled` is passed.
- [x] Support `-CheckShortcuts` for disposable-profile verification of Start Menu shortcut creation and removal.
- [x] Package the roundtrip script in the portable output so release artifacts include their own verification tooling.
- [x] Add contract tests and final-audit markers for the roundtrip path.
- [x] Document the command and the `-AllowInstall` safety gate.

**Verification command:**

```powershell
powershell -ExecutionPolicy Bypass -File packaging\windows\smoke_installer_roundtrip.ps1
python -m pytest tests/test_desktop_packaging_contracts.py tests/test_delivery_assets_contracts.py
python -B run_desktop_release_check.py
python -B run_product_readiness.py
```

**Rollback risk:** Low. The script is additive and plan-only unless `-AllowInstall` is explicitly supplied; it does not alter application runtime logic.

**What not to touch:** Do not run the installer on a normal user profile; do not install over the repository; do not delete user runtime state; do not commit compiled setup executables or installer logs.

### Milestone 8.13: Source and Portable Shortcut Helper

**Goal:** Let a normal Windows user create Start Menu or Desktop shortcuts for either a source checkout or portable package without requiring the installer first.

**Files likely to change:**

- Create: `启动NASDX桌面.bat`
- Create: `packaging/windows/create_shortcuts.ps1`
- Modify: `packaging/windows/build_portable.ps1`
- Modify: `packaging/windows/README.md`
- Modify: `run_final_audit.py`
- Modify: `tests/test_desktop_packaging_contracts.py`
- Modify: `tests/test_delivery_assets_contracts.py`
- Modify: `README.md`
- Modify: `docs/WINDOWS_DESKTOP.md`
- Modify: `CONTEXT.md`

**Implementation steps:**

- [x] Add a tracked root `启动NASDX桌面.bat` that opens `desktop\control_panel.py` and falls back to `desktop\launcher.py --webview --page plan`.
- [x] Add `create_shortcuts.ps1` with plan-only default output and explicit `-Apply` before writing Start Menu or Desktop `.lnk` files.
- [x] Point shortcuts to `启动NASDX桌面.bat`, not directly to `app.py` or Streamlit internals.
- [x] Support `-Remove` for current-user shortcut cleanup without deleting app files or user runtime state.
- [x] Include the shortcut script in portable packages.
- [x] Add final-audit markers and contract tests for root batch, shortcut safety, and portable inclusion.
- [x] Document source checkout and portable shortcut commands.

**Verification command:**

```powershell
powershell -ExecutionPolicy Bypass -File packaging\windows\create_shortcuts.ps1 -Desktop
python -m pytest tests/test_desktop_packaging_contracts.py tests/test_delivery_assets_contracts.py
python -B run_desktop_release_check.py
python -B run_product_readiness.py
```

**Rollback risk:** Low. Default behavior is preview-only; `-Apply` writes only current-user shortcut files and does not change application logic.

**What not to touch:** Do not auto-create shortcuts during tests or default release checks; do not point shortcuts directly at `app.py`; do not remove existing `启动网页.bat` or CLI scripts.

### Milestone 8.14: Desktop Doctor Diagnostics

**Goal:** Provide a safe diagnostic command for Windows users and release handoff that checks desktop prerequisites, config metadata, runtime paths, launch plan, optional WebView, and Inno Setup availability without starting Streamlit or printing secrets.

**Files likely to change:**

- Create: `desktop/doctor.py`
- Create: `run_desktop_doctor.py`
- Create: `tests/test_desktop_doctor_contracts.py`
- Modify: `run_desktop_release_check.py`
- Modify: `run_final_audit.py`
- Modify: `tests/test_desktop_release_check_contracts.py`
- Modify: `tests/test_delivery_assets_contracts.py`
- Modify: `.pre-commit-config.yaml`
- Modify: `README.md`
- Modify: `docs/WINDOWS_DESKTOP.md`
- Modify: `packaging/windows/README.md`
- Modify: `CONTEXT.md`

**Implementation steps:**

- [x] Add read-only desktop diagnostics for app-root files, Python version, core modules, optional feature modules, config metadata, desktop env paths, launch plan, optional WebView, and Inno Setup.
- [x] Report config paths and loaded key names only; never print API key values.
- [x] Keep runtime write probes behind explicit `--check-write`.
- [x] Add JSON output for release automation.
- [x] Add `desktop_doctor` to `run_desktop_release_check.py` in safe `--json` mode.
- [x] Add final-audit markers and contract tests for no launch/no secret behavior.
- [x] Document source and packaged doctor commands.

**Verification command:**

```powershell
python -B run_desktop_doctor.py
python -B run_desktop_doctor.py --json
python -m pytest tests/test_desktop_doctor_contracts.py tests/test_desktop_release_check_contracts.py tests/test_delivery_assets_contracts.py
python -B run_desktop_release_check.py
python -B run_product_readiness.py
```

**Rollback risk:** Low. The default command is read-only and does not start Streamlit, install packages, or write config.

**What not to touch:** Do not print secrets; do not make optional `pywebview` or Inno Setup absence fail the default doctor; do not mutate `app.py` or CLI workflows.

### Milestone 8.15: Smoke Coverage for Doctor and Shortcut Entrypoints

**Goal:** Ensure portable and installed-layout smoke tests validate the newly added desktop doctor and shortcut helper, not only the launcher/control panel.

**Files likely to change:**

- Modify: `packaging/windows/smoke_portable.ps1`
- Modify: `packaging/windows/smoke_installed.ps1`
- Modify: `run_final_audit.py`
- Modify: `tests/test_desktop_packaging_contracts.py`
- Modify: `docs/WINDOWS_DESKTOP.md`
- Modify: `packaging/windows/README.md`
- Modify: `PLANS.md`
- Modify: `CONTEXT.md`

**Implementation steps:**

- [x] Add `desktop\doctor.py`, `run_desktop_doctor.py`, `create_shortcuts.ps1`, and `smoke_installer_roundtrip.ps1` to smoke-required files.
- [x] Run `run_desktop_doctor.py --json` during portable and installed smoke, failing only on `FAIL` status while allowing expected WARNs for optional tools.
- [x] Run `create_shortcuts.ps1 -Desktop` in plan-only mode during smoke and verify it points to `启动NASDX桌面.bat`.
- [x] Keep smoke scripts from writing shortcuts or starting installer executables.
- [x] Add final-audit and contract markers for the stronger smoke coverage.

**Verification command:**

```powershell
python -m pytest tests/test_desktop_packaging_contracts.py tests/test_delivery_assets_contracts.py
python -B run_desktop_release_check.py
python -B run_product_readiness.py
```

**Rollback risk:** Low. The checks are stricter but still non-invasive; they only run read-only doctor and plan-only shortcut preview before the existing headless smoke.

**What not to touch:** Do not create real shortcuts in smoke tests; do not run installer executables; do not change Streamlit UI or CLI workflows.

### Milestone 8.16: Verifiable Desktop Batch Entrypoint

**Goal:** Make the actual Windows batch entry used by source checkouts and portable packages testable without opening the GUI.

**Files likely to change:**

- Modify: `启动NASDX桌面.bat`
- Modify: `packaging/windows/build_portable.ps1`
- Modify: `packaging/windows/smoke_portable.ps1`
- Modify: `packaging/windows/smoke_installed.ps1`
- Modify: `run_final_audit.py`
- Modify: `tests/test_desktop_packaging_contracts.py`
- Modify: `README.md`
- Modify: `docs/WINDOWS_DESKTOP.md`
- Modify: `packaging/windows/README.md`
- Modify: `PLANS.md`
- Modify: `CONTEXT.md`

**Implementation steps:**

- [x] Forward batch arguments to `desktop\control_panel.py` and fallback `desktop\launcher.py`.
- [x] Verify root `启动NASDX桌面.bat --dry-run --page plan` returns control-panel JSON without opening the GUI.
- [x] Verify portable and installed smoke run the packaged batch dry-run and parse required actions.
- [x] Add contract and final-audit markers for `%*` forwarding and batch dry-run coverage.
- [x] Document source and portable batch dry-run commands.

**Verification command:**

```powershell
.\启动NASDX桌面.bat --dry-run --page plan
powershell -ExecutionPolicy Bypass -File packaging\windows\smoke_portable.ps1 -PackageDir dist\NASDX-Desktop -Timeout 60
powershell -ExecutionPolicy Bypass -File packaging\windows\smoke_installed.ps1 -InstallDir dist\NASDX-Desktop -Timeout 60
python -B run_desktop_release_check.py
python -B run_product_readiness.py
```

**Rollback risk:** Low. Default double-click behavior is unchanged; only argument forwarding and smoke verification are added.

**What not to touch:** Do not remove `启动网页.bat`; do not point the batch directly to `app.py`; do not create GUI windows in dry-run tests.

### Milestone 8.17: Desktop Completion Evidence Audit

**Goal:** Add a read-only evidence matrix that shows which desktop-product requirements are already satisfied and which packaging proofs remain incomplete.

**Files likely to change:**

- Create: `run_desktop_completion_audit.py`
- Create: `tests/test_desktop_completion_audit_contracts.py`
- Modify: `run_desktop_release_check.py`
- Modify: `packaging/windows/smoke_portable.ps1`
- Modify: `packaging/windows/smoke_installed.ps1`
- Modify: `run_final_audit.py`
- Modify: `tests/test_desktop_release_check_contracts.py`
- Modify: `tests/test_desktop_packaging_contracts.py`
- Modify: `tests/test_delivery_assets_contracts.py`
- Modify: `.pre-commit-config.yaml`
- Modify: `README.md`
- Modify: `docs/WINDOWS_DESKTOP.md`
- Modify: `packaging/windows/README.md`
- Modify: `PLANS.md`
- Modify: `CONTEXT.md`

**Implementation steps:**

- [x] Add `run_desktop_completion_audit.py` with JSON and text output.
- [x] Report preserved legacy entrypoints, desktop launcher MVP, local config, packaging chain, release gates, ignored generated files, optional WebView, Inno Setup availability, and installer roundtrip status.
- [x] Keep the default audit read-only and non-failing for `INCOMPLETE` items; use `--strict` only when a release process wants to fail on incomplete packaging proofs.
- [x] Add the audit to `run_desktop_release_check.py` and to portable/installed smoke scripts, failing only when the audit reports `FAIL`.
- [x] Add contract tests and final-audit markers so the evidence matrix remains part of the desktop delivery surface.
- [x] Document the command in README, desktop guide, and packaging guide.

**Verification command:**

```powershell
python -B run_desktop_completion_audit.py
python -B run_desktop_completion_audit.py --json
python -m pytest tests/test_desktop_completion_audit_contracts.py tests/test_desktop_release_check_contracts.py tests/test_desktop_packaging_contracts.py tests/test_delivery_assets_contracts.py
python -B run_desktop_release_check.py
python -B run_product_readiness.py
```

**Rollback risk:** Low. The new audit is read-only and only inspects files, ignore rules, optional module availability, and packaging-tool availability.

**What not to touch:** Do not make missing optional `pywebview` fail the MVP; do not treat missing `ISCC.exe` as a completed installer proof; do not start Streamlit, install dependencies, run installers, change `app.py`, or modify CLI workflows.

### Milestone 8.18: Full Portable Package Timeout Controls

**Goal:** Make the full dependency-contained portable package build diagnosable and tunable instead of failing with an uncaught timeout traceback.

**Files likely to change:**

- Modify: `run_desktop_release_check.py`
- Modify: `tests/test_desktop_release_check_contracts.py`
- Modify: `run_final_audit.py`
- Modify: `tests/test_delivery_assets_contracts.py`
- Modify: `README.md`
- Modify: `docs/WINDOWS_DESKTOP.md`
- Modify: `packaging/windows/README.md`
- Modify: `PLANS.md`
- Modify: `CONTEXT.md`

**Implementation steps:**

- [x] Catch `subprocess.TimeoutExpired` inside `run_desktop_release_check.py` and report it as a normal failed command result.
- [x] Use a longer default timeout for `--full-package` than for the fast `-SkipDependencyInstall` release gate.
- [x] Add `--package-timeout`, `--smoke-timeout`, `--audit-timeout`, `--pip-timeout`, and `--pip-retries` CLI options.
- [x] Pass `-PipTimeout` and `-PipRetries` through to `build_portable.ps1` for real dependency-contained package builds.
- [x] Pass `-RequireVenv` to portable and installed-layout smoke during `--full-package`, proving the package uses its bundled `.venv` instead of the developer machine's global Python.
- [x] Add `portable_runtime_bundle` to `run_desktop_completion_audit.py` so the evidence matrix shows whether the current portable artifact contains bundled Python.
- [x] Add contract and final-audit markers for timeout handling and full-package pip tuning.
- [x] Document slow-network full-package commands.

**Verification command:**

```powershell
python -m pytest tests/test_desktop_release_check_contracts.py tests/test_delivery_assets_contracts.py
python -B run_desktop_release_check.py --full-package --package-timeout 1200 --pip-timeout 120 --pip-retries 3 --skip-final-audit --fail-fast
python -B run_desktop_release_check.py
python -B run_product_readiness.py
```

**Rollback risk:** Low. The default fast release gate remains non-invasive; new options only affect explicit full-package or timeout tuning paths.

**What not to touch:** Do not hide dependency-install failures; do not make the fast CI gate install full dependencies; do not allow `--full-package` smoke to fall back to global Python; do not change Streamlit app logic or CLI workflows.

### Milestone 8.19: Distributable Portable Zip Artifact

**Goal:** Provide a user-shareable portable zip path that does not depend on Inno Setup, while keeping the installer wrapper available for later.

**Files likely to change:**

- Create: `packaging/windows/build_portable_zip.ps1`
- Create: `packaging/windows/smoke_portable_zip.ps1`
- Modify: `run_desktop_release_check.py`
- Modify: `run_desktop_completion_audit.py`
- Modify: `run_final_audit.py`
- Modify: `tests/test_desktop_release_check_contracts.py`
- Modify: `tests/test_desktop_packaging_contracts.py`
- Modify: `tests/test_delivery_assets_contracts.py`
- Modify: `README.md`
- Modify: `docs/WINDOWS_DESKTOP.md`
- Modify: `packaging/windows/README.md`
- Modify: `PLANS.md`
- Modify: `CONTEXT.md`

**Implementation steps:**

- [x] Add `build_portable_zip.ps1` to zip the tested `dist\NASDX-Desktop` folder into `dist\NASDX-Desktop-portable.zip`.
- [x] Refuse to write the zip inside the package directory and verify forbidden runtime/user artifacts are not present before zipping.
- [x] Add `smoke_portable_zip.ps1` to extract the zip to a temporary directory and run the packaged `smoke_installed.ps1`.
- [x] Support `-RequireVenv` for dependency-contained zip verification.
- [x] Add `--zip-package` to `run_desktop_release_check.py`, inserting `portable_zip` and `portable_zip_smoke` before installer input validation.
- [x] Add contract tests, final-audit markers, completion-audit file coverage, and documentation.

**Verification command:**

```powershell
powershell -ExecutionPolicy Bypass -File packaging\windows\build_portable_zip.ps1 -PackageDir dist\NASDX-Desktop -OutputZip dist\NASDX-Desktop-portable.zip -RequireVenv
powershell -ExecutionPolicy Bypass -File packaging\windows\smoke_portable_zip.ps1 -ZipPath dist\NASDX-Desktop-portable.zip -Timeout 60 -RequireVenv
python -B run_desktop_release_check.py --full-package --zip-package --package-timeout 1200 --zip-timeout 900 --pip-timeout 120 --pip-retries 3 --skip-final-audit --fail-fast
python -B run_product_readiness.py
```

**Rollback risk:** Low to moderate. Zip creation can be slow for a dependency-contained `.venv`, but it is explicit via `--zip-package`, tunable with `--zip-timeout`, and does not affect the fast default release gate.

**What not to touch:** Do not put `config.toml`, `.env`, reports, market snapshots, logs, local DBs, or build artifacts in the zip; do not replace the installer wrapper; do not change Streamlit app logic or CLI workflows.

### Milestone 8.20: Portable Zip Release Manifest

**Goal:** Add verifiable release evidence for the portable zip so a Windows user or maintainer can confirm artifact integrity without running the app.

**Files likely to change:**

- Modify: `packaging/windows/build_portable_zip.ps1`
- Modify: `packaging/windows/smoke_portable_zip.ps1`
- Modify: `run_desktop_release_check.py`
- Modify: `run_desktop_completion_audit.py`
- Modify: `run_final_audit.py`
- Modify: `tests/test_desktop_packaging_contracts.py`
- Modify: `tests/test_desktop_release_check_contracts.py`
- Modify: `tests/test_desktop_completion_audit_contracts.py`
- Modify: `tests/test_delivery_assets_contracts.py`
- Modify: `README.md`
- Modify: `docs/WINDOWS_DESKTOP.md`
- Modify: `packaging/windows/README.md`
- Modify: `PLANS.md`
- Modify: `CONTEXT.md`

**Implementation steps:**

- [x] Generate `dist\NASDX-Desktop-portable.zip.sha256` beside the portable zip.
- [x] Generate `dist\NASDX-Desktop-portable.manifest.json` with `nasdx_portable_release.v1`, zip size, zip hash, zip engine, `-RequireVenv` state, and a sanitized source packaging manifest summary.
- [x] Verify the checksum and manifest in `smoke_portable_zip.ps1` before extracting the zip.
- [x] Wire the sidecar paths into `run_desktop_release_check.py --zip-package`.
- [x] Add audit/test/documentation markers and keep sidecar outputs ignored under `dist/`.

**Verification command:**

```powershell
python -m pytest tests/test_desktop_packaging_contracts.py tests/test_desktop_release_check_contracts.py tests/test_desktop_completion_audit_contracts.py tests/test_delivery_assets_contracts.py
powershell -ExecutionPolicy Bypass -File packaging\windows\build_portable_zip.ps1 -PackageDir dist\NASDX-Desktop -OutputZip dist\NASDX-Desktop-portable.zip -RequireVenv
powershell -ExecutionPolicy Bypass -File packaging\windows\smoke_portable_zip.ps1 -ZipPath dist\NASDX-Desktop-portable.zip -Timeout 60 -RequireVenv
python -B run_desktop_release_check.py --full-package --zip-package --package-timeout 1200 --zip-timeout 900 --pip-timeout 120 --pip-retries 3 --skip-final-audit --fail-fast
```

**Rollback risk:** Low. The release sidecars are generated artifacts under ignored `dist/`; removing this milestone leaves the previous portable zip path intact.

**What not to touch:** Do not include secrets or user config values in the manifest; do not require zip sidecars for the fast non-zip release gate; do not change `app.py`, quant modules, scanner scripts, or CLI workflows.

### Milestone 8.21: Dependency-Contained Portable Runtime Proof

**Goal:** Prove that the current portable Windows folder and zip can run from their bundled `.venv`, so normal users do not depend on the developer machine's global Python environment.

**Files likely to change:**

- Modify: `PLANS.md`
- Modify: `CONTEXT.md`
- Generated only: `dist\NASDX-Desktop\`
- Generated only: `dist\NASDX-Desktop-portable.zip`
- Generated only: `dist\NASDX-Desktop-portable.zip.sha256`
- Generated only: `dist\NASDX-Desktop-portable.manifest.json`

**Implementation steps:**

- [x] Rebuild `dist\NASDX-Desktop` without `-SkipDependencyInstall`.
- [x] Install runtime dependencies into `dist\NASDX-Desktop\.venv` using `requirements_nasdx.txt` and `constraints-win.txt`.
- [x] Run portable smoke with `-RequireVenv`.
- [x] Run installed-layout smoke with `-RequireVenv`.
- [x] Build portable zip with `-RequireVenv`, checksum sidecar, and release manifest.
- [x] Run portable zip smoke with `-RequireVenv` and verify extracted package uses its own `.venv`.
- [x] Re-run completion audit and confirm `portable_runtime_bundle` is PASS.

**Verification command:**

```powershell
powershell -ExecutionPolicy Bypass -File packaging\windows\build_portable.ps1 -PipTimeout 30 -PipRetries 1
powershell -ExecutionPolicy Bypass -File packaging\windows\smoke_portable.ps1 -PackageDir dist\NASDX-Desktop -Timeout 60 -RequireVenv
powershell -ExecutionPolicy Bypass -File packaging\windows\smoke_installed.ps1 -InstallDir dist\NASDX-Desktop -Timeout 60 -RequireVenv
powershell -ExecutionPolicy Bypass -File packaging\windows\build_portable_zip.ps1 -PackageDir dist\NASDX-Desktop -OutputZip dist\NASDX-Desktop-portable.zip -RequireVenv
powershell -ExecutionPolicy Bypass -File packaging\windows\smoke_portable_zip.ps1 -ZipPath dist\NASDX-Desktop-portable.zip -Timeout 60 -RequireVenv
python -B run_desktop_completion_audit.py
```

**Rollback risk:** Low. All runtime bundle outputs are ignored build artifacts under `dist/`; deleting `dist\NASDX-Desktop*` returns the repository to source-only state.

**What not to touch:** Do not commit the generated package, zip, checksum, manifest, `.venv`, logs, reports, local DB, `.env`, or `config.toml`; do not run portable and installed smoke in parallel against the same package directory because portable smoke temporarily creates `_smoke_runtime`.

### Milestone 8.22: Inno Setup Compiler Bootstrap Handoff

**Goal:** Make the remaining installer compile gap actionable on a Windows packaging machine without silently installing system software during normal checks.

**Files likely to change:**

- Create: `packaging/windows/install_inno_setup.ps1`
- Modify: `run_desktop_completion_audit.py`
- Modify: `run_final_audit.py`
- Modify: `tests/test_desktop_packaging_contracts.py`
- Modify: `tests/test_desktop_completion_audit_contracts.py`
- Modify: `tests/test_delivery_assets_contracts.py`
- Modify: `README.md`
- Modify: `docs/WINDOWS_DESKTOP.md`
- Modify: `packaging/windows/README.md`
- Modify: `PLANS.md`
- Modify: `CONTEXT.md`

**Implementation steps:**

- [x] Add `install_inno_setup.ps1` as a plan-only bootstrap script that detects `ISCC.exe` and `winget`.
- [x] Require explicit `-Install -AcceptAgreements` before invoking `winget install --id JRSoftware.InnoSetup`.
- [x] Keep normal audits non-mutating; `run_desktop_completion_audit.py` now points to the bootstrap script as the next step when `ISCC.exe` is missing.
- [x] Add contract tests so the bootstrap remains plan-only by default and does not use `Start-Process`.
- [x] Document the packaging-machine command sequence before `build_installer.ps1` and `smoke_installer_roundtrip.ps1`.

**Verification command:**

```powershell
powershell -ExecutionPolicy Bypass -File packaging\windows\install_inno_setup.ps1
python -m pytest tests/test_desktop_packaging_contracts.py tests/test_desktop_completion_audit_contracts.py tests/test_delivery_assets_contracts.py
python -B run_desktop_completion_audit.py
python -B run_final_audit.py
```

**Rollback risk:** Low. The script is a packaging helper, defaults to read-only plan output, and does not alter the runtime app or portable package.

**What not to touch:** Do not install Inno Setup without explicit user permission; do not run the real installer outside a disposable Windows profile or VM; do not mark installer roundtrip complete until `NASDX-Desktop-Setup.exe` is compiled and `smoke_installer_roundtrip.ps1 -AllowInstall -CheckShortcuts` passes.

### Milestone 8.23: Installer Roundtrip Bundled-Python Proof

**Goal:** Ensure the final real installer smoke can prove that the installed app uses the bundled `.venv`, not the packaging machine's global Python.

**Files likely to change:**

- Modify: `packaging/windows/smoke_installer_roundtrip.ps1`
- Modify: `run_desktop_completion_audit.py`
- Modify: `run_final_audit.py`
- Modify: `tests/test_desktop_packaging_contracts.py`
- Modify: `tests/test_desktop_completion_audit_contracts.py`
- Modify: `tests/test_delivery_assets_contracts.py`
- Modify: `README.md`
- Modify: `docs/WINDOWS_DESKTOP.md`
- Modify: `packaging/windows/README.md`
- Modify: `PLANS.md`
- Modify: `CONTEXT.md`

**Implementation steps:**

- [x] Add `-RequireVenv` to `smoke_installer_roundtrip.ps1`.
- [x] Pass `-RequireVenv` through to the packaged `smoke_installed.ps1` during real install smoke.
- [x] Update completion-audit next steps to require `-AllowInstall -CheckShortcuts -RequireVenv`.
- [x] Update docs and contract tests so final installer proof includes bundled Python verification.

**Verification command:**

```powershell
powershell -ExecutionPolicy Bypass -File packaging\windows\smoke_installer_roundtrip.ps1
python -m pytest tests/test_desktop_packaging_contracts.py tests/test_desktop_completion_audit_contracts.py tests/test_delivery_assets_contracts.py
python -B run_desktop_completion_audit.py
python -B run_final_audit.py
```

**Rollback risk:** Low. The new switch is additive and only affects real installer runs when explicitly passed.

**What not to touch:** Do not run the installer without `-AllowInstall`; do not remove the plan-only default; do not mark installer roundtrip complete until the command passes on a disposable Windows profile or VM.

### Milestone 8.24: Read-Only Installer Release Preflight

**Goal:** Give the packaging machine a single read-only preflight that verifies portable package, zip sidecars, Inno Setup compiler availability, and exact next commands before compiling or running the installer.

**Files likely to change:**

- Create: `packaging/windows/preflight_installer_release.ps1`
- Modify: `run_desktop_completion_audit.py`
- Modify: `run_final_audit.py`
- Modify: `tests/test_desktop_packaging_contracts.py`
- Modify: `tests/test_desktop_completion_audit_contracts.py`
- Modify: `tests/test_delivery_assets_contracts.py`
- Modify: `README.md`
- Modify: `docs/WINDOWS_DESKTOP.md`
- Modify: `packaging/windows/README.md`
- Modify: `PLANS.md`
- Modify: `CONTEXT.md`

**Implementation steps:**

- [x] Add `preflight_installer_release.ps1` with read-only checks for `dist\NASDX-Desktop`, bundled `.venv`, portable zip, `.sha256`, release manifest, `ISCC.exe`, and `NASDX-Desktop.iss`.
- [x] Keep default mode non-mutating and non-strict, so machines without `ISCC.exe` can still see the handoff state.
- [x] Add `-Strict` for packaging machines that want missing `ISCC.exe` or sidecars to fail the command.
- [x] Print exact compile and roundtrip commands, including `-RequireVenv`.
- [x] Add contract tests and documentation markers.

**Verification command:**

```powershell
powershell -ExecutionPolicy Bypass -File packaging\windows\preflight_installer_release.ps1 -RequireVenv
python -m pytest tests/test_desktop_packaging_contracts.py tests/test_desktop_completion_audit_contracts.py tests/test_delivery_assets_contracts.py
python -B run_final_audit.py
```

**Rollback risk:** Low. The script is read-only and does not build, install, or run the installer.

**What not to touch:** Do not make preflight compile or install anything; do not hide missing `ISCC.exe`; do not treat preflight as proof of real install/uninstall roundtrip.

### Milestone 8.25: Installer Roundtrip Proof Receipt

**Goal:** Make the final real installer smoke produce durable, machine-checkable evidence after install, installed smoke, and uninstall succeed.

**Files likely to change:**

- Modify: `packaging/windows/smoke_installer_roundtrip.ps1`
- Modify: `run_desktop_completion_audit.py`
- Modify: `run_final_audit.py`
- Modify: `tests/test_desktop_packaging_contracts.py`
- Modify: `tests/test_desktop_completion_audit_contracts.py`
- Modify: `tests/test_delivery_assets_contracts.py`
- Modify: `README.md`
- Modify: `docs/WINDOWS_DESKTOP.md`
- Modify: `packaging/windows/README.md`

**Implementation steps:**

- [x] Add `-ProofPath` to `smoke_installer_roundtrip.ps1`, defaulting to ignored `dist\installer\NASDX-Desktop-roundtrip-proof.json`.
- [x] Keep plan-only mode non-mutating and print the intended proof path.
- [x] Write proof only after real `-AllowInstall` install, `smoke_installed.ps1`, uninstall, and shortcut cleanup pass; do not write final proof when `-KeepInstalled` skips uninstall.
- [x] Include `nasdx_installer_roundtrip_proof.v1`, installer SHA256, installer size, install directory, `RequireVenv`, `CheckShortcuts`, installed-smoke status, uninstall status, and `kept_installed`.
- [x] Update `run_desktop_completion_audit.py` so installer roundtrip becomes `PASS` only when the proof matches the current setup executable hash and proves `-RequireVenv`, `-CheckShortcuts`, installed smoke, and uninstall.
- [x] Add contract coverage and documentation markers.

**Verification command:**

```powershell
powershell -ExecutionPolicy Bypass -File packaging\windows\smoke_installer_roundtrip.ps1
python -m pytest tests/test_desktop_packaging_contracts.py tests/test_desktop_completion_audit_contracts.py tests/test_delivery_assets_contracts.py
python -B run_desktop_completion_audit.py
python -B run_final_audit.py
```

**Rollback risk:** Low. The proof file is generated only after explicit real installer smoke and is ignored under `dist/`.

**What not to touch:** Do not write proof for plan-only runs; do not write final proof when uninstall is skipped; do not accept stale proof whose hash does not match the current setup executable; do not run the installer without `-AllowInstall`; do not change Streamlit app logic or CLI workflows.

### Milestone 8.26: Optional Launcher-Only Executable Path

**Goal:** Provide a future PyInstaller path for a normal Windows double-click executable without turning NASDX into a brittle full one-file bundle.

**Files likely to change:**

- Create: `desktop/exe_launcher.py`
- Create: `packaging/windows/build_launcher_exe.ps1`
- Modify: `packaging/windows/build_portable.ps1`
- Modify: `run_desktop_completion_audit.py`
- Modify: `run_final_audit.py`
- Modify: `tests/test_desktop_launcher_contracts.py`
- Modify: `tests/test_desktop_packaging_contracts.py`
- Modify: `tests/test_desktop_completion_audit_contracts.py`
- Modify: `tests/test_delivery_assets_contracts.py`
- Modify: `README.md`
- Modify: `docs/WINDOWS_DESKTOP.md`
- Modify: `packaging/windows/README.md`

**Implementation steps:**

- [x] Add `desktop/exe_launcher.py` as a tiny stdlib-only shim that locates the portable root, prefers `.venv\Scripts\python.exe`, runs `desktop\control_panel.py`, and falls back to `desktop\launcher.py --webview --page plan`.
- [x] Add `build_launcher_exe.ps1` with a safe `-SkipBuild` plan-only mode and PyInstaller command generation.
- [x] Keep PyInstaller optional and packaging-machine-only; do not add it to runtime dependencies.
- [x] Keep the launcher exe path launcher-only; do not bundle `app.py`, AkShare, pandas, reports, local config, logs, caches, or API keys into a one-file app.
- [x] Include the script in portable package allow-list and audit/contract coverage.
- [x] Document the command and the boundary between launcher-only exe and full app packaging.

**Verification command:**

```powershell
python -B desktop\exe_launcher.py --dry-run --page plan
powershell -ExecutionPolicy Bypass -File packaging\windows\build_launcher_exe.ps1 -SkipBuild
python -m pytest tests/test_desktop_launcher_contracts.py tests/test_desktop_packaging_contracts.py tests/test_desktop_completion_audit_contracts.py tests/test_delivery_assets_contracts.py
python -B run_desktop_completion_audit.py
python -B run_final_audit.py
```

**Rollback risk:** Low. The shim and build script are additive, and `-SkipBuild` does not require PyInstaller or write build artifacts.

**What not to touch:** Do not add PyInstaller to runtime dependencies; do not make launcher exe build part of the default release gate; do not bundle analytics dependencies into the exe; do not change `app.py`, Streamlit UI, quant modules, scanner scripts, CLI workflows, or local config behavior.

### Milestone 8.27: Desktop Release Evidence Bundle

**Goal:** Produce a single read-only JSON evidence bundle for desktop release handoff without running the app, installing dependencies, or executing the installer.

**Files likely to change:**

- Create: `run_desktop_release_evidence.py`
- Create: `tests/test_desktop_release_evidence_contracts.py`
- Modify: `run_desktop_release_check.py`
- Modify: `run_desktop_completion_audit.py`
- Modify: `run_final_audit.py`
- Modify: `tests/test_desktop_release_check_contracts.py`
- Modify: `tests/test_desktop_completion_audit_contracts.py`
- Modify: `tests/test_desktop_packaging_contracts.py`
- Modify: `tests/test_delivery_assets_contracts.py`
- Modify: `.pre-commit-config.yaml`
- Modify: `README.md`
- Modify: `docs/WINDOWS_DESKTOP.md`
- Modify: `packaging/windows/README.md`
- Modify: `CONTEXT.md`

**Implementation steps:**

- [x] Add `run_desktop_release_evidence.py` to aggregate completion audit output, desktop doctor output, artifact metadata, ignored path checks, and next packaging commands.
- [x] Keep the script read-only by default; only `--write` writes ignored `dist\release-evidence\NASDX-desktop-release-evidence.json`.
- [x] Add `release_evidence` to the default desktop release gate before portable packaging starts.
- [x] Extend completion/final audits so the release evidence helper, tests, docs, and ignored output path stay covered.
- [x] Add contract tests proving JSON output is machine-readable and fake API key environment variables are not leaked.
- [x] Document the command in README, Windows desktop guide, and packaging README.

**Verification command:**

```powershell
python -B run_desktop_release_evidence.py --json
python -m pytest tests/test_desktop_release_evidence_contracts.py tests/test_desktop_release_check_contracts.py tests/test_desktop_completion_audit_contracts.py tests/test_desktop_packaging_contracts.py tests/test_delivery_assets_contracts.py
python -B run_desktop_completion_audit.py
python -B run_final_audit.py
python -B run_desktop_release_check.py
```

**Rollback risk:** Low. The script is additive, default read-only, and generated evidence stays under ignored `dist/`.

**What not to touch:** Do not run or install the generated installer from this script; do not scan or package `reports/`, API keys, `.env`, `config.toml`, caches, local databases, or logs; do not change Streamlit app logic, quant modules, scanner scripts, CLI workflows, or existing report schemas.

### Milestone 8.28: Release Evidence Targets the Tested Package

**Goal:** Make desktop release evidence describe the actual portable package verified by the current release gate, not only the default formal `dist\NASDX-Desktop` artifact.

**Files likely to change:**

- Modify: `run_desktop_release_evidence.py`
- Modify: `run_desktop_release_check.py`
- Modify: `run_desktop_completion_audit.py`
- Modify: `run_final_audit.py`
- Modify: `tests/test_desktop_release_evidence_contracts.py`
- Modify: `tests/test_desktop_release_check_contracts.py`
- Modify: `tests/test_desktop_completion_audit_contracts.py`
- Modify: `tests/test_delivery_assets_contracts.py`
- Modify: `README.md`
- Modify: `docs/WINDOWS_DESKTOP.md`
- Modify: `packaging/windows/README.md`
- Modify: `CONTEXT.md`

**Implementation steps:**

- [x] Add `--package-dir`, `--zip-path`, `--zip-manifest`, `--installer-path`, and `--installer-proof` to `run_desktop_release_evidence.py`.
- [x] Include the resolved portable package path in JSON evidence.
- [x] Move the default `release_evidence` gate after portable and installed-layout smoke so it can summarize `dist\NASDX-Desktop-check`.
- [x] Keep full release behavior aligned: `--full-package` naturally points evidence at `dist\NASDX-Desktop`.
- [x] Add tests proving explicit `--package-dir` is honored and does not leak fake API keys.
- [x] Update docs and audit markers so release evidence remains tied to the package under test.

**Verification command:**

```powershell
python -B run_desktop_release_evidence.py --json --package-dir dist\NASDX-Desktop-check
python -m pytest tests/test_desktop_release_evidence_contracts.py tests/test_desktop_release_check_contracts.py tests/test_desktop_completion_audit_contracts.py tests/test_delivery_assets_contracts.py
python -B run_desktop_completion_audit.py
python -B run_final_audit.py
python -B run_desktop_release_check.py
```

**Rollback risk:** Low. The change only redirects read-only evidence collection to a caller-selected artifact directory and keeps generated outputs ignored.

**What not to touch:** Do not run the installer; do not require `dist\NASDX-Desktop-check` to be a final dependency-contained release package; do not treat quick-gate evidence as proof of real installer roundtrip; do not change `app.py`, Streamlit routes, quant modules, scanner scripts, CLI workflows, reports, or API key handling.

### Milestone 8.29: Optional Release Gate Evidence Output

**Goal:** Let a packaging machine run the normal desktop release gate and explicitly persist a handoff evidence JSON without making file writes the default behavior.

**Files likely to change:**

- Modify: `run_desktop_release_check.py`
- Modify: `run_desktop_completion_audit.py`
- Modify: `run_final_audit.py`
- Modify: `tests/test_desktop_release_check_contracts.py`
- Modify: `tests/test_desktop_completion_audit_contracts.py`
- Modify: `tests/test_delivery_assets_contracts.py`
- Modify: `README.md`
- Modify: `docs/WINDOWS_DESKTOP.md`
- Modify: `packaging/windows/README.md`
- Modify: `PLANS.md`
- Modify: `CONTEXT.md`

**Implementation steps:**

- [x] Add `--write-evidence` to `run_desktop_release_check.py`.
- [x] Add `--evidence-output`, defaulting to ignored `dist\release-evidence\NASDX-desktop-release-evidence.json`.
- [x] Keep default release gate behavior read-only: it prints JSON evidence and does not write the evidence file.
- [x] When `--write-evidence` is passed, run `run_desktop_release_evidence.py --write --output ... --package-dir <tested package>` after portable and installed-layout smoke.
- [x] Add contract coverage so `--write` is never included in the default command list.
- [x] Document the packaging-machine command and the default no-write boundary.

**Verification command:**

```powershell
python -m pytest tests/test_desktop_release_check_contracts.py tests/test_desktop_completion_audit_contracts.py tests/test_delivery_assets_contracts.py
python -B run_desktop_release_check.py
python -B run_desktop_release_check.py --write-evidence --skip-final-audit
python -B run_final_audit.py
```

**Rollback risk:** Low. The new path is opt-in and writes only under ignored `dist/`.

**What not to touch:** Do not write evidence by default; do not run the installer; do not store API keys, reports, logs, caches, local DBs, `.env`, or `config.toml`; do not change Streamlit UI, quant modules, scanner scripts, report schemas, CLI workflows, or existing API key handling.

### Milestone 8.30: Release Evidence Package Safety Findings

**Goal:** Make the release evidence fail when the tested portable package contains forbidden runtime, secret, report, log, cache, local database, or build-output paths.

**Files likely to change:**

- Modify: `run_desktop_release_evidence.py`
- Modify: `tests/test_desktop_release_evidence_contracts.py`
- Modify: `run_final_audit.py`
- Modify: `tests/test_delivery_assets_contracts.py`
- Modify: `README.md`
- Modify: `docs/WINDOWS_DESKTOP.md`
- Modify: `packaging/windows/README.md`
- Modify: `PLANS.md`
- Modify: `CONTEXT.md`

**Implementation steps:**

- [x] Add forbidden package path patterns for `.env`, `config.toml`, `reports/`, market snapshots, local DBs, logs, generated confidence artifacts, `.git`, `dist/`, `build/`, and `wheelhouse/`.
- [x] Add `forbidden_present` to portable package evidence as package-relative paths only.
- [x] Add `package_forbidden_failures` to the evidence summary and return non-zero when forbidden files are present.
- [x] Add tests proving normal test packages pass, forbidden package files fail, and forbidden file contents are not printed.
- [x] Update audits and docs so the safety evidence remains part of the desktop release contract.

**Verification command:**

```powershell
python -m pytest tests/test_desktop_release_evidence_contracts.py tests/test_desktop_release_check_contracts.py tests/test_desktop_completion_audit_contracts.py tests/test_delivery_assets_contracts.py
python -B run_desktop_release_evidence.py --json --package-dir dist\NASDX-Desktop-check
python -B run_desktop_release_check.py
python -B run_final_audit.py
```

**Rollback risk:** Low. The change is read-only evidence and contract coverage; removing it returns to the prior artifact-summary behavior.

**What not to touch:** Do not read or print file contents from forbidden paths; do not run the installer; do not change packaging copy semantics beyond evidence checks; do not change Streamlit UI, quant modules, scanner scripts, report schemas, CLI workflows, or API key handling.

### Milestone 8.31: Portable Package Cache Scrub

**Goal:** Ensure the portable package build does not copy ignored Python cache files or other generated artifacts from the source checkout into the Windows desktop package.

**Files likely to change:**

- Modify: `packaging/windows/build_portable.ps1`
- Modify: `run_desktop_release_evidence.py`
- Modify: `run_desktop_completion_audit.py`
- Modify: `run_final_audit.py`
- Modify: `tests/test_desktop_packaging_contracts.py`
- Modify: `tests/test_desktop_release_evidence_contracts.py`
- Modify: `tests/test_desktop_completion_audit_contracts.py`
- Modify: `tests/test_delivery_assets_contracts.py`
- Modify: `README.md`
- Modify: `docs/WINDOWS_DESKTOP.md`
- Modify: `packaging/windows/README.md`
- Modify: `PLANS.md`
- Modify: `CONTEXT.md`

**Implementation steps:**

- [x] Add `scrubbed_patterns` to `PACKAGING_MANIFEST.json` for package-local cleanup evidence.
- [x] Add `Remove-PackageExcludedArtifacts` after allow-list copy to remove `__pycache__/`, `*.pyc`, `*.pyo`, reports, logs, user config, local DBs, cache directories, wheelhouse, and build outputs from the package.
- [x] Extend release evidence forbidden patterns to catch `__pycache__/`, `*.pyc`, and `*.pyo` in any package subdirectory.
- [x] Extend completion audit packaged-root checks so cache artifacts make `generated_files_excluded` fail.
- [x] Add contract tests proving skip-dependency packages contain no Python cache artifacts and evidence fails on cache contamination without printing contents.
- [x] Update docs, final audit, and the current context.

**Verification command:**

```powershell
python -m pytest tests/test_desktop_packaging_contracts.py tests/test_desktop_release_evidence_contracts.py tests/test_desktop_completion_audit_contracts.py tests/test_delivery_assets_contracts.py
powershell -ExecutionPolicy Bypass -File packaging\windows\build_portable.ps1 -OutputDir dist\NASDX-Desktop-check -SkipDependencyInstall
python -B run_desktop_release_evidence.py --json --package-dir dist\NASDX-Desktop-check
python -B run_desktop_release_check.py
python -B run_final_audit.py
```

**Rollback risk:** Low to medium. The scrub runs only inside the package output directory after files are copied; the main risk is accidentally removing a package file whose directory name matches a generated-artifact pattern.

**What not to touch:** Do not delete anything from the source checkout; do not scrub the dependency `.venv` after it is created for full packages; do not run installers; do not change `app.py`, Streamlit UI, quant modules, scanner scripts, CLI workflows, reports, or API key handling.

### Milestone 8.32: Portable Zip Forbidden Content Evidence

**Goal:** Ensure the portable zip artifact itself cannot hide runtime, cache, secret, report, local database, or build-output files after the folder package has been scrubbed.

**Files likely to change:**

- Modify: `packaging/windows/build_portable_zip.ps1`
- Modify: `packaging/windows/smoke_portable_zip.ps1`
- Modify: `run_desktop_release_evidence.py`
- Modify: `run_final_audit.py`
- Modify: `tests/test_desktop_packaging_contracts.py`
- Modify: `tests/test_desktop_release_evidence_contracts.py`
- Modify: `tests/test_delivery_assets_contracts.py`
- Modify: `README.md`
- Modify: `docs/WINDOWS_DESKTOP.md`
- Modify: `packaging/windows/README.md`
- Modify: `PLANS.md`
- Modify: `CONTEXT.md`

**Implementation steps:**

- [x] Add pre-zip forbidden artifact scanning to `build_portable_zip.ps1`.
- [x] Add post-extract forbidden artifact scanning to `smoke_portable_zip.ps1`.
- [x] Add zip entry scanning to `run_desktop_release_evidence.py` without reading file contents.
- [x] Add `zip_forbidden_failures` to the release evidence summary.
- [x] Keep the default release gate on `--skip-zip`; only `--zip-package` points evidence at the zip produced and smoked in that run.
- [x] Add tests proving zip build rejects `__pycache__/`/`*.pyc` contamination and release evidence fails on forbidden zip entries without leaking contents.
- [x] Update final audit markers and desktop packaging docs.

**Verification command:**

```powershell
python -m pytest tests/test_desktop_packaging_contracts.py tests/test_desktop_release_evidence_contracts.py tests/test_delivery_assets_contracts.py
powershell -ExecutionPolicy Bypass -File packaging\windows\build_portable.ps1 -OutputDir dist\NASDX-Desktop-check -SkipDependencyInstall
powershell -ExecutionPolicy Bypass -File packaging\windows\build_portable_zip.ps1 -PackageDir dist\NASDX-Desktop-check -OutputZip dist\NASDX-Desktop-check-portable.zip -ManifestPath dist\NASDX-Desktop-check-portable.manifest.json
powershell -ExecutionPolicy Bypass -File packaging\windows\smoke_portable_zip.ps1 -ZipPath dist\NASDX-Desktop-check-portable.zip -ManifestPath dist\NASDX-Desktop-check-portable.manifest.json -Timeout 60
python -B run_desktop_release_evidence.py --json --package-dir dist\NASDX-Desktop-check --zip-path dist\NASDX-Desktop-check-portable.zip --zip-manifest dist\NASDX-Desktop-check-portable.manifest.json
python -B run_desktop_release_check.py
```

**Rollback risk:** Low. The checks are read-only except for the existing explicit zip build command, and they fail fast before packaging or after temporary extraction.

**What not to touch:** Do not inspect forbidden file contents; do not include `.env`, `config.toml`, reports, logs, caches, local DBs, or build artifacts in the zip; do not run installers; do not change Streamlit UI, quant modules, scanner scripts, CLI workflows, reports, or API key handling.

### Milestone 8.33: Portable Manifest Path Hygiene

**Goal:** Prevent distributed portable packages from exposing packaging-machine absolute directories in `PACKAGING_MANIFEST.json`.

**Files likely to change:**

- Modify: `packaging/windows/build_portable.ps1`
- Modify: `run_desktop_release_evidence.py`
- Modify: `run_final_audit.py`
- Modify: `tests/test_desktop_packaging_contracts.py`
- Modify: `tests/test_desktop_release_evidence_contracts.py`
- Modify: `tests/test_delivery_assets_contracts.py`
- Modify: `README.md`
- Modify: `docs/WINDOWS_DESKTOP.md`
- Modify: `packaging/windows/README.md`
- Modify: `PLANS.md`
- Modify: `CONTEXT.md`

**Implementation steps:**

- [x] Replace package-manifest absolute `repo_root` / `output_dir` fields with `path_policy=relative-or-redacted`, `source_root=<source-checkout>`, and `package_root=.`.
- [x] Write `constraints_file` and `wheelhouse_dir` as repository-relative paths or `<external-path>` placeholders.
- [x] Keep the actual build-time absolute paths in script variables so dependency install behavior is unchanged.
- [x] Include the sanitized path-policy fields in release evidence manifest summaries.
- [x] Add contract tests proving generated manifests do not contain the source checkout path, package output path, or temp build path.
- [x] Update final audit markers and desktop packaging docs.

**Verification command:**

```powershell
python -m pytest tests/test_desktop_packaging_contracts.py tests/test_desktop_release_evidence_contracts.py tests/test_delivery_assets_contracts.py
powershell -ExecutionPolicy Bypass -File packaging\windows\build_portable.ps1 -OutputDir dist\NASDX-Desktop-check -SkipDependencyInstall
python -B run_desktop_release_evidence.py --json --package-dir dist\NASDX-Desktop-check --skip-zip
python -B run_desktop_release_check.py
python -B run_final_audit.py
```

**Rollback risk:** Low. This changes manifest metadata only, not runtime files or dependency installation behavior.

**What not to touch:** Do not remove useful inclusion/exclusion metadata from the manifest; do not write absolute packaging-machine paths into distributed artifacts; do not run installers; do not change Streamlit UI, quant modules, scanner scripts, CLI workflows, reports, or API key handling.

### Milestone 8.34: Inno Setup 7 Discovery and Installer Roundtrip Proof

**Goal:** Make the installer release path work on the current Windows packaging machine with Inno Setup 7 installed outside PATH, then prove the setup executable can install, smoke-test, and uninstall while using the bundled `.venv`.

**Files likely to change:**

- Create: `packaging/windows/inno_paths.ps1`
- Create: `desktop/inno.py`
- Modify: `packaging/windows/build_installer.ps1`
- Modify: `packaging/windows/preflight_installer_release.ps1`
- Modify: `packaging/windows/install_inno_setup.ps1`
- Modify: `packaging/windows/build_portable.ps1`
- Modify: `packaging/windows/smoke_installer_roundtrip.ps1`
- Modify: `desktop/doctor.py`
- Modify: `run_desktop_completion_audit.py`
- Modify: `tests/test_desktop_packaging_contracts.py`
- Modify: `tests/test_desktop_completion_audit_contracts.py`
- Modify: `README.md`
- Modify: `docs/WINDOWS_DESKTOP.md`
- Modify: `packaging/windows/README.md`
- Modify: `PLANS.md`
- Modify: `CONTEXT.md`

**Implementation steps:**

- [x] Add shared PowerShell ISCC discovery that checks explicit `-IsccPath`, PATH, Windows uninstall registry metadata, and common Inno Setup 7/6 install locations.
- [x] Add a Python ISCC discovery helper for `desktop doctor` and completion audit so all diagnostics agree with the packaging scripts.
- [x] Update installer preflight, installer build, and bootstrap scripts to use the shared discovery logic.
- [x] Update `build_portable.ps1` so dependency installation keeps the bundled `.venv` but scrubs `__pycache__/`, `*.pyc`, and `*.pyo` after pip finishes.
- [x] Update installer roundtrip smoke to remove its default temporary install directory after uninstall when the directory is empty.
- [x] Update the Inno Setup script to remove the app-owned `{app}` install directory on uninstall so runtime Python caches do not leave a broken install tree behind.
- [x] Rebuild the dependency-contained portable package, rebuild the installer, and run `smoke_installer_roundtrip.ps1 -AllowInstall -CheckShortcuts -RequireVenv`.
- [x] Update docs and tests so `installer_roundtrip` can become PASS when proof matches the current setup executable.

**Verification command:**

```powershell
python -m pytest tests/test_desktop_packaging_contracts.py tests/test_desktop_completion_audit_contracts.py tests/test_desktop_doctor_contracts.py
powershell -ExecutionPolicy Bypass -File packaging\windows\preflight_installer_release.ps1 -RequireVenv -Strict
powershell -ExecutionPolicy Bypass -File packaging\windows\build_portable.ps1 -OutputDir dist\NASDX-Desktop -PipTimeout 120 -PipRetries 3
powershell -ExecutionPolicy Bypass -File packaging\windows\build_installer.ps1 -SkipPortableBuild
powershell -ExecutionPolicy Bypass -File packaging\windows\smoke_installer_roundtrip.ps1 -InstallerPath dist\installer\NASDX-Desktop-Setup.exe -AllowInstall -CheckShortcuts -RequireVenv -Timeout 90
python -B run_desktop_completion_audit.py
python -B run_desktop_release_evidence.py --json --package-dir dist\NASDX-Desktop --zip-path dist\NASDX-Desktop-portable.zip --zip-manifest dist\NASDX-Desktop-portable.manifest.json
python -B run_desktop_release_check.py
python -B run_final_audit.py
```

**Rollback risk:** Medium. This touches release tooling and runs a real user-scope installer, but the installer is still built from the existing portable Streamlit app and the roundtrip installs into a temporary directory by default.

**What not to touch:** Do not run installer smoke without explicit `-AllowInstall`; do not install system software automatically; do not delete user runtime state outside `{app}`; do not package `.env`, `config.toml`, reports, logs, local DBs, caches, or build artifacts; do not change `app.py`, Streamlit routes, quant modules, scanner scripts, CLI workflows, reports, or API key handling.

### Milestone 9: Optional UI Refactor After Desktop MVP

**Goal:** Reduce maintenance cost without changing user workflows or migrating UI frameworks.

**Files likely to change:**

- Create: `ui_helpers.py` or `nasdx/ui/streamlit_tables.py`
- Modify carefully: `app.py`
- Modify: `tests/test_streamlit_state_contracts.py`

**Implementation steps:**

- [x] Extract repeated HTML table builders from the `plan` page into helper functions.
- [x] Keep all page keys, labels, data fields, and report readers unchanged.
- [x] Add tests for helper output escaping and required markers.
- [x] Run full audit after every small extraction.

**Verification command:**

```powershell
python -B -m unittest discover -s tests
python -B run_final_audit.py
```

**Rollback risk:** High compared with launcher work because it touches `app.py`.

**What not to touch:** Do not rewrite `app.py`; do not migrate to Electron/Tauri/Qt; do not change investment logic, schemas, or CLI scripts.

### Milestone 9.1: Streamlit Sidebar Theme Console Regression

**Goal:** Close GitHub issue #27 by removing repeated empty sidebar color warnings without changing the existing dark UI or application workflow.

**Files changed:**

- `requirements_nasdx.txt`
- `packaging/windows/constraints-win.txt`
- `packaging/windows/requirements-win-core.lock`
- `packaging/windows/requirements-win-webview.lock`
- `tests/test_delivery_assets_contracts.py`
- `README.md`
- `CONTEXT.md`
- `PLANS.md`

**Implementation steps:**

- [x] Reproduce the warning on Streamlit 1.52.2 and verify that explicit supported `[theme.sidebar]` colors do not remove it.
- [x] Run the same page on Streamlit 1.59.2 and verify zero browser console warnings.
- [x] Bound the runtime dependency to `>=1.59.2,<1.60.0` and align legacy constraints plus both hashed Windows release locks.
- [x] Add a contract that prevents development and packaged dependency versions from drifting back to the affected release.
- [x] Add a TOML contract that rejects empty color values in root or nested theme sections.
- [x] Verify the plan page at desktop and mobile viewport sizes while preserving the existing dark sidebar.

**Verification command:**

```powershell
python -B -m pytest tests\test_delivery_assets_contracts.py tests\test_desktop_packaging_contracts.py
python -B run_dependency_lock_check.py --static-only
streamlit run app.py --server.port 8513 --browser.serverPort 8513
python -B run_final_audit.py
```

**Rollback risk:** Low. The UI and theme files are unchanged; the release dependency graph moves to the tested Streamlit patch line and its compatible PyArrow version.

**What not to touch:** Do not suppress browser warnings, patch installed Streamlit files, add undocumented theme keys, change page routes, or modify investment logic.

### Milestone 9.2: GitHub Actions Node 24 Migration

**Goal:** Close GitHub issue #28 by removing the Node.js 20 deprecation annotation from the Windows desktop workflow.

**Files changed:**

- `.github/workflows/windows-desktop.yml`
- `tests/test_desktop_ci_contracts.py`
- `README.md`
- `docs/WINDOWS_DESKTOP.md`
- `CONTEXT.md`
- `PLANS.md`

**Implementation steps:**

- [x] Capture the Node.js 20 deprecation annotation from a successful master workflow run.
- [x] Upgrade checkout to `actions/checkout@v5` and Python setup to `actions/setup-python@v6`.
- [x] Add CI contracts that require the Node 24 action lines and reject the deprecated majors.
- [x] Run the full local gates and verify a new master workflow run has no Node.js 20 annotation.

**Verification command:**

```powershell
python -B -m pytest tests\test_desktop_ci_contracts.py tests\test_delivery_assets_contracts.py tests\test_desktop_release_check_contracts.py
python -B -m pytest
python -B run_final_audit.py
```

**Rollback risk:** Low. Only official action runtime majors change; Python, runner image, dependency locks, security order, and release commands remain unchanged.

**What not to touch:** Do not change CI secrets, permissions, branch triggers, Python version, dependency toolchain pins, installer behavior, or application code.

## Self-Review Against Requirements

| Requirement | Covered |
|---|---|
| Current repository structure | Section 1 |
| Existing entry points and commands | Section 2 |
| Existing Streamlit UI structure | Section 3 |
| Existing data layer | Section 4 |
| Existing quant/backtest/factor modules | Section 5 |
| Generated files and files not to commit | Section 6 |
| What should be preserved | Section 7 |
| What should be refactored | Section 8 |
| Recommended Windows desktop strategy | Section 9 |
| Recommended packaging strategy | Section 10 |
| Testing strategy | Section 11 |
| Step-by-step milestones with required fields | Section 12 |
| Dependency plan grouped by requested tool category | Phase 1 Dependency Plan |

Current stop point: Milestone 9.1 is implemented; Streamlit is bounded to the browser-verified `>=1.59.2,<1.60.0` line, both Windows release locks use 1.59.2, and the plan page renders at desktop/mobile sizes with zero console errors or warnings. Milestone 9 remains implemented; the 10 investment-plan table builders live in the Streamlit-independent `nasdx/ui/plan_tables.py`, with shared escaping, safe external links, empty-state and marker contracts, while `app.py` keeps its route keys, report readers, data fields, labels, and call sites. Milestone 8.34 Inno Setup 7 discovery and installer roundtrip proof remains implemented; `D:\Inno Setup 7\ISCC.exe` is detected through shared PATH/registry/common-path discovery, `NASDX-Desktop-Setup.exe` compiles, and `smoke_installer_roundtrip.ps1 -AllowInstall -CheckShortcuts -RequireVenv` has installed to a temporary directory, run installed smoke with the bundled `.venv`, uninstalled, removed shortcuts, and written ignored proof for completion audit. Application logic still should not be rewritten.
