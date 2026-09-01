# Changelog

## 2026-09-01

- Added a background job center (`server/jobhub.py` + `server/jobs_api.py`) so slow analyses run detached from the HTTP connection: submit returns a `job_id` immediately, then poll with a cursor for incremental events. `/api/jobs/analysis` and `/api/jobs/debate` wrap the existing deep-analysis and multi-agent debate pipelines; the old synchronous `/api/analysis/{code}` and `/api/debate` endpoints stay untouched.
- Deep Analysis and Multi-Agent Debate pages now use background jobs: the job id is persisted to localStorage, so navigating away (or even refreshing) does not cancel the work — come back later and the result is already there. Debate keeps its progressive per-speaker rendering by replaying streamed events from the job log.
- Job center is in-memory, single-process, with a 5k-event cap per job, TTL reaping of terminal results, and cooperative cancellation (`NASDX_JOB_MAX_WORKERS` to tune parallelism).
- Added `tests/test_jobhub_contracts.py` covering the job state machine, cursor increments, stale-cursor full replay, cancellation, generator runners, TTL reaping, and HTTP route contracts.

## 2026-06-22

- Added five `.claude/agents` templates for upstream analysis, single-feature implementation, contract audit, Streamlit verification, and delivery closeout.
- Added `docs/SUBAGENT_WORKFLOW.md` to document the subagent collaboration model and automation safety boundaries.
- Added `run_product_readiness.py` as a product-readiness runner for unit tests, final audit, and optional environment-only LLM smoke validation.
- Added contract tests for the subagent templates, workflow documentation, and readiness runner.
- Made `requirements_nasdx.txt` versionable, expanded its runtime dependencies, and added a final-audit dependency manifest check.

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
