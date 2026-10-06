"""Shared fixtures: synthetic OHLC frames so no test ever touches the network."""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
import pytest

# Make the `app` package importable when pytest runs from the repo root.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import prices  # noqa: E402


def make_daily(rows: list[tuple], start: str = "2026-01-05", tz: str = "America/New_York"
               ) -> pd.DataFrame:
    """Build a daily OHLCV frame from (open, high, low, close) tuples on
    consecutive business days."""
    index = pd.bdate_range(start=start, periods=len(rows), tz=tz)
    return pd.DataFrame(
        {
            "Open": [r[0] for r in rows],
            "High": [r[1] for r in rows],
            "Low": [r[2] for r in rows],
            "Close": [r[3] for r in rows],
            "Adj Close": [r[3] for r in rows],
            "Volume": [1_000_000.0] * len(rows),
        },
        index=index,
    )


def make_intraday(closes: list[float], day: str = "2026-02-02",
                  tz: str = "America/New_York") -> pd.DataFrame:
    """1-minute bars for a single session, starting at the 09:30 open."""
    index = pd.date_range(start=f"{day} 09:30", periods=len(closes), freq="min", tz=tz)
    return pd.DataFrame(
        {
            "Open": closes,
            "High": [c + 0.5 for c in closes],
            "Low": [c - 0.5 for c in closes],
            "Close": closes,
            "Adj Close": closes,
            "Volume": [1000.0] * len(closes),
        },
        index=index,
    )


def flat(n: int, price: float = 100.0, start: str = "2025-01-06") -> pd.DataFrame:
    """n identical bars — warm-up history for rolling-window statistics."""
    return make_daily([(price, price, price, price)] * n, start=start)


@pytest.fixture(autouse=True)
def clear_price_cache():
    """Every test starts with cold caches; module-level state must not leak."""
    with prices._state_lock:
        prices._cache.clear()
        prices._slot_locks.clear()
    yield
    with prices._state_lock:
        prices._cache.clear()
        prices._slot_locks.clear()


def make_session_bars(day: str, closes: list[float], tz: str = "America/New_York"
                      ) -> pd.DataFrame:
    """Five-minute bars for one session from 09:30. Highs/lows sit 0.5 either
    side of each close, so a session's high is at its largest close."""
    index = pd.date_range(start=f"{day} 09:30", periods=len(closes), freq="5min", tz=tz)
    return pd.DataFrame(
        {
            "Open": closes,
            "High": [c + 0.5 for c in closes],
            "Low": [c - 0.5 for c in closes],
            "Close": closes,
            "Adj Close": closes,
            "Volume": [1000.0] * len(closes),
        },
        index=index,
    )


def make_closes(closes: list[float], opens: list[float] | None = None,
                start: str = "2026-01-05") -> pd.DataFrame:
    """Daily frame from closes (opens default to the close, so no gaps);
    high/low are widened so they never decide anything."""
    opens = opens if opens is not None else closes
    return make_daily([(o, max(o, c) + 1, min(o, c) - 1, c)
                       for o, c in zip(opens, closes, strict=True)], start=start)


def weekly_pattern(weeks: int, monday_effect: bool) -> pd.DataFrame:
    """A base bar (Monday 2026-01-05) plus `weeks` x 5 sessions. Session j
    moves +1% when j is odd and -1% when even; since 5k+d has the parity of
    k+d, every weekday is green exactly half the time. With monday_effect,
    every Monday is +2% instead."""
    closes = [100.0]
    for j in range(1, weeks * 5 + 1):
        if monday_effect and j % 5 == 0:
            change = 2.0
        else:
            change = 1.0 if j % 2 else -1.0
        closes.append(closes[-1] * (1 + change / 100))
    return make_closes(closes)
