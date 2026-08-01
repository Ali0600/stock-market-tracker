"""Hand-computed fixtures for the backtest engine.

Every expectation below is derived by hand from the synthetic bars in the
test, so a change in engine behavior shows up as a specific arithmetic
mismatch rather than a vague diff. These pin the honesty invariants the
engine promises (see app/backtest.py's module docstring): no look-ahead,
conservative fills, stop-before-target, no entry-day take-profit.
"""
from __future__ import annotations

import pandas as pd
import pytest

from app import backtest
from tests.conftest import flat, make_daily

LOOKBACK = 20
# Enough flat warm-up that `len(bars) - lookback >= MIN_TRADEABLE_DAYS`.
WARMUP = 45
WARMUP_PRICE = 100.0


def rule(**overrides) -> dict:
    base = {
        "entry_mode": "pct", "entry_value": 2.0,
        "avg_in_enabled": False, "avg_in_level": 4.0,
        "tp_mode": "pct", "tp_value": 3.0,
        "stop_pct": 5.0, "max_hold_days": 10,
        "cost_bps": 0.0,            # costs off by default so P&L math is exact
        "lookback": LOOKBACK,
    }
    base.update(overrides)
    return base


def frame(scenario: list[tuple]) -> pd.DataFrame:
    """Flat warm-up history followed by the scenario's bars."""
    warm = flat(WARMUP, WARMUP_PRICE)
    tail = make_daily(scenario, start="2025-03-10")
    return pd.concat([warm, tail])


# --------------------------------------------------------------------------
# 1. A clean round trip: limit entry, then the target is reached
# --------------------------------------------------------------------------

def test_limit_entry_then_target_exit():
    # Day 1 opens at 100, dips to 97 → the 2%-below-prev-close limit (98.00)
    # is touched, filling at 98.00 (not the low). Day 2 reaches 101.50, at or
    # above the 3% target (98.00 * 1.03 = 100.94) → exit at the target price.
    df = frame([
        (100.0, 100.5, 97.0, 99.0),
        (99.5, 101.5, 99.0, 101.0),
    ])
    out = backtest.run_backtest(df, rule())
    assert len(out["trades"]) == 1
    trade = out["trades"][0]
    assert trade["tranches"][0]["price"] == 98.00, "fills at the limit, not the low"
    assert trade["exit_price"] == pytest.approx(100.94, abs=0.01)
    assert trade["reason"] == "target"
    assert trade["pnl_pct"] == pytest.approx(3.0, abs=0.01)


# --------------------------------------------------------------------------
# 2. Gapping through the entry level fills at the open (the worse price)
# --------------------------------------------------------------------------

def test_gap_through_the_entry_level_fills_at_the_open():
    # Prev close 100 → limit 98.00. The day OPENS at 96 (already through the
    # limit), so the fill is 96.00, not 98.00 — you cannot buy at a price the
    # market gapped past.
    df = frame([(96.0, 97.0, 95.0, 96.5)])
    out = backtest.run_backtest(df, rule())
    trades = out["trades"] or ([out["open_trade"]] if out["open_trade"] else [])
    assert trades, "the gap should still trigger an entry"
    fill = (out["trades"][0]["tranches"][0]["price"] if out["trades"]
            else out["open_trade"]["avg_cost"])
    assert fill == 96.00, "a gap below the limit fills at the open"


# --------------------------------------------------------------------------
# 3. When both the stop and the target are touchable, the stop wins
# --------------------------------------------------------------------------

def test_stop_is_assumed_to_hit_before_the_target():
    # Entry day: limit 98.00 filled. Stop = 98 * 0.95 = 93.10,
    # target = 98 * 1.03 = 100.94.
    # Day 2's range (92.00 low, 102.00 high) touches BOTH. Daily bars cannot
    # order the intraday path, so the engine must assume the stop.
    df = frame([
        (100.0, 100.5, 97.5, 99.0),
        (99.0, 102.0, 92.0, 101.0),
    ])
    out = backtest.run_backtest(df, rule())
    assert len(out["trades"]) == 1
    trade = out["trades"][0]
    assert trade["reason"] == "stop", "a day that touches both must resolve as the stop"
    assert trade["exit_price"] == pytest.approx(93.10, abs=0.01)


# --------------------------------------------------------------------------
# 4. No take-profit on the entry day
# --------------------------------------------------------------------------

def test_no_take_profit_on_the_entry_day():
    # The day dips to the 98.00 limit AND rallies to 105 — above the 100.94
    # target. Because the intraday order of the dip and the rally is unknowable
    # from a daily bar, the position must still be open at the close.
    df = frame([(100.0, 105.0, 97.0, 104.0)])
    out = backtest.run_backtest(df, rule())
    assert out["trades"] == [], "entry-day target touches must not be taken"
    assert out["open_trade"] is not None
    assert out["open_trade"]["avg_cost"] == 98.00


def test_the_stop_still_applies_on_the_entry_day():
    # Conservative asymmetry: the same unknowable ordering resolves AGAINST
    # the trader, so an entry-day stop touch does close the position.
    # Limit 98.00, stop 93.10; the day's low of 92 reaches it.
    df = frame([(100.0, 100.5, 92.0, 94.0)])
    out = backtest.run_backtest(df, rule())
    assert len(out["trades"]) == 1
    assert out["trades"][0]["reason"] == "stop"


# --------------------------------------------------------------------------
# 5. Gapping below the stop exits at the open
# --------------------------------------------------------------------------

def test_gap_below_the_stop_exits_at_the_open():
    # Entry at 98.00 on day 1 (stop 93.10). Day 2 opens at 90 — already below
    # the stop — so the exit is 90.00, worse than the stop price.
    df = frame([
        (100.0, 100.5, 97.5, 99.0),
        (90.0, 91.0, 89.0, 90.5),
    ])
    out = backtest.run_backtest(df, rule())
    assert len(out["trades"]) == 1
    trade = out["trades"][0]
    assert trade["reason"] == "stop"
    assert trade["exit_price"] == 90.00, "a gap below the stop cannot fill at the stop"


# --------------------------------------------------------------------------
# 6. Time exit closes at the close after max_hold_days
# --------------------------------------------------------------------------

def test_time_exit_closes_at_the_close():
    # Entry at 98.00, then two quiet days that reach neither 93.10 nor 100.94.
    # With max_hold_days=2 the position is closed at day 3's close (99.00).
    df = frame([
        (100.0, 100.5, 97.5, 99.0),
        (99.0, 99.5, 98.5, 99.2),
        (99.2, 99.6, 98.6, 99.0),
    ])
    out = backtest.run_backtest(df, rule(max_hold_days=2))
    assert len(out["trades"]) == 1
    trade = out["trades"][0]
    assert trade["reason"] == "time"
    assert trade["exit_price"] == 99.00
    assert trade["held_days"] == 2


# --------------------------------------------------------------------------
# 7. Sigma entry mode uses only strictly-prior bars
# --------------------------------------------------------------------------

def test_sigma_mode_threshold_uses_only_prior_bars():
    """The entry threshold on day T must be computable from data through T−1.
    Flat warm-up gives a zero-sigma distribution, so no sigma entry can fire
    until enough varied history exists — proving the stats are backward-looking
    rather than peeking at the day they are evaluating."""
    df = frame([(100.0, 100.5, 90.0, 95.0)])
    out = backtest.run_backtest(df, rule(entry_mode="sigma", entry_value=2.0))
    assert out["trades"] == [] and out["open_trade"] is None, (
        "a zero-sigma trailing window cannot produce a threshold, so the deep "
        "dip on the final day must not trigger an entry"
    )


def test_sigma_mode_can_enter_once_the_trailing_window_has_spread():
    # Give the lookback window real dispersion, then a deep dip.
    varied = [(100.0, 101.0, 98.0 - (i % 3), 100.0) for i in range(WARMUP)]
    df = pd.concat([
        make_daily(varied, start="2025-01-06"),
        make_daily([(100.0, 100.5, 85.0, 90.0)], start="2025-03-10"),
    ])
    out = backtest.run_backtest(df, rule(entry_mode="sigma", entry_value=1.0))
    assert out["trades"] or out["open_trade"], "a large dip should clear the sigma threshold"


# --------------------------------------------------------------------------
# 8. Averaging in adds a tranche and re-derives the levels
# --------------------------------------------------------------------------

def test_average_in_adds_a_tranche_and_moves_the_levels():
    # Day 1: limit 98.00 filled; the same day's low of 95.5 also reaches the
    # 4%-below-prev-close second tranche at 96.00 → two fills, average cost
    # (98.00 + 96.00) / 2 = 97.00.
    df = frame([(100.0, 100.5, 95.5, 96.0)])
    out = backtest.run_backtest(df, rule(avg_in_enabled=True, avg_in_level=4.0))
    open_trade = out["open_trade"]
    assert open_trade is not None, "both tranches fill; nothing exits on the entry day"
    assert open_trade["avg_cost"] == 97.00


def test_average_in_target_is_measured_from_the_blended_cost():
    # Two tranches → avg cost 97.00, so the 3% target is 99.91 (not 100.94).
    df = frame([
        (100.0, 100.5, 95.5, 96.0),
        (96.0, 100.0, 95.8, 99.9),
    ])
    out = backtest.run_backtest(df, rule(avg_in_enabled=True, avg_in_level=4.0))
    assert len(out["trades"]) == 1
    trade = out["trades"][0]
    assert trade["avg_cost"] == 97.00
    assert trade["reason"] == "target"
    assert trade["exit_price"] == pytest.approx(99.91, abs=0.01)


# --------------------------------------------------------------------------
# 9. Costs are applied per side
# --------------------------------------------------------------------------

def test_costs_are_charged_on_both_sides():
    df = frame([
        (100.0, 100.5, 97.0, 99.0),
        (99.5, 101.5, 99.0, 101.0),
    ])
    free = backtest.run_backtest(df, rule())["trades"][0]["pnl_pct"]
    charged = backtest.run_backtest(df, rule(cost_bps=50.0))["trades"][0]["pnl_pct"]
    # 50 bps a side ≈ 1% round trip off a ~3% gross gain.
    assert free - charged == pytest.approx(1.0, abs=0.05)


# --------------------------------------------------------------------------
# 10. Guard rails
# --------------------------------------------------------------------------

def test_insufficient_history_is_reported_not_crashed():
    short = flat(10, WARMUP_PRICE)
    out = backtest.run_backtest(short, rule())
    assert "error" in out and "insufficient history" in out["error"]


def test_missing_frame_is_reported():
    assert "error" in backtest.run_backtest(None, rule())
    assert "error" in backtest.run_backtest(pd.DataFrame(), rule())


def test_a_flat_market_produces_no_trades_but_still_summarizes():
    out = backtest.run_backtest(flat(WARMUP + 10, WARMUP_PRICE), rule())
    assert out["trades"] == []
    assert out["summary"]["trades"] == 0
    assert out["summary"]["buy_hold_pct"] == pytest.approx(0.0)
    assert out["summary"]["time_in_market_pct"] == 0.0


# --------------------------------------------------------------------------
# 11. The no-look-ahead invariant, stated directly
# --------------------------------------------------------------------------

def test_future_bars_cannot_change_an_already_closed_trade():
    """The strongest statement of the no-look-ahead promise: rewriting every
    bar AFTER a trade closed must leave that trade byte-identical."""
    scenario = [
        (100.0, 100.5, 97.0, 99.0),
        (99.5, 101.5, 99.0, 101.0),
    ]
    baseline = backtest.run_backtest(frame(scenario), rule())["trades"][0]

    # Append wildly different future history; the closed trade must not move.
    extended = frame(scenario + [(200.0, 250.0, 10.0, 220.0)] * 5)
    after = backtest.run_backtest(extended, rule())["trades"][0]
    assert after == baseline, "a closed trade must not depend on later bars"


def test_trailing_stats_do_not_include_the_evaluated_day():
    """Sigma-mode entry on day T must not be able to 'see' day T's own dip.
    Deepening only the final bar's low must not change whether earlier days
    produced entries."""
    varied = [(100.0, 101.0, 98.0 - (i % 3), 100.0) for i in range(WARMUP + 5)]
    mild = make_daily(varied + [(100.0, 100.5, 99.0, 100.0)], start="2025-01-06")
    harsh = make_daily(varied + [(100.0, 100.5, 60.0, 65.0)], start="2025-01-06")

    mild_trades = backtest.run_backtest(mild, rule(entry_mode="sigma"))["trades"]
    harsh_trades = backtest.run_backtest(harsh, rule(entry_mode="sigma"))["trades"]
    # Trades that closed before the final bar must be identical in both runs.
    last_date = str(mild.index[-1].date())
    mild_before = [t for t in mild_trades if t["exit_date"] < last_date]
    harsh_before = [t for t in harsh_trades if t["exit_date"] < last_date]
    assert mild_before == harsh_before, "changing the last bar rewrote earlier history"


# --------------------------------------------------------------------------
# 12. Summary arithmetic
# --------------------------------------------------------------------------

def test_summary_reports_hit_rate_and_expectancy_over_the_trades():
    df = frame([
        (100.0, 100.5, 97.0, 99.0),      # entry at 98.00
        (99.5, 101.5, 99.0, 101.0),      # target → +3%
        (101.0, 101.5, 98.0, 99.0),      # entry at 98.98 (2% below 101.0)
        (99.0, 99.5, 90.0, 91.0),        # stop → −5%
    ])
    summary = backtest.run_backtest(df, rule())["summary"]
    assert summary["trades"] == 2
    assert summary["hit_rate"] == pytest.approx(50.0)
    # One +3% and one −5% → expectancy −1%.
    assert summary["expectancy_pct"] == pytest.approx(-1.0, abs=0.05)
    assert summary["max_drawdown_pct"] == pytest.approx(5.0, abs=0.05)
