# Learnings

Transferable concepts that came up while building Market Tracker. Project-specific
conventions live in `CLAUDE.md`; this file is for the ideas worth re-reading later.

## Lock scope vs. lock duration

A mutex protects *state*, but how long you hold it decides your throughput. Holding one
lock across a network call turns an unrelated cache read into a queued request.

**Why it came up:** `_cached_fetch` in `app/prices.py` held a single module-wide lock for
the entire yfinance download. While a cold 1Y fetch ran, every other read — news, stats,
even warm cache hits for other periods — waited behind it. The fix was two-layered: a
short-lived lock around the dictionary access only, plus a *per-slot* lock so just the
duplicate fetches of the same data collapse into one.

**Takeaway:** hold a lock for dictionary access, not for I/O — and if you're serializing
callers who want different things, you need more locks, not a longer one.

## Double-checked locking needs a version, not just a re-read

The standard "check, lock, check again" pattern has a subtle hole when one caller wants to
*bypass* the cache. Re-reading freshness inside the lock can't distinguish "someone else
refreshed while I waited" from "the same entry I already decided to skip."

**Why it came up:** after adding per-slot locks, the force-refresh path silently became a
no-op — the inner re-check saw a fresh entry and returned it, so the Refresh button stopped
refreshing. Capturing the entry's timestamp *before* acquiring the lock and comparing it
after fixed it: a different timestamp means another thread did the work, an identical one
means it's the entry I already rejected. A test caught this, not review.

**Takeaway:** in double-checked locking, compare an identity (timestamp, version, generation
counter), not just a predicate — "is it fresh?" and "is it *newer than what I saw*?" are
different questions.

## A test that passes under sabotage is measuring the wrong thing

Fail-first isn't a formality. Two tests here were written, passed, and looked meaningful —
then survived deliberate sabotage of the exact behavior they claimed to protect.

**Why it came up:** (1) a perf-scoping test asserted on `pc_high`, but that metric drops its
first bar anyway for lack of a prior close, so it reports one day whether or not scoping
works — no possible result distinguishes the two. Switching to an open-based metric, where
scoped and unscoped genuinely differ (10.0 vs 7.5), made it bite. (2) a concurrency test
exercised a warm cache read that returns *before* taking any lock, so it could never observe
lock contention; a second test where both slots do cold fetches was needed.

**Takeaway:** before trusting a test, ask "what result would I see if the behavior were
broken?" If the answer is "the same one", the test is decoration — pick an input where the
correct and broken code must disagree.

## Total functions at module load

Code that runs during import has no error boundary above it. An exception there doesn't
degrade one feature — it prevents everything after it from existing.

**Why it came up:** `new Set(JSON.parse(localStorage.getItem("mt.collapsed")))` ran at the
top level of `app.js`. One malformed value in browser storage threw before any render, so
the page was permanently blank with no path to recovery from the UI. Its two sibling loaders
already had try/catch; this one had been missed.

**Takeaway:** any parse of external/persisted input during initialization must be total —
return a safe default, never throw. Audit *all* the loaders when you fix one; they were
written at different times and the survivor is the one nobody re-read.

## Last-write-wins is the default for concurrent async UI

Async handlers don't queue. Fire two and the one that *finishes* last paints, regardless of
which the user asked for last.

**Why it came up:** switching the dashboard period quickly could let a slower earlier request
resolve after a newer one, painting stale data under the highlighted button. A monotonic
sequence number gates render, error display, *and* the spinner teardown — a superseded
response must not clear the newer request's loading state either.

**Takeaway:** any user-triggered async fetch that can be re-triggered needs a staleness guard
(sequence token or `AbortController`), and the guard belongs on every side effect, not just
the success path.

## Strip decoration, not structure, when parsing user input

Over-eager normalization silently converts typos into valid values, which is worse than
rejecting them.

**Why it came up:** the filter parser stripped all whitespace before matching, so a
mistyped `"10 20"` became `1020` — a real, silently wrong threshold. Stripping only currency
and grouping characters (`$ , %`) while allowing a single gap before a magnitude suffix
(`"5 k"`) keeps the convenience without inventing values.

**Takeaway:** normalize characters that carry no meaning; preserve whitespace that separates
tokens. When a parser can't be sure, returning `null` beats guessing.

## Verify against the environment CI will actually build

A long-lived local venv keeps working on versions it resolved months ago, so it can't tell
you whether your declared floors still install — or whether the code survives what a fresh
resolve picks up.

**Why it came up:** `requirements.txt` said `yfinance>=0.2.50`, but the code parses the 1.x
news schema and treats `dividendYield` as a percentage — a 0.2.x resolve would fail silently
(empty news, yields 100× off). Building a throwaway venv from the corrected floors resolved
to yfinance 1.5.2 and pandas 3.0.5 — both newer than local — and running the full suite there
confirmed the code works on what CI will get, not just on what happens to be installed.

**Takeaway:** after changing dependency floors, build a clean environment and run the suite in
it. Anything that clones fresh — CI, a new machine, another contributor — is the real test.

## A "best" bucket always exists — read it against chance

Split any data into buckets and one of them comes out on top, even when nothing real
distinguishes them. With many buckets compared at once (5 weekdays × 37 stocks), some will
clear a "significant-looking" bar by luck alone — the multiple-comparisons problem.

**Why it came up:** asked which weekday has the most green closes, the portfolio answered
— and 10 of 185 weekday cells sat beyond two standard errors over one year, against ~8 that
pure coin flips would produce. Over two years the count fell to 6: more data shrank the
"patterns", exactly as noise does. The Patterns page now compares each bucket with the rest
of the days, outlines only cells past a Bonferroni-style threshold (|z| ≥ 2.6 across five
weekdays), and prints "N stand out; chance alone would flag about M" next to the grid.

**Takeaway:** before reporting a winner among buckets, count how many winners chance would
hand you — and show that number beside the result.

## A fixture whose outcomes cancel can't see a flipped comparison

A rate is a count over a total. If a fixture's samples split evenly between the two outcomes,
inverting the test that decides the outcome swaps which samples count — and leaves the rate
unchanged.

**Why it came up:** the gap fixture had two gap-ups, one that filled and one that didn't. Flipping
`low <= prev_close` to `>=` made the filled one unfilled and vice versa: still 50%, and the
sabotage survived. A third gap-up made the correct answer 66.67% and the flipped one 33.33%.

**Takeaway:** for any rate or ratio, give the fixture an uneven split, so that flipping the
outcome test changes the number it produces.

## A live feed serves the session that hasn't finished

During market hours, Yahoo's daily download already contains today's bar, built from the
session so far, and the five-minute feed contains its bars up to now. Both look like ordinary
completed data.

**Why it came up:** a probe at 15:09 ET returned a daily bar dated today and a session with 68
of its 78 five-minute bars. Counted as history, that half day would score as a full one (a
morning dip filed as "a red Tuesday"). The pattern statistics drop any session dated today
until 16:00 ET.

**Takeaway:** treat the newest bar of a live market feed as provisional; derive "completed"
from the clock and the exchange calendar, never from the bar's presence.
