"""Historical rule simulation over daily bars — strictly descriptive.

Honesty constraints baked in:
- Trailing statistics are computed on a rolling window shifted by one day —
  a rule evaluated on day T only sees data through T−1 (no look-ahead).
- Limit fills happen only when the day's range actually reached the price;
  gaps through a level fill at the open (the worse price for the trader).
- When both the stop and the target were touchable in one day, the stop is
  assumed to hit first. No take-profit on the entry day (intraday ordering
  of the dip and any rebound is unknowable from daily bars).
- Costs are applied per side.

This module computes history only. Past results do not predict future ones,
and nothing here is investment advice.
"""
from __future__ import annotations

import math
from typing import Optional

import pandas as pd

ASSUMPTIONS = [
    "Trailing stats use a rolling window ending the prior day (no look-ahead).",
    "Limit fills require the day's range to reach the price; gaps fill at the open.",
    "If both stop and target were touchable in one day, the stop is assumed first.",
    "No take-profit on the entry day (daily bars can't order the intraday path).",
    "Completed daily sessions only; costs applied per side.",
    "Historical simulation on ~1 year of data — not a prediction, not advice.",
]

MIN_TRADEABLE_DAYS = 20


def _round2(value: Optional[float]) -> Optional[float]:
    return None if value is None else round(float(value), 2)


def run_backtest(df: Optional[pd.DataFrame], rule: dict) -> dict:
    """Simulate one ticker. Returns {trades, open_trade, summary} or {error}."""
    if df is None or df.empty:
        return {"error": "no price data"}
    bars = df.dropna(subset=["Open", "High", "Low", "Close"])
    lookback = int(rule["lookback"])
    if len(bars) - lookback < MIN_TRADEABLE_DAYS:
        return {"error": f"insufficient history ({len(bars)} bars for lookback {lookback})"}

    closes = bars["Close"]
    prev_close = closes.shift(1)
    pc_low_series = (bars["Low"] - prev_close) / prev_close * 100
    l_high_series = (bars["High"] - bars["Low"]) / bars["Low"] * 100
    # .shift(1): the stats a trader could have known that morning.
    avg_pc_low = pc_low_series.rolling(lookback).mean().shift(1)
    std_pc_low = pc_low_series.rolling(lookback).std(ddof=1).shift(1)
    avg_l_high = l_high_series.rolling(lookback).mean().shift(1)

    fee = float(rule["cost_bps"]) / 10000.0
    trades: list[dict] = []
    pos: Optional[dict] = None

    def close_position(exit_price: float, exit_date, reason: str) -> None:
        nonlocal pos
        cost = sum(t["price"] for t in pos["tranches"]) * (1 + fee)
        proceeds = len(pos["tranches"]) * exit_price * (1 - fee)
        gross = sum(t["price"] for t in pos["tranches"])
        trades.append({
            "entry_date": str(pos["tranches"][0]["date"].date()),
            "tranches": [{"date": str(t["date"].date()), "price": _round2(t["price"])}
                         for t in pos["tranches"]],
            "avg_cost": _round2(gross / len(pos["tranches"])),
            "exit_date": str(exit_date.date()),
            "exit_price": _round2(exit_price),
            "reason": reason,
            "pnl_pct": _round2((proceeds / cost - 1) * 100),
            "held_days": pos["held_days"],
            "entry_threshold_pct": _round2(pos["threshold_pct"]),
        })
        pos = None

    def levels() -> tuple[float, float]:
        """(stop price, target price) from the current average cost."""
        avg_cost = sum(t["price"] for t in pos["tranches"]) / len(pos["tranches"])
        return (avg_cost * (1 - float(rule["stop_pct"]) / 100),
                avg_cost * (1 + pos["tp_pct"] / 100))

    first_tradeable = None
    for i in range(len(bars)):
        date = bars.index[i]
        o = float(bars["Open"].iloc[i])
        h = float(bars["High"].iloc[i])
        lo = float(bars["Low"].iloc[i])
        c = float(bars["Close"].iloc[i])

        if pos is None:
            # ---- look for an entry ----
            if i < lookback + 1:
                continue
            pc = float(prev_close.iloc[i])
            if math.isnan(pc):
                continue
            if first_tradeable is None:
                first_tradeable = i
            if rule["entry_mode"] == "sigma":
                mean = float(avg_pc_low.iloc[i]) if not math.isnan(avg_pc_low.iloc[i]) else None
                sigma = float(std_pc_low.iloc[i]) if not math.isnan(std_pc_low.iloc[i]) else None
                if mean is None or sigma is None or sigma == 0:
                    continue
                threshold_pct = mean - float(rule["entry_value"]) * sigma
            else:
                threshold_pct = -float(rule["entry_value"])
            limit = pc * (1 + threshold_pct / 100)
            if lo > limit:
                continue
            fill = o if o < limit else limit  # gap through the level → open

            if rule["tp_mode"] == "avg_range":
                alh = float(avg_l_high.iloc[i]) if not math.isnan(avg_l_high.iloc[i]) else None
                if alh is None or alh <= 0:
                    continue
                tp_pct = float(rule["tp_value"]) * alh
            else:
                tp_pct = float(rule["tp_value"])

            pos = {
                "tranches": [{"date": date, "price": fill}],
                "tp_pct": tp_pct,
                "threshold_pct": threshold_pct,
                "tranche2_limit": (pc * (1 - float(rule["avg_in_level"]) / 100)
                                   if rule["avg_in_enabled"] else None),
                "held_days": 0,
            }
            # Entry day: stop can trigger (conservative), take-profit cannot.
            stop, _ = levels()
            if pos["tranche2_limit"] is not None and lo <= pos["tranche2_limit"] and fill > pos["tranche2_limit"]:
                t2_fill = o if o < pos["tranche2_limit"] else pos["tranche2_limit"]
                pos["tranches"].append({"date": date, "price": t2_fill})
                pos["tranche2_limit"] = None
                stop, _ = levels()
            if lo <= stop:
                close_position(min(stop, c) if o > stop else o, date, "stop")
            continue

        # ---- manage an open position ----
        pos["held_days"] += 1
        stop, target = levels()

        # Gap below the stop → out at the open, nothing else happens today.
        if o <= stop:
            close_position(o, date, "stop")
            continue

        # Second tranche on the way down (its level is below the current stop
        # only when the user set it that deep — then the stop wins first).
        if pos["tranche2_limit"] is not None and lo <= pos["tranche2_limit"]:
            if pos["tranche2_limit"] >= stop:
                t2_fill = o if o < pos["tranche2_limit"] else pos["tranche2_limit"]
                pos["tranches"].append({"date": date, "price": t2_fill})
                pos["tranche2_limit"] = None
                stop, target = levels()

        if lo <= stop:
            close_position(stop, date, "stop")
            continue
        if o >= target:  # gap above the target → out at the (better) open
            close_position(o, date, "target")
            continue
        if h >= target:
            close_position(target, date, "target")
            continue
        if pos["held_days"] >= int(rule["max_hold_days"]):
            close_position(c, date, "time")
            continue

    open_trade = None
    if pos is not None:
        last_close = float(closes.iloc[-1])
        avg_cost = sum(t["price"] for t in pos["tranches"]) / len(pos["tranches"])
        open_trade = {
            "entry_date": str(pos["tranches"][0]["date"].date()),
            "avg_cost": _round2(avg_cost),
            "unrealized_pct": _round2((last_close * (1 - fee)) / (avg_cost * (1 + fee)) * 100 - 100),
            "held_days": pos["held_days"],
        }

    return {
        "trades": trades,
        "open_trade": open_trade,
        "summary": _summarize(trades, bars, first_tradeable, fee),
    }


def _summarize(trades: list[dict], bars: pd.DataFrame,
               first_tradeable: Optional[int], fee: float) -> dict:
    start = first_tradeable if first_tradeable is not None else len(bars) - 1
    span_days = len(bars) - start
    buy_hold = (float(bars["Close"].iloc[-1]) / float(bars["Close"].iloc[start]) - 1) * 100

    if not trades:
        return {"trades": 0, "hit_rate": None, "avg_win_pct": None, "avg_loss_pct": None,
                "expectancy_pct": None, "cumulative_pct": None, "max_drawdown_pct": None,
                "cost_drag_pct": None, "time_in_market_pct": 0.0,
                "buy_hold_pct": _round2(buy_hold), "span_days": span_days}

    pnls = [t["pnl_pct"] for t in trades]
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p <= 0]

    equity, peak, max_dd = 1.0, 1.0, 0.0
    for p in pnls:
        equity *= 1 + p / 100
        peak = max(peak, equity)
        max_dd = max(max_dd, (peak - equity) / peak)

    # Round-trip cost drag ≈ 2 × fee per tranche-leg, expressed per trade.
    legs = sum(len(t["tranches"]) + 1 for t in trades)
    cost_drag = legs * fee * 100

    return {
        "trades": len(trades),
        "hit_rate": _round2(len(wins) / len(pnls) * 100),
        "avg_win_pct": _round2(sum(wins) / len(wins)) if wins else None,
        "avg_loss_pct": _round2(sum(losses) / len(losses)) if losses else None,
        "expectancy_pct": _round2(sum(pnls) / len(pnls)),
        "cumulative_pct": _round2((equity - 1) * 100),
        "max_drawdown_pct": _round2(max_dd * 100),
        "cost_drag_pct": _round2(cost_drag),
        "time_in_market_pct": _round2(sum(t["held_days"] + 1 for t in trades) / span_days * 100),
        "buy_hold_pct": _round2(buy_hold),
        "span_days": span_days,
    }
