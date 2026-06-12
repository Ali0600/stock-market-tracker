# Market Tracker — project notes

## Run
- `./run.sh` → http://127.0.0.1:8000 (FastAPI via uvicorn).
- Venv is `venv/`, NOT `.venv/` (sandboxed tools can't read dot-directories here). The rename broke the console-script shebangs — always invoke `venv/bin/python -m uvicorn ...`, never `venv/bin/uvicorn`.
- Needs Homebrew Python 3.12 (`/opt/homebrew/bin/python3.12`); system `python3` is 3.9 and too old for the pinned deps.

## Architecture map
- `app/main.py` — API routes · `app/prices.py` — yfinance batch fetch, TTL cache (10 min daily / 5 min intraday), period windowing · `app/metrics.py` — "Things to Track" registry · `app/db.py` — SQLite at `data/tracker.db` (gitignored).
- Adding a Things-to-Track column = one `@metric(...)` function in `app/metrics.py`; it auto-appears in the UI column picker. No frontend changes needed.
- Current Price stays live by folding 1-minute intraday bars into the daily window (`_with_live_bar` in `app/prices.py`) — Yahoo's daily feed sometimes returns a NaN close for the latest session. Don't "simplify" this away.
- DB seeds the owner's 27-stock portfolio across 9 thematic sectors on first run (`SEED_PORTFOLIO` in `app/db.py`). The owner's list said "OKLA" — seeded as OKLO (Oklo Inc.) because OKLA has no Yahoo data.
- Per-stock AI analyses (markdown in `stocks.analysis`) are seeded/refreshed by `scripts/seed_analyses.py`; they're informational research notes — never add buy/sell/hold language. Detail view: `#/stock/<TICKER>`, backed by `GET /api/stocks/{ticker}/detail`.

## Stocks Vault (Obsidian knowledge base)
- Karpathy-LLM-wiki at `~/Documents/Stocks Vault` (env `STOCKS_VAULT`); read its `SCHEMA.md` before touching it. `app/vault.py` gives the app READ-ONLY access — the app never writes the vault; Claude and the user are the writers.
- Ingest articles with the `/ingest-article` skill; health-check with `/vault-lint`; `scripts/refresh_article_prices.py` updates the per-article price tables.
- `scripts/create_llm_wiki.py` = standalone generic scaffolder for Karpathy-style vaults on any topic (`--from-tracker` reproduces the Stocks Vault; `--export-config` makes it portable). Read-only DB view lives at `#/db` (`GET /api/db`, `GET /api/db/prices/{ticker}`).
- **Regeneration contract**: when refreshing the SQLite AI analyses, first read the stock's vault note and ground the synthesis in its Facts/Speculations; append a `log.md` line. Vault = knowledge base, DB = rendered synthesis.

## Analysis tools & boundaries
- `app/backtest.py` (`POST /api/backtest`, `#/backtest`) and `GET /api/deviations` (`#/deviations`) are **descriptive-only by explicit agreement with the owner**: never add buy/sell/hold output, signal language, position-sizing advice, or auto-firing alerts anywhere in the app or vault. The tools compute; the owner decides.
- Backtest honesty invariants to preserve when touching the engine: trailing stats shifted one day (no look-ahead), limit fills require the day's range to reach the price, gaps fill at the open, same-day stop-before-target, no entry-day take-profit, costs on by default, buy-&-hold benchmark always shown. Engine changes must keep the hand-computed fixture tests passing (currently a scratch file in /tmp — moving them into the repo is on the backlog).

## Workflow
- Commit each verified change as you go (owner preference). Descriptive messages, author = owner only — never a Claude co-author trailer. The repo is destined for public GitHub: run the leak check (no `data/`, `venv/`, `.claude/settings.local.json`, secrets, or absolute personal paths) before commits that add files.

## Verifying UI changes
- The Claude Preview panel cannot start servers from this folder (see global CLAUDE.md → machine notes). Run the server with Bash and screenshot with headless Chrome instead.
