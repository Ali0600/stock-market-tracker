"""Contract tests for the HTTP API.

Every test runs against a throwaway SQLite file and canned price data — no
network, no touching the real portfolio.
"""
from __future__ import annotations

from datetime import datetime

import pytest
from fastapi.testclient import TestClient

from app import db, main, patterns, prices, vault
from tests.conftest import make_daily, weekly_pattern

PRICED = make_daily([
    (100.0, 100.0, 100.0, 100.0),
    (100.0, 110.0, 95.0, 105.0),
    (105.0, 112.0, 99.0, 110.0),
])

HISTORY = weekly_pattern(16, True)
TUESDAY_NOON = datetime(2026, 5, 5, 12, 0, tzinfo=patterns.MARKET_TZ)


@pytest.fixture
def client(tmp_path, monkeypatch):
    """A fresh app over an empty DB with all outbound calls stubbed."""
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "test.db")
    monkeypatch.setattr(db, "SEED_PORTFOLIO", {
        "Semis": [("NVDA", "NVIDIA Corporation"), ("INTC", "Intel Corporation")],
        "Cloud": [("MSFT", "Microsoft Corporation")],
    })
    monkeypatch.setattr(prices, "get_daily",
                        lambda tickers, period, force=False: (
                            {t: PRICED for t in tickers}, 1_700_000_000.0, False, None))
    monkeypatch.setattr(prices, "get_intraday", lambda tickers, force=False: {})
    monkeypatch.setattr(prices, "get_history",
                        lambda tickers, force=False: (
                            {t: HISTORY for t in tickers}, 1_700_000_000.0, False, None))
    monkeypatch.setattr(prices, "get_intraday_history",
                        lambda tickers, force=False: ({}, 1_700_000_000.0, False, None))
    monkeypatch.setattr(patterns, "now", lambda: TUESDAY_NOON)
    monkeypatch.setattr(prices, "get_stats", lambda t, force=False: None)
    monkeypatch.setattr(prices, "get_news", lambda t, force=False: [])
    monkeypatch.setattr(vault, "stock_note", lambda t: None)
    monkeypatch.setattr(vault, "sector_note", lambda n: None)
    monkeypatch.setattr(vault, "articles_for", lambda t: [])
    monkeypatch.setattr(vault, "status", lambda: {
        "path": "/nope", "available": False, "stocks": 0, "sectors": 0, "articles": 0})
    with TestClient(main.app) as c:      # context manager runs lifespan → db.init()
        yield c


# --------------------------------------------------------------------------
# /api/overview
# --------------------------------------------------------------------------

def test_overview_returns_sectors_with_computed_rows(client):
    body = client.get("/api/overview?period=3M").json()
    assert body["period"] == "3M"
    assert {s["name"] for s in body["sectors"]} == {"Semis", "Cloud"}
    row = next(r for s in body["sectors"] for r in s["stocks"] if r["ticker"] == "NVDA")
    assert row["current"] == 110.0
    assert row["error"] is None
    assert "green_days" in row["metrics"]


def test_overview_averages_only_the_stocks_that_priced(client):
    body = client.get("/api/overview?period=3M").json()
    semis = next(s for s in body["sectors"] if s["name"] == "Semis")
    # Both tickers get the same frame, so the average equals either one's pct.
    assert semis["avg_pct"] == semis["stocks"][0]["pct"]


def test_overview_market_cap_is_shares_times_current_price(client):
    db.set_shares("NVDA", 1_000_000.0)
    body = client.get("/api/overview?period=3M").json()
    row = next(r for s in body["sectors"] for r in s["stocks"] if r["ticker"] == "NVDA")
    assert row["market_cap"] == 110_000_000, "1M shares x the 110.0 live price"


def test_overview_isolates_a_ticker_with_no_data(client, monkeypatch):
    monkeypatch.setattr(prices, "get_daily",
                        lambda tickers, period, force=False: (
                            {"NVDA": PRICED}, 1_700_000_000.0, False, None))
    body = client.get("/api/overview?period=3M").json()
    rows = {r["ticker"]: r for s in body["sectors"] for r in s["stocks"]}
    assert rows["NVDA"]["error"] is None
    assert rows["INTC"]["error"], "a ticker missing from the fetch renders as an error row"
    semis = next(s for s in body["sectors"] if s["name"] == "Semis")
    assert semis["avg_pct"] == rows["NVDA"]["pct"], "error rows stay out of the average"


def test_overview_surfaces_a_fetch_failure(client, monkeypatch):
    monkeypatch.setattr(prices, "get_daily",
                        lambda tickers, period, force=False: (
                            {}, 1_700_000_000.0, True, "Price fetch failed (RuntimeError)"))
    body = client.get("/api/overview?period=3M").json()
    assert body["stale"] is True
    assert "RuntimeError" in body["fetch_error"]


def test_overview_rejects_an_unknown_period(client):
    res = client.get("/api/overview?period=7Y")
    assert res.status_code == 422
    assert "period must be one of" in res.json()["detail"]


def test_overview_reports_when_the_code_on_disk_changed_since_startup(
        client, tmp_path, monkeypatch):
    def outdated():
        return client.get("/api/overview").json()["server_outdated"]

    assert outdated() is False                 # the real app/, unchanged since import

    code = tmp_path / "code"
    code.mkdir()
    (code / "a.py").write_text("x = 1\n")
    monkeypatch.setattr(main, "CODE_DIR", code)
    monkeypatch.setattr(main, "STARTUP_FINGERPRINT", main._code_fingerprint())
    assert outdated() is False

    (code / "a.py").write_text("x = 2\n")      # a pulled change the server never loaded
    assert outdated() is True
    (code / "a.py").write_text("x = 1\n")      # same bytes again: content, not mtime
    assert outdated() is False
    (code / "b.py").write_text("")             # a new module counts too
    assert outdated() is True


def test_static_assets_must_be_revalidated_before_reuse(client):
    # A heuristically cached app.js would run against a newer index.html.
    for path in ("/", "/app.js", "/lib.js", "/styles.css"):
        res = client.get(path)
        assert res.status_code == 200
        assert res.headers["cache-control"] == "no-cache"
    etag = client.get("/app.js").headers["etag"]
    res = client.get("/app.js", headers={"If-None-Match": etag})
    assert res.status_code == 304              # revalidating stays a cheap round-trip
    assert res.headers["cache-control"] == "no-cache"


@pytest.mark.parametrize("period", ["1D", "5D", "1M", "3M", "YTD", "1Y"])
def test_every_supported_period_renders(client, period):
    assert client.get(f"/api/overview?period={period}").status_code == 200


def test_period_is_case_insensitive(client):
    assert client.get("/api/overview?period=ytd").json()["period"] == "YTD"


# --------------------------------------------------------------------------
# POST /api/stocks
# --------------------------------------------------------------------------

def test_add_stock_creates_the_row(client, monkeypatch):
    monkeypatch.setattr(prices, "validate_ticker", lambda t: ("Advanced Micro", 1234.0))
    sector_id = next(s["id"] for s in client.get("/api/sectors").json() if s["name"] == "Semis")
    res = client.post("/api/stocks", json={"ticker": "amd", "sector_id": sector_id})
    assert res.status_code == 201
    assert res.json()["ticker"] == "AMD", "tickers are upper-cased"
    assert db.get_stock_by_ticker("AMD")["name"] == "Advanced Micro"


def test_add_stock_can_create_a_new_sector(client, monkeypatch):
    monkeypatch.setattr(prices, "validate_ticker", lambda t: ("Rocket Lab", 1.0))
    res = client.post("/api/stocks", json={"ticker": "RKLB", "new_sector_name": "Space"})
    assert res.status_code == 201
    assert "Space" in {s["name"] for s in client.get("/api/sectors").json()}


def test_add_stock_rejects_a_malformed_ticker(client):
    res = client.post("/api/stocks", json={"ticker": "BAD TICKER!", "sector_id": 1})
    assert res.status_code == 422


def test_add_stock_rejects_a_duplicate(client):
    sector_id = next(s["id"] for s in client.get("/api/sectors").json() if s["name"] == "Semis")
    res = client.post("/api/stocks", json={"ticker": "NVDA", "sector_id": sector_id})
    assert res.status_code == 409


def test_add_stock_requires_a_real_sector(client):
    res = client.post("/api/stocks", json={"ticker": "AMD", "sector_id": 9999})
    assert res.status_code == 422


def test_a_rejected_ticker_leaves_no_orphan_sector(client, monkeypatch):
    """Validation must happen before any write, or a failed add would strand an
    empty sector the user then has to clean up."""
    def unknown(ticker):
        raise ValueError(f"No price data found for '{ticker}'")

    monkeypatch.setattr(prices, "validate_ticker", unknown)
    before = {s["name"] for s in client.get("/api/sectors").json()}
    res = client.post("/api/stocks", json={"ticker": "ZZZZ", "new_sector_name": "Ghosts"})
    assert res.status_code == 422
    after = {s["name"] for s in client.get("/api/sectors").json()}
    assert after == before, "the new sector must not have been created"


# --------------------------------------------------------------------------
# PATCH / DELETE
# --------------------------------------------------------------------------

def test_move_stock_between_sectors(client):
    stock = db.get_stock_by_ticker("NVDA")
    cloud = next(s["id"] for s in client.get("/api/sectors").json() if s["name"] == "Cloud")
    assert client.patch(f"/api/stocks/{stock['id']}", json={"sector_id": cloud}).status_code == 200
    assert db.get_stock_by_ticker("NVDA")["sector"] == "Cloud"


def test_move_reports_missing_stock_and_sector(client):
    stock = db.get_stock_by_ticker("NVDA")
    assert client.patch(f"/api/stocks/{stock['id']}", json={"sector_id": 9999}).status_code == 404
    assert client.patch("/api/stocks/9999", json={"sector_id": 1}).status_code == 404


def test_delete_stock(client):
    stock = db.get_stock_by_ticker("INTC")
    assert client.delete(f"/api/stocks/{stock['id']}").status_code == 204
    assert db.get_stock_by_ticker("INTC") is None
    assert client.delete(f"/api/stocks/{stock['id']}").status_code == 404


def test_a_sector_with_stocks_cannot_be_deleted(client):
    semis = next(s["id"] for s in client.get("/api/sectors").json() if s["name"] == "Semis")
    res = client.delete(f"/api/sectors/{semis}")
    assert res.status_code == 409, "deleting a populated sector would orphan its stocks"


def test_an_empty_sector_can_be_deleted(client):
    for ticker in ("NVDA", "INTC"):
        db.delete_stock(db.get_stock_by_ticker(ticker)["id"])
    semis = next(s["id"] for s in client.get("/api/sectors").json() if s["name"] == "Semis")
    assert client.delete(f"/api/sectors/{semis}").status_code == 204
    assert client.delete("/api/sectors/9999").status_code == 404


# --------------------------------------------------------------------------
# detail view
# --------------------------------------------------------------------------

def test_detail_returns_the_full_payload(client):
    body = client.get("/api/stocks/NVDA/detail?period=3M").json()
    assert body["ticker"] == "NVDA"
    assert body["sector"] == "Semis"
    assert body["current"] == 110.0
    assert body["chart"], "a chart series is always included"
    assert body["range"]["period"] == "3M"


def test_detail_404s_for_an_untracked_ticker(client):
    assert client.get("/api/stocks/ZZZZ/detail").status_code == 404


def test_detail_rejects_a_bad_period(client):
    assert client.get("/api/stocks/NVDA/detail?period=nope").status_code == 422


def test_detail_includes_vault_content_when_present(client, monkeypatch):
    monkeypatch.setattr(vault, "stock_note",
                        lambda t: {"note_md": "## Facts\n- one", "updated": "2026-06-10"})
    monkeypatch.setattr(vault, "articles_for",
                        lambda t: [{"title": "A", "url": "http://x", "published": "2026-06-01",
                                    "price_at": 100.0}])
    body = client.get("/api/stocks/NVDA/detail").json()
    assert "Facts" in body["vault"]["note_md"]
    article = body["vault"]["articles"][0]
    assert article["change_pct"] == 10.0, "110 now vs 100 at publication"


# --------------------------------------------------------------------------
# read-only DB view
# --------------------------------------------------------------------------

def test_db_view_lists_tables_without_leaking_full_analyses(client):
    db.set_analysis("NVDA", "x" * 500, "2026-06-10T00:00:00")
    body = client.get("/api/db").json()
    row = next(r for r in body["tables"]["stocks"]["rows"] if r["ticker"] == "NVDA")
    assert "analysis" not in row, "the full markdown must not ride along in the dump"
    assert row["analysis_chars"] == 500
    assert len(row["analysis_preview"]) <= 81
    assert "analysis" not in body["tables"]["stocks"]["columns"]


def test_db_prices_returns_bars_for_a_tracked_ticker(client):
    body = client.get("/api/db/prices/NVDA?period=3M").json()
    assert len(body["rows"]) == len(PRICED)
    assert body["rows"][0]["close"] == 100.0


def test_db_prices_404s_for_an_untracked_ticker(client):
    assert client.get("/api/db/prices/ZZZZ").status_code == 404


# --------------------------------------------------------------------------
# backtest & deviations
# --------------------------------------------------------------------------

def test_backtest_runs_over_the_portfolio(client, monkeypatch):
    # The default 63-day lookback plus MIN_TRADEABLE_DAYS needs 83+ bars.
    long_frame = make_daily([(100.0, 101.0, 99.0, 100.0)] * 120, start="2025-01-06")
    monkeypatch.setattr(prices, "get_daily",
                        lambda tickers, period, force=False: (
                            {t: long_frame for t in tickers}, 1_700_000_000.0, False, None))
    body = client.post("/api/backtest", json={"tickers": "all", "rule": {}}).json()
    assert body["rollup"]["tickers_tested"] >= 1
    assert body["assumptions"], "the honesty caveats always ship with the results"


def test_backtest_rejects_an_average_in_shallower_than_the_entry(client):
    res = client.post("/api/backtest", json={"rule": {
        "entry_mode": "pct", "entry_value": 5.0,
        "avg_in_enabled": True, "avg_in_level": 2.0}})
    assert res.status_code == 422, "the second tranche must sit deeper than the first"


def test_backtest_rejects_an_untracked_ticker(client):
    res = client.post("/api/backtest", json={"tickers": ["ZZZZ"], "rule": {}})
    assert res.status_code == 422


@pytest.mark.parametrize("bad", [
    {"entry_value": 0},        # must be > 0
    {"stop_pct": 200},         # capped at 90
    {"lookback": 5},           # floor is 20
    {"max_hold_days": 0},      # at least one day
    {"entry_mode": "vibes"},   # not a supported mode
])
def test_backtest_validates_rule_bounds(client, bad):
    assert client.post("/api/backtest", json={"rule": bad}).status_code == 422


def test_deviations_scores_each_stock(client, monkeypatch):
    varied = [(100.0, 101.0 + (i % 3), 98.0 - (i % 3), 100.0 + (i % 5) - 2)
              for i in range(80)]
    monkeypatch.setattr(prices, "get_daily",
                        lambda tickers, period, force=False: (
                            {t: make_daily(varied, start="2025-01-06") for t in tickers},
                            1_700_000_000.0, False, None))
    body = client.get("/api/deviations").json()
    assert len(body["stocks"]) == 3
    assert {"ticker", "sector", "metrics", "current"} <= set(body["stocks"][0])


# --------------------------------------------------------------------------
# static hosting
# --------------------------------------------------------------------------

def test_the_spa_and_its_assets_are_served(client):
    assert client.get("/").status_code == 200
    assert client.get("/lib.js").status_code == 200
    assert client.get("/app.js").status_code == 200


# --------------------------------------------------------------------------
# /api/stocks/{ticker}/patterns and /api/patterns
# --------------------------------------------------------------------------

def test_stock_patterns_returns_every_family(client):
    body = client.get("/api/stocks/nvda/patterns").json()
    assert body["ticker"] == "NVDA"
    assert body["lookback"] == "2Y"
    assert body["target_session"] == "2026-05-05"
    prof = body["profile"]
    assert {"weekday", "follow_through", "gaps", "turn_of_month", "intraday"} <= set(prof)
    mon = next(r for r in prof["weekday"]["rows"] if r["key"] == "mon")
    assert mon["cells"]["green_rate"]["value"] == 100.0
    assert prof["intraday"] is None, "no five-minute history: the family is absent, not invented"


def test_stock_patterns_404_for_an_untracked_ticker(client):
    assert client.get("/api/stocks/ZZZZ/patterns").status_code == 404


def test_portfolio_patterns_lists_every_tracked_stock(client, monkeypatch):
    """A stock without history is reported with an error — never dropped, or
    the grid would quietly shrink."""
    monkeypatch.setattr(prices, "get_history", lambda tickers, force=False: (
        {"NVDA": HISTORY, "INTC": HISTORY}, 1_700_000_000.0, False, None))
    body = client.get("/api/patterns").json()
    by_ticker = {s["ticker"]: s for s in body["stocks"]}
    assert set(by_ticker) == {"NVDA", "INTC", "MSFT"}
    assert by_ticker["MSFT"]["profile"] is None
    assert by_ticker["MSFT"]["error"]
    assert by_ticker["NVDA"]["sector"] == "Semis"
    assert (body["min_n"], body["stands_out_z"]) == (patterns.MIN_N, patterns.STANDS_OUT_Z)


def test_pattern_fetch_failures_surface_as_stale(client, monkeypatch):
    monkeypatch.setattr(prices, "get_history", lambda tickers, force=False: (
        {}, 1_700_000_000.0, True, "Price fetch failed (RuntimeError)"))
    body = client.get("/api/patterns").json()
    assert body["stale"] is True
    assert body["fetch_error"] == "Price fetch failed (RuntimeError)"
    assert all(s["profile"] is None for s in body["stocks"])
