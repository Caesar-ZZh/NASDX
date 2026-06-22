# Changelog

## 2026-06-22

- Added five `.claude/agents` templates for upstream analysis, single-feature implementation, contract audit, Streamlit verification, and delivery closeout.
- Added `docs/SUBAGENT_WORKFLOW.md` to document the subagent collaboration model and automation safety boundaries.
- Added `run_product_readiness.py` as a product-readiness runner for unit tests, final audit, and optional environment-only LLM smoke validation.
- Added contract tests for the subagent templates, workflow documentation, and readiness runner.

## 2026-06-18

- Refactored Phase 1 research execution so the five specialist agents run through `ThreadPoolExecutor` by default, while preserving `AGENT_ORDER` in returned results.
- Added `NASDX_RESEARCH_MAX_WORKERS` as an operational switch; set it to `1` to force sequential research execution for debugging or rate-limit control.
- Removed the Streamlit entrypoint's global `requests.get` monkey patch. HTTP routing policy is no longer applied from `app.py`.
- Added architecture contract tests in `tests/test_architecture_contracts.py` for research concurrency and HTTP monkey patch regression protection.
- Extended `run_final_audit.py` with a production-readiness check covering Phase 1 concurrency and Streamlit HTTP isolation.
- Added an intraday data-gate guard to execution queues so stale-data routes still expose pre-market, intraday, and post-market stages.
- Added `nasdx.history_store` with a local `nasdx_history.db` SQLite history store for generated artifacts, single-stock reports, daily scans, and ETF pool snapshots.
- Wired investment briefs, portfolio plans, recommendation reviews, account reviews, report saves, and scanner JSON outputs to append SQLite history while keeping the existing Markdown/JSON files unchanged.
- Removed remaining import-time HTTP side effects from `fetch_stock_data.py`, `scan_etf50.py`, `quant.data`, and `quant.patch_requests`; added regression coverage so data modules do not mutate global `requests` or proxy environment variables on import.
- Added structured LLM output handling: agents now append a JSON response contract and prefer `signal`, `confidence`, `conclusion`, and `key_points` payload fields before falling back to legacy text-tail parsing.
- Hardened Streamlit state boundaries: API configuration is passed only to subprocess environments, background threads are tracked by `task_id` outside `session_state`, and analysis logs now include a unique task id.
