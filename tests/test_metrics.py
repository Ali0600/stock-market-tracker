"""Behavior tests for the Things-to-Track metrics and daily range statistics."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from app import metrics
from tests.conftest import make_daily, make_intraday

# --------------------------------------------------------------------------
# range_stats — the 11 OHLC relationships aggregated over a window
# --------------------------------------------------------------------------

def test_range_stats_computes_each_aggregate_by_hand():
    # Two scored days (the first bar only supplies the prev-close base):
    #   day2: prev close 100, high 110 → +10.0%
    #   day3: prev close 100, high 120 → +20.0%
    df = make_daily([
        (100.0, 100.0, 100.0, 100.0),
        (100.0, 110.0, 95.0, 100.0),
        (100.0, 120.0, 90.0, 100.0),
    ])
    stats = metrics.range_stats(df)
    pc_high = stats["pc_high"]
    assert pc_high["days"] == 2, "the first bar has no prior close and is excluded"
    assert pc_high["avg"] == pytest.approx(15.0)
    assert pc_high["med"] == pytest.approx(15.0)
    assert pc_high["min"]["value"] == pytest.approx(10.0)
    assert pc_high["max"]["value"] == pytest.approx(20.0)
    # Sample standard deviation (ddof=1) of {10, 20} = 7.07…
    assert pc_high["std"] == pytest.approx(7.07, abs=0.01)


def test_range_stats_carries_the_date_of_each_extreme():
    df = make_daily([
        (100.0, 100.0, 100.0, 100.0),
        (100.0, 110.0, 95.0, 100.0),
        (100.0, 120.0, 90.0, 100.0),
    ])
    stats = metrics.range_stats(df)
    assert stats["pc_high"]["max"]["date"] == str(df.index[2].date())
    assert stats["pc_high"]["min"]["date"] == str(df.index[1].date())


def test_range_stats_scopes_to_perf_so_1d_counts_one_day():
    """Regression: a 1D window holds two bars (the prev-close reference plus
    today), and the stats must cover today only — not both days.

    Asserted on an OPEN-based metric on purpose: the prev-close family already
    drops the first bar for lack of a prior close, so it reports one day either
    way and cannot tell a scoped window from an unscoped one.
    """
    df = make_daily([
        (100.0, 105.0, 95.0, 100.0),     # reference bar: open→high = +5%
        (100.0, 110.0, 90.0, 100.0),     # today:         open→high = +10%
    ])
    unscoped = metrics.range_stats(df)
    assert unscoped["o_high"]["days"] == 2
    assert unscoped["o_high"]["avg"] == pytest.approx(7.5), "mean of 5% and 10%"

    scoped = metrics.range_stats(df, df.iloc[1:])
    assert scoped["o_high"]["days"] == 1, "1D must score today only"
    assert scoped["o_high"]["avg"] == pytest.approx(10.0), "today's move, not the mean"
    # The prev-close family is unaffected either way — that is why it is the
    # wrong instrument for this assertion.
    assert unscoped["pc_high"]["days"] == scoped["pc_high"]["days"] == 1


def test_range_stats_computes_the_intraday_relationships():
    # Scope to the second bar so each relationship has exactly one sample:
    # open 100, high 110, low 90, close 105 (prev close 100).
    df = make_daily([
        (100.0, 100.0, 100.0, 100.0),
        (100.0, 110.0, 90.0, 105.0),
    ])
    stats = metrics.range_stats(df, df.iloc[1:])
    assert stats["o_close"]["avg"] == pytest.approx(5.0)     # 100 → 105
    assert stats["o_high"]["avg"] == pytest.approx(10.0)     # 100 → 110
    assert stats["o_low"]["avg"] == pytest.approx(-10.0)     # 100 → 90
    assert stats["l_high"]["avg"] == pytest.approx(22.22, abs=0.01)   # 90 → 110
    assert stats["h_low"]["avg"] == pytest.approx(-18.18, abs=0.01)   # 110 → 90
    assert stats["h_close"]["avg"] == pytest.approx(-4.55, abs=0.01)  # 110 → 105
    assert stats["l_close"]["avg"] == pytest.approx(16.67, abs=0.01)  # 90 → 105
    assert stats["gap"]["avg"] == pytest.approx(0.0)         # prev close 100 → open 100


def test_only_prev_close_metrics_drop_the_first_bar():
    """Open/high/low relationships are self-contained within a bar, so an
    unscoped window scores all n days for them but only n−1 for the prev-close
    family. Mixing the two up silently shifts every average."""
    df = make_daily([
        (100.0, 100.0, 100.0, 100.0),
        (100.0, 110.0, 90.0, 105.0),
    ])
    stats = metrics.range_stats(df)
    assert stats["o_close"]["days"] == 2, "open-based metrics need no prior bar"
    assert stats["pc_high"]["days"] == 1, "prev-close metrics lose the first bar"
    assert stats["o_close"]["avg"] == pytest.approx(2.5), "mean of 0% and 5%"


def test_range_stats_scrubs_infinities_from_a_zero_base():
    df = make_daily([
        (0.0, 0.0, 0.0, 0.0),        # zero prev close → division by zero
        (100.0, 110.0, 90.0, 105.0),
    ])
    stats = metrics.range_stats(df)
    # The infinite row is dropped rather than poisoning the aggregate.
    assert stats["pc_high"] is None or np.isfinite(stats["pc_high"]["avg"])


def test_range_stats_needs_two_bars_for_a_standard_deviation():
    df = make_daily([
        (100.0, 100.0, 100.0, 100.0),
        (100.0, 110.0, 95.0, 100.0),
    ])
    assert metrics.range_stats(df)["pc_high"]["std"] is None, "one sample has no spread"


def test_range_stats_handles_missing_input():
    assert metrics.range_stats(None) is None
    assert metrics.range_stats(pd.DataFrame()) is None


# --------------------------------------------------------------------------
# green/red day counters
# --------------------------------------------------------------------------

def test_green_and_red_days_count_closes_against_the_prior_close():
    df = make_daily([
        (100.0, 100.0, 100.0, 100.0),
        (100.0, 100.0, 100.0, 102.0),   # up
        (100.0, 100.0, 100.0, 101.0),   # down
        (100.0, 100.0, 100.0, 101.0),   # flat — neither
        (100.0, 100.0, 100.0, 103.0),   # up
    ])
    assert metrics.green_days(df, None) == 2
    assert metrics.red_days(df, None) == 1


def test_day_counters_need_at_least_two_bars():
    assert metrics.green_days(make_daily([(1.0, 1.0, 1.0, 1.0)]), None) is None
    assert metrics.green_days(None, None) is None


# --------------------------------------------------------------------------
# time-of-day metrics
# --------------------------------------------------------------------------

def test_day_high_and_low_times_come_from_the_intraday_session():
    # Highs are close+0.5 and lows close−0.5 (see the fixture), so the session
    # high is at the max close and the low at the min close.
    intra = make_intraday([10.0, 15.0, 12.0, 9.0], day="2026-02-02")
    assert metrics.day_high_time(None, intra).startswith("09:31")   # the 15.0 bar
    assert metrics.day_low_time(None, intra).startswith("09:33")    # the 9.0 bar


def test_time_metrics_are_none_without_intraday_data():
    assert metrics.day_high_time(None, None) is None
    assert metrics.day_low_time(None, pd.DataFrame()) is None


# --------------------------------------------------------------------------
# compute_all — the flattened payload the table consumes
# --------------------------------------------------------------------------

def test_compute_all_flattens_every_registered_and_range_column():
    df = make_daily([(100.0, 100.0, 100.0, 100.0), (100.0, 110.0, 90.0, 105.0)])
    out = metrics.compute_all(df, None)
    assert out["green_days"] == 1
    # Range columns are keyed rs:<metric>:<aggregate>; min/max flatten to values.
    assert out["rs:pc_high:avg"] == pytest.approx(10.0)
    assert out["rs:pc_high:min"] == pytest.approx(10.0)
    assert isinstance(out["rs:pc_high:min"], float), "min flattens to a number in the table"


def test_compute_all_survives_a_metric_that_raises(monkeypatch):
    """One broken metric must not take down the whole row."""
    def boom(daily, intraday):
        raise ValueError("bad metric")

    monkeypatch.setitem(metrics.REGISTRY, "green_days",
                        {**metrics.REGISTRY["green_days"], "fn": boom})
    out = metrics.compute_all(make_daily([(1.0, 1.0, 1.0, 1.0)] * 3), None)
    assert out["green_days"] is None
    assert "red_days" in out, "the other metrics still computed"


def test_metric_defs_expose_every_column_to_the_picker():
    defs = metrics.metric_defs()
    keys = {d["key"] for d in defs}
    assert {"green_days", "red_days", "day_high_time", "day_low_time"} <= keys
    # 11 relationships x 5 aggregates
    assert len([k for k in keys if k.startswith("rs:")]) == 55
    registered = [d for d in defs if not d["key"].startswith("rs:")]
    assert all(d["default_on"] for d in registered), "Things to Track default visible"
    assert all(not d["default_on"] for d in defs if d["key"].startswith("rs:")), \
        "the 55 range columns stay off until the user opts in"


# --------------------------------------------------------------------------
# today_vs_typical (the σ Today page)
# --------------------------------------------------------------------------

def build_history(n: int, spread: float = 1.0) -> pd.DataFrame:
    """n bars whose daily moves have real dispersion."""
    rows = []
    price = 100.0
    for i in range(n):
        delta = spread if i % 2 else -spread
        close = price + delta
        rows.append((price, max(price, close) + 0.5, min(price, close) - 0.5, close))
        price = close
    return make_daily(rows, start="2025-06-02")


def test_today_vs_typical_scores_the_latest_session():
    hist = build_history(80)
    today_open = float(hist["Close"].iloc[-1])
    # A dramatic final day: closes 10% below the prior close.
    final = make_daily([(today_open, today_open, today_open * 0.88, today_open * 0.90)],
                       start="2025-10-01")
    df = pd.concat([hist, final])
    out = metrics.today_vs_typical(df)
    assert out is not None
    assert out["session_date"] == str(df.index[-1].date())
    pc_close = out["metrics"]["pc_close"]
    assert pc_close["today"] == pytest.approx(-10.0, abs=0.1)
    assert pc_close["z"] < -2, "a 10% drop is far outside a ±1% typical distribution"


def test_today_vs_typical_needs_enough_history():
    assert metrics.today_vs_typical(build_history(10)) is None
    assert metrics.today_vs_typical(None) is None


def test_today_vs_typical_is_none_when_the_distribution_has_no_spread():
    """A perfectly flat history gives sigma = 0, so a z-score is undefined
    rather than infinite."""
    flat_hist = make_daily([(100.0, 100.0, 100.0, 100.0)] * 40)
    out = metrics.today_vs_typical(flat_hist)
    assert out is None or all(v is None for v in out["metrics"].values())
