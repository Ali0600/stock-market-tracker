"""Behavior tests for the price data layer: caching, windowing, live bars."""
from __future__ import annotations

import threading
import time
from datetime import date

import pandas as pd
import pytest

from app import prices
from tests.conftest import make_daily, make_intraday

KEY = ("AAA", "BBB")


# --------------------------------------------------------------------------
# _cached_fetch — TTL, force, stale fallback, and never caching a failure
# --------------------------------------------------------------------------

class Counter:
    """Fetch stub that records how many times it actually ran."""

    def __init__(self, value=None, fail=False):
        self.calls = 0
        self.value = value if value is not None else {"AAA": "v1"}
        self.fail = fail

    def __call__(self):
        self.calls += 1
        if self.fail:
            raise RuntimeError("yahoo down")
        return self.value


def test_cold_cache_fetches_and_stores():
    fetch = Counter()
    frames, fetched_at, stale = prices._cached_fetch("s", KEY, 600, False, fetch)
    assert frames == {"AAA": "v1"}
    assert stale is False
    assert fetch.calls == 1
    assert fetched_at <= time.time()


def test_warm_cache_does_not_refetch():
    fetch = Counter()
    prices._cached_fetch("s", KEY, 600, False, fetch)
    frames, _, stale = prices._cached_fetch("s", KEY, 600, False, fetch)
    assert fetch.calls == 1, "a fresh entry must be served without a second fetch"
    assert frames == {"AAA": "v1"}
    assert stale is False


def test_expired_ttl_refetches():
    fetch = Counter()
    prices._cached_fetch("s", KEY, 600, False, fetch)
    # ttl=0 makes every existing entry stale.
    prices._cached_fetch("s", KEY, 0, False, fetch)
    assert fetch.calls == 2


def test_force_bypasses_a_fresh_entry():
    fetch = Counter()
    prices._cached_fetch("s", KEY, 600, False, fetch)
    prices._cached_fetch("s", KEY, 600, True, fetch)
    assert fetch.calls == 2


def test_a_different_ticker_set_is_a_miss():
    fetch = Counter()
    prices._cached_fetch("s", KEY, 600, False, fetch)
    prices._cached_fetch("s", ("AAA", "CCC"), 600, False, fetch)
    assert fetch.calls == 2, "the cache key includes the ticker set"


def test_failed_fetch_serves_stale_data_and_flags_it():
    good = Counter({"AAA": "v1"})
    prices._cached_fetch("s", KEY, 600, False, good)
    bad = Counter(fail=True)
    frames, _, stale = prices._cached_fetch("s", KEY, 0, False, bad)
    assert frames == {"AAA": "v1"}, "the previous result is served on failure"
    assert stale is True, "and the caller is told it is stale"


def test_a_failure_is_never_cached():
    """A transient outage must not poison the slot: the failure itself is never
    written to the cache, so the next expired-TTL call retries and recovers
    instead of serving an empty result until the TTL runs out."""
    prices._cached_fetch("s", KEY, 0, False, Counter({"AAA": "v1"}))
    prices._cached_fetch("s", KEY, 0, False, Counter(fail=True))  # degrade

    with prices._state_lock:
        cached = prices._cache["s"]
    assert cached[2] == {"AAA": "v1"}, "the good entry must survive the failure"

    recovered = Counter({"AAA": "v2"})
    frames, _, stale = prices._cached_fetch("s", KEY, 0, False, recovered)
    assert recovered.calls == 1, "the next call must retry, not serve a cached failure"
    assert frames == {"AAA": "v2"}
    assert stale is False


def test_failure_with_nothing_cached_raises():
    with pytest.raises(RuntimeError):
        prices._cached_fetch("s", KEY, 600, False, Counter(fail=True))


# --------------------------------------------------------------------------
# Locking: a slow fetch must not stall unrelated slots (review finding #4)
# --------------------------------------------------------------------------

def test_a_warm_read_never_waits_on_an_in_flight_fetch():
    """Regression: the old design took one global lock BEFORE looking at the
    cache, so a cold 1Y fetch stalled every other read for its whole duration.
    A fresh entry must now be served without acquiring any lock."""
    release = threading.Event()

    def slow():
        release.wait(timeout=5)
        return {"AAA": "slow"}

    prices._cached_fetch("fast-slot", KEY, 600, False, Counter({"AAA": "cached"}))

    blocked = threading.Thread(
        target=lambda: prices._cached_fetch("slow-slot", KEY, 600, False, slow))
    blocked.start()
    try:
        time.sleep(0.1)  # let the slow fetch get in and hold its slot lock
        started = time.monotonic()
        frames, _, _ = prices._cached_fetch(
            "fast-slot", KEY, 600, False, Counter({"AAA": "unused"}))
        elapsed = time.monotonic() - started
        assert frames == {"AAA": "cached"}
        assert elapsed < 0.5, f"warm read waited {elapsed:.2f}s on another slot's fetch"
    finally:
        release.set()
        blocked.join(timeout=5)


def test_two_slots_fetch_concurrently_rather_than_serializing():
    """Per-slot locks: a cold fetch in one slot must not delay a cold fetch in
    another. With a single shared lock the second fetch cannot even begin until
    the first returns, which is what this asserts against."""
    release = threading.Event()
    started = {}

    def fetch_for(slot):
        def run():
            started[slot] = time.monotonic()
            release.wait(timeout=5)
            return {"AAA": slot}
        return run

    threads = [
        threading.Thread(
            target=lambda s=slot: prices._cached_fetch(s, KEY, 600, False, fetch_for(s)))
        for slot in ("slot-a", "slot-b")
    ]
    for t in threads:
        t.start()
    try:
        # Both fetches should be in flight together; neither has returned yet.
        deadline = time.monotonic() + 2
        while len(started) < 2 and time.monotonic() < deadline:
            time.sleep(0.02)
        assert set(started) == {"slot-a", "slot-b"}, (
            f"only {sorted(started)} started — the second fetch is waiting on the first"
        )
    finally:
        release.set()
        for t in threads:
            t.join(timeout=5)


def test_concurrent_requests_for_one_slot_collapse_into_one_fetch():
    release = threading.Event()
    calls = []

    def slow():
        calls.append(1)
        release.wait(timeout=5)
        return {"AAA": "v1"}

    threads = [threading.Thread(
        target=lambda: prices._cached_fetch("s", KEY, 600, False, slow))
        for _ in range(4)]
    for t in threads:
        t.start()
    time.sleep(0.2)
    release.set()
    for t in threads:
        t.join(timeout=5)
    assert len(calls) == 1, "duplicate concurrent fetches for one slot must collapse"


# --------------------------------------------------------------------------
# period_window
# --------------------------------------------------------------------------

def test_1d_window_keeps_two_bars_and_scopes_perf_to_today():
    df = make_daily([(10, 11, 9, 10), (10, 12, 9, 11), (11, 13, 10, 12)])
    window, perf, partial = prices.period_window(df, "1D")
    assert len(window) == 2, "1D needs the prev-close reference bar plus today"
    assert len(perf) == 1, "high/low must cover today only, not the reference bar"
    assert float(perf["High"].iloc[0]) == 13
    assert partial is None


def test_5d_window_keeps_six_bars():
    df = make_daily([(10, 11, 9, 10)] * 10)
    window, perf, _ = prices.period_window(df, "5D")
    assert len(window) == 6
    assert len(perf) == 5


def test_short_history_reports_partial_since_for_bar_sliced_periods():
    df = make_daily([(10, 11, 9, 10), (10, 12, 9, 11)])
    window, _, partial = prices.period_window(df, "5D")
    assert len(window) == 2
    assert partial == str(df.index[0].date())


def test_whole_range_periods_use_every_bar():
    df = make_daily([(10, 11, 9, 10)] * 40)
    window, perf, _ = prices.period_window(df, "3M")
    assert len(window) == len(perf) == 40, "whole-range windows include the first bar"


def test_partial_since_flags_a_recent_ipo(monkeypatch):
    """A ticker whose history starts well inside the window is marked partial."""
    class FakeDate(date):
        @classmethod
        def today(cls):
            return date(2026, 6, 12)

    monkeypatch.setattr(prices, "date", FakeDate)
    recent = make_daily([(10, 11, 9, 10)] * 5, start="2026-06-01")
    _, _, partial = prices.period_window(recent, "3M")
    assert partial == "2026-06-01"

    old = make_daily([(10, 11, 9, 10)] * 60, start="2026-01-05")
    _, _, no_partial = prices.period_window(old, "3M")
    assert no_partial is None


# --------------------------------------------------------------------------
# _with_live_bar
# --------------------------------------------------------------------------

def test_live_bar_is_appended_and_aggregates_the_session():
    daily = make_daily([(10, 11, 9, 10)], start="2026-01-30")
    intra = make_intraday([20.0, 25.0, 22.0], day="2026-02-02")
    merged = prices._with_live_bar(daily, intra)
    assert len(merged) == 2, "a newer intraday session becomes one synthetic daily bar"
    bar = merged.iloc[-1]
    assert bar["Open"] == 20.0                      # first intraday open
    assert bar["High"] == pytest.approx(25.5)       # max of intraday highs
    assert bar["Low"] == pytest.approx(19.5)        # min of intraday lows
    assert bar["Close"] == 22.0                     # last intraday close


def test_live_bar_is_not_appended_when_the_day_already_closed():
    daily = make_daily([(10, 11, 9, 10)], start="2026-02-02")
    intra = make_intraday([20.0, 21.0], day="2026-02-02")
    merged = prices._with_live_bar(daily, intra)
    assert len(merged) == 1, "the daily feed already has this session — no duplicate"


def test_live_bar_handles_missing_or_empty_intraday():
    daily = make_daily([(10, 11, 9, 10)])
    assert len(prices._with_live_bar(daily, None)) == 1
    assert len(prices._with_live_bar(daily, make_intraday([]))) == 1


# --------------------------------------------------------------------------
# _split
# --------------------------------------------------------------------------

def test_split_normalizes_a_multiindex_download():
    idx = pd.bdate_range("2026-01-05", periods=3, tz="America/New_York")
    cols = pd.MultiIndex.from_product([["AAA", "BBB"], ["Open", "High", "Low", "Close"]])
    df = pd.DataFrame(1.0, index=idx, columns=cols)
    out = prices._split(df, ("AAA", "BBB"))
    assert set(out) == {"AAA", "BBB"}
    assert list(out["AAA"].columns) == ["Open", "High", "Low", "Close"]


def test_split_handles_a_single_ticker_flat_frame():
    out = prices._split(make_daily([(10, 11, 9, 10)]), ("AAA",))
    assert set(out) == {"AAA"}


def test_split_drops_tickers_with_no_usable_rows():
    idx = pd.bdate_range("2026-01-05", periods=2, tz="America/New_York")
    cols = pd.MultiIndex.from_product([["AAA", "DEAD"], ["Open", "High", "Low", "Close"]])
    df = pd.DataFrame(1.0, index=idx, columns=cols)
    df[("DEAD", "Close")] = float("nan")            # delisted: no closes at all
    out = prices._split(df, ("AAA", "DEAD"))
    assert set(out) == {"AAA"}, "a ticker with no closes is dropped, not returned empty"


def test_split_ignores_a_ticker_missing_from_the_response():
    out = prices._split(make_daily([(10, 11, 9, 10)]), ("AAA",))
    assert "ZZZ" not in out
    assert prices._split(None, ("AAA",)) == {}
    assert prices._split(pd.DataFrame(), ("AAA",)) == {}


# --------------------------------------------------------------------------
# small pure helpers
# --------------------------------------------------------------------------

def test_pct_is_none_for_a_zero_or_non_finite_reference():
    assert prices._pct(110, 100) == 10.0
    assert prices._pct(90, 100) == -10.0
    assert prices._pct(100, 0) is None
    assert prices._pct(100, float("nan")) is None
    assert prices._pct(float("inf"), 100) is None


def test_round_price_gives_sub_dollar_tickers_more_precision():
    assert prices._round_price(123.4567) == 123.46
    assert prices._round_price(0.12345) == 0.1235


def test_effective_shares_prefers_market_cap_over_reported_shares():
    # ADR case: marketCap/price is the ratio-correct count, so it wins.
    assert prices.effective_shares(1000.0, 10.0, 999.0) == 100.0
    assert prices.effective_shares(None, 10.0, 999.0) == 999.0
    assert prices.effective_shares(1000.0, None, None) is None
    assert prices.effective_shares(None, None, None) is None


# --------------------------------------------------------------------------
# compute_core / current_and_day_pct / chart_series
# --------------------------------------------------------------------------

def test_compute_core_builds_the_table_row():
    df = make_daily([(10, 10, 10, 10), (10, 15, 8, 12)])
    row, window, perf = prices.compute_core(df, "1D")
    assert row["current"] == 12
    assert row["ago"] == 10
    assert row["pct"] == 20.0            # 12 vs 10
    assert row["high"] == 15
    assert row["low"] == 8
    assert row["high_pct"] == -20.0      # 12 is 20% below the 15 high
    assert row["low_pct"] == 50.0        # 12 is 50% above the 8 low
    assert len(window) == 2 and len(perf) == 1


def test_current_and_day_pct_folds_in_the_live_bar():
    daily = make_daily([(10, 10, 10, 10)], start="2026-01-30")
    intra = make_intraday([11.0, 12.0], day="2026-02-02")
    current, day_pct = prices.current_and_day_pct(daily, intra)
    assert current == 12.0
    assert day_pct == 20.0
    assert prices.current_and_day_pct(None, None) == (None, None)


def test_chart_series_uses_intraday_times_for_1d():
    daily = make_daily([(10, 10, 10, 10)], start="2026-01-30")
    intra = make_intraday([11.0, 12.0], day="2026-02-02")
    points = prices.chart_series(daily, intra, "1D")
    assert points[0][0] == "09:30", "1D plots clock times, not dates"
    assert points[-1][1] == 12.0


def test_chart_series_falls_back_to_two_daily_bars_without_intraday():
    daily = make_daily([(10, 10, 10, 10), (11, 11, 11, 11), (12, 12, 12, 12)])
    points = prices.chart_series(daily, None, "1D")
    assert len(points) == 2, "no intraday — show prev close plus latest"
    assert points[0][0] == str(daily.index[-2].date())


def test_chart_series_scopes_other_periods_to_the_window():
    daily = make_daily([(10, 10, 10, 10)] * 10)
    assert len(prices.chart_series(daily, None, "5D")) == 6
    assert prices.chart_series(None, None, "3M") == []


# --------------------------------------------------------------------------
# get_daily error handling
# --------------------------------------------------------------------------

def test_get_daily_reports_a_fetch_error_instead_of_raising(monkeypatch):
    def boom(*args, **kwargs):
        raise RuntimeError("network down")

    monkeypatch.setattr(prices.yf, "download", boom)
    frames, _, stale, error = prices.get_daily(["AAA"], "3M")
    assert frames == {}
    assert stale is True
    assert error is not None and "RuntimeError" in error


def test_get_daily_caches_across_calls(monkeypatch):
    calls = []

    def fake_download(tickers, **kwargs):
        calls.append(tickers)
        return make_daily([(10, 11, 9, 10)] * 3)

    monkeypatch.setattr(prices.yf, "download", fake_download)
    prices.get_daily(["AAA"], "3M")
    prices.get_daily(["AAA"], "3M")
    assert len(calls) == 1, "the second call is served from the TTL cache"


def test_cache_status_reports_each_slot():
    prices._cached_fetch("daily:3mo", KEY, 600, False, Counter())
    prices._cached_fetch("intraday", KEY, 300, False, Counter())
    status = {s["slot"]: s for s in prices.cache_status()}
    assert set(status) == {"daily:3mo", "intraday"}
    assert status["daily:3mo"]["ttl_seconds"] == prices.DAILY_TTL
    assert status["intraday"]["ttl_seconds"] == prices.INTRADAY_TTL
    assert status["daily:3mo"]["tickers"] == len(KEY)


# --------------------------------------------------------------------------
# pattern histories — their own slots, windows and TTL
# --------------------------------------------------------------------------

def test_pattern_histories_request_their_windows_and_cache_separately(monkeypatch):
    calls = []

    def fake_download(tickers, **kwargs):
        calls.append((kwargs.get("period"), kwargs.get("interval"), kwargs.get("prepost")))
        return make_daily([(10, 11, 9, 10)] * 3)

    monkeypatch.setattr(prices.yf, "download", fake_download)
    frames, _, stale, error = prices.get_history(["AAA"])
    prices.get_history(["AAA"])
    prices.get_intraday_history(["AAA"])
    prices.get_daily(["AAA"], "1Y")
    assert (stale, error) == (False, None) and "AAA" in frames
    # One fetch each: the 2y history, the 60-session five-minute history, and
    # the live 1Y table — the history slots never answer for the table's.
    assert calls == [("2y", "1d", None), ("60d", "5m", False), ("1y", "1d", None)]


def test_pattern_slots_report_their_own_ttl():
    prices._cached_fetch(prices.HISTORY_SLOT, KEY, prices.PATTERN_TTL, False, Counter())
    prices._cached_fetch(prices.INTRADAY_HISTORY_SLOT, KEY, prices.PATTERN_TTL, False, Counter())
    status = {s["slot"]: s for s in prices.cache_status()}
    assert status["daily:2y"]["ttl_seconds"] == 3600
    assert status["intraday:60d:5m"]["ttl_seconds"] == 3600


def test_pattern_history_reports_a_fetch_error_instead_of_raising(monkeypatch):
    def boom(*args, **kwargs):
        raise RuntimeError("network down")

    monkeypatch.setattr(prices.yf, "download", boom)
    for fetch in (prices.get_history, prices.get_intraday_history):
        frames, _, stale, error = fetch(["AAA"])
        assert frames == {} and stale is True and "RuntimeError" in error
