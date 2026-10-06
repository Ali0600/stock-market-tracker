"""Stock Tracker — local market dashboard over yfinance."""
from __future__ import annotations

import hashlib
import math
import re
import time
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
from statistics import median
from typing import Literal, Optional, Union

from fastapi import FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles
from pandas import notna as pd_notna
from pydantic import BaseModel, Field

from . import alerts, backtest, db, metrics, patterns, prices, vault

CODE_DIR = Path(__file__).resolve().parent
STATIC_DIR = CODE_DIR.parent / "static"
TICKER_RE = re.compile(r"^[A-Z0-9.\-]{1,12}$")


def _code_fingerprint() -> Optional[str]:
    """Hash of the app's Python source as it is on disk right now.

    The server runs without auto-reload while static/ is served live, so after
    a pull the page can be newer than the routes answering it (a new page's
    endpoint then 404s). Comparing this with the startup value lets the page
    say "restart the server" instead of failing quietly.
    """
    digest = hashlib.sha256()
    try:
        for path in sorted(CODE_DIR.glob("*.py")):
            digest.update(path.name.encode())
            digest.update(path.read_bytes())
    except OSError:
        return None     # a file vanished mid-read: the code is changing
    return digest.hexdigest()


STARTUP_FINGERPRINT = _code_fingerprint()


class RevalidatingStaticFiles(StaticFiles):
    """Static files the browser must revalidate before each use.

    Without a Cache-Control header browsers cache heuristically, and a normal
    reload refetches only the HTML — so a new index.html could run against an
    old app.js. `no-cache` keeps the ETag round-trip (a cheap 304) but never
    reuses a copy unchecked.
    """

    def file_response(self, *args, **kwargs):
        response = super().file_response(*args, **kwargs)
        response.headers["Cache-Control"] = "no-cache"
        return response


@asynccontextmanager
async def lifespan(app: FastAPI):
    db.init()
    yield


app = FastAPI(title="Stock Tracker", lifespan=lifespan)


class StockCreate(BaseModel):
    ticker: str = Field(min_length=1, max_length=12)
    sector_id: Optional[int] = None
    new_sector_name: Optional[str] = Field(default=None, max_length=40)


class StockMove(BaseModel):
    sector_id: int


class BacktestRule(BaseModel):
    entry_mode: Literal["pct", "sigma"] = "pct"
    entry_value: float = Field(2.0, gt=0, le=50)
    avg_in_enabled: bool = False
    avg_in_level: float = Field(4.0, gt=0, le=80)
    tp_mode: Literal["pct", "avg_range"] = "pct"
    tp_value: float = Field(3.0, gt=0, le=100)
    stop_pct: float = Field(5.0, gt=0, le=90)
    max_hold_days: int = Field(10, ge=1, le=120)
    cost_bps: float = Field(5.0, ge=0, le=100)
    lookback: int = Field(63, ge=20, le=200)


class BacktestRequest(BaseModel):
    tickers: Union[Literal["all"], list[str]] = "all"
    rule: BacktestRule = BacktestRule()


class AlertCreate(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    ticker: Optional[str] = Field(default=None, max_length=12)
    period: str = "3M"
    metric_key: str = Field(min_length=1, max_length=60)
    op: Literal["gte", "lte"] = "gte"
    value: float


class AlertToggle(BaseModel):
    enabled: bool


@app.get("/api/overview")
def overview(period: str = "3M", refresh: bool = False):
    ui_period = period.upper()
    if ui_period not in prices.PERIODS:
        raise HTTPException(422, f"period must be one of {', '.join(prices.PERIODS)}")

    sectors = db.sectors_with_stocks()
    tickers = [s["ticker"] for sec in sectors for s in sec["stocks"]]

    frames: dict = {}
    intraday: dict = {}
    fetched_at, stale, fetch_error = time.time(), False, None
    if tickers:
        frames, fetched_at, stale, fetch_error = prices.get_daily(
            tickers, ui_period, force=refresh)
        intraday = prices.get_intraday(tickers, force=refresh)

    out_sectors = []
    for sec in sectors:
        rows, pcts = [], []
        for stock in sec["stocks"]:
            base = {"id": stock["id"], "ticker": stock["ticker"], "name": stock["name"]}
            frame = frames.get(stock["ticker"])
            if frame is None or frame.empty:
                rows.append({**base, "error": fetch_error or "No price data (delisted?)",
                             "metrics": {}})
                continue
            core, window, perf = prices.compute_core(frame, ui_period,
                                                     intraday.get(stock["ticker"]))
            extras = metrics.compute_all(window, intraday.get(stock["ticker"]), perf)
            shares = stock.get("shares")
            market_cap = (round(shares * core["current"])
                          if shares and core["current"] else None)
            row = {**base, **core, "market_cap": market_cap,
                   "metrics": extras, "error": None}
            if row["pct"] is not None:
                pcts.append(row["pct"])
            rows.append(row)
        out_sectors.append({
            "id": sec["id"],
            "name": sec["name"],
            "avg_pct": round(sum(pcts) / len(pcts), 2) if pcts else None,
            "stocks": rows,
        })

    return {
        "period": ui_period,
        "as_of": datetime.fromtimestamp(fetched_at).isoformat(timespec="seconds"),
        "stale": stale,
        "fetch_error": fetch_error,
        "server_outdated": _code_fingerprint() != STARTUP_FINGERPRINT,
        "metric_defs": metrics.metric_defs(),
        "sectors": out_sectors,
    }


@app.get("/api/stocks/{ticker}/detail")
def stock_detail(ticker: str, period: str = "3M", refresh: bool = False):
    ui_period = period.upper()
    if ui_period not in prices.PERIODS:
        raise HTTPException(422, f"period must be one of {', '.join(prices.PERIODS)}")
    symbol = ticker.strip().upper()
    stock = db.get_stock_by_ticker(symbol)
    if not stock:
        raise HTTPException(404, f"{symbol} is not tracked")

    tickers = db.all_tickers()
    frames, _, stale, fetch_error = prices.get_daily(tickers, "1Y", force=refresh)
    intraday = prices.get_intraday(tickers, force=refresh)
    current, day_pct = prices.current_and_day_pct(
        frames.get(symbol), intraday.get(symbol))

    # Chart and range statistics both follow the requested period window.
    range_block = None
    period_frames, _, _, _ = prices.get_daily(tickers, ui_period, force=refresh)
    period_frame = period_frames.get(symbol)
    chart = prices.chart_series(period_frame, intraday.get(symbol), ui_period)
    if period_frame is not None and not period_frame.empty:
        merged = prices._with_live_bar(period_frame, intraday.get(symbol))
        window, perf, _ = prices.period_window(merged, ui_period)
        window_stats = metrics.range_stats(window, perf)
        if window_stats:
            range_block = {
                "period": ui_period,
                "metrics": [{"key": key, "label": label, **window_stats[key]}
                            for key, label, _, _ in metrics.DAILY_RANGE_METRICS
                            if window_stats.get(key)],
            }

    stats = prices.get_stats(symbol, force=refresh)
    if stats and current:
        # Opportunistically re-sync cached shares so the overview's market cap
        # tracks splits/dilution for stocks the user actually looks at.
        db.set_shares(symbol, prices.effective_shares(
            stats.get("market_cap"), current, None))

    note = vault.stock_note(symbol)
    vault_articles = [
        {**article,
         "change_pct": (prices._pct(current, article["price_at"])
                        if current and article["price_at"] else None)}
        for article in vault.articles_for(symbol)
    ]
    vault_block = (
        {"note_md": note["note_md"] if note else None,
         "updated": note["updated"] if note else None,
         "articles": vault_articles}
        if note or vault_articles else None
    )

    return {
        "ticker": symbol,
        "name": stock["name"],
        "sector": stock["sector"],
        "current": prices._round_price(current) if current is not None else None,
        "day_pct": day_pct,
        "stale": stale,
        "fetch_error": fetch_error,
        "chart": chart,
        "stats": stats,
        "range": range_block,
        "news": prices.get_news(symbol, force=refresh),
        "vault": vault_block,
        "analysis_md": stock["analysis"],
        "analysis_at": stock["analysis_at"],
    }


@app.post("/api/stocks", status_code=201)
def add_stock(body: StockCreate):
    ticker = body.ticker.strip().upper()
    if not TICKER_RE.match(ticker):
        raise HTTPException(422, "Ticker may only contain letters, digits, '.' and '-'")
    if db.ticker_exists(ticker):
        raise HTTPException(409, f"{ticker} is already tracked")

    sector_name = (body.new_sector_name or "").strip()
    if not sector_name and (body.sector_id is None or not db.sector_exists(body.sector_id)):
        raise HTTPException(422, "Pick an existing sector or name a new one")

    # Validate the ticker before touching the database so a failed add
    # doesn't leave an orphan empty sector behind.
    try:
        name, shares = prices.validate_ticker(ticker)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc

    sector_id = db.get_or_create_sector(sector_name) if sector_name else body.sector_id
    stock_id = db.add_stock(ticker, name, sector_id)
    db.set_shares(ticker, shares)
    return {"id": stock_id, "ticker": ticker, "name": name, "sector_id": sector_id}


@app.patch("/api/stocks/{stock_id}")
def move_stock(stock_id: int, body: StockMove):
    if not db.sector_exists(body.sector_id):
        raise HTTPException(404, "Sector not found")
    if not db.move_stock(stock_id, body.sector_id):
        raise HTTPException(404, "Stock not found")
    return {"ok": True}


@app.delete("/api/stocks/{stock_id}", status_code=204)
def remove_stock(stock_id: int):
    if not db.delete_stock(stock_id):
        raise HTTPException(404, "Stock not found")


@app.get("/api/db")
def db_view():
    tables = db.dump_tables()
    # Full analyses are heavy and already visible in the detail view — the DB
    # view gets a preview plus the length.
    for row in tables["stocks"]["rows"]:
        analysis = row.pop("analysis", None)
        row["analysis_chars"] = len(analysis) if analysis else 0
        row["analysis_preview"] = ((analysis[:80] + "…")
                                   if analysis and len(analysis) > 80 else analysis)
    cols = [c for c in tables["stocks"]["columns"] if c != "analysis"]
    tables["stocks"]["columns"] = cols + ["analysis_chars", "analysis_preview"]

    try:
        size_bytes = db.DB_PATH.stat().st_size
    except OSError:
        size_bytes = None
    return {
        "db": {"path": str(db.DB_PATH), "size_bytes": size_bytes},
        "tables": tables,
        "caches": prices.cache_status(),
        "vault": vault.status(),
    }


@app.get("/api/db/prices/{ticker}")
def db_prices(ticker: str, period: str = "3M"):
    ui_period = period.upper()
    if ui_period not in prices.PERIODS:
        raise HTTPException(422, f"period must be one of {', '.join(prices.PERIODS)}")
    symbol = ticker.strip().upper()
    if not db.ticker_exists(symbol):
        raise HTTPException(404, f"{symbol} is not tracked")

    frames, fetched_at, stale, fetch_error = prices.get_daily(db.all_tickers(), ui_period)
    frame = frames.get(symbol)
    rows = []
    if frame is not None and not frame.empty:
        for idx, bar in frame.iterrows():
            rows.append({
                "date": str(idx.date()),
                "open": round(float(bar["Open"]), 4) if pd_notna(bar["Open"]) else None,
                "high": round(float(bar["High"]), 4) if pd_notna(bar["High"]) else None,
                "low": round(float(bar["Low"]), 4) if pd_notna(bar["Low"]) else None,
                "close": round(float(bar["Close"]), 4) if pd_notna(bar["Close"]) else None,
                "volume": int(bar["Volume"]) if pd_notna(bar.get("Volume")) else None,
            })
    return {"ticker": symbol, "period": ui_period, "stale": stale,
            "fetch_error": fetch_error, "rows": rows}


@app.post("/api/backtest")
def run_backtest_api(body: BacktestRequest):
    rule = body.rule
    if (rule.avg_in_enabled and rule.entry_mode == "pct"
            and rule.avg_in_level <= rule.entry_value):
        raise HTTPException(422, "avg_in_level must be deeper than entry_value")

    tracked = db.all_tickers()
    if body.tickers == "all":
        targets = tracked
    else:
        targets = [t.strip().upper() for t in body.tickers]
        unknown = [t for t in targets if t not in tracked]
        if unknown:
            raise HTTPException(422, f"Not tracked: {', '.join(unknown)}")
    if not targets:
        raise HTTPException(422, "No tickers to test")

    frames, _, stale, fetch_error = prices.get_daily(tracked, "1Y")
    rule_dict = rule.model_dump()
    results = {t: backtest.run_backtest(frames.get(t), rule_dict) for t in targets}

    tested = {t: r for t, r in results.items() if "summary" in r}
    expectancies = [r["summary"]["expectancy_pct"] for r in tested.values()
                    if r["summary"]["expectancy_pct"] is not None]
    cumulatives = [(t, r["summary"]["cumulative_pct"]) for t, r in tested.items()
                   if r["summary"]["cumulative_pct"] is not None]
    rollup = {
        "tickers_tested": len(tested),
        "tickers_skipped": len(results) - len(tested),
        "total_trades": sum(r["summary"]["trades"] for r in tested.values()),
        "median_expectancy_pct": round(median(expectancies), 2) if expectancies else None,
        "mean_expectancy_pct": (round(sum(expectancies) / len(expectancies), 2)
                                if expectancies else None),
        "net_negative_tickers": sum(1 for _, c in cumulatives if c < 0),
        "beat_buy_hold": sum(1 for t, c in cumulatives
                             if c > (tested[t]["summary"]["buy_hold_pct"] or 0)),
        "best": max(cumulatives, key=lambda x: x[1]) if cumulatives else None,
        "worst": min(cumulatives, key=lambda x: x[1]) if cumulatives else None,
    }
    return {"rule": rule_dict, "stale": stale, "fetch_error": fetch_error,
            "results": results, "rollup": rollup,
            "assumptions": backtest.ASSUMPTIONS}


@app.get("/api/deviations")
def deviations():
    tickers = db.all_tickers()
    frames, fetched_at, stale, fetch_error = prices.get_daily(tickers, "1Y")
    intraday = prices.get_intraday(tickers)
    rows = []
    for sec in db.sectors_with_stocks():
        for stock in sec["stocks"]:
            frame = frames.get(stock["ticker"])
            if frame is None or frame.empty:
                continue
            merged = prices._with_live_bar(frame, intraday.get(stock["ticker"]))
            dev = metrics.today_vs_typical(merged)
            if dev:
                rows.append({"ticker": stock["ticker"], "name": stock["name"],
                             "sector": sec["name"], **dev})
    return {"as_of": datetime.fromtimestamp(fetched_at).isoformat(timespec="seconds"),
            "stale": stale, "fetch_error": fetch_error, "stocks": rows}


def _pattern_inputs(force: bool) -> tuple[dict, dict, float, bool, Optional[str]]:
    """Two years of daily bars plus 60 sessions of five-minute bars for every
    tracked stock — one batched, cached fetch each, shared by both endpoints."""
    tickers = db.all_tickers()
    daily, fetched_at, stale, error = prices.get_history(tickers, force=force)
    bars, _, bars_stale, bars_error = prices.get_intraday_history(tickers, force=force)
    return daily, bars, fetched_at, stale or bars_stale, error or bars_error


@app.get("/api/stocks/{ticker}/patterns")
def stock_patterns(ticker: str, refresh: bool = False):
    """Weekday, follow-through, gap, turn-of-month and intraday-timing
    statistics over completed sessions. Historical frequencies only."""
    symbol = ticker.strip().upper()
    if not db.ticker_exists(symbol):
        raise HTTPException(404, f"{symbol} is not tracked")
    daily, bars, fetched_at, stale, fetch_error = _pattern_inputs(refresh)
    at = patterns.now()
    prof = patterns.profile(daily.get(symbol), bars.get(symbol), at)
    return {
        "ticker": symbol,
        "lookback": patterns.LOOKBACK,
        "as_of": datetime.fromtimestamp(fetched_at).isoformat(timespec="seconds"),
        "stale": stale,
        "fetch_error": fetch_error,
        "target_session": str(patterns.target_session(at)),
        "profile": prof,
        "error": None if prof else "Not enough price history",
    }


@app.get("/api/patterns")
def portfolio_patterns(refresh: bool = False):
    """The same statistics for every tracked stock, for the Patterns page.
    A stock without enough history is listed with an error, never dropped."""
    daily, bars, fetched_at, stale, fetch_error = _pattern_inputs(refresh)
    at = patterns.now()
    stocks = []
    for sec in db.sectors_with_stocks():
        for stock in sec["stocks"]:
            prof = patterns.profile(daily.get(stock["ticker"]), bars.get(stock["ticker"]), at)
            stocks.append({
                "ticker": stock["ticker"], "name": stock["name"], "sector": sec["name"],
                "profile": prof,
                "error": None if prof else "Not enough price history",
            })
    return {
        "lookback": patterns.LOOKBACK,
        "as_of": datetime.fromtimestamp(fetched_at).isoformat(timespec="seconds"),
        "stale": stale,
        "fetch_error": fetch_error,
        "target_session": str(patterns.target_session(at)),
        "min_n": patterns.MIN_N,
        "stands_out_z": patterns.STANDS_OUT_Z,
        "stocks": stocks,
    }


@app.get("/api/alerts")
def list_alerts(refresh: bool = False):
    """Owner-authored watch conditions and whether each currently holds.
    Descriptive only: the app reports the observed statistic, never what to
    do about it."""
    rules = db.list_alerts()
    enabled = [r for r in rules if r["enabled"]]
    evaluated, stale, fetch_error = alerts.evaluate(enabled, force=refresh)
    by_id = {r["id"]: r for r in evaluated}
    out = [by_id.get(r["id"], {**r, "matches": [], "unavailable": []}) for r in rules]
    return {
        "as_of": datetime.now().isoformat(timespec="seconds"),
        "stale": stale,
        "fetch_error": fetch_error,
        "stat_defs": alerts.stat_defs(),
        "triggered_count": sum(1 for r in out if r["enabled"] and r["matches"]),
        "rules": out,
    }


@app.post("/api/alerts", status_code=201)
def add_alert(body: AlertCreate):
    period = body.period.upper()
    if period not in prices.PERIODS:
        raise HTTPException(422, f"period must be one of {', '.join(prices.PERIODS)}")
    if body.metric_key not in alerts.allowed_keys():
        raise HTTPException(422, "Unknown statistic — pick one from the list")
    # Checked here rather than on the model so the rejection message never
    # echoes a value the JSON encoder can't serialize.
    if not math.isfinite(body.value):
        raise HTTPException(422, "Threshold must be a finite number")
    ticker = body.ticker.strip().upper() if body.ticker and body.ticker.strip() else None
    if ticker and not db.ticker_exists(ticker):
        raise HTTPException(422, f"{ticker} is not tracked")
    alert_id = db.add_alert(body.name.strip(), ticker, period,
                            body.metric_key, body.op, body.value)
    return {"id": alert_id}


@app.patch("/api/alerts/{alert_id}")
def toggle_alert(alert_id: int, body: AlertToggle):
    if not db.set_alert_enabled(alert_id, body.enabled):
        raise HTTPException(404, "Alert not found")
    return {"ok": True}


@app.delete("/api/alerts/{alert_id}", status_code=204)
def remove_alert(alert_id: int):
    if not db.delete_alert(alert_id):
        raise HTTPException(404, "Alert not found")


@app.get("/api/sectors")
def list_sectors():
    return db.list_sectors()


@app.get("/api/sectors/{sector_id}/note")
def get_sector_note(sector_id: int):
    sector = next((s for s in db.list_sectors() if s["id"] == sector_id), None)
    if not sector:
        raise HTTPException(404, "Sector not found")
    note = vault.sector_note(sector["name"])
    if not note:
        raise HTTPException(404, f"No vault note for {sector['name']}")
    return {"id": sector_id, "name": sector["name"],
            "stock_count": sector["stock_count"], **note}


@app.delete("/api/sectors/{sector_id}", status_code=204)
def remove_sector(sector_id: int):
    result = db.delete_sector(sector_id)
    if result == "not_found":
        raise HTTPException(404, "Sector not found")
    if result == "not_empty":
        raise HTTPException(409, "Sector still has stocks — remove them first")


# Mounted last so /api/* routes take precedence; html=True serves index.html at /.
app.mount("/", RevalidatingStaticFiles(directory=STATIC_DIR, html=True), name="static")
