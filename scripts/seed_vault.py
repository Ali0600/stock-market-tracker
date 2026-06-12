"""Create and seed the Obsidian "Stocks Vault" skeleton.

Safe to re-run: existing files are never overwritten (prints created/skipped).
Stock-note Overviews are seeded from the tracker's stored AI analyses.

    venv/bin/python scripts/seed_vault.py
"""
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import db  # noqa: E402
from app.vault import VAULT_PATH  # noqa: E402

TODAY = date.today().isoformat()

SECTOR_OVERVIEWS = {
    "Semiconductors": "Chip designers, the leading-edge foundry, and wafer-fab equipment — the supply side of the AI build-out.",
    "Networking & Optical": "Optical transceivers, photonics and network gear connecting AI data centers.",
    "AI Infrastructure": "Servers, GPU clouds and powered land — the build-and-operate layer of AI compute.",
    "Software & Cloud": "Platform software incumbents navigating the AI transition.",
    "Consumer & Fintech": "Consumer-facing platforms: gaming, retail brokerage, and a packaged-food turnaround.",
    "Frontier Tech": "Pre-commercial moonshots: quantum computing, satellite direct-to-device, nuclear SMRs.",
    "Biotech": "Clinical-stage therapeutics: NK-cell platforms, kinase inhibitors, RNA editing.",
    "Cannabis": "Canadian licensed producers diversifying while awaiting US federal reform.",
    "Materials": "US rare-earth mining and magnet production.",
}

SCHEMA_MD = f"""# Stocks Vault — Schema & Conventions

An LLM-maintained wiki (Karpathy pattern) for stocks and market sectors.
Claude reads this file before touching the vault.

## Layers
1. **Raw sources** — `Articles/` and `_inbox/`. Immutable once ingested (only
   the auto-refreshed price table inside an article note may change).
2. **Wiki** — `Stocks/` and `Sectors/` notes. Maintained by Claude; the human
   edits anything at will in Obsidian.
3. **Schema** — this file.

`index.md` is the catalog (read it first). `log.md` is an append-only
chronological record of every change.

## Division of labor
- **Human**: curates sources (URLs or files in `_inbox/`), asks questions,
  reviews and edits notes.
- **Claude**: all bookkeeping — summarizing, fact-checking, routing takeaways,
  cross-linking, maintaining `index.md` and `log.md`.
- **The tracker app** (`~/Documents/stock-analysis-ui`) only READS this vault;
  it renders stock/sector notes and article price impact in its detail views.

## Rules
- Every Facts/Speculations bullet: `- YYYY-MM-DD — <statement> ([[article note]])`.
  No unsourced claims in wiki notes.
- **Fact** = verified against market data or corroborated reporting.
  **Speculation** = forecast or unverified claim; ends with `(status: open)`.
  When later evidence lands, flip to `confirmed`/`busted` with the new date and
  source rather than deleting.
- Never use buy/sell/hold or position-advice language anywhere in the vault.
- Article frontmatter `prices:` = close on the publish date (prior close on
  non-trading days; ingestion-day price if the publish date is unknown — note it).
- Filenames: stocks `TICKER.md` · sectors exactly the sector name ·
  articles `YYYY-MM-DD short-slug.md`.
- After any change: bump `updated:` on touched notes, refresh `index.md`,
  append one line to `log.md`. Never rewrite `log.md` history.
- Ignore Obsidian/iCloud conflicted copies (`NVDA 2.md`) — flag them in chat.

## Workflows
- **Ingest** an article: `/ingest-article <url-or-inbox-path>` in Claude Code.
- **Lint** the vault: `/vault-lint` — contradictions, stale speculations,
  orphan articles, missing links.
- **Synthesis**: when the tracker's AI analyses are regenerated, they must be
  grounded in each stock's vault Facts/Speculations.

*Vault created {TODAY}.*
"""

STOCK_TEMPLATE = """---
ticker: {ticker}
sector: {sector}
updated: {today}
---
# {name} ({ticker})

## Overview
{overview}

## Facts

## Speculations

## Open Questions

## Articles
"""

SECTOR_TEMPLATE = """---
sector: {name}
updated: {today}
---
# {name}

## Overview
{overview}

## Members
{members}

## Facts

## Speculations

## Open Questions

## Articles
"""


def write_if_missing(path: Path, content: str) -> bool:
    if path.exists():
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return True


def overview_from_analysis(analysis: str | None) -> str:
    if not analysis:
        return "_(no overview yet)_"
    return analysis.split("\n## ")[0].strip()


def main() -> None:
    db.init()
    sectors = db.sectors_with_stocks()
    created = skipped = 0

    for folder in ("Stocks", "Sectors", "Articles", "_inbox"):
        (VAULT_PATH / folder).mkdir(parents=True, exist_ok=True)

    files: list[tuple[Path, str]] = [(VAULT_PATH / "SCHEMA.md", SCHEMA_MD)]

    index_sectors = []
    for sec in sectors:
        members = "\n".join(
            f"- [[{s['ticker']}]] — {s['name']}" for s in sec["stocks"]) or "_(empty)_"
        files.append((
            VAULT_PATH / "Sectors" / f"{sec['name']}.md",
            SECTOR_TEMPLATE.format(
                name=sec["name"], today=TODAY,
                overview=SECTOR_OVERVIEWS.get(sec["name"], "_(add an overview)_"),
                members=members),
        ))
        tickers = " · ".join(f"[[{s['ticker']}]]" for s in sec["stocks"])
        index_sectors.append(f"- [[{sec['name']}]] ({len(sec['stocks'])}): {tickers}")

        for s in sec["stocks"]:
            stock = db.get_stock_by_ticker(s["ticker"])
            files.append((
                VAULT_PATH / "Stocks" / f"{s['ticker']}.md",
                STOCK_TEMPLATE.format(
                    ticker=s["ticker"], sector=sec["name"], today=TODAY,
                    name=stock["name"] or s["ticker"],
                    overview=overview_from_analysis(stock["analysis"])),
            ))

    files.append((
        VAULT_PATH / "index.md",
        "# Stocks Vault — Index\n\nRead this first. Maintained by Claude after every change.\n\n"
        "## Sectors\n" + "\n".join(index_sectors) +
        "\n\n## Articles\n_(none ingested yet)_\n",
    ))
    files.append((
        VAULT_PATH / "log.md",
        "# Change log (append-only)\n\n"
        f"- {TODAY} · vault created; seeded 27 stock notes + 9 sector notes "
        "(overviews from tracker AI analyses)\n",
    ))

    for path, content in files:
        if write_if_missing(path, content):
            created += 1
        else:
            skipped += 1
    print(f"Vault at {VAULT_PATH}: created {created}, skipped {skipped} existing.")


if __name__ == "__main__":
    main()
