"""Behavior tests for owner-authored alert rules.

An alert states a threshold the owner chose; these tests pin that the app
reports whether it currently holds, with the observed value, and that a
statistic it cannot compute is surfaced as unavailable rather than quietly
counted as "no".
"""
from __future__ import annotations

import json
import shutil
import subprocess
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd
import pytest
from fastapi.testclient import TestClient

from app import alerts, db, main, metrics, patterns, prices, vault
from tests.conftest import make_daily, make_session_bars, weekly_pattern

REPO_ROOT = Path(__file__).resolve().parent.parent

# NVDA-style frame: closes 100 → 105 → 110, i.e. two straight green days.
RISING = make_daily([
    (100.0, 100.0, 100.0, 100.0),
    (100.0, 110.0, 95.0, 105.0),
    (105.0, 112.0, 99.0, 110.0),
])

ET = ZoneInfo("America/New_York")
MONDAY_NOON = datetime(2026, 5, 4, 12, 0, tzinfo=ET)
TUESDAY_NOON = datetime(2026, 5, 5, 12, 0, tzinfo=ET)

# Two years of history for the pattern rules: NVDA closes green on every one
# of 16 Mondays, INTC's weekdays all behave alike, MSFT has no data.
HISTORY = {"NVDA": weekly_pattern(16, True), "INTC": weekly_pattern(16, False)}

# Ten completed sessions in which NVDA set its high in the first five minutes.
APRIL_DAYS = [13, 14, 15, 16, 17, 20, 21, 22, 23, 24]
INTRADAY_HISTORY = {"NVDA": pd.concat([
    make_session_bars(f"2026-04-{d}", [110.0] + [100.0] * 77) for d in APRIL_DAYS])}

# Two straight red days.
FALLING = make_daily([
    (100.0, 100.0, 100.0, 100.0),
    (100.0, 101.0, 90.0, 95.0),
    (95.0, 96.0, 88.0, 90.0),
])


@pytest.fixture
def client(tmp_path, monkeypatch):
    """Fresh app over an empty DB: NVDA rising, INTC falling, MSFT no data."""
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "test.db")
    monkeypatch.setattr(db, "SEED_PORTFOLIO", {
        "Semis": [("NVDA", "NVIDIA Corporation"), ("INTC", "Intel Corporation")],
        "Cloud": [("MSFT", "Microsoft Corporation")],
    })
    frames = {"NVDA": RISING, "INTC": FALLING}      # MSFT deliberately absent
    monkeypatch.setattr(prices, "get_daily",
                        lambda tickers, period, force=False: (
                            dict(frames), 1_700_000_000.0, False, None))
    monkeypatch.setattr(prices, "get_intraday", lambda tickers, force=False: {})
    monkeypatch.setattr(prices, "get_history", lambda tickers, force=False: (
        dict(HISTORY), 1_700_000_000.0, False, None))
    monkeypatch.setattr(prices, "get_intraday_history", lambda tickers, force=False: (
        dict(INTRADAY_HISTORY), 1_700_000_000.0, False, None))
    monkeypatch.setattr(patterns, "now", lambda: MONDAY_NOON)
    monkeypatch.setattr(prices, "get_stats", lambda t, force=False: None)
    monkeypatch.setattr(prices, "get_news", lambda t, force=False: [])
    monkeypatch.setattr(vault, "stock_note", lambda t: None)
    monkeypatch.setattr(vault, "sector_note", lambda n: None)
    monkeypatch.setattr(vault, "articles_for", lambda t: [])
    monkeypatch.setattr(vault, "status", lambda: {
        "path": "/nope", "available": False, "stocks": 0, "sectors": 0, "articles": 0})
    with TestClient(main.app) as c:
        yield c


def make_rule(**overrides) -> dict:
    rule = {"id": 1, "name": "test", "ticker": None, "period": "3M",
            "metric_key": "m:green_days", "op": "gte", "value": 2.0,
            "enabled": True, "created_at": "2026-08-11"}
    rule.update(overrides)
    return rule


# --------------------------------------------------------------------------
# the evaluator
# --------------------------------------------------------------------------

def test_a_met_condition_reports_the_stock_and_its_observed_value(client):
    """NVDA closed up on both scored days, so "green days ≥ 2" holds at 2."""
    results, _, _ = alerts.evaluate([make_rule(ticker="NVDA")])
    assert results[0]["matches"] == [{"ticker": "NVDA", "observed": 2.0}]
    assert results[0]["unavailable"] == []


def test_an_unmet_condition_reports_no_matches(client):
    """INTC fell on both days, so the same rule does not hold for it."""
    results, _, _ = alerts.evaluate([make_rule(ticker="INTC")])
    assert results[0]["matches"] == []
    assert results[0]["unavailable"] == [], "the stat computed fine — it just didn't hold"


def test_an_any_stock_rule_screens_every_tracked_ticker(client):
    """A rule with no ticker is a screener: it reports each stock that holds."""
    results, _, _ = alerts.evaluate([make_rule(ticker=None)])
    assert [m["ticker"] for m in results[0]["matches"]] == ["NVDA"]
    assert results[0]["unavailable"] == ["MSFT"], "no price data — surfaced, not dropped"


def test_lte_compares_in_the_other_direction(client):
    """The same statistic with ≤ selects the falling stock instead."""
    rule = make_rule(ticker=None, metric_key="m:red_days", op="gte", value=2)
    results, _, _ = alerts.evaluate([rule])
    assert [m["ticker"] for m in results[0]["matches"]] == ["INTC"]

    flipped = make_rule(ticker=None, metric_key="m:red_days", op="lte", value=0)
    results, _, _ = alerts.evaluate([flipped])
    assert [m["ticker"] for m in results[0]["matches"]] == ["NVDA"]


def test_a_threshold_is_inclusive_at_the_boundary(client):
    """"≥ 2" holds when the observed value is exactly 2 — the owner asked for
    at-or-beyond, not strictly beyond."""
    results, _, _ = alerts.evaluate([make_rule(ticker="NVDA", value=2.0)])
    assert results[0]["matches"], "2 >= 2 must hold"
    results, _, _ = alerts.evaluate([make_rule(ticker="NVDA", value=2.01)])
    assert results[0]["matches"] == []


def test_a_core_column_rule_reads_the_price_row(client):
    """Rules can target core table columns too, not just Things to Track."""
    # 3M window: first close 100 → last close 110 = +10%.
    results, _, _ = alerts.evaluate([make_rule(ticker="NVDA", metric_key="pct", value=10.0)])
    assert results[0]["matches"] == [{"ticker": "NVDA", "observed": 10.0}]


def test_a_pattern_metric_rule_works_end_to_end(client):
    """The new streak metric is alertable like any other numeric column."""
    rule = make_rule(ticker=None, metric_key="m:streak", op="lte", value=-2)
    results, _, _ = alerts.evaluate([rule])
    assert [m["ticker"] for m in results[0]["matches"]] == ["INTC"], "two straight red closes"


def test_an_unknown_statistic_is_unavailable_not_a_crash(client):
    """A rule whose statistic disappeared must degrade visibly."""
    results, _, _ = alerts.evaluate([make_rule(ticker="NVDA", metric_key="m:no_such_stat")])
    assert results[0]["matches"] == []
    assert results[0]["unavailable"] == ["NVDA"]


def test_a_rule_for_an_untracked_stock_is_unavailable(client):
    """Deleting a stock must not make its old rule silently report "no"."""
    results, _, _ = alerts.evaluate([make_rule(ticker="DELISTED")])
    assert results[0]["unavailable"] == ["DELISTED"]
    assert results[0]["matches"] == []


def test_each_period_is_fetched_once_however_many_rules_use_it(client, monkeypatch):
    """Ten rules over two periods must not mean ten Yahoo round trips."""
    calls = []
    real = prices.get_daily

    def counting(tickers, period, force=False):
        calls.append(period)
        return real(tickers, period, force=force)

    monkeypatch.setattr(prices, "get_daily", counting)
    rules = ([make_rule(id=i, period="3M") for i in range(5)] +
             [make_rule(id=10 + i, period="1M") for i in range(5)])
    alerts.evaluate(rules)
    assert sorted(calls) == ["1M", "3M"], f"expected one fetch per period, got {calls}"


def test_evaluate_with_no_rules_does_no_work(client):
    assert alerts.evaluate([]) == ([], False, None)


# --------------------------------------------------------------------------
# pattern statistics (s:)
# --------------------------------------------------------------------------

def test_a_pattern_rule_reports_matches_and_whether_they_stand_out(client):
    """NVDA was green on 16 of 16 Mondays — far beyond its other days — so the
    match says so; MSFT has no history and is unavailable, not "not met"."""
    rule = make_rule(metric_key="s:wd:mon:green_rate", op="gte", value=90)
    results, _, _ = alerts.evaluate([rule])
    assert results[0]["matches"] == [{"ticker": "NVDA", "observed": 100.0, "stands_out": True}]
    assert results[0]["unavailable"] == ["MSFT"]


def test_a_matching_value_inside_normal_variation_says_so(client):
    """INTC's Mondays are green half the time, like every other day — a rule
    that matches it must not imply the value is unusual."""
    rule = make_rule(ticker="INTC", metric_key="s:wd:mon:green_rate", op="gte", value=50)
    results, _, _ = alerts.evaluate([rule])
    assert results[0]["matches"] == [{"ticker": "INTC", "observed": 50.0, "stands_out": False}]


def test_a_todays_weekday_rule_follows_the_session(client, monkeypatch):
    rule = make_rule(ticker="NVDA", metric_key="s:wd:today:green_rate", op="gte", value=90)
    results, _, _ = alerts.evaluate([rule])
    assert [m["ticker"] for m in results[0]["matches"]] == ["NVDA"], "Monday: 100%"

    monkeypatch.setattr(patterns, "now", lambda: TUESDAY_NOON)
    results, _, _ = alerts.evaluate([rule])
    assert results[0]["matches"] == [], "Tuesday: 50%, below the threshold"
    assert results[0]["unavailable"] == [], "it computed fine — it just didn't hold"


def spy(monkeypatch, name):
    calls = []
    real = getattr(prices, name)

    def wrapper(*args, **kwargs):
        calls.append(name)
        return real(*args, **kwargs)

    monkeypatch.setattr(prices, name, wrapper)
    return calls


def test_pattern_rules_skip_the_period_fetch_and_the_intraday_history(client, monkeypatch):
    """A weekday rule reads two-year daily bars once — no period window, no
    five-minute history — however many pattern rules there are."""
    daily = spy(monkeypatch, "get_daily")
    history = spy(monkeypatch, "get_history")
    intraday = spy(monkeypatch, "get_intraday_history")
    alerts.evaluate([make_rule(id=1, metric_key="s:wd:mon:green_rate"),
                     make_rule(id=2, metric_key="s:ft:after_red:next_green_rate")])
    assert (len(daily), len(history), len(intraday)) == (0, 1, 0)


def test_an_intraday_timing_rule_fetches_the_intraday_history_once(client, monkeypatch):
    intraday = spy(monkeypatch, "get_intraday_history")
    rule = make_rule(ticker=None, metric_key="s:intra:first30_high", op="gte", value=90)
    results, _, _ = alerts.evaluate([rule, {**rule, "id": 2}])
    assert len(intraday) == 1
    # NVDA set its high in the first bar of all ten sessions; intraday timing
    # has no comparison baseline, so it makes no stands-out claim either way.
    assert results[0]["matches"] == [{"ticker": "NVDA", "observed": 100.0, "stands_out": None}]
    assert results[0]["unavailable"] == ["INTC", "MSFT"]


def test_creating_a_pattern_alert_over_http(client):
    created = client.post("/api/alerts", json={
        "name": "Monday green share", "metric_key": "s:wd:mon:green_rate",
        "op": "gte", "value": 90})
    assert created.status_code == 201
    body = client.get("/api/alerts").json()
    assert body["rules"][0]["matches"][0]["stands_out"] is True
    assert body["triggered_count"] == 1


def test_an_unknown_pattern_statistic_is_refused(client):
    res = client.post("/api/alerts", json={
        "name": "weekend", "metric_key": "s:wd:sat:green_rate", "op": "gte", "value": 50})
    assert res.status_code == 422


# --------------------------------------------------------------------------
# the statistic allow-list
# --------------------------------------------------------------------------

def test_stat_defs_offer_metrics_core_columns_and_z_scores(client):
    keys = alerts.allowed_keys()
    assert "m:green_days" in keys and "m:streak" in keys
    assert "m:rs:pc_high:avg" in keys, "range statistics are alertable"
    assert "pct" in keys and "market_cap" in keys
    assert "z:pc_close" in keys


def test_non_numeric_columns_cannot_carry_a_threshold(client):
    """A clock time has no meaningful ≥ threshold, so it isn't offered."""
    keys = alerts.allowed_keys()
    assert "m:day_high_time" not in keys
    assert "m:day_low_time" not in keys


def test_every_offered_statistic_has_a_label(client):
    assert all(d["label"] and d["group"] for d in alerts.stat_defs())


# --------------------------------------------------------------------------
# the HTTP surface
# --------------------------------------------------------------------------

def test_creating_and_listing_an_alert(client):
    created = client.post("/api/alerts", json={
        "name": "NVDA green run", "ticker": "NVDA", "period": "3M",
        "metric_key": "m:green_days", "op": "gte", "value": 2})
    assert created.status_code == 201
    body = client.get("/api/alerts").json()
    assert len(body["rules"]) == 1
    rule = body["rules"][0]
    assert rule["name"] == "NVDA green run"
    assert rule["matches"] == [{"ticker": "NVDA", "observed": 2.0}]
    assert body["triggered_count"] == 1


def test_triggered_count_only_counts_rules_that_currently_hold(client):
    client.post("/api/alerts", json={"name": "holds", "ticker": "NVDA",
                                     "metric_key": "m:green_days", "op": "gte", "value": 2})
    client.post("/api/alerts", json={"name": "does not", "ticker": "INTC",
                                     "metric_key": "m:green_days", "op": "gte", "value": 2})
    assert client.get("/api/alerts").json()["triggered_count"] == 1


def test_a_disabled_rule_is_listed_but_not_evaluated(client):
    alert_id = client.post("/api/alerts", json={
        "name": "paused", "ticker": "NVDA",
        "metric_key": "m:green_days", "op": "gte", "value": 2}).json()["id"]
    assert client.patch(f"/api/alerts/{alert_id}", json={"enabled": False}).status_code == 200

    body = client.get("/api/alerts").json()
    rule = body["rules"][0]
    assert rule["enabled"] is False
    assert rule["matches"] == [], "a paused rule reports nothing"
    assert body["triggered_count"] == 0


def test_re_enabling_a_rule_brings_it_back(client):
    alert_id = client.post("/api/alerts", json={
        "name": "paused", "ticker": "NVDA",
        "metric_key": "m:green_days", "op": "gte", "value": 2}).json()["id"]
    client.patch(f"/api/alerts/{alert_id}", json={"enabled": False})
    client.patch(f"/api/alerts/{alert_id}", json={"enabled": True})
    assert client.get("/api/alerts").json()["triggered_count"] == 1


def test_deleting_an_alert(client):
    alert_id = client.post("/api/alerts", json={
        "name": "temp", "metric_key": "m:green_days", "op": "gte", "value": 2}).json()["id"]
    assert client.delete(f"/api/alerts/{alert_id}").status_code == 204
    assert client.get("/api/alerts").json()["rules"] == []


def test_unknown_alert_ids_are_404(client):
    assert client.patch("/api/alerts/999", json={"enabled": False}).status_code == 404
    assert client.delete("/api/alerts/999").status_code == 404


def test_an_unknown_statistic_is_rejected_at_creation(client):
    """Fail closed: a typo'd key must not be stored as a rule that can never
    match — the owner would read the silence as "condition not met"."""
    res = client.post("/api/alerts", json={
        "name": "typo", "metric_key": "m:greeen_days", "op": "gte", "value": 2})
    assert res.status_code == 422
    assert client.get("/api/alerts").json()["rules"] == []


def test_an_untracked_ticker_is_rejected_at_creation(client):
    res = client.post("/api/alerts", json={
        "name": "ghost", "ticker": "NOPE",
        "metric_key": "m:green_days", "op": "gte", "value": 2})
    assert res.status_code == 422


def test_a_bad_period_is_rejected(client):
    res = client.post("/api/alerts", json={
        "name": "bad", "period": "10Y",
        "metric_key": "m:green_days", "op": "gte", "value": 2})
    assert res.status_code == 422


def test_invalid_rule_shapes_are_rejected(client):
    """A blank name, a missing threshold, or an unknown operator."""
    for payload in (
        {"name": "", "metric_key": "m:green_days", "op": "gte", "value": 2},
        {"name": "op", "metric_key": "m:green_days", "op": "between", "value": 2},
        {"name": "no value", "metric_key": "m:green_days", "op": "gte"},
        # JSON.stringify turns Infinity into null, so this is what a browser
        # sending a runaway value actually puts on the wire.
        {"name": "null", "metric_key": "m:green_days", "op": "gte", "value": None},
    ):
        assert client.post("/api/alerts", json=payload).status_code == 422, payload


def test_a_non_finite_threshold_is_rejected(client):
    """Infinity is not valid JSON but Python's parser accepts the literal, so
    a threshold no observation could ever fall below must be refused rather
    than stored as a rule that always (or never) holds."""
    raw = ('{"name": "inf", "metric_key": "m:green_days", '
           '"op": "gte", "value": Infinity}')
    res = client.post("/api/alerts", content=raw,
                      headers={"Content-Type": "application/json"})
    assert res.status_code == 422
    assert client.get("/api/alerts").json()["rules"] == []


def test_the_alerts_table_appears_in_the_data_view(client):
    client.post("/api/alerts", json={
        "name": "shown", "metric_key": "m:green_days", "op": "gte", "value": 2})
    tables = client.get("/api/db").json()["tables"]
    assert "alerts" in tables
    assert tables["alerts"]["rows"][0]["name"] == "shown"


# --------------------------------------------------------------------------
# parity with the frontend's filter engine
# --------------------------------------------------------------------------

@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
def test_server_and_browser_agree_on_the_same_condition(client):
    """The All-view filters (lib.js) and the alert engine (alerts.py) both
    decide "does this row satisfy key/op/value". They are separate
    implementations, so pin that they answer identically — otherwise a
    threshold previewed as a filter could evaluate differently as an alert.
    """
    rows = [
        {"ticker": "NVDA", "metrics": {"green_days": 2}},
        {"ticker": "INTC", "metrics": {"green_days": 0}},
        {"ticker": "MSFT", "metrics": {"green_days": None}},
    ]
    cases = [("gte", 2.0), ("lte", 0.0), ("gte", 5.0), ("lte", 99.0)]

    script = """
    const lib = require(process.argv[1]);
    const [rows, cases] = JSON.parse(process.argv[2]);
    const defs = {"m:green_days": {key: "m:green_days", metric: "green_days"}};
    const out = cases.map(([op, value]) =>
      lib.applyFilters(rows, [{key: "m:green_days", op, value}], defs).map(r => r.ticker));
    console.log(JSON.stringify(out));
    """
    proc = subprocess.run(
        ["node", "-e", script, str(REPO_ROOT / "static" / "lib.js"),
         json.dumps([rows, cases])],
        capture_output=True, text=True, timeout=30, check=True)
    browser = json.loads(proc.stdout)

    server = []
    for op, value in cases:
        matched = []
        for row in rows:
            observed = alerts._observed("m:green_days", {}, row["metrics"], None)
            if observed is not None and alerts._holds(observed, op, value):
                matched.append(row["ticker"])
        server.append(matched)

    assert server == browser, f"server {server} != browser {browser}"


def test_the_parity_fixture_is_not_vacuous(client):
    """Guard the test above: if every case matched everything (or nothing),
    the comparison would pass without discriminating anything."""
    metrics_by_ticker = {"NVDA": {"green_days": 2}, "INTC": {"green_days": 0}}
    matched = [t for t, m in metrics_by_ticker.items()
               if alerts._holds(alerts._observed("m:green_days", {}, m, None), "gte", 2.0)]
    assert matched == ["NVDA"], "the fixture must separate the two stocks"


# --------------------------------------------------------------------------
# no advice language anywhere in the alert surface
# --------------------------------------------------------------------------

def test_the_alert_surface_carries_no_trading_language(client):
    """Project rule: the tools compute, the owner decides. Nothing in the
    payload may suggest an action."""
    client.post("/api/alerts", json={
        "name": "NVDA green run", "ticker": "NVDA",
        "metric_key": "m:green_days", "op": "gte", "value": 2})
    body = json.dumps(client.get("/api/alerts").json()).lower()
    for word in (" buy", " sell", "hold ", "signal", "recommend",
                 "target price", "position size", "entry point"):
        assert word not in body, f"advice language leaked into the alerts API: {word!r}"


def test_stat_labels_stay_descriptive(client):
    labels = " ".join(d["label"] for d in alerts.stat_defs()).lower()
    for word in ("buy", "sell", "signal", "oversold", "overbought"):
        assert word not in labels, f"{word!r} implies a judgement, not a measurement"


def test_metric_descriptions_stay_descriptive():
    text = " ".join(f"{d['label']} {d.get('description', '')}"
                    for d in metrics.metric_defs()).lower()
    for word in ("buy", "sell", "oversold", "overbought", "signal"):
        assert word not in text, f"{word!r} implies a judgement, not a measurement"
