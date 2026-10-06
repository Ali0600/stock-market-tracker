# Decisions

Forks in the design, the roads not taken, and where trying them later would plug in.

## Backlog — alternatives worth trying later

- **Five-year pattern lookback** — would make month-of-year statistics possible (≈5 samples per
  month instead of 1). Hook: `prices.get_history()` period and `patterns.LOOKBACK`.
- **Desktop notifications for alerts** — a scheduled poller that evaluates rules while the app is
  closed. Hook: `alerts.evaluate()` already returns everything a notifier needs; the rules table
  is notification-ready.

---

## 2026-10-06 — How much history the pattern statistics use

**Fork:** weekday, next-day, gap and month statistics can't follow the dashboard's period selector
(3M gives ~13 Mondays), so they need their own fixed window.

| Option | Tradeoff | Status |
|---|---|---|
| **2 years** | ~99 sessions per weekday (measured on 37 stocks); one extra cached batch fetch (1.5 s) | **chosen** |
| 1 year | ~49 per weekday, reuses the existing fetch, but apparent patterns are noisier | rejected — halves every sample for no saving that matters |
| 5 years | ~250 per weekday and enables month-of-year, but mixes very different market regimes and leaves recent IPOs short | deferred — worth trying |

**Why:** measured before choosing — over 2 years, weekday cells beyond two standard errors fell
from 10 to 6 of 185 against ~8 expected from chance, i.e. more data shrank the "patterns" exactly
as noise does. **Revisit hook:** `prices.get_history()` period + `patterns.LOOKBACK`.

## 2026-10-06 — Where the pattern statistics are shown

**Fork:** per-stock only, or also side by side across the portfolio.

| Option | Tradeoff | Status |
|---|---|---|
| Detail view + alerts | Smallest surface; one stock at a time | rejected — owner wanted cross-stock comparison |
| **Detail view + alerts + a portfolio Patterns page** | Stocks × buckets grid per statistic; more frontend | **chosen** |

**Why:** the owner's choice; the page also carries the "N cells stand out, chance would flag ~M"
line, which only makes sense across many stocks.

## 2026-08-11 — How a met alert reaches the owner (backfilled)

**Fork:** report alerts inside the app, or push them while it's closed.

| Option | Tradeoff | Status |
|---|---|---|
| **In-app panel + topbar badge** | No background moving parts; only seen when the app is open | **chosen** |
| In-app + macOS notifications via a scheduled job | Fires while away; adds an unattended job (stripped env, TCC on ~/Documents) | deferred — worth trying |

**Revisit hook:** a launchd job calling `alerts.evaluate()`; keep the job and its data outside
TCC-protected folders.
