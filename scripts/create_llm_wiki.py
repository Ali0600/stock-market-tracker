#!/usr/bin/env python3
"""create_llm_wiki.py — scaffold a Karpathy-style LLM-wiki Obsidian vault for ANY topic.

Standalone: Python 3.9+, stdlib only, no app imports. Never overwrites
existing files, so it is safe to re-run over a live vault.

Modes
-----
1) Generic, from a config file:

       python3 create_llm_wiki.py my-topic.json

2) Replicate this repo's Stocks Vault from the tracker database (reads
   data/tracker.db and, when present, the existing vault's Overview sections):

       python3 create_llm_wiki.py --from-tracker [path/to/tracker.db]

   Add --export-config stocks-vault.json to also write the equivalent config
   (carry it to another machine and run mode 1 there).

Options: --skills-dir PATH writes the ingest/lint skill files into a project's
.claude/skills/ directory; otherwise they land in <vault>/_skills/ to copy
manually.

Config format (JSON)
--------------------
{
  "vault_path": "~/Documents/Research Vault",
  "topic": "AI Research",
  "entity_label": "Paper",          // singular noun for a tracked entity
  "entities_dir": "Papers",         // folder name for entity notes
  "category_label": "Field",
  "categories_dir": "Fields",
  "price_tracking": false,          // true = stocks-style price-at-publication capture
  "extra_rules": ["Optional extra SCHEMA.md rules"],
  "categories": {"Alignment": "One-line overview of the category."},
  "entities": [
    {"key": "SCALING-LAWS", "name": "Scaling Laws (Kaplan et al.)",
     "category": "Foundations", "overview": "What this entity is."}
  ]
}
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sqlite3
import sys
from datetime import date
from pathlib import Path

TODAY = date.today().isoformat()

# --------------------------------------------------------------- templates

SCHEMA_TEMPLATE = """# {topic} Vault — Schema & Conventions

An LLM-maintained wiki (Karpathy pattern, gist 442a6bf) for {topic}.
Claude reads this file before touching the vault.

## Layers
1. **Raw sources** — `Articles/` and `_inbox/`. Immutable once ingested{price_immutable_note}.
2. **Wiki** — `{entities_dir}/` and `{categories_dir}/` notes. Maintained by
   Claude; the human edits anything at will in Obsidian.
3. **Schema** — this file.

`index.md` is the catalog (read it first). `log.md` is an append-only
chronological record of every change.

## Division of labor
- **Human**: curates sources (URLs or files in `_inbox/`), asks questions,
  reviews and edits notes.
- **Claude**: all bookkeeping — summarizing, fact-checking, routing takeaways,
  cross-linking, maintaining `index.md` and `log.md`.

## Rules
- Every Facts/Speculations bullet: `- YYYY-MM-DD — <statement> ([[article note]])`.
  No unsourced claims in wiki notes.
- **Fact** = verified against authoritative data or corroborated reporting.
  **Speculation** = forecast or unverified claim; ends with `(status: open)`.
  When later evidence lands, flip to `confirmed`/`busted` with the new date and
  source rather than deleting.
{price_rule}{extra_rules}- Filenames: {entity_label_lower}s `KEY.md` · {category_label_lower}s exactly the
  {category_label_lower} name · articles `YYYY-MM-DD short-slug.md`.
- After any change: bump `updated:` on touched notes, refresh `index.md`,
  append one line to `log.md`. Never rewrite `log.md` history.
- Ignore Obsidian/iCloud conflicted copies (`X 2.md`) — flag them in chat.

## Workflows
- **Ingest** an article/source: `/ingest-article <url-or-inbox-path>` in Claude Code.
- **Lint** the vault: `/vault-lint` — contradictions, stale speculations,
  orphan articles, missing links.

*Vault created {today} by create_llm_wiki.py.*
"""

ENTITY_TEMPLATE = """---
{key_field}: {key}
{category_field}: {category}
updated: {today}
---
# {name} ({key})

## Overview
{overview}

## Facts

## Speculations

## Open Questions

## Articles
"""

CATEGORY_TEMPLATE = """---
{category_field}: {name}
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

INGEST_SKILL_TEMPLATE = """---
name: ingest-article
description: Ingest a source article into the {topic} vault — fact-check its claims and route Facts/Speculations into the correlated {entity_label_lower} and {category_label_lower} notes{price_desc}. Use when the user says "ingest <url>" or points at a file in the vault _inbox.
---

# Ingest an article into the {topic} vault

Vault: `{vault_path}`. **Read the vault's `SCHEMA.md` first** — it is the
constitution; its rules win over this file if they ever drift.

Input (`$ARGUMENTS`): a URL, or the path/filename of a file in `_inbox/`.

## Procedure

1. **Dedupe** — grep `log.md` and `Articles/*.md` frontmatter for the URL or
   title. Already ingested → say so, stop.
2. **Get content** — WebFetch the URL (paywalled/failed → ask the user to save
   the text into `_inbox/` and stop) or Read the inbox file.
3. **Identify** — publish date; every {entity_label_lower} mentioned (see the
   vault `index.md` for the tracked list) and the {category_label_lower}s affected.
4. **Extract & verify claims** (the 5–10 that matter) — check numeric claims
   against authoritative data and material factual claims via WebSearch; label
   each **verified / unverified / inaccurate** plus one line on how it was checked.
{price_step}5. **Write the article note** at `Articles/YYYY-MM-DD <slug>.md` with
   frontmatter (`title`, `url`, `published`, `ingested`, `{key_field}s`{price_front}) and
   sections: `## Summary`, `## Claims & Verification`, `## Takeaways Routed`{price_section}.
6. **Route takeaways** — append to each affected {entity_label_lower}/{category_label_lower} note:
   `## Facts`: `- YYYY-MM-DD — <verified, durable takeaway> ([[article note]])` ·
   `## Speculations`: same + ` (status: open)` · `## Articles`: `- [[article note]]` ·
   bump `updated:`.
7. **Bookkeep** — add the article to `index.md` (newest first); append one
   `log.md` line: `- YYYY-MM-DD · ingested [[<article>]] → updated <notes>`.
8. **Report in chat** — claims verified/flagged, notes touched, anything
   needing the user's judgment.
"""

LINT_SKILL_TEMPLATE = """---
name: vault-lint
description: Health-check the {topic} vault — contradictions between facts, stale speculation statuses, orphan articles, index/log drift; apply safe fixes. Use when the user says "lint the vault" or "vault health".
---

# Lint the {topic} vault

Vault: `{vault_path}`. Read `SCHEMA.md`, `index.md`, and `log.md` first, then
scan `{entities_dir}/`, `{categories_dir}/`, `Articles/`.

## Checks
1. **Contradictions** — conflicting facts within or across notes; newer,
   better-sourced fact wins, annotate the loser as superseded.
2. **Speculation statuses** — `(status: open)` items later confirmed or
   contradicted → flip to `confirmed`/`busted` with date and [[source]].
3. **Orphan articles** — article notes with no routed takeaways or backlinks.
4. **Index/log drift** — notes missing from `index.md`; `updated:` older than
   the latest `log.md` mention; broken [[wikilinks]].
5. **Conflicted copies** — `X 2.md` sync artifacts: report, never auto-merge.

Apply unambiguous fixes directly; report judgment calls. Append one `log.md`
line summarizing the pass.
"""

STOCKS_PRICE_RULE = (
    "- Article frontmatter `prices:` = close on the publish date (prior close on\n"
    "  non-trading days; ingestion-day price if the publish date is unknown — note it).\n"
)
STOCKS_PRICE_STEP = (
    "   Then capture each mentioned ticker's **publication price** (daily close\n"
    "   on the publish date; prior session on non-trading days).\n"
)


# --------------------------------------------------------------- generation

def write_if_missing(path: Path, content: str) -> bool:
    if path.exists():
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return True


def slug_fields(config: dict) -> dict:
    entity_label = config.get("entity_label", "Entity")
    category_label = config.get("category_label", "Category")
    return {
        "topic": config.get("topic", "Knowledge"),
        "entity_label": entity_label,
        "entity_label_lower": entity_label.lower(),
        "category_label": category_label,
        "category_label_lower": category_label.lower(),
        "entities_dir": config.get("entities_dir", entity_label + "s"),
        "categories_dir": config.get("categories_dir", category_label + "s"),
        "key_field": config.get("key_field", entity_label.lower()),
        "category_field": category_label.lower(),
        "today": TODAY,
    }


def generate(config: dict, skills_dir: Path | None) -> None:
    vault = Path(os.path.expanduser(config["vault_path"]))
    f = slug_fields(config)
    price_tracking = bool(config.get("price_tracking"))
    extra_rules = "".join(f"- {rule}\n" for rule in config.get("extra_rules", []))

    created = skipped = 0
    for folder in (f["entities_dir"], f["categories_dir"], "Articles", "_inbox"):
        (vault / folder).mkdir(parents=True, exist_ok=True)

    files: list[tuple[Path, str]] = [(
        vault / "SCHEMA.md",
        SCHEMA_TEMPLATE.format(
            **f,
            price_immutable_note=(" (only the auto-refreshed price table inside an "
                                  "article note may change)" if price_tracking else ""),
            price_rule=STOCKS_PRICE_RULE if price_tracking else "",
            extra_rules=extra_rules),
    )]

    categories = config.get("categories", {})
    entities = config.get("entities", [])
    by_category: dict[str, list[dict]] = {}
    for e in entities:
        by_category.setdefault(e.get("category", "Uncategorized"), []).append(e)

    index_lines = []
    for cat in sorted(set(categories) | set(by_category)):
        members = by_category.get(cat, [])
        member_lines = "\n".join(
            f"- [[{m['key']}]] — {m.get('name', m['key'])}" for m in members) or "_(empty)_"
        files.append((
            vault / f["categories_dir"] / f"{cat}.md",
            CATEGORY_TEMPLATE.format(
                name=cat, today=TODAY, category_field=f["category_field"],
                overview=categories.get(cat, "_(add an overview)_"),
                members=member_lines),
        ))
        keys = " · ".join(f"[[{m['key']}]]" for m in members)
        index_lines.append(f"- [[{cat}]] ({len(members)}): {keys}")
        for m in members:
            files.append((
                vault / f["entities_dir"] / f"{m['key']}.md",
                ENTITY_TEMPLATE.format(
                    key=m["key"], name=m.get("name", m["key"]), category=cat,
                    today=TODAY, key_field=f["key_field"],
                    category_field=f["category_field"],
                    overview=m.get("overview") or "_(no overview yet)_"),
            ))

    files.append((
        vault / "index.md",
        f"# {f['topic']} Vault — Index\n\nRead this first. Maintained by Claude "
        f"after every change.\n\n## {f['categories_dir']}\n"
        + "\n".join(index_lines) + "\n\n## Articles\n_(none ingested yet)_\n",
    ))
    files.append((
        vault / "log.md",
        "# Change log (append-only)\n\n"
        f"- {TODAY} · vault created by create_llm_wiki.py; seeded "
        f"{len(entities)} {f['entity_label_lower']} notes + "
        f"{len(set(categories) | set(by_category))} {f['category_label_lower']} notes\n",
    ))

    # Skills (topic-adapted)
    skill_kwargs = dict(
        **f, vault_path=str(vault),
        price_desc=(", and capture each mentioned ticker's price at publication"
                    if price_tracking else ""),
        price_step=STOCKS_PRICE_STEP if price_tracking else "",
        price_front=", `prices:`" if price_tracking else "",
        price_section=", `## Price Since Publication`" if price_tracking else "",
    )
    skills_root = skills_dir if skills_dir else vault / "_skills"
    files.append((skills_root / "ingest-article" / "SKILL.md",
                  INGEST_SKILL_TEMPLATE.format(**skill_kwargs)))
    files.append((skills_root / "vault-lint" / "SKILL.md",
                  LINT_SKILL_TEMPLATE.format(**skill_kwargs)))

    for path, content in files:
        if write_if_missing(path, content):
            created += 1
        else:
            skipped += 1

    print(f"Vault at {vault}: created {created}, skipped {skipped} existing.")
    if not skills_dir:
        print(f"Skills written to {skills_root} — copy each folder into a "
              "project's .claude/skills/ to enable /ingest-article and /vault-lint.")


# --------------------------------------------------------- tracker export

def _overview_from_note(note_path: Path) -> str | None:
    """Pull the ## Overview section out of an existing vault note."""
    try:
        text = note_path.read_text(encoding="utf-8")
    except OSError:
        return None
    match = re.search(r"^## Overview\s*\n(.*?)(?=\n## |\Z)", text, re.S | re.M)
    return match.group(1).strip() if match else None


def config_from_tracker(db_path: Path) -> dict:
    """Build the Stocks Vault config from the tracker DB, preferring Overview
    text from the live vault (true replication) over DB analysis leads."""
    vault = Path(os.path.expanduser(os.environ.get("STOCKS_VAULT",
                                                   "~/Documents/Stocks Vault")))
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        """SELECT st.ticker, st.name, st.analysis, se.name AS sector
           FROM stocks st JOIN sectors se ON se.id = st.sector_id
           ORDER BY se.name, st.ticker""").fetchall()
    sectors = [r["name"] for r in conn.execute("SELECT name FROM sectors ORDER BY name")]
    conn.close()

    entities = []
    for r in rows:
        overview = (_overview_from_note(vault / "Stocks" / f"{r['ticker']}.md")
                    or (r["analysis"] or "").split("\n## ")[0].strip()
                    or "_(no overview yet)_")
        entities.append({"key": r["ticker"], "name": r["name"],
                         "category": r["sector"], "overview": overview})
    categories = {
        s: (_overview_from_note(vault / "Sectors" / f"{s}.md") or "_(add an overview)_")
        for s in sectors
    }
    return {
        "vault_path": str(vault),
        "topic": "Stocks & Market Sectors",
        "entity_label": "Stock", "entities_dir": "Stocks", "key_field": "ticker",
        "category_label": "Sector", "categories_dir": "Sectors",
        "price_tracking": True,
        "extra_rules": [
            "Never use buy/sell/hold or position-advice language anywhere in the vault."],
        "categories": categories,
        "entities": entities,
    }


# ------------------------------------------------------------------- main

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Scaffold a Karpathy-style LLM-wiki Obsidian vault.")
    parser.add_argument("config", nargs="?", help="JSON config file (see docstring)")
    parser.add_argument("--from-tracker", nargs="?", const="data/tracker.db",
                        metavar="DB", help="replicate the Stocks Vault from the tracker DB")
    parser.add_argument("--skills-dir", type=Path,
                        help="write skills into this .claude/skills directory")
    parser.add_argument("--export-config", type=Path,
                        help="write the resolved config JSON to this path")
    parser.add_argument("--vault", help="override vault_path from the config")
    args = parser.parse_args()

    if args.from_tracker:
        db_path = Path(args.from_tracker)
        if not db_path.exists():
            sys.exit(f"Tracker DB not found: {db_path}")
        config = config_from_tracker(db_path)
    elif args.config:
        config = json.loads(Path(args.config).read_text(encoding="utf-8"))
    else:
        parser.error("provide a config file or --from-tracker")

    if args.vault:
        config["vault_path"] = args.vault
    if args.export_config:
        args.export_config.write_text(json.dumps(config, indent=2), encoding="utf-8")
        print(f"Config exported to {args.export_config}")

    generate(config, args.skills_dir)


if __name__ == "__main__":
    main()
