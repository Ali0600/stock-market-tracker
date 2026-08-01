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
