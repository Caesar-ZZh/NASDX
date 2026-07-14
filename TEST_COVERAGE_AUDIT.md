# NASDX Test Coverage Audit

本轮范围：只做审计和文档。结论先行：现有 114 个测试里，桌面打包合同覆盖强，投研核心和策略可信度覆盖弱。

## Current Test Shape

| Area | Evidence |
|---|---|
| Total tests | `rg -n "def test_" tests` => 114 |
| Desktop-heavy files | `tests/test_desktop_launcher_contracts.py` 24, `tests/test_desktop_packaging_contracts.py` 26 |
| Quant core | `tests/test_quant_core_contracts.py` 6 |
| Workflow | `tests/test_workflow_contracts.py` 4 |
| Streamlit state | `tests/test_streamlit_state_contracts.py` 3, mostly source marker checks |
| Readiness gate | `run_product_readiness.py:34` runs unittest plus final audit |

## Findings

| Priority | Gap | Evidence | Why it matters | Suggested tests |
|---|---|---|---|---|
| P1 | No no-lookahead backtest contract | `tests/test_quant_core_contracts.py:76`, `quant/backtest.py:125` | P0 strategy issue can recur silently | Strategy only sees data before execution bar; trade date is next bar |
| P1 | No ETF50 rolling rebalance contract | `quant/etf50_quant.py:183` | Full-sample TopN bias not blocked | Synthetic two-ETF sample where future winner differs from early winner |
| P1 | No reports/runtime path contract | `desktop/paths.py:58`, `nasdx/portfolio.py:213` | Desktop install can write into app dir | Temp `NASDX_REPORTS_DIR` full plan/brief/snapshot tests |
| P1 | No scanner import-safe contract | `scan_etf50.py:26`, `scan_stocks_full.py:177` | Importing modules can run network/write logic | Import scanner modules under monkeypatch and assert no writes |
| P1 | Selector failure and stale latest not covered | `selector_page.py:90`, `selector_page.py:200` | UI can show old result as fresh | Mock subprocess returncode != 0 and assert error state |
| P1 | Workflow service behavior only lightly tested | `tests/test_workflow_contracts.py:45`, `run_investment_workflow.py:185` | quick/full/selector failures not fully protected | Mock step runner for all workflow modes and stop-on-fail |
| P2 | Stock pool semantics not tested | `scan_stocks_full.py:18` | Board mismatch corrupts explanations | Pool config test for code uniqueness, board labels, expected count |
| P2 | UI flow mostly source-marker based | `tests/test_streamlit_state_contracts.py:9` | Real buttons can regress while markers pass | Small service-level UI action tests before Playwright |

## Recommended Validation Commands

| Scope | Command |
|---|---|
| Core non-UI | `python -B -m pytest -p no:cacheprovider tests/test_quant_core_contracts.py tests/test_workflow_contracts.py tests/test_history_store_contracts.py` |
| Desktop contracts | `python -B -m pytest -p no:cacheprovider tests/test_desktop_launcher_contracts.py tests/test_desktop_packaging_contracts.py tests/test_desktop_release_evidence_contracts.py` |
| Product readiness | `python -B run_product_readiness.py` |
| Release evidence | `python -B run_desktop_release_evidence.py --json --package-dir dist\\NASDX-Desktop` |
| Security | `python -B run_security_checks.py --skip-optional` |

## Test Roadmap

| Phase | Add tests for | Gate |
|---|---|---|
| A | performance/cache/path contracts, scanner import-safe, selector failure state | `pytest -p no:cacheprovider` |
| B | no-lookahead, rolling rebalance, pool semantics, selector sector mapping | quant/strategy tests |
| C | workflow service, report history, installed runtime write boundary | release check + product readiness |

## 2026-07-14 Plan Table Coverage Update

| Item | Evidence |
|---|---|
| Full suite | 208 tests collected and 208 passed under CPython 3.11.9. |
| New UI helper contracts | `tests/test_plan_table_contracts.py` covers all 10 builders, empty states, required table markers, hostile text escaping, rich-cell escaping, safe external links, app wiring, and final-audit ownership. |
| Shared security boundary | `tests/test_ui_security_contracts.py` now verifies that both `app.py` and `nasdx/ui/plan_tables.py` use the shared HTML/URL safety helpers. |
| Runtime evidence | Playwright opened `/?page=plan`; the page rendered 10 `.plan-table` tables with zero Streamlit exceptions and zero browser console errors. |
