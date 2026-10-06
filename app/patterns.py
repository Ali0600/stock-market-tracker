"""Pattern statistics: how a stock has behaved by weekday, on the day after
particular kinds of days, around opening gaps, at the turn of the month, and
through the trading day.

Strictly descriptive (CLAUDE.md -> Analysis tools & boundaries). Every value is
a historical frequency or average over COMPLETED sessions, reported with its
sample size. Nothing here says what the next session will do.

Noise. A bucket always has a "best" value, and across the portfolio most such
differences are what chance alone produces: on 2026-10-06, 6-10 of 185
weekday green-rate cells sat beyond 2 standard errors, against ~8 expected from
pure chance. Each comparative cell therefore carries a z-score against the
REST of the days and is marked `stands_out` only beyond STANDS_OUT_Z, which is
about a 5% false-alarm rate across the five weekdays together, not per cell.
"""
from __future__ import annotations

import math
from datetime import date, datetime, time, timedelta
from typing import Optional
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

from . import metrics

MARKET_TZ = ZoneInfo("America/New_York")
MARKET_CLOSE = time(16, 0)
LOOKBACK = "2Y"

MIN_N = 10            # fewer samples than this: shown dimmed, never alertable
STANDS_OUT_Z = 2.6    # ~5% family-wise across five weekdays (Bonferroni)
MIN_SESSION_BARS = 12  # five-minute bars; shorter sessions are data glitches

WEEKDAYS: list[tuple[str, str]] = [
    ("mon", "Mon"), ("tue", "Tue"), ("wed", "Wed"), ("thu", "Thu"), ("fri", "Fri"),
]

# (key, label, column, kind, fmt). kind decides the comparison test; fmt tells
# the UI how to print the value ("rate" 0-100, "chip" signed %, "plain" %).
WEEKDAY_METRICS: list[tuple[str, str, str, str, str]] = [
    ("green_rate", "Green days", "green", "rate", "rate"),
    ("avg_day", "Avg day %", "day", "mean", "chip"),
    ("avg_o_high", "Open→High", "o_high", "mean", "plain"),
    ("avg_o_low", "Open→Low", "o_low", "mean", "plain"),
    ("avg_range", "Range", "range", "mean", "plain"),
    ("avg_gap", "Gap", "gap", "mean", "chip"),
    ("avg_close_loc", "Close location", "close_loc", "mean", "rate"),
]
ALERT_WEEKDAY_METRICS = ["green_rate", "avg_day", "avg_o_high", "avg_range"]

FOLLOW_CONDITIONS: list[tuple[str, str]] = [
    ("after_green", "After a green day"),
    ("after_red", "After a red day"),
    ("after_rise3", f"After a ≥{metrics.BIG_MOVE_PCT:g}% rise"),
    ("after_drop3", f"After a ≥{metrics.BIG_MOVE_PCT:g}% drop"),
    ("after_red3", "After 3+ red days in a row"),
]
FOLLOW_METRICS: list[tuple[str, str, str]] = [
    ("next_green_rate", "Next-day green", "rate"),
    ("avg_next_day", "Avg next-day %", "chip"),
]

GAP_DIRECTIONS: list[tuple[str, str]] = [
    ("up", f"Gap up ≥{metrics.GAP_PCT:g}%"),
    ("down", f"Gap down ≥{metrics.GAP_PCT:g}%"),
]
GAP_METRICS: list[tuple[str, str, str]] = [
    ("fill_rate", "Filled same day", "rate"),
    ("continue_rate", "Continued", "rate"),
    ("avg_gap", "Avg gap", "chip"),
]
ALERT_GAP_METRICS = ["fill_rate", "continue_rate"]

MONTH_WINDOWS: list[tuple[str, str]] = [
    ("first3", "First 3 days of month"),
    ("middle", "Middle of month"),
    ("last3", "Last 3 days of month"),
]
MONTH_METRICS: list[tuple[str, str, str, str, str]] = [
    ("green_rate", "Green days", "green", "rate", "rate"),
    ("avg_day", "Avg day %", "day", "mean", "chip"),
]

N_BUCKETS = 13        # 30-minute buckets, 09:30 ... 15:30
INTRADAY_SUMMARY: list[tuple[str, str]] = [
    ("first30_high", "High set in the first 30 min"),
    ("first30_low", "Low set in the first 30 min"),
    ("last30_high", "High set in the last 30 min"),
    ("last30_low", "Low set in the last 30 min"),
]


def now() -> datetime:
    """The current time in the market's timezone. Tests replace this."""
    return datetime.now(MARKET_TZ)


# --------------------------------------------------------------------------
# Sessions
# --------------------------------------------------------------------------

def _et_index(index: pd.DatetimeIndex) -> pd.DatetimeIndex:
    return index.tz_convert(MARKET_TZ) if index.tz is not None else index.tz_localize(MARKET_TZ)


def completed(df: Optional[pd.DataFrame], at: datetime) -> Optional[pd.DataFrame]:
    """Drop every bar from a session that is still trading. Yahoo serves the
    unfinished session in both the daily and the intraday feed during market
    hours, and a half-finished day must not count as a full one."""
    if df is None or df.empty:
        return df
    at_et = at.astimezone(MARKET_TZ)
    if at_et.time() >= MARKET_CLOSE:
        return df
    dates = np.array(_et_index(df.index).date)
    return df[dates != at_et.date()]


def target_session(at: datetime) -> date:
    """The session in progress, or the next one once the market has closed.
    Holidays are not modelled: a holiday Monday still reads as Monday."""
    at_et = at.astimezone(MARKET_TZ)
    day = at_et.date()
    if day.weekday() < 5 and at_et.time() < MARKET_CLOSE:
        return day
    day += timedelta(days=1)
    while day.weekday() >= 5:
        day += timedelta(days=1)
    return day


def _daily_frame(daily: Optional[pd.DataFrame]) -> Optional[pd.DataFrame]:
    """One row per session with the day's derived values. The first bar only
    supplies the previous close, so it never scores."""
    if daily is None or len(daily) < 2:
        return None
    op, hi, lo, cl = daily["Open"], daily["High"], daily["Low"], daily["Close"]
    pc = cl.shift(1)
    frame = pd.DataFrame({
        "open": op, "high": hi, "low": lo, "close": cl, "prev_close": pc,
        "day": (cl - pc) / pc * 100,
        "green": (cl > pc).astype(float),    # a flat day counts, as not-green
        "o_high": (hi - op) / op * 100,
        "o_low": (lo - op) / op * 100,
        "range": (hi - lo) / lo * 100,
        "gap": (op - pc) / pc * 100,
        "close_loc": ((cl - lo) / (hi - lo) * 100).where(hi != lo),
    }).iloc[1:]
    frame = frame.replace([np.inf, -np.inf], np.nan)
    frame = frame[frame["prev_close"].notna() & (frame["prev_close"] != 0)]
    frame.index = _et_index(frame.index)
    return frame if not frame.empty else None


# --------------------------------------------------------------------------
# Cells
# --------------------------------------------------------------------------

def _cell(value: Optional[float], n: int, z: Optional[float]) -> dict:
    finite_z = z is not None and math.isfinite(z)
    return {
        "value": value,
        "n": int(n),
        "z": round(float(z), 2) if finite_z else None,
        "stands_out": bool(finite_z and abs(z) >= STANDS_OUT_Z),
        "thin": n < MIN_N,
    }


def _value(series: pd.Series, kind: str) -> Optional[float]:
    if series.empty:
        return None
    raw = float(series.mean()) * (100 if kind == "rate" else 1)
    return round(raw, 2) if math.isfinite(raw) else None


def _compare(bucket: pd.Series, rest: pd.Series, kind: str) -> dict:
    """The bucket's value, tested against every other day. Rates use a
    two-proportion z-test, averages a Welch z; either side under MIN_N gets no
    z at all rather than a confident-looking one."""
    b, r = bucket.dropna(), rest.dropna()
    z = None
    if len(b) >= MIN_N and len(r) >= MIN_N:
        if kind == "rate":
            pooled = (b.sum() + r.sum()) / (len(b) + len(r))
            se = math.sqrt(pooled * (1 - pooled) * (1 / len(b) + 1 / len(r)))
        else:
            se = math.sqrt(b.var(ddof=1) / len(b) + r.var(ddof=1) / len(r))
        if se > 0:
            z = (float(b.mean()) - float(r.mean())) / se
    return _cell(_value(b, kind), len(b), z)


def _plain(series: pd.Series, kind: str) -> dict:
    """A value with no comparison (baselines, and stats with no natural one)."""
    s = series.dropna()
    return _cell(_value(s, kind), len(s), None)


# --------------------------------------------------------------------------
# Families
# --------------------------------------------------------------------------

def weekday_profile(frame: pd.DataFrame) -> dict:
    dow = frame.index.dayofweek
    rows = []
    for i, (key, label) in enumerate(WEEKDAYS):
        mask = dow == i
        rows.append({
            "key": key, "label": label, "n": int(mask.sum()),
            "cells": {m: _compare(frame.loc[mask, col], frame.loc[~mask, col], kind)
                      for m, _l, col, kind, _f in WEEKDAY_METRICS},
        })
    return {
        "metrics": [{"key": m, "label": label, "fmt": fmt}
                    for m, label, _c, _k, fmt in WEEKDAY_METRICS],
        "rows": rows,
        "all": {"key": "all", "label": "All days", "n": len(frame),
                "cells": {m: _plain(frame[col], kind)
                          for m, _l, col, kind, _f in WEEKDAY_METRICS}},
    }


def _red_run(day: pd.Series) -> pd.Series:
    red = day < 0
    return red.astype(int).groupby((~red).cumsum()).cumsum()


def follow_through(frame: pd.DataFrame) -> dict:
    """What the NEXT session did after each kind of day. The latest session
    has no next day yet, so it is never a sample."""
    day = frame["day"]
    next_day = day.shift(-1)
    next_green = frame["green"].shift(-1)
    has_next = next_day.notna()
    conditions = {
        "after_green": day > 0,
        "after_red": day < 0,
        "after_rise3": day >= metrics.BIG_MOVE_PCT,
        "after_drop3": day <= -metrics.BIG_MOVE_PCT,
        "after_red3": _red_run(day) >= 3,
    }
    series = {"next_green_rate": (next_green, "rate"), "avg_next_day": (next_day, "mean")}
    rows = []
    for key, label in FOLLOW_CONDITIONS:
        cond = conditions[key]
        mask, rest = cond & has_next, ~cond & has_next
        rows.append({
            "key": key, "label": label, "n": int(mask.sum()),
            "cells": {m: _compare(s[mask], s[rest], kind) for m, (s, kind) in series.items()},
        })
    return {
        "metrics": [{"key": m, "label": label, "fmt": fmt} for m, label, fmt in FOLLOW_METRICS],
        "rows": rows,
        "all": {"key": "all", "label": "All days", "n": int(has_next.sum()),
                "cells": {m: _plain(s[has_next], kind) for m, (s, kind) in series.items()}},
    }


def gap_behavior(frame: pd.DataFrame) -> dict:
    """Opens at least GAP_PCT away from the previous close — the same gap
    definition as the gap_frequency column. A gap "filled" when the session
    traded back to the previous close; it "continued" when it closed beyond
    its own open in the gap's direction."""
    gap = frame["gap"]
    op, hi, lo, cl, pc = (frame[k] for k in ("open", "high", "low", "close", "prev_close"))
    masks = {"up": gap >= metrics.GAP_PCT, "down": gap <= -metrics.GAP_PCT}
    outcomes = {
        "up": {"fill_rate": (lo <= pc), "continue_rate": (cl > op)},
        "down": {"fill_rate": (hi >= pc), "continue_rate": (cl < op)},
    }
    rows = []
    for key, label in GAP_DIRECTIONS:
        mask = masks[key]
        cells = {m: _plain(s[mask].astype(float), "rate") for m, s in outcomes[key].items()}
        cells["avg_gap"] = _plain(gap[mask], "mean")
        rows.append({"key": key, "label": label, "n": int(mask.sum()), "cells": cells})
    return {"metrics": [{"key": m, "label": label, "fmt": fmt} for m, label, fmt in GAP_METRICS],
            "rows": rows}


def _month_window(position: int, from_end: int) -> str:
    if position < 3:
        return "first3"
    return "last3" if from_end < 3 else "middle"


def turn_of_month(frame: pd.DataFrame, at: datetime) -> dict:
    """Sessions grouped by their place in the calendar month, counted in
    actual sessions. The window's first month is usually partial (its first
    sessions predate the data) and the current month is unfinished (its last
    sessions haven't happened), so both are left out rather than misfiled."""
    months = frame.index.tz_localize(None).to_period("M")
    current = pd.Period(at.astimezone(MARKET_TZ).date(), freq="M")
    keep = (months != months[0]) & (months != current)
    position = pd.Series(months).groupby(months).cumcount().to_numpy()
    size = pd.Series(months).groupby(months).transform("size").to_numpy()
    windows = np.array([_month_window(p, s - 1 - p) for p, s in zip(position, size, strict=True)])
    kept = frame[keep]
    kept_windows = windows[keep]
    rows = []
    for key, label in MONTH_WINDOWS:
        mask = kept_windows == key
        rows.append({
            "key": key, "label": label, "n": int(mask.sum()),
            "cells": {m: _compare(kept.loc[mask, col], kept.loc[~mask, col], kind)
                      for m, _l, col, kind, _f in MONTH_METRICS},
        })
    return {
        "metrics": [{"key": m, "label": label, "fmt": fmt} for m, label, _c, _k, fmt in MONTH_METRICS],
        "rows": rows,
        "all": {"key": "all", "label": "All days", "n": len(kept),
                "cells": {m: _plain(kept[col], kind) for m, _l, col, kind, _f in MONTH_METRICS}},
    }


def bucket_labels() -> list[str]:
    start = 9 * 60 + 30
    return [f"{(start + 30 * i) // 60:02d}:{(start + 30 * i) % 60:02d}" for i in range(N_BUCKETS)]


def intraday_timing(bars: Optional[pd.DataFrame], at: datetime) -> Optional[dict]:
    """Which 30-minute window held each completed session's high and low.
    Ties go to the earlier bar. No stands-out marking: highs and lows bunch
    at the open and close in most stocks, so a uniform baseline would flag
    the expected shape."""
    bars = completed(bars, at)
    if bars is None or bars.empty:
        return None
    bars = bars.dropna(subset=["High", "Low"])
    idx = _et_index(bars.index)
    minutes = np.asarray(idx.hour * 60 + idx.minute - (9 * 60 + 30))
    regular = (minutes >= 0) & (minutes < N_BUCKETS * 30)
    bars, idx, minutes = bars[regular], idx[regular], minutes[regular]
    if bars.empty:
        return None
    bucket = pd.Series(minutes // 30, index=bars.index)
    high_counts = [0] * N_BUCKETS
    low_counts = [0] * N_BUCKETS
    sessions = 0
    for _day, session in bars.groupby(np.array(idx.date)):
        if len(session) < MIN_SESSION_BARS:
            continue
        sessions += 1
        high_counts[int(bucket[session["High"].idxmax()])] += 1
        low_counts[int(bucket[session["Low"].idxmin()])] += 1
    if not sessions:
        return None

    def shares(counts: list[int]) -> list[float]:
        return [round(c / sessions * 100, 2) for c in counts]

    high, low = shares(high_counts), shares(low_counts)
    summary = {"first30_high": high[0], "first30_low": low[0],
               "last30_high": high[-1], "last30_low": low[-1]}
    return {
        "n": sessions,
        "thin": sessions < MIN_N,
        "buckets": bucket_labels(),
        "high": high,
        "low": low,
        "summary": [{"key": k, "label": label, "value": summary[k]}
                    for k, label in INTRADAY_SUMMARY],
    }


# --------------------------------------------------------------------------
# One stock
# --------------------------------------------------------------------------

def _month_window_for(day: date, frame: pd.DataFrame) -> str:
    """The month window the given session falls in. Sessions already in the
    data give its position; weekdays left in the month approximate how close
    it is to the end (holidays aren't modelled)."""
    month_start = day.replace(day=1)
    dates = np.array(frame.index.date)
    position = int(((dates >= month_start) & (dates < day)).sum())
    next_month = (month_start + timedelta(days=32)).replace(day=1)
    from_end = int(np.busday_count(day + timedelta(days=1), next_month))
    return _month_window(position, from_end)


def profile(daily: Optional[pd.DataFrame], intraday: Optional[pd.DataFrame],
            at: datetime) -> Optional[dict]:
    """Every pattern family for one stock, or None without enough history."""
    frame = _daily_frame(completed(daily, at))
    if frame is None:
        return None
    target = target_session(at)
    last_day = frame["day"].iloc[-1]
    last_direction = ("after_green" if last_day > 0 else
                      "after_red" if last_day < 0 else None)
    return {
        "sessions": len(frame),
        "first_session": str(frame.index[0].date()),
        "last_session": str(frame.index[-1].date()),
        "target_session": str(target),
        "today": {
            "weekday": WEEKDAYS[target.weekday()][0],
            "month_window": _month_window_for(target, frame),
            "last_direction": last_direction,
        },
        "weekday": weekday_profile(frame),
        "follow_through": follow_through(frame),
        "gaps": gap_behavior(frame),
        "turn_of_month": turn_of_month(frame, at),
        "intraday": intraday_timing(intraday, at),
    }


# --------------------------------------------------------------------------
# Alerts
# --------------------------------------------------------------------------

def alert_defs() -> list[dict]:
    """Every pattern statistic an alert may reference: the declaration the
    alert builder and the API allow-list read."""
    defs: list[dict] = []
    weekday_labels = {m: label for m, label, *_ in WEEKDAY_METRICS}
    for key, label in WEEKDAYS + [("today", "Session's weekday")]:
        for m in ALERT_WEEKDAY_METRICS:
            defs.append({"key": f"s:wd:{key}:{m}", "label": f"{label} — {weekday_labels[m]}",
                         "group": "Patterns — Weekday"})
    follow_labels = {m: label for m, label, _f in FOLLOW_METRICS}
    for key, label in FOLLOW_CONDITIONS + [("last", "After a day like the last session")]:
        for m in follow_labels:
            defs.append({"key": f"s:ft:{key}:{m}", "label": f"{label} — {follow_labels[m]}",
                         "group": "Patterns — Follow-through"})
    gap_labels = {m: label for m, label, _f in GAP_METRICS}
    for key, label in GAP_DIRECTIONS:
        for m in ALERT_GAP_METRICS:
            defs.append({"key": f"s:gap:{key}:{m}", "label": f"{label} — {gap_labels[m]}",
                         "group": "Patterns — Gaps"})
    month_labels = {m: label for m, label, *_ in MONTH_METRICS}
    for key, label in MONTH_WINDOWS + [("today", "Session's month window")]:
        for m in month_labels:
            defs.append({"key": f"s:tom:{key}:{m}", "label": f"{label} — {month_labels[m]}",
                         "group": "Patterns — Turn of month"})
    for key, label in INTRADAY_SUMMARY:
        defs.append({"key": f"s:intra:{key}", "label": label,
                     "group": "Patterns — Intraday timing"})
    return defs


def _alertable(cell: Optional[dict]) -> tuple[Optional[float], Optional[bool]]:
    """A thin cell has no alertable value: too few days to state a rate."""
    if not cell or cell["value"] is None or cell["thin"]:
        return None, None
    return cell["value"], cell["stands_out"] if cell["z"] is not None else None


def flatten(prof: Optional[dict]) -> tuple[dict[str, Optional[float]], dict[str, Optional[bool]]]:
    """(values, stands_out) keyed like alert_defs(). Every declared key is
    present; None means the statistic can't be stated for this stock."""
    values: dict[str, Optional[float]] = {d["key"]: None for d in alert_defs()}
    stands: dict[str, Optional[bool]] = dict.fromkeys(values)
    if not prof:
        return values, stands

    def put(key: str, cell: Optional[dict], comparative: bool = True) -> None:
        value, flag = _alertable(cell)
        values[key] = value
        stands[key] = flag if comparative else None

    today = prof["today"]
    rows = {r["key"]: r for r in prof["weekday"]["rows"]}
    for key, _label in WEEKDAYS + [("today", "")]:
        row = rows.get(today["weekday"] if key == "today" else key)
        for m in ALERT_WEEKDAY_METRICS:
            put(f"s:wd:{key}:{m}", row["cells"][m] if row else None)

    rows = {r["key"]: r for r in prof["follow_through"]["rows"]}
    for key, _label in FOLLOW_CONDITIONS + [("last", "")]:
        row = rows.get(today["last_direction"]) if key == "last" else rows.get(key)
        for m, _l, _f in FOLLOW_METRICS:
            put(f"s:ft:{key}:{m}", row["cells"][m] if row else None)

    rows = {r["key"]: r for r in prof["gaps"]["rows"]}
    for key, _label in GAP_DIRECTIONS:
        for m in ALERT_GAP_METRICS:
            put(f"s:gap:{key}:{m}", rows[key]["cells"][m], comparative=False)

    rows = {r["key"]: r for r in prof["turn_of_month"]["rows"]}
    for key, _label in MONTH_WINDOWS + [("today", "")]:
        row = rows.get(today["month_window"] if key == "today" else key)
        for m, *_rest in MONTH_METRICS:
            put(f"s:tom:{key}:{m}", row["cells"][m] if row else None)

    intra = prof.get("intraday")
    if intra and not intra["thin"]:
        for item in intra["summary"]:
            values[f"s:intra:{item['key']}"] = item["value"]
    return values, stands
