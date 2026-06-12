---
name: vault-lint
description: Health-check the Stocks Vault — find contradictions between facts, stale speculation statuses, orphan articles, and index/log drift; apply safe fixes. Use when the user says "lint the vault", "check the vault", or "vault health".
---

# Lint the Stocks Vault

Vault: `~/Documents/Stocks Vault` (override via `STOCKS_VAULT` env). Read
`SCHEMA.md`, `index.md`, and `log.md` first, then scan `Stocks/`, `Sectors/`,
`Articles/`.

## Checks

1. **Contradictions** — facts that conflict within or across notes. Newer,
   better-sourced fact wins; annotate the loser as superseded (date + source)
   rather than deleting.
2. **Speculation statuses** — any `(status: open)` speculation that later
   facts confirm or contradict → flip to `confirmed`/`busted` with the date
   and [[source]].
3. **Orphan articles** — article notes with no routed takeaways or no
   backlinks from any stock/sector note.
4. **Index/log drift** — notes or articles missing from `index.md`; notes
   whose `updated:` predates their latest `log.md` mention; broken
   [[wikilinks]].
5. **Conflicted copies** — files like `NVDA 2.md` (Obsidian/iCloud sync
   artifacts): report them, never edit or merge them automatically.
6. **House rules** — any buy/sell/hold language that crept into wiki notes;
   unsourced Facts/Speculations bullets.

## Fixes

Apply unambiguous fixes directly (status flips with evidence, index additions,
missing backlinks, `updated:` bumps). Report judgment calls instead of
guessing. Append one `log.md` line summarizing the lint pass.
