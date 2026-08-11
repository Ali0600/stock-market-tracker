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
- `app/backtest.py` (`POST /api/backtest`, `#/backtest`), `GET /api/deviations` (`#/deviations`) and `app/alerts.py` (`/api/alerts`, `#/alerts`) are **descriptive-only by explicit agreement with the owner**: never add buy/sell/hold output, signal language, or position-sizing advice anywhere in the app or vault. The tools compute; the owner decides.
- **Alerts boundary (refined 2026-08-11)**: an alert is a condition the *owner* writes over an existing statistic, and the app reports only whether it currently holds plus the observed value. Never auto-create rules, suggest thresholds, or add urgency/action copy — and never let the app fire a rule it invented. A statistic that can't be computed is reported as `unavailable`, never folded into "not met", so silence always means one specific thing. Unknown stat keys are refused at creation for the same reason. `tests/test_alerts.py` asserts no trading vocabulary reaches the API payload or the stat labels.
- Alert stat keys reuse the frontend's column convention (`m:<metric>`, bare core column, `z:<deviation>`) so the All-view filter engine and the alert engine stay interchangeable; a node-subprocess parity test pins that both answer identically.
- Backtest honesty invariants to preserve when touching the engine: trailing stats shifted one day (no look-ahead), limit fills require the day's range to reach the price, gaps fill at the open, same-day stop-before-target, no entry-day take-profit, costs on by default, buy-&-hold benchmark always shown. These are pinned by hand-computed fixtures in `tests/test_backtest.py` — engine changes must keep them green.

## Tests
- `venv/bin/python -m pytest -q` (backend) · `node --test tests/frontend/*.test.mjs` (frontend, no npm) · `venv/bin/ruff check app/ tests/ scripts/`. All three run in CI (`.github/workflows/ci.yml`) on push and PR.
- `node --test <dir>` does NOT walk a directory on Node 22 — it treats the path as a module and fails. Always pass the glob.
- Tests never hit the network or `data/tracker.db`: `tests/conftest.py` builds synthetic OHLC frames, and the API tests monkeypatch `db.DB_PATH` to a tmp file plus stub every `prices.*`/`vault.*` call.
- Prove a new gate fails before trusting it: sabotage the code with an inverse Edit pair (never `git checkout` — the tree usually has uncommitted work), confirm the *specific* test goes red, restore, and check the file's checksum returned to its pre-sabotage value. Two tests here passed against sabotage and had to be strengthened — a metric that reports the same value scoped or unscoped can't detect a scoping regression.
- Lint config lives in `pyproject.toml` and deliberately selects the correctness families (`F`, `E4/E7/E9`, `B`, `I`) rather than the style-modernization ones — the codebase consistently uses `Optional[...]`, and churning that would bury real findings.

## Workflow
- Commit each verified change as you go (owner preference). Descriptive messages, author = owner only — never a Claude co-author trailer. The repo is destined for public GitHub: run the leak check (no `data/`, `venv/`, `.claude/settings.local.json`, secrets, or absolute personal paths) before commits that add files.
- Port 8000 is often taken by another local app — verify on a free port (e.g. 8010) rather than killing whatever holds it.

## Verifying UI changes
- The Claude Preview panel cannot start servers from this folder (see global CLAUDE.md → machine notes). Run the server with Bash and screenshot with headless Chrome instead.
