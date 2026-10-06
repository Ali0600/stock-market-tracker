"""Evaluation of owner-authored alert rules over the tracked statistics.

Strictly descriptive, by the same agreement that governs the backtester and
the deviations page: a rule is a threshold the OWNER chose, and this module
reports only whether it currently holds and what the observed value is. It
never proposes rules or thresholds, and it never says what a met condition
means for a position.

Stat keys use the frontend's column convention so the UI can reuse its filter
machinery unchanged:
  "m:<metric>"  — a Things-to-Track / range-stat value (metrics.compute_all)
  "<core>"      — a core table column (prices.compute_core), e.g. "pct"
  "z:<metric>"  — a σ Today z-score (metrics.today_vs_typical)
  "s:<pattern>" — a pattern statistic over two years of completed sessions
                  (patterns.flatten); the rule's period does not apply
"""
from __future__ import annotations

import logging
from typing import Optional

from . import db, metrics, patterns, prices

log = logging.getLogger("uvicorn.error")

# Core numeric columns an alert may reference, with the labels the UI shows.
CORE_STATS: dict[str, str] = {
    "current": "Current Price",
    "pct": "Period %",
    "high_pct": "Off High %",
    "low_pct": "Off Low %",
    "market_cap": "Market Cap",
}

# Numeric metric formats; "time"/"text" columns can't carry a threshold.
NUMERIC_FMTS = {"int", "pct", "pct_abs", "mcap", "price"}

Z_LABELS = {
    "z:pc_close": "Day % (z-score)",
    "z:pc_low": "Dip % (z-score)",
    "z:pc_high": "Stretch % (z-score)",
    "z:l_high": "Range % (z-score)",
}


def stat_defs() -> list[dict]:
    """Every statistic an alert rule may reference, for the builder UI.
    This is also the allow-list the API validates against — a key that isn't
    here is rejected at creation rather than silently never matching."""
    defs = [{"key": key, "label": label, "group": "Price & Performance"}
            for key, label in CORE_STATS.items()]
    defs += [{"key": "m:" + m["key"], "label": m["label"],
              "group": m.get("group") or "Things to Track"}
             for m in metrics.metric_defs() if m["fmt"] in NUMERIC_FMTS]
    defs += [{"key": key, "label": label, "group": "Today vs typical"}
             for key, label in Z_LABELS.items()]
    defs += [{"key": d["key"], "label": d["label"], "group": d["group"]}
             for d in patterns.alert_defs()]
    return defs


def allowed_keys() -> set[str]:
    return {d["key"] for d in stat_defs()}


def _observed(key: str, core: dict, extras: dict, z_scores: Optional[dict],
              s_values: Optional[dict] = None) -> Optional[float]:
    """The rule's statistic for one stock, or None when it can't be computed."""
    if key.startswith("s:"):
        value = (s_values or {}).get(key)
        return value if isinstance(value, (int, float)) and not isinstance(value, bool) else None
    if key.startswith("z:"):
        if not z_scores:
            return None
        entry = (z_scores.get("metrics") or {}).get(key[2:])
        return entry["z"] if entry else None
    value = extras.get(key[2:]) if key.startswith("m:") else core.get(key)
    return value if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def _holds(observed: float, op: str, threshold: float) -> bool:
    return observed >= threshold if op == "gte" else observed <= threshold


def evaluate(rules: list[dict], force: bool = False) -> tuple[list[dict], bool, Optional[str]]:
    """Evaluate each rule against current data.

    Returns (results, stale, fetch_error). Each result carries the rule plus
    `matches` (the stocks whose statistic satisfies the condition, with the
    observed value) and `unavailable` (stocks whose statistic could not be
    computed) — an unmeasurable stock is reported, never silently dropped,
    so an empty match list can't be mistaken for a confident "no".
    """
    tickers = db.all_tickers()
    results: list[dict] = []
    if not rules:
        return results, False, None

    stale_any, first_error = False, None
    frames_by_period: dict[str, dict] = {}
    computed: dict[tuple[str, str], tuple[dict, dict]] = {}   # (period, ticker) -> (core, extras)
    z_by_ticker: dict[str, Optional[dict]] = {}
    s_by_ticker: dict[str, tuple[dict, dict]] = {}
    history: dict[str, dict] = {}
    # The 60-session five-minute history is the heaviest fetch here; only pay
    # for it when a rule actually reads an intraday-timing statistic.
    needs_intraday_history = any(r["metric_key"].startswith("s:intra:") for r in rules)

    intraday = prices.get_intraday(tickers, force=force) if tickers else {}

    def frames_for(period: str) -> dict:
        nonlocal stale_any, first_error
        if period not in frames_by_period:
            frames, _, stale, error = prices.get_daily(tickers, period, force=force)
            frames_by_period[period] = frames
            stale_any = stale_any or stale
            if error and first_error is None:
                first_error = error
        return frames_by_period[period]

    def stats_for(period: str, ticker: str) -> Optional[tuple[dict, dict]]:
        """(core row, metrics dict) for one stock in one period window."""
        cache_key = (period, ticker)
        if cache_key not in computed:
            frame = frames_for(period).get(ticker)
            if frame is None or frame.empty:
                computed[cache_key] = None
            else:
                try:
                    core, window, perf = prices.compute_core(
                        frame, period, intraday.get(ticker))
                    extras = metrics.compute_all(window, intraday.get(ticker), perf)
                    computed[cache_key] = (core, extras)
                except Exception:
                    log.exception("alert stats failed for %s (%s)", ticker, period)
                    computed[cache_key] = None
        return computed[cache_key]

    def z_for(ticker: str) -> Optional[dict]:
        if ticker not in z_by_ticker:
            frame = frames_for("1Y").get(ticker)
            if frame is None or frame.empty:
                z_by_ticker[ticker] = None
            else:
                try:
                    merged = prices._with_live_bar(frame, intraday.get(ticker))
                    z_by_ticker[ticker] = metrics.today_vs_typical(merged)
                except Exception:
                    log.exception("alert z-scores failed for %s", ticker)
                    z_by_ticker[ticker] = None
        return z_by_ticker[ticker]

    def history_frames(slot: str) -> dict:
        nonlocal stale_any, first_error
        if slot not in history:
            fetch = (prices.get_intraday_history if slot == "intraday"
                     else prices.get_history)
            frames, _, stale, error = fetch(tickers, force=force)
            history[slot] = frames
            stale_any = stale_any or stale
            if error and first_error is None:
                first_error = error
        return history[slot]

    def s_for(ticker: str) -> tuple[dict, dict]:
        """(values, stands_out) for every pattern statistic of one stock."""
        if ticker not in s_by_ticker:
            daily = history_frames("daily").get(ticker)
            bars = history_frames("intraday").get(ticker) if needs_intraday_history else None
            try:
                prof = patterns.profile(daily, bars, patterns.now())
            except Exception:
                log.exception("alert pattern stats failed for %s", ticker)
                prof = None
            s_by_ticker[ticker] = patterns.flatten(prof)
        return s_by_ticker[ticker]

    for rule in rules:
        key = rule["metric_key"]
        targets = [rule["ticker"]] if rule["ticker"] else tickers
        matches, unavailable = [], []
        for ticker in targets:
            if ticker not in tickers:
                unavailable.append(ticker)   # rule outlived the stock
                continue
            core, extras, z_scores, s_values, s_flags = {}, {}, None, None, {}
            if key.startswith("s:"):
                s_values, s_flags = s_for(ticker)
            elif key.startswith("z:"):
                z_scores = z_for(ticker)
            else:
                stats = stats_for(rule["period"], ticker)
                core, extras = stats if stats else ({}, {})
            observed = _observed(key, core, extras, z_scores, s_values)
            if observed is None:
                unavailable.append(ticker)
            elif _holds(observed, rule["op"], rule["value"]):
                match = {"ticker": ticker, "observed": round(float(observed), 2)}
                if key.startswith("s:"):
                    # Whether this stock's value differs from its other days by
                    # more than normal variation (None: no comparison exists).
                    match["stands_out"] = s_flags.get(key)
                matches.append(match)
        results.append({**rule, "matches": matches, "unavailable": unavailable})

    return results, stale_any, first_error
