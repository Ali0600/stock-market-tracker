""""Things to Track" — pluggable computed metric columns.

To add a new tracked metric, write a function below and decorate it with
@metric. It automatically appears as a toggleable column in the UI.

Each metric function receives:
  daily    — DataFrame of daily OHLC bars for the selected period window
             (first bar is the "ago" reference bar), or None
  intraday — DataFrame of 1-minute bars for the latest session, or None
and returns a JSON-serializable value, or None when not computable.
"""
from __future__ import annotations

from typing import Callable, Optional

import numpy as np
import pandas as pd

REGISTRY: dict[str, dict] = {}

# --- Daily range statistics --------------------------------------------------
# Each daily metric is (target − base) / base × 100 computed per trading day,
# then aggregated over the selected period window. base "prev_close" uses the
# prior bar's close (first bar of the window drops out, same convention as
# green/red days).
DAILY_RANGE_METRICS: list[tuple[str, str, str, str]] = [
    # (key, label, base column, target column)
    ("pc_high", "PC→High", "prev_close", "High"),
    ("pc_low", "PC→Low", "prev_close", "Low"),
    ("pc_close", "PC→Close", "prev_close", "Close"),
    ("gap", "Gap (PC→Open)", "prev_close", "Open"),
    ("o_close", "Open→Close", "Open", "Close"),
    ("o_high", "Open→High", "Open", "High"),
    ("o_low", "Open→Low", "Open", "Low"),
    ("l_high", "Low→High", "Low", "High"),
    ("h_low", "High→Low", "High", "Low"),
    ("h_close", "High→Close", "High", "Close"),
    ("l_close", "Low→Close", "Low", "Close"),
]

AGGREGATES: list[tuple[str, str]] = [
    ("avg", "Avg"), ("med", "Median"), ("min", "Min"), ("max", "Max"), ("std", "σ"),
]


def range_stats(daily: Optional[pd.DataFrame],
                perf: Optional[pd.DataFrame] = None) -> Optional[dict]:
    """Aggregate every daily OHLC relationship over the window. Returns
    {metric_key: {avg, med, min: {value, date}, max: {value, date}, std, days}}
    with None per metric (or overall) when not computable.

    `perf` restricts the stats to the period's actual days (for bar-sliced
    windows like 1D/5D, the window's first bar is only the prev-close base,
    not a period day)."""
    if daily is None or daily.empty:
        return None
    scope = perf if perf is not None and not perf.empty else daily
    out: dict = {}
    prev_close = daily["Close"].shift(1)
    for key, _label, base_col, target_col in DAILY_RANGE_METRICS:
        base = prev_close if base_col == "prev_close" else daily[base_col]
        series = ((daily[target_col] - base) / base * 100)
        series = (series.reindex(scope.index)
                  .replace([np.inf, -np.inf], np.nan).dropna())
        if series.empty:
            out[key] = None
            continue
        std = float(series.std(ddof=1)) if len(series) > 1 else None
        out[key] = {
            "avg": round(float(series.mean()), 2),
            "med": round(float(series.median()), 2),
            "min": {"value": round(float(series.min()), 2),
                    "date": str(series.idxmin().date())},
            "max": {"value": round(float(series.max()), 2),
                    "date": str(series.idxmax().date())},
            "std": round(std, 2) if std is not None else None,
            "days": int(len(series)),
        }
    return out if any(out.values()) else None


# The four relationships shown on the deviation dashboard.
DEVIATION_METRICS = {
    "pc_close": ("prev_close", "Close"),
    "pc_low": ("prev_close", "Low"),
    "pc_high": ("prev_close", "High"),
    "l_high": ("Low", "High"),
}


def today_vs_typical(daily: Optional[pd.DataFrame], lookback: int = 63) -> Optional[dict]:
    """The latest session's moves expressed as z-scores against the stock's
    own trailing distribution (excluding that session). Descriptive only."""
    if daily is None or len(daily) < 22:
        return None
    hist, today = daily.iloc[:-1], daily.iloc[-1]
    prev_close = float(hist["Close"].iloc[-1])
    t_close, t_high, t_low = float(today["Close"]), float(today["High"]), float(today["Low"])
    if not all(np.isfinite([prev_close, t_close, t_high, t_low])) or prev_close == 0 or t_low == 0:
        return None

    today_vals = {
        "pc_close": (t_close - prev_close) / prev_close * 100,
        "pc_low": (t_low - prev_close) / prev_close * 100,
        "pc_high": (t_high - prev_close) / prev_close * 100,
        "l_high": (t_high - t_low) / t_low * 100,
    }
    hist_prev = hist["Close"].shift(1)
    out = {}
    for key, (base_col, target_col) in DEVIATION_METRICS.items():
        base = hist_prev if base_col == "prev_close" else hist[base_col]
        series = ((hist[target_col] - base) / base * 100)
        series = series.replace([np.inf, -np.inf], np.nan).dropna().tail(lookback)
        sigma = float(series.std(ddof=1)) if len(series) > 1 else 0.0
        if len(series) < 20 or sigma == 0:
            out[key] = None
            continue
        avg = float(series.mean())
        out[key] = {
            "today": round(today_vals[key], 2),
            "avg": round(avg, 2),
            "sigma": round(sigma, 2),
            "z": round((today_vals[key] - avg) / sigma, 2),
        }
    if not any(out.values()):
        return None
    return {
        "current": round(t_close, 4 if abs(t_close) < 1 else 2),
        "session_date": str(daily.index[-1].date()),
        "metrics": out,
    }


def range_defs() -> list[dict]:
    """Column-picker definitions for every (metric, aggregate) pair."""
    defs = []
    for key, label, _base_col, _target_col in DAILY_RANGE_METRICS:
        for agg_key, agg_label in AGGREGATES:
            defs.append({
                "key": f"rs:{key}:{agg_key}",
                "label": f"{label} {agg_label}",
                "fmt": "pct_abs" if agg_key == "std" else "pct",
                "group": label,
                "default_on": False,
                "description": (f"{agg_label} of the daily {label} % "
                                "over the selected period"),
            })
    return defs


def metric(key: str, label: str, fmt: str = "text", description: str = "",
           default_on: bool = True):
    def decorator(fn: Callable) -> Callable:
        REGISTRY[key] = {
            "key": key,
            "label": label,
            "fmt": fmt,
            "description": description,
            "default_on": default_on,
            "fn": fn,
        }
        return fn

    return decorator


def metric_defs() -> list[dict]:
    """Column definitions sent to the frontend (drives table + column picker)."""
    return [
        {"key": m["key"], "label": m["label"], "fmt": m["fmt"],
         "description": m["description"], "group": "Things to Track",
         "default_on": m["default_on"]}
        for m in REGISTRY.values()
    ] + range_defs()


def compute_all(daily: Optional[pd.DataFrame], intraday: Optional[pd.DataFrame],
                perf: Optional[pd.DataFrame] = None) -> dict:
    out = {}
    for key, m in REGISTRY.items():
        try:
            out[key] = m["fn"](daily, intraday)
        except Exception:
            out[key] = None

    # Flatten the daily-range statistics into the same namespace the column
    # picker uses (min/max as plain values here; dates surface in the detail
    # matrix).
    try:
        stats = range_stats(daily, perf)
    except Exception:
        stats = None
    for key, _label, _base, _target in DAILY_RANGE_METRICS:
        metric = stats.get(key) if stats else None
        for agg_key, _ in AGGREGATES:
            column = f"rs:{key}:{agg_key}"
            if metric is None:
                out[column] = None
            elif agg_key in ("min", "max"):
                out[column] = metric[agg_key]["value"]
            else:
                out[column] = metric[agg_key]
    return out


# --- v1 metrics ------------------------------------------------------------

def _day_changes(daily: Optional[pd.DataFrame]) -> Optional[pd.Series]:
    """Close-over-previous-close changes within the window (first bar has no
    prior close and is excluded)."""
    if daily is None or len(daily) < 2:
        return None
    return daily["Close"].diff().dropna()


@metric("green_days", "Green Days", fmt="int",
        description="Days that closed above the prior close, within the selected period")
def green_days(daily, intraday):
    changes = _day_changes(daily)
    return int((changes > 0).sum()) if changes is not None else None


@metric("red_days", "Red Days", fmt="int",
        description="Days that closed below the prior close, within the selected period")
def red_days(daily, intraday):
    changes = _day_changes(daily)
    return int((changes < 0).sum()) if changes is not None else None


def _session_time_of(intraday: Optional[pd.DataFrame], column: str, find_max: bool) -> Optional[str]:
    if intraday is None or intraday.empty or column not in intraday:
        return None
    series = intraday[column].dropna()
    if series.empty:
        return None
    ts = series.idxmax() if find_max else series.idxmin()
    tz = ts.tzname() or ""
    return f"{ts:%H:%M} {tz}".strip()


@metric("day_high_time", "Day High Time", fmt="time",
        description="Clock time of the latest session's high (exchange timezone)")
def day_high_time(daily, intraday):
    return _session_time_of(intraday, "High", find_max=True)


@metric("day_low_time", "Day Low Time", fmt="time",
        description="Clock time of the latest session's low (exchange timezone)")
def day_low_time(daily, intraday):
    return _session_time_of(intraday, "Low", find_max=False)


# --- Pattern metrics -------------------------------------------------------
# Default-off columns describing how a stock's up/down days are arranged over
# the window, rather than how far it moved. All are derived from the same
# close-over-previous-close changes as green_days/red_days, so the window's
# first bar is the reference and never scores.

BIG_MOVE_PCT = 3.0   # |close-over-prev-close| that counts as a "big move" day
GAP_PCT = 1.0        # |open vs prev close| that counts as a gap


def _signed_runs(changes: pd.Series) -> list[int]:
    """Consecutive same-direction runs as signed lengths (+3 = three straight
    green closes). A flat day belongs to no run and ends whichever run
    preceded it."""
    runs: list[int] = []
    extending = False
    for change in changes:
        direction = 1 if change > 0 else -1 if change < 0 else 0
        if direction == 0:
            extending = False
            continue
        if extending and (runs[-1] > 0) == (direction > 0):
            runs[-1] += direction
        else:
            runs.append(direction)
            extending = True
    return runs


@metric("streak", "Streak", fmt="int", default_on=False,
        description="Current run of consecutive green (+) or red (−) closes; "
                    "0 when the latest day was flat")
def streak(daily, intraday):
    changes = _day_changes(daily)
    if changes is None or changes.empty:
        return None
    runs = _signed_runs(changes)
    if not runs:
        return 0
    # A trailing flat day breaks the run, so only report the last run when it
    # actually reaches the final day.
    return int(runs[-1]) if changes.iloc[-1] != 0 else 0


@metric("max_green_streak", "Max Green Run", fmt="int", default_on=False,
        description="Longest run of consecutive green closes within the selected period")
def max_green_streak(daily, intraday):
    changes = _day_changes(daily)
    if changes is None or changes.empty:
        return None
    greens = [r for r in _signed_runs(changes) if r > 0]
    return int(max(greens)) if greens else 0


@metric("max_red_streak", "Max Red Run", fmt="int", default_on=False,
        description="Longest run of consecutive red closes within the selected period")
def max_red_streak(daily, intraday):
    changes = _day_changes(daily)
    if changes is None or changes.empty:
        return None
    reds = [-r for r in _signed_runs(changes) if r < 0]
    return int(max(reds)) if reds else 0


@metric("green_ratio", "Green Day %", fmt="pct_abs", default_on=False,
        description="Share of the period's trading days that closed green "
                    "(flat days count in the total)")
def green_ratio(daily, intraday):
    changes = _day_changes(daily)
    if changes is None or changes.empty:
        return None
    return round(float((changes > 0).sum()) / len(changes) * 100, 2)


@metric("days_since_big_move", "Days Since ±3% Day", fmt="int", default_on=False,
        description="Trading days since the last close that moved 3% or more from "
                    "the prior close; blank when the period has none")
def days_since_big_move(daily, intraday):
    if daily is None or len(daily) < 2:
        return None
    prev_close = daily["Close"].shift(1)
    pct = ((daily["Close"] - prev_close) / prev_close * 100)
    pct = pct.replace([np.inf, -np.inf], np.nan).dropna()
    if pct.empty:
        return None
    big = pct[pct.abs() >= BIG_MOVE_PCT]
    if big.empty:
        return None
    # 0 = it happened on the most recent day in the window.
    return int(len(pct) - 1 - pct.index.get_loc(big.index[-1]))


@metric("gap_frequency", "Gaps ≥1%", fmt="int", default_on=False,
        description="Days that opened 1% or more away from the prior close, "
                    "in either direction, within the selected period")
def gap_frequency(daily, intraday):
    if daily is None or len(daily) < 2 or "Open" not in daily:
        return None
    prev_close = daily["Close"].shift(1)
    gaps = ((daily["Open"] - prev_close) / prev_close * 100).dropna()
    gaps = gaps.replace([np.inf, -np.inf], np.nan).dropna()
    if gaps.empty:
        return None
    return int((gaps.abs() >= GAP_PCT).sum())
