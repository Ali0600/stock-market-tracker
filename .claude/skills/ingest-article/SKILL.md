---
name: ingest-article
description: Ingest a market/stock article into the Stocks Vault — fact-check its claims, route Facts/Speculations into the correlated stock and sector notes, and capture each mentioned ticker's price at publication. Use when the user says "ingest <url>", "add this article to the vault", or points at a file in the vault _inbox.
---

# Ingest an article into the Stocks Vault

Vault: `~/Documents/Stocks Vault` (override via `STOCKS_VAULT` env). **Read the
vault's `SCHEMA.md` first** — it is the constitution; its rules win over this
file if they ever drift. Tracker API: http://127.0.0.1:8000 (start with
`./run.sh` from the app repo if down).

Input (`$ARGUMENTS`): a URL, or the path/filename of a file in `_inbox/`.

## Procedure

1. **Dedupe** — grep `log.md` and `Articles/*.md` frontmatter for the URL or
   title. Already ingested → say so, stop.

2. **Get content** — WebFetch the URL. Paywalled or fetch fails → ask the user
   to save the article text into `_inbox/` and stop. For `_inbox` files, Read
   them (the file may be md/txt; PDFs via the Read tool).

3. **Identify** — publish date; every company mentioned → ticker (tracked
   portfolio: `curl -s localhost:8000/api/sectors` and the vault `index.md`
   list); affected sectors. Untracked tickers still get price capture in the
   article note — but no stock note is created for them.

4. **Extract & verify claims** (the 5–10 that matter):
   - Market/price/valuation claims → verify against the tracker
     (`/api/stocks/{t}/detail`) or yfinance history via
     `venv/bin/python` in the app repo.
   - Business/factual claims → WebSearch when material.
   - Label each **verified / unverified / inaccurate** + one line on how it
     was checked. An article with several *inaccurate* claims should say so
     prominently in its Summary.

5. **Capture publication prices** — per mentioned ticker, the daily close on
   the publish date (yfinance: `yf.download(t, start=pub, end=pub+3d)`, first
   row). Non-trading day → the prior session's close. Unknown publish date →
   today's price, and say so in the note.

6. **Write the article note** at `Articles/YYYY-MM-DD <slug>.md`:

   ```markdown
   ---
   title: <headline>
   url: <url>
   published: YYYY-MM-DD
   ingested: YYYY-MM-DD
   tickers: NVDA, TSM
   prices:
     NVDA: 200.42
     TSM: 408.75
   ---
   # <headline>

   ## Summary
   2–4 sentences, neutral tone.

   ## Claims & Verification
   - "<claim>" — **verified** · <how checked>
   - "<claim>" — **inaccurate** · <what the data actually shows>

   ## Takeaways Routed
   - [[NVDA]] ← fact: <...>
   - [[Semiconductors]] ← speculation: <...>

   ## Price Since Publication
   <!-- prices:start -->
   <!-- prices:end -->
   ```

   Then run `venv/bin/python scripts/refresh_article_prices.py` (app repo) to
   fill the price table.

7. **Route takeaways** — append to each affected stock/sector note:
   - `## Facts`: `- YYYY-MM-DD — <verified, durable takeaway> ([[article note]])`
   - `## Speculations`: same + ` (status: open)` for forecasts/unverified claims
   - `## Articles`: `- [[article note]]`
   - bump the note's `updated:` frontmatter.
   House rule: **never buy/sell/hold or position-advice language.**

8. **Bookkeep** — add the article under `## Articles` in `index.md`
   (newest first); append one line to `log.md`:
   `- YYYY-MM-DD · ingested [[<article>]] → updated NVDA, TSM, Semiconductors`.

9. **Report in chat** — claims verified/flagged, notes touched, price
   baselines captured, anything needing the user's judgment.
