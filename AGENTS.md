# AGENTS.md

## Project

NASDX is an A-share quantitative analysis and multi-agent investment research system.

It currently uses:
- Python
- Streamlit
- AkShare
- mootdx fallback data source
- OpenAI-compatible LLM APIs
- pandas-based factor and backtesting modules
- local JSON/HTML report outputs

The goal is to evolve this project into a maintainable Windows desktop application without rewriting the existing system from scratch.

## Current Important Entry Points

- `app.py`: Streamlit UI entry
- `启动网页.bat`: Windows one-click Streamlit launcher
- `fetch_stock_data.py`: market data refresh
- `scan_etf50.py`: ETF50 rule-based scan
- `scan_stocks_full.py`: stock scan
- `run_analysis.py`: single-stock multi-agent analysis
- `run_investment_workflow.py`: one-click investment workflow
- `run_portfolio_plan.py`: portfolio plan generation
- `run_final_audit.py`: final self-check
- `quant_page.py`: quantitative strategy page
- `quant/data.py`: unified OHLCV data layer
- `quant/factors.py`: factor calculation
- `quant/backtest.py`: backtesting engine

## Do Not Rewrite

Do not rewrite the entire project.
Do not replace Streamlit until a desktop launcher/wrapper approach has been evaluated.
Do not remove existing CLI scripts.
Do not break existing README commands.
Do not hardcode API keys.
Do not commit generated reports, logs, local config, cache files, or build artifacts.

## Preferred Direction

First preserve the existing app and make it easier to run on Windows.

The preferred path is:
1. Improve dependency management.
2. Add safe local configuration.
3. Extract service-layer functions from the Streamlit UI only when safe.
4. Add a Windows desktop launcher or wrapper.
5. Add packaging with PyInstaller or another suitable tool.
6. Add smoke tests and unit tests for non-UI modules.

## Architecture Rules

Keep these layers separate where possible:

- UI layer: Streamlit pages and desktop launcher
- Service layer: workflow orchestration
- Data layer: AkShare/mootdx adapters and standardized OHLCV
- Quant layer: factors, strategies, backtesting, overfitting diagnostics
- Agent layer: LLM-based analysis
- Report layer: JSON/HTML/Markdown outputs
- Config layer: user settings, API keys, paths

## Security Rules

Never commit:
- `.env`
- `config.toml`
- API keys
- local reports
- logs
- cache files
- local databases
- packaged executables
- build artifacts

Use `config.example.toml` for examples only.

## Testing Priority

Prioritize tests for:
- code classification: stock vs ETF
- OHLCV standardization
- factor calculation
- backtest metrics
- strategy output
- config loading
- smoke import of app modules

Do not spend too much effort on Streamlit UI tests initially.

## Verification

After each change, provide exact commands such as:

```bash
python -m compileall .
python run_final_audit.py
streamlit run app.py
python -m pytest
```
