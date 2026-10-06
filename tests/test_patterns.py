"""Behavior tests for the pattern statistics (weekday, follow-through, gaps,
turn of month, intraday timing).

Every expected value is derived by hand in the comments. The fixtures run on
business days from Monday 2026-01-05, so bar j falls on weekday j % 5; the
first bar only supplies the previous close and never scores.
"""
from __future__ import annotations

import json
from datetime import date, datetime
from zoneinfo import ZoneInfo

import pandas as pd
import pytest

from app import metrics, patterns
from tests.conftest import make_closes, make_daily, make_session_bars, weekly_pattern

ET = ZoneInfo("America/New_York")
UTC = ZoneInfo("UTC")
LATER = datetime(2026, 9, 1, 12, 0, tzinfo=ET)     # long after every fixture


def test_fixture_premise_first_bar_is_a_monday():
    assert date(2026, 1, 5).weekday() == 0, "every weekday fixture below relies on this"


def frame_of(daily):
    return patterns._daily_frame(daily)


# --------------------------------------------------------------------------
# completed sessions and the target session
# --------------------------------------------------------------------------

FIVE_DAYS = make_closes([100.0, 101.0, 102.0, 103.0, 104.0])   # Mon 5 .. Fri 9 Jan


@pytest.mark.parametrize("at, expected_len", [
    (datetime(2026, 1, 9, 15, 0, tzinfo=ET), 4),     # Friday's session still trading
    (datetime(2026, 1, 9, 16, 30, tzinfo=ET), 5),    # Friday closed
    (datetime(2026, 1, 10, 10, 0, tzinfo=ET), 5),    # Saturday
    (datetime(2026, 1, 9, 20, 30, tzinfo=UTC), 4),   # 15:30 ET, given in UTC
])
def test_an_unfinished_session_is_left_out(at, expected_len):
    """Yahoo serves today's half-finished bar during market hours; counting
    it would score a partial day as a full one."""
    assert len(patterns.completed(FIVE_DAYS, at)) == expected_len


def test_completed_drops_every_intraday_bar_of_the_unfinished_session():
    bars = pd.concat([make_session_bars("2026-01-05", [100.0] * 20),
                      make_session_bars("2026-01-06", [100.0] * 20)])
    kept = patterns.completed(bars, datetime(2026, 1, 6, 12, 0, tzinfo=ET))
    assert len(kept) == 20
    assert {ts.date() for ts in kept.index} == {date(2026, 1, 5)}


@pytest.mark.parametrize("at, expected", [
    (datetime(2026, 1, 6, 15, 0, tzinfo=ET), date(2026, 1, 6)),     # Tue, trading
    (datetime(2026, 1, 6, 16, 30, tzinfo=ET), date(2026, 1, 7)),    # Tue, closed
    (datetime(2026, 1, 9, 16, 30, tzinfo=ET), date(2026, 1, 12)),   # Fri, closed
    (datetime(2026, 1, 10, 10, 0, tzinfo=ET), date(2026, 1, 12)),   # Sat
    (datetime(2026, 1, 11, 10, 0, tzinfo=ET), date(2026, 1, 12)),   # Sun
    (datetime(2026, 1, 6, 20, 30, tzinfo=UTC), date(2026, 1, 6)),   # 15:30 ET
    (datetime(2026, 1, 6, 21, 30, tzinfo=UTC), date(2026, 1, 7)),   # 16:30 ET
])
def test_target_session_is_the_one_trading_or_the_next(at, expected):
    assert patterns.target_session(at) == expected


# --------------------------------------------------------------------------
# weekday profile
# --------------------------------------------------------------------------

# Mon5 base 100 | Tue6 101 G | Wed7 100 R | Thu8 100 flat | Fri9 102 G
# Mon12 103 G | Tue13 102 R | Wed14 104 G | Thu15 103 R | Fri16 103 flat
SMALL = make_closes([100.0, 101.0, 100.0, 100.0, 102.0, 103.0, 102.0, 104.0, 103.0, 103.0])


def rows_by_key(family):
    return {r["key"]: r for r in family["rows"]}


def test_weekday_green_rates_by_hand():
    wd = patterns.weekday_profile(frame_of(SMALL))
    rows = rows_by_key(wd)
    green = {k: (r["n"], r["cells"]["green_rate"]["value"]) for k, r in rows.items()}
    assert green == {
        "mon": (1, 100.0),   # the base Monday never scores — only Mon 12
        "tue": (2, 50.0),
        "wed": (2, 50.0),
        "thu": (2, 0.0),     # a flat day and a red day: flat counts, as not-green
        "fri": (2, 50.0),    # green and flat
    }
    assert wd["all"]["n"] == 9
    assert wd["all"]["cells"]["green_rate"]["value"] == pytest.approx(44.44, abs=0.01)


def test_cells_under_the_minimum_sample_are_thin_with_no_z():
    cell = rows_by_key(patterns.weekday_profile(frame_of(SMALL)))["mon"]["cells"]["green_rate"]
    assert cell["thin"] is True
    assert cell["z"] is None
    assert cell["stands_out"] is False, "one Monday can't stand out, however green"


def test_close_location_by_hand_and_skips_zero_range_days():
    daily = make_daily([
        (100.0, 100.0, 100.0, 100.0),    # base
        (100.0, 110.0, 100.0, 105.0),    # closed halfway up its range: 50%
        (105.0, 105.0, 105.0, 105.0),    # no range at all: skipped
        (105.0, 106.0, 102.0, 106.0),    # closed at its high: 100%
    ])
    cell = patterns.weekday_profile(frame_of(daily))["all"]["cells"]["avg_close_loc"]
    assert cell["n"] == 2
    assert cell["value"] == pytest.approx(75.0)


def test_a_planted_monday_effect_stands_out():
    # Mondays 16/16 green; Tue-Fri 32/64. Pooled p = 48/80 = 0.6,
    # se = sqrt(0.24 * (1/16 + 1/64)) = 0.1369, z = 0.5 / 0.1369 = 3.65.
    rows = rows_by_key(patterns.weekday_profile(frame_of(weekly_pattern(16, True))))
    mon = rows["mon"]["cells"]["green_rate"]
    assert (rows["mon"]["n"], mon["value"]) == (16, 100.0)
    assert mon["z"] == pytest.approx(3.65, abs=0.01)
    assert mon["stands_out"] is True
    # Tuesday at 50% against a rest at 62.5%: z = -0.125 / 0.1369 = -0.91.
    tue = rows["tue"]["cells"]["green_rate"]
    assert tue["value"] == 50.0
    assert tue["z"] == pytest.approx(-0.91, abs=0.01)
    assert tue["stands_out"] is False


def test_no_weekday_stands_out_when_every_weekday_behaves_the_same():
    """The other half of the pair: identical weekdays must never light up,
    even though one of them is always 'best' by a hair."""
    rows = patterns.weekday_profile(frame_of(weekly_pattern(16, False)))["rows"]
    for row in rows:
        for key, cell in row["cells"].items():
            assert cell["stands_out"] is False, f"{row['key']} {key} flagged on pure alternation"
        assert row["cells"]["green_rate"]["value"] == 50.0


# --------------------------------------------------------------------------
# follow-through
# --------------------------------------------------------------------------

# base 100 | j1 101 G | j2 100 R | j3 99 R | j4 98 R (3rd red) | j5 99 G | j6 100 G (last)
FOLLOW = make_closes([100.0, 101.0, 100.0, 99.0, 98.0, 99.0, 100.0])


def test_follow_through_reads_the_next_session_by_hand():
    rows = rows_by_key(patterns.follow_through(frame_of(FOLLOW)))
    # after green: j1 -> j2 red, j5 -> j6 green. j6 is green too, but it is the
    # latest session and has no next day, so it is not a sample.
    assert rows["after_green"]["n"] == 2
    assert rows["after_green"]["cells"]["next_green_rate"]["value"] == 50.0
    # after red: j2 -> red, j3 -> red, j4 -> green
    assert rows["after_red"]["n"] == 3
    assert rows["after_red"]["cells"]["next_green_rate"]["value"] == pytest.approx(33.33, abs=0.01)


def test_three_red_days_fires_on_the_third_and_reads_the_day_after():
    rows = rows_by_key(patterns.follow_through(frame_of(FOLLOW)))
    red3 = rows["after_red3"]
    assert red3["n"] == 1, "only j4 completes a run of three"
    assert red3["cells"]["next_green_rate"]["value"] == 100.0
    # j5: 98 -> 99 = +1.0204%
    assert red3["cells"]["avg_next_day"]["value"] == pytest.approx(1.02, abs=0.005)


def test_follow_through_baseline_excludes_the_latest_session():
    fam = patterns.follow_through(frame_of(FOLLOW))
    assert fam["all"]["n"] == 5
    # next days of j1..j5 are j2 R, j3 R, j4 R, j5 G, j6 G -> 2 of 5
    assert fam["all"]["cells"]["next_green_rate"]["value"] == 40.0


def test_big_move_conditions_by_hand():
    # j1 100->96 = -4% | j2 97 (+1.04%) | j3 100 (+3.09%) | j4 99 (-1%, last)
    rows = rows_by_key(patterns.follow_through(frame_of(make_closes([100.0, 96.0, 97.0, 100.0, 99.0]))))
    assert (rows["after_drop3"]["n"], rows["after_drop3"]["cells"]["next_green_rate"]["value"]) == (1, 100.0)
    assert (rows["after_rise3"]["n"], rows["after_rise3"]["cells"]["next_green_rate"]["value"]) == (1, 0.0)


# --------------------------------------------------------------------------
# gaps
# --------------------------------------------------------------------------

GAPS = make_daily([
    (100.0, 100.0, 100.0, 100.0),    # base close 100
    (101.0, 103.0, 99.5, 102.0),     # gap up exactly 1%: low 99.5 <= 100 filled; 102 > 101 continued
    (102.5, 103.0, 99.0, 100.0),     # +0.49%: not a gap
    (99.0, 99.5, 97.0, 98.0),        # gap down exactly 1%: high 99.5 < 100 unfilled; 98 < 99 continued
    (99.96, 100.0, 98.5, 99.0),      # gap up 2%: low 98.5 > 98 unfilled; 99 < 99.96 not continued
])


def test_gap_fill_and_continuation_by_hand():
    rows = rows_by_key(patterns.gap_behavior(frame_of(GAPS)))
    up, down = rows["up"], rows["down"]
    assert up["n"] == 2, "a gap of exactly 1% counts; 0.49% does not"
    assert up["cells"]["fill_rate"]["value"] == 50.0
    assert up["cells"]["continue_rate"]["value"] == 50.0
    assert up["cells"]["avg_gap"]["value"] == pytest.approx(1.5, abs=0.01)
    assert down["n"] == 1
    assert down["cells"]["fill_rate"]["value"] == 0.0
    assert down["cells"]["continue_rate"]["value"] == 100.0


def test_gap_counts_agree_with_the_gap_frequency_column():
    """Two code paths define a gap; the column on the dashboard and this
    family must never disagree about which days were gaps."""
    rows = rows_by_key(patterns.gap_behavior(frame_of(GAPS)))
    assert rows["up"]["n"] + rows["down"]["n"] == metrics.gap_frequency(GAPS, None) == 3


# --------------------------------------------------------------------------
# turn of month
# --------------------------------------------------------------------------

FIRST3 = {date(2026, 2, 2), date(2026, 2, 3), date(2026, 2, 4),
          date(2026, 3, 2), date(2026, 3, 3), date(2026, 3, 4)}
LAST3 = {date(2026, 2, 25), date(2026, 2, 26), date(2026, 2, 27),
         date(2026, 3, 27), date(2026, 3, 30), date(2026, 3, 31)}


def month_fixture():
    """Jan 5 .. Apr 14 2026. First-3 days +1%, last-3 days +2%, everything else
    -1%. January (partial) and April (current) must both be left out."""
    days = pd.bdate_range("2026-01-05", "2026-04-14")
    closes = [100.0]
    for d in days[1:]:
        change = 1.0 if d.date() in FIRST3 else 2.0 if d.date() in LAST3 else -1.0
        closes.append(closes[-1] * (1 + change / 100))
    return make_closes(closes)


def test_turn_of_month_windows_by_hand():
    at = datetime(2026, 4, 15, 12, 0, tzinfo=ET)
    rows = rows_by_key(patterns.turn_of_month(frame_of(month_fixture()), at))
    # Feb has 20 weekdays and Mar 22: first 3 + last 3 each, the rest middle.
    assert {k: r["n"] for k, r in rows.items()} == {"first3": 6, "middle": 30, "last3": 6}
    assert rows["first3"]["cells"]["avg_day"]["value"] == pytest.approx(1.0)
    assert rows["last3"]["cells"]["avg_day"]["value"] == pytest.approx(2.0)
    assert rows["middle"]["cells"]["avg_day"]["value"] == pytest.approx(-1.0)


@pytest.mark.parametrize("day, sessions_before, expected", [
    (date(2026, 2, 3), [2], "first3"),
    (date(2026, 2, 4), [2, 3], "first3"),
    (date(2026, 2, 5), [2, 3, 4], "middle"),
    (date(2026, 2, 24), list(range(2, 24)), "middle"),   # 25, 26, 27 still to come
    (date(2026, 2, 25), list(range(2, 25)), "last3"),
    (date(2026, 2, 27), list(range(2, 27)), "last3"),
])
def test_the_target_sessions_month_window(day, sessions_before, expected):
    index = pd.DatetimeIndex([pd.Timestamp(2026, 2, d) for d in sessions_before
                              if pd.Timestamp(2026, 2, d).weekday() < 5]).tz_localize(ET)
    assert patterns._month_window_for(day, pd.DataFrame(index=index)) == expected


# --------------------------------------------------------------------------
# intraday timing
# --------------------------------------------------------------------------

def session(day, high_at=(), low_at=()):
    closes = [100.0] * 78
    for i in high_at:
        closes[i] = 110.0
    for i in low_at:
        closes[i] = 90.0
    return make_session_bars(day, closes)


THREE_SESSIONS = pd.concat([
    session("2026-01-05", high_at=[1], low_at=[77]),    # 09:35 -> bucket 0, 15:55 -> 12
    session("2026-01-06", high_at=[13], low_at=[6]),    # 10:35 -> bucket 2, 10:00 -> 1
    session("2026-01-07", high_at=[30, 60]),            # tie: 12:00 -> bucket 5 wins
])                                                       # flat lows: first bar -> 0


def test_intraday_buckets_by_hand():
    timing = patterns.intraday_timing(THREE_SESSIONS, LATER)
    assert timing["n"] == 3
    third = pytest.approx(33.33, abs=0.01)
    assert timing["high"][0] == third and timing["high"][2] == third
    assert timing["high"][5] == third, "a tied high counts at its first bar"
    assert timing["high"][11] == 0.0, "the later tied bar does not count"
    assert timing["low"][12] == third and timing["low"][1] == third and timing["low"][0] == third
    assert sum(timing["high"]) == pytest.approx(100.0, abs=0.05)


def test_intraday_skips_the_unfinished_session_and_glitch_sessions():
    bars = pd.concat([THREE_SESSIONS, make_session_bars("2026-01-08", [100.0] * 5)])
    assert patterns.intraday_timing(bars, LATER)["n"] == 3, "a 5-bar session is a glitch"
    partial = patterns.intraday_timing(THREE_SESSIONS, datetime(2026, 1, 7, 12, 0, tzinfo=ET))
    assert partial["n"] == 2
    assert partial["high"][5] == 0.0, "the still-trading session's high is not counted"


def test_bucket_labels_cover_the_regular_session():
    labels = patterns.bucket_labels()
    assert (len(labels), labels[0], labels[1], labels[-1]) == (13, "09:30", "10:00", "15:30")


# --------------------------------------------------------------------------
# profile, today variants, and the alert surface
# --------------------------------------------------------------------------

PLANTED = weekly_pattern(16, True)          # Jan 5 .. Mon Apr 27 2026


def test_profile_excludes_todays_unfinished_bar():
    prof = patterns.profile(PLANTED, None, datetime(2026, 4, 27, 12, 0, tzinfo=ET))
    assert prof["last_session"] == "2026-04-24"
    prof = patterns.profile(PLANTED, None, datetime(2026, 4, 27, 16, 30, tzinfo=ET))
    assert prof["last_session"] == "2026-04-27"


@pytest.mark.parametrize("at, weekday", [
    (datetime(2026, 4, 28, 12, 0, tzinfo=ET), "tue"),
    (datetime(2026, 5, 4, 12, 0, tzinfo=ET), "mon"),
])
def test_todays_weekday_variant_follows_the_clock(at, weekday):
    values, stands = patterns.flatten(patterns.profile(PLANTED, None, at))
    for m in patterns.ALERT_WEEKDAY_METRICS:
        assert values[f"s:wd:today:{m}"] == values[f"s:wd:{weekday}:{m}"]
    assert stands["s:wd:today:green_rate"] is (weekday == "mon")


def test_last_session_variant_uses_the_latest_days_direction():
    # The fixture ends on a +2% Monday, so "a day like the last one" is green.
    prof = patterns.profile(PLANTED, None, datetime(2026, 4, 28, 12, 0, tzinfo=ET))
    assert prof["today"]["last_direction"] == "after_green"
    values, _ = patterns.flatten(prof)
    assert values["s:ft:last:next_green_rate"] == values["s:ft:after_green:next_green_rate"]
    assert values["s:ft:last:next_green_rate"] is not None


def test_thin_cells_are_unavailable_to_alerts():
    values, stands = patterns.flatten(patterns.profile(SMALL, None, LATER))
    assert values["s:wd:mon:green_rate"] is None, "1 Monday: shown dimmed, never alertable"
    values, stands = patterns.flatten(patterns.profile(PLANTED, None, LATER))
    assert values["s:wd:mon:green_rate"] == 100.0
    assert stands["s:wd:mon:green_rate"] is True


def test_gap_and_intraday_stats_carry_no_stands_out_flag():
    """They have no comparison baseline, so they must not claim one."""
    values, stands = patterns.flatten(patterns.profile(PLANTED, THREE_SESSIONS, LATER))
    assert stands["s:gap:up:fill_rate"] is None
    assert stands["s:intra:first30_high"] is None


def test_alert_keys_and_flattened_keys_match_exactly():
    """The declaration the API validates against and the values the engine
    reads must be the same set — in both directions, with and without the
    intraday family — and the declaration is pinned to literal counts."""
    declared = {d["key"] for d in patterns.alert_defs()}
    assert len(declared) == 52   # weekday 6x4, follow 6x2, gaps 2x2, month 4x2, intraday 4
    assert {"s:wd:mon:green_rate", "s:wd:today:avg_range", "s:ft:after_red3:avg_next_day",
            "s:gap:down:continue_rate", "s:tom:today:avg_day",
            "s:intra:last30_low"} <= declared
    for intraday in (None, THREE_SESSIONS):
        values, stands = patterns.flatten(patterns.profile(PLANTED, intraday, LATER))
        assert set(values) == declared and set(stands) == declared
    assert set(patterns.flatten(None)[0]) == declared


def test_a_stock_without_history_has_every_statistic_unavailable():
    assert patterns.profile(None, None, LATER) is None
    assert patterns.profile(make_closes([100.0]), None, LATER) is None
    values, _ = patterns.flatten(None)
    assert all(v is None for v in values.values())


def test_the_profile_is_strict_json():
    prof = patterns.profile(PLANTED, THREE_SESSIONS, LATER)
    json.dumps(prof, allow_nan=False)


def test_pattern_wording_stays_descriptive():
    words = [d["label"] for d in patterns.alert_defs()]
    words += [label for _k, label in patterns.FOLLOW_CONDITIONS + patterns.GAP_DIRECTIONS
              + patterns.MONTH_WINDOWS + patterns.INTRADAY_SUMMARY]
    text = " ".join(words).lower()
    for word in ("buy", "sell", "signal", "likely", "expect", "predict",
                 "best time", "oversold", "overbought"):
        assert word not in text, f"{word!r} implies a forecast or an action"
