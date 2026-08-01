"""Price data layer: batched yfinance fetches, TTL cache, period windowing."""
from __future__ import annotations

import logging
import math
import threading
import time
from datetime import date, timedelta
from typing import Callable, Optional

import pandas as pd
import yfinance as yf

log = logging.getLogger("uvicorn.error")

# UI period -> (yfinance period to fetch, trailing daily bars in the window).
# bars=None means the whole fetched range is the window. The window's first
# bar is the "ago" reference; for bar-sliced periods the high/low is computed
# over the bars after it (e.g. 1D high/low = today's session only).
PERIODS: dict[str, tuple[str, Optional[int]]] = {
    "1D": ("1mo", 2),
    "5D": ("1mo", 6),
    "1M": ("1mo", None),
    "3M": ("3mo", None),
    "YTD": ("ytd", None),
    "1Y": ("1y", None),
}

# Rough calendar length of each whole-range window, used to flag tickers whose
# history starts well inside the window (recent IPOs).
_EXPECTED_DAYS = {"1M": 31, "3M": 92, "1Y": 366}

DAILY_TTL = 600
INTRADAY_TTL = 300
INFO_TTL = 3600
NEWS_TTL = 900

# _state_lock guards the two dicts below and is only ever held for dict
# access — never across a network fetch. Each slot then gets its own lock so a
# slow fetch in one slot can't stall cache reads (or fetches) in another.
_state_lock = threading.Lock()
# slot -> (tickers_key, fetched_at_epoch, {ticker: DataFrame})
_cache: dict[str, tuple[tuple, float, dict]] = {}
_slot_locks: dict[str, threading.Lock] = {}


def _slot_lock(slot: str) -> threading.Lock:
    with _state_lock:
        lock = _slot_locks.get(slot)
        if lock is None:
            lock = _slot_locks[slot] = threading.Lock()
        return lock


def _read_cache(slot: str, tickers_key: tuple, ttl: int
                ) -> tuple[Optional[tuple], bool]:
    """(entry, is_fresh) for the slot — entry is None when nothing usable is
    cached for this ticker set."""
    with _state_lock:
        hit = _cache.get(slot)
    if hit is None or hit[0] != tickers_key:
        return None, False
    return hit, (time.time() - hit[1]) < ttl


def _split(df: Optional[pd.DataFrame], tickers: tuple) -> dict[str, pd.DataFrame]:
    """Normalize a yf.download result into {ticker: OHLC frame}, dropping
    tickers that returned no usable rows (delisted, bad symbol, etc.)."""
    out: dict[str, pd.DataFrame] = {}
    if df is None or df.empty:
        return out
    for t in tickers:
        if isinstance(df.columns, pd.MultiIndex):
            if t not in df.columns.get_level_values(0):
                continue
            sub = df[t]
        else:
            sub = df  # single ticker, flat columns
        if "Close" not in sub.columns:
            continue
        sub = sub.dropna(subset=["Close"])
        if not sub.empty:
            out[t] = sub
    return out


def _cached_fetch(slot: str, tickers_key: tuple, ttl: int, force: bool,
                  fetch: Callable[[], dict]) -> tuple[dict, float, bool]:
    """Return (frames, fetched_at, stale). Serves the previous result if the
    fetch fails; raises only when there is nothing cached to fall back on.
    The per-slot lock collapses concurrent requests for the SAME slot into one
    Yahoo fetch, while leaving other slots free to read their caches."""
    hit, fresh = _read_cache(slot, tickers_key, ttl)
    if fresh and not force:
        return hit[2], hit[1], False
    seen_at = hit[1] if hit else None

    with _slot_lock(slot):
        # Re-check: another thread may have refreshed this slot while we waited.
        # A forced refresh accepts that thread's result (so concurrent refreshes
        # collapse into one fetch) but never accepts the entry it already saw —
        # otherwise force would be a no-op.
        hit, fresh = _read_cache(slot, tickers_key, ttl)
        if fresh and (not force or hit[1] != seen_at):
            return hit[2], hit[1], False
        try:
            frames = fetch()
        except Exception:
            # Never cache a failure: leaving the old entry in place means the
            # next call retries instead of serving an empty result for the TTL.
            log.exception("yfinance fetch failed (%s)", slot)
            if hit is not None:
                return hit[2], hit[1], True
            raise
        entry = (tickers_key, time.time(), frames)
        with _state_lock:
            _cache[slot] = entry
        return frames, entry[1], False


def get_daily(tickers: list[str], ui_period: str,
              force: bool = False) -> tuple[dict[str, pd.DataFrame], float, bool, Optional[str]]:
    """Daily OHLC frames for all tickers covering the UI period.
    Returns (frames, fetched_at, stale, fetch_error)."""
    fetch_period, _ = PERIODS[ui_period]
    key = tuple(sorted(set(tickers)))

    def fetch() -> dict:
        df = yf.download(list(key), period=fetch_period, interval="1d",
                         group_by="ticker", auto_adjust=False, threads=True,
                         progress=False)
        return _split(df, key)

    try:
        frames, fetched_at, stale = _cached_fetch(
            f"daily:{fetch_period}", key, DAILY_TTL, force, fetch)
        return frames, fetched_at, stale, None
    except Exception as exc:
        return {}, time.time(), True, f"Price fetch failed ({type(exc).__name__})"


def get_intraday(tickers: list[str], force: bool = False) -> dict[str, pd.DataFrame]:
    """1-minute bars for the latest session (falls back to 5m), used by the
    time-of-day metrics. Failures degrade to an empty dict — never fatal."""
    key = tuple(sorted(set(tickers)))

    def fetch() -> dict:
        for interval in ("1m", "5m"):
            df = yf.download(list(key), period="1d", interval=interval,
                             group_by="ticker", auto_adjust=False, prepost=False,
                             threads=True, progress=False)
            frames = _split(df, key)
            if frames:
                return frames
        return {}

    try:
        frames, _, _ = _cached_fetch("intraday", key, INTRADAY_TTL, force, fetch)
        return frames
    except Exception:
        return {}


def _with_live_bar(df: pd.DataFrame, intraday: Optional[pd.DataFrame]) -> pd.DataFrame:
    """Append a synthetic daily bar built from the latest intraday session when
    the daily feed doesn't have a completed bar for it yet (market currently
    open, or Yahoo's occasional NaN close on the newest daily row). Keeps
    Current Price live and lets every window metric include today."""
    if intraday is None or intraday.empty or df.empty:
        return df
    bars = intraday.dropna(subset=["Close"])
    if bars.empty:
        return df
    live_date = bars.index[-1].date()
    if live_date <= df.index[-1].date():
        return df
    synthetic = pd.DataFrame(
        {
            "Open": [float(bars["Open"].dropna().iloc[0])],
            "High": [float(bars["High"].max())],
            "Low": [float(bars["Low"].min())],
            "Close": [float(bars["Close"].iloc[-1])],
            "Adj Close": [float(bars["Close"].iloc[-1])],
            "Volume": [float(bars["Volume"].sum()) if "Volume" in bars else 0.0],
        },
        index=[pd.Timestamp(live_date, tz=df.index.tz)],
    )
    return pd.concat([df, synthetic])


def _round_price(value: float) -> float:
    # Sub-dollar tickers (e.g. penny EV stocks) need more precision than cents.
    return round(value, 4 if abs(value) < 1 else 2)


def _pct(current: float, reference: float) -> Optional[float]:
    if not (math.isfinite(current) and math.isfinite(reference)) or reference == 0:
        return None
    return round((current - reference) / reference * 100, 2)


def _expected_window_days(ui_period: str) -> Optional[int]:
    if ui_period == "YTD":
        return (date.today() - date(date.today().year, 1, 1)).days + 1
    return _EXPECTED_DAYS.get(ui_period)


def period_window(df: pd.DataFrame, ui_period: str
                  ) -> tuple[pd.DataFrame, pd.DataFrame, Optional[str]]:
    """Slice a daily frame to the UI period. Returns (window, perf,
    partial_since): window includes the "ago" reference bar; perf is the
    slice that high/low stats run over."""
    _, bars = PERIODS[ui_period]
    partial_since: Optional[str] = None

    if bars is not None:
        window = df.tail(bars)
        if len(window) < bars:
            partial_since = str(window.index[0].date())
        # High/low over the period itself, excluding the "ago" reference bar.
        perf = window.iloc[1:] if len(window) > 1 else window
    else:
        window = df
        perf = window
        expected = _expected_window_days(ui_period)
        first_day = window.index[0].date()
        if expected and first_day > date.today() - timedelta(days=expected) + timedelta(days=7):
            partial_since = str(first_day)
    return window, perf, partial_since


def compute_core(df: pd.DataFrame, ui_period: str,
                 intraday: Optional[pd.DataFrame] = None
                 ) -> tuple[dict, pd.DataFrame, pd.DataFrame]:
    """Core table columns for one ticker. Returns (row, window, perf): window
    is the daily slice handed to the Things-to-Track metrics; perf is the
    period's actual days (window minus the "ago" reference bar)."""
    df = _with_live_bar(df, intraday)
    window, perf, partial_since = period_window(df, ui_period)

    current = float(window["Close"].iloc[-1])
    ago = float(window["Close"].iloc[0])
    high = float(perf["High"].max())
    low = float(perf["Low"].min())

    row = {
        "current": _round_price(current),
        "ago": _round_price(ago) if math.isfinite(ago) else None,
        "pct": _pct(current, ago),
        "high": _round_price(high) if math.isfinite(high) else None,
        "high_pct": _pct(current, high),
        "low": _round_price(low) if math.isfinite(low) else None,
        "low_pct": _pct(current, low),
        "partial_since": partial_since,
    }
    return row, window, perf


def cache_status() -> list[dict]:
    """Snapshot of the in-memory fetch caches, for the DB view."""
    now = time.time()
    out = []
    with _state_lock:
        for slot, (key, fetched_at, data) in list(_cache.items()):
            if slot.startswith("info:"):
                ttl = INFO_TTL
            elif slot.startswith("news:"):
                ttl = NEWS_TTL
            elif slot == "intraday":
                ttl = INTRADAY_TTL
            else:
                ttl = DAILY_TTL
            out.append({
                "slot": slot,
                "tickers": len(key),
                "items": len(data) if hasattr(data, "__len__") else 1,
                "age_seconds": round(now - fetched_at),
                "ttl_seconds": ttl,
            })
    return sorted(out, key=lambda entry: entry["slot"])


def current_and_day_pct(df: Optional[pd.DataFrame],
                        intraday: Optional[pd.DataFrame]) -> tuple[Optional[float], Optional[float]]:
    """(current price, day %) with the live intraday bar folded in, independent
    of whatever chart window the detail view is showing."""
    if df is None or df.empty:
        return None, None
    closes = _with_live_bar(df, intraday)["Close"].dropna()
    current = float(closes.iloc[-1]) if len(closes) else None
    day_pct = _pct(current, float(closes.iloc[-2])) if len(closes) >= 2 else None
    return current, day_pct


def chart_series(df: Optional[pd.DataFrame], intraday: Optional[pd.DataFrame],
                 ui_period: str) -> list:
    """Chart points for the detail view, scoped to the UI period. 1D renders
    the live session's 1-minute line (time labels); other periods slice the
    daily closes through the same window logic as the table and matrix."""
    if ui_period == "1D" and intraday is not None and not intraday.empty:
        closes = intraday["Close"].dropna()
        if len(closes) >= 2:
            return [[f"{idx:%H:%M}", round(float(v), 4)] for idx, v in closes.items()]
    if df is None or df.empty:
        return []
    merged = _with_live_bar(df, intraday)
    if ui_period == "1D":
        window = merged.tail(2)  # no intraday available — prev close + latest
    else:
        window, _, _ = period_window(merged, ui_period)
    closes = window["Close"].dropna()
    return [[str(idx.date()), round(float(v), 4)] for idx, v in closes.items()]


def get_stats(ticker: str, force: bool = False) -> Optional[dict]:
    """Fundamentals/analyst snapshot from Ticker.info for the detail view.
    None when Yahoo has nothing (sparse small caps) or the fetch fails.
    Note: yfinance 1.4 returns dividendYield already in percent."""
    def fetch() -> dict:
        t = yf.Ticker(ticker)
        info = t.info or {}
        next_earnings = None
        try:
            dates = (t.calendar or {}).get("Earnings Date") or []
            if dates:
                next_earnings = str(dates[0])
        except Exception:
            pass
        return {ticker: {
            "market_cap": info.get("marketCap"),
            "trailing_pe": info.get("trailingPE"),
            "forward_pe": info.get("forwardPE"),
            "eps_ttm": info.get("trailingEps"),
            "beta": info.get("beta"),
            "dividend_yield": info.get("dividendYield"),
            "profit_margin": info.get("profitMargins"),
            "revenue_growth": info.get("revenueGrowth"),
            "year_high": info.get("fiftyTwoWeekHigh"),
            "year_low": info.get("fiftyTwoWeekLow"),
            "avg_volume": info.get("averageVolume"),
            "target_mean": info.get("targetMeanPrice"),
            "recommendation": info.get("recommendationKey"),
            "analyst_count": info.get("numberOfAnalystOpinions"),
            "next_earnings": next_earnings,
            "yahoo_sector": info.get("sector"),
            "industry": info.get("industry"),
        }}

    try:
        data, _, _ = _cached_fetch(f"info:{ticker}", (ticker,), INFO_TTL, force, fetch)
        return data.get(ticker)
    except Exception:
        return None


def get_news(ticker: str, force: bool = False) -> list[dict]:
    """Latest headlines for the detail view; [] on any failure. yfinance 1.4
    nests fields under item["content"]."""
    def fetch() -> dict:
        items = []
        for raw in (yf.Ticker(ticker).news or [])[:5]:
            content = raw.get("content") or raw
            url = ((content.get("canonicalUrl") or {}).get("url")
                   or (content.get("clickThroughUrl") or {}).get("url")
                   or raw.get("link"))
            title = content.get("title")
            if not (title and url):
                continue
            items.append({
                "title": title,
                "link": url,
                "publisher": (content.get("provider") or {}).get("displayName"),
                "published": content.get("pubDate") or content.get("displayTime"),
            })
        return {ticker: items}

    try:
        data, _, _ = _cached_fetch(f"news:{ticker}", (ticker,), NEWS_TTL, force, fetch)
        return data.get(ticker, [])
    except Exception:
        return []


def validate_ticker(ticker: str) -> tuple[str, Optional[float]]:
    """Check Yahoo has price data for the ticker; return (display name,
    effective shares outstanding). Raises ValueError when the symbol has no
    data (unknown/delisted). Shares are derived from marketCap / price when
    possible so ADR ratios come out right."""
    t = yf.Ticker(ticker)
    hist = t.history(period="5d")
    if hist is None or hist.empty:
        raise ValueError(f"No price data found for '{ticker}' — check the symbol")
    name, shares = None, None
    try:
        info = t.info
        name = info.get("longName") or info.get("shortName")
        shares = effective_shares(info.get("marketCap"),
                                  info.get("regularMarketPrice"),
                                  info.get("sharesOutstanding"))
    except Exception:
        pass
    return name or ticker, shares


def effective_shares(market_cap: Optional[float], price: Optional[float],
                     shares_outstanding: Optional[float]) -> Optional[float]:
    if market_cap and price:
        return market_cap / price
    return shares_outstanding or None
