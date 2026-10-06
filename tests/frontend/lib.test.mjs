/* Behavior tests for the frontend's pure helpers (static/lib.js).
   Run with:  node --test tests/frontend/
   No npm dependencies — Node's built-in test runner only. */

import test from "node:test";
import assert from "node:assert/strict";
import { createRequire } from "node:module";
import { fileURLToPath } from "node:url";
import path from "node:path";

const require = createRequire(import.meta.url);
const here = path.dirname(fileURLToPath(import.meta.url));
const lib = require(path.join(here, "..", "..", "static", "lib.js"));

/* ---------- parseNumInput ---------- */

test("parseNumInput accepts plain, suffixed and decorated numbers", () => {
  assert.equal(lib.parseNumInput("10B"), 10e9);
  assert.equal(lib.parseNumInput("$500m"), 5e8);
  assert.equal(lib.parseNumInput("1.5t"), 1.5e12);
  assert.equal(lib.parseNumInput(" 5 k "), 5000);
  assert.equal(lib.parseNumInput("3,000"), 3000);
  assert.equal(lib.parseNumInput("10%"), 10);
  assert.equal(lib.parseNumInput("-2.5"), -2.5);
  assert.equal(lib.parseNumInput("-0.5b"), -5e8);
  assert.equal(lib.parseNumInput(".5"), 0.5);
});

test("parseNumInput rejects junk rather than coercing it", () => {
  for (const bad of ["abc", "", "--5", "5x", "1e9", "10 20", "B", "$"]) {
    assert.equal(lib.parseNumInput(bad), null, `expected null for ${JSON.stringify(bad)}`);
  }
});

/* ---------- persisted-state loaders (corrupt storage must never throw) ---------- */

const storeFrom = (map) => (key) => (key in map ? map[key] : null);

test("loadCollapsed survives corrupted storage instead of blanking the app", () => {
  // Regression: this parse was unguarded and ran at module load, so one bad
  // value threw before any render and left a permanently blank page.
  assert.deepEqual(lib.loadCollapsed(storeFrom({ "mt.collapsed": "{not json" })), []);
  assert.deepEqual(lib.loadCollapsed(storeFrom({ "mt.collapsed": "null" })), []);
  assert.deepEqual(lib.loadCollapsed(storeFrom({ "mt.collapsed": '{"a":1}' })), []);
  assert.deepEqual(lib.loadCollapsed(storeFrom({})), []);
  assert.deepEqual(
    lib.loadCollapsed(storeFrom({ "mt.collapsed": '["Biotech","Cannabis"]' })),
    ["Biotech", "Cannabis"],
  );
  // Non-string entries are dropped — sector names are strings.
  assert.deepEqual(lib.loadCollapsed(storeFrom({ "mt.collapsed": '["Biotech",7,null]' })), ["Biotech"]);
});

test("loadCols returns a map, migrates the legacy list, and tolerates junk", () => {
  assert.deepEqual(lib.loadCols(storeFrom({ "mt.cols": '{"green_days":false}' })),
    { green_days: false });
  assert.deepEqual(lib.loadCols(storeFrom({ "mt.hiddenCols": '["red_days"]' })),
    { red_days: false });
  assert.deepEqual(lib.loadCols(storeFrom({ "mt.cols": "%%%" })), {});
  assert.deepEqual(lib.loadCols(storeFrom({})), {});
});

test("loadFilters keeps only well-formed filters", () => {
  const raw = JSON.stringify([
    { key: "market_cap", op: "gte", value: 1e10 },   // keep
    { key: "pct", op: "lte", value: 0 },             // keep
    { key: "pct", op: "between", value: 1 },         // bad op
    { key: 7, op: "gte", value: 1 },                 // bad key
    { key: "pct", op: "gte", value: "5" },           // bad value type
    { key: "pct", op: "gte", value: Infinity },      // non-finite
    null,
  ]);
  assert.deepEqual(lib.loadFilters(storeFrom({ "mt.filters": raw })), [
    { key: "market_cap", op: "gte", value: 1e10 },
    { key: "pct", op: "lte", value: 0 },
  ]);
  assert.deepEqual(lib.loadFilters(storeFrom({ "mt.filters": "[[[" })), []);
  assert.deepEqual(lib.loadFilters(storeFrom({ "mt.filters": '{"a":1}' })), []);
});

/* ---------- filtering ---------- */

const DEFS = {
  market_cap: { key: "market_cap", type: "mcap", label: "Mkt Cap" },
  pct: { key: "pct", type: "pct", label: "3M %" },
  "m:green_days": { key: "m:green_days", type: "int", label: "Green Days", metric: "green_days" },
};
const ROWS = [
  { ticker: "MSFT", market_cap: 2.88e12, pct: -3.47, metrics: { green_days: 27 } },
  { ticker: "MP", market_cap: 1.02e10, pct: -4.6, metrics: { green_days: 32 } },
  { ticker: "IKT", market_cap: 4e7, pct: 12.5, metrics: { green_days: 40 } },
  { ticker: "NULLCAP", market_cap: null, pct: 1.0, metrics: {} },
  { ticker: "BAD", error: "No price data", metrics: {} },
];
const tickers = (rows) => rows.map((r) => r.ticker);

test("applyFilters with no filters is a passthrough", () => {
  assert.equal(lib.applyFilters(ROWS, [], DEFS), ROWS);
});

test("applyFilters honors gte/lte and stacks conditions", () => {
  assert.deepEqual(
    tickers(lib.applyFilters(ROWS, [{ key: "market_cap", op: "gte", value: 1e10 }], DEFS)),
    ["MSFT", "MP"],
  );
  assert.deepEqual(
    tickers(lib.applyFilters(ROWS, [
      { key: "market_cap", op: "gte", value: 1e10 },
      { key: "pct", op: "lte", value: -4 },
    ], DEFS)),
    ["MP"],
  );
});

test("applyFilters drops rows whose filtered value is null or an error row", () => {
  const out = lib.applyFilters(ROWS, [{ key: "market_cap", op: "gte", value: 0 }], DEFS);
  assert.ok(!tickers(out).includes("NULLCAP"), "null market cap must not pass a numeric filter");
  assert.ok(!tickers(out).includes("BAD"), "error rows have no values to compare");
});

test("applyFilters reads metric columns through row.metrics", () => {
  assert.deepEqual(
    tickers(lib.applyFilters(ROWS, [{ key: "m:green_days", op: "gte", value: 32 }], DEFS)),
    ["MP", "IKT"],
  );
});

test("applyFilters ignores a filter whose column no longer exists", () => {
  // The chip stays removable in the UI; it must not empty the table.
  const out = lib.applyFilters(ROWS, [{ key: "rs:gone:avg", op: "gte", value: 1 }], DEFS);
  assert.equal(out.length, ROWS.length);
});

/* ---------- sorting ---------- */

test("sortRows sinks nulls in both directions", () => {
  const col = DEFS.market_cap;
  assert.deepEqual(tickers(lib.sortRows(ROWS, col, -1)).slice(0, 3), ["MSFT", "MP", "IKT"]);
  assert.deepEqual(tickers(lib.sortRows(ROWS, col, 1)).slice(0, 3), ["IKT", "MP", "MSFT"]);
  // Rows without a value land last no matter the direction.
  for (const dir of [1, -1]) {
    const tail = tickers(lib.sortRows(ROWS, col, dir)).slice(-2);
    assert.deepEqual(tail.sort(), ["BAD", "NULLCAP"]);
  }
});

test("sortRows compares strings lexically and does not mutate the input", () => {
  const col = { key: "ticker", type: "stock" };
  const before = tickers(ROWS);
  // BAD is an error row — cellValue returns null for every column, so it sinks
  // even here, where its ticker string would otherwise sort first.
  assert.deepEqual(tickers(lib.sortRows(ROWS, col, 1)), ["IKT", "MP", "MSFT", "NULLCAP", "BAD"]);
  assert.deepEqual(tickers(ROWS), before, "sortRows must return a new array");
});

/* ---------- formatting & escaping ---------- */

test("escapeHtml neutralizes every HTML-significant character", () => {
  assert.equal(lib.escapeHtml(`<script>alert("x")&'`),
    "&lt;script&gt;alert(&quot;x&quot;)&amp;&#39;");
});

test("renderMarkdown escapes input before applying its formatting whitelist", () => {
  const html = lib.renderMarkdown("<script>alert(1)</script>");
  assert.ok(!html.includes("<script>"), "raw script tag must not survive");
  assert.ok(html.includes("&lt;script&gt;"));
});

test("renderMarkdown renders the supported subset", () => {
  assert.ok(lib.renderMarkdown("## Overview").includes("<h3>Overview</h3>"));
  assert.ok(lib.renderMarkdown("### Risks").includes("<h4>Risks</h4>"));
  const list = lib.renderMarkdown("- one\n- two");
  assert.ok(list.includes("<ul>") && list.includes("<li>one</li>") && list.includes("</ul>"));
  assert.ok(lib.renderMarkdown("**bold**").includes("<strong>bold</strong>"));
  assert.ok(lib.renderMarkdown("*it*").includes("<em>it</em>"));
  assert.ok(lib.renderMarkdown("[[NVDA]]").includes('<span class="wikilink">NVDA</span>'));
});

test("fmtBig scales into K/M/B/T and passes small numbers through", () => {
  assert.equal(lib.fmtBig(2.88e12), "2.88T");
  assert.equal(lib.fmtBig(1.02e10), "10.20B");
  assert.equal(lib.fmtBig(4e7), "40.0M");
  assert.equal(lib.fmtBig(4500), "4.5K");
  assert.equal(lib.fmtBig(42), "42");
  assert.equal(lib.fmtBig(null), "—");
  assert.equal(lib.fmtBig(Infinity), "—");
});

test("fmtPrice gives sub-dollar tickers extra precision", () => {
  assert.equal(lib.fmtPrice(null), "—");
  assert.ok(lib.fmtPrice(123.456).startsWith("$123.46"));
  assert.ok(lib.fmtPrice(0.1234).startsWith("$0.1234"));
});

test("relDate buckets by whole days from a fixed now", () => {
  const now = Date.parse("2026-06-12T12:00:00Z");
  assert.equal(lib.relDate("2026-06-12T09:00:00Z", now), "today");
  assert.equal(lib.relDate("2026-06-11T09:00:00Z", now), "1d ago");
  assert.equal(lib.relDate("2026-06-09T09:00:00Z", now), "3d ago");
  assert.equal(lib.relDate(null, now), "");
  assert.equal(lib.relDate("not-a-date", now), "");
});

/* ---------- alert rules ---------- */

test("describeRule restates the condition without interpreting it", () => {
  const rule = { ticker: "AAOI", period: "1M", metric_key: "m:red_days",
                 op: "gte", value: 12 };
  assert.equal(lib.describeRule(rule, "Red Days"), "AAOI · Red Days (1M) ≥ 12");
});

test("describeRule labels a ticker-less rule as a screener", () => {
  const rule = { ticker: null, period: "3M", metric_key: "m:streak",
                 op: "lte", value: -3 };
  assert.equal(lib.describeRule(rule, "Streak"), "Any stock · Streak (3M) ≤ -3");
});

test("describeRule falls back to the raw key when no label is known", () => {
  const rule = { ticker: "NVDA", period: "1D", metric_key: "m:mystery",
                 op: "gte", value: 1 };
  assert.ok(lib.describeRule(rule, null).includes("m:mystery"));
});

test("composeRuleName builds a default name from the rule's parts", () => {
  const rule = { ticker: "NVDA", metric_key: "m:green_days", op: "gte", value: 5 };
  assert.equal(lib.composeRuleName(rule, "Green Days"), "NVDA: Green Days ≥ 5");
  assert.equal(lib.composeRuleName({ ...rule, ticker: null }, "Green Days"),
               "Any stock: Green Days ≥ 5");
});

test("ruleState separates met, unmet, unavailable and paused", () => {
  const base = { enabled: true, matches: [], unavailable: [] };
  assert.equal(lib.ruleState({ ...base, matches: [{ ticker: "NVDA", observed: 3 }] }), "met");
  assert.equal(lib.ruleState(base), "unmet");
  assert.equal(lib.ruleState({ ...base, unavailable: ["MSFT"] }), "unavailable");
  assert.equal(lib.ruleState({ ...base, enabled: false }), "paused");
});

test("a rule that matched somewhere reads as met even if another stock had no reading", () => {
  /* Otherwise one unreadable stock would mask a condition that genuinely
     holds elsewhere. */
  const rule = { enabled: true, matches: [{ ticker: "NVDA", observed: 4 }],
                 unavailable: ["MSFT"] };
  assert.equal(lib.ruleState(rule), "met");
});

test("a paused rule reports paused regardless of its stored matches", () => {
  const rule = { enabled: false, matches: [{ ticker: "NVDA", observed: 4 }],
                 unavailable: [] };
  assert.equal(lib.ruleState(rule), "paused");
});

/* ---------- pattern statistics ---------- */

test("ruleWindowLabel names the window each kind of statistic covers", () => {
  assert.equal(lib.ruleWindowLabel({ metric_key: "s:wd:mon:green_rate", period: "3M" }), "2Y history");
  assert.equal(lib.ruleWindowLabel({ metric_key: "z:pc_close", period: "3M" }), "latest session");
  assert.equal(lib.ruleWindowLabel({ metric_key: "m:green_days", period: "1M" }), "1M");
});

test("describeRule shows the pattern window instead of an ignored timeframe", () => {
  const rule = { ticker: null, period: "3M", metric_key: "s:wd:today:green_rate", op: "gte", value: 60 };
  assert.equal(lib.describeRule(rule, "Session's weekday — Green days"),
               "Any stock · Session's weekday — Green days (2Y history) ≥ 60");
});

test("patternCellClass highlights only what clears the noise band", () => {
  assert.equal(lib.patternCellClass({ value: 62, n: 99, z: 3.1, stands_out: true, thin: false }), "pt-out");
  assert.equal(lib.patternCellClass({ value: 55, n: 99, z: 0.4, stands_out: false, thin: false }), "");
  /* thin wins even over a stands-out flag: too few days to compare at all */
  assert.equal(lib.patternCellClass({ value: 100, n: 1, z: 3.0, stands_out: true, thin: true }), "pt-thin");
  assert.equal(lib.patternCellClass({ value: null, n: 0, z: null, stands_out: false, thin: true }), "pt-empty");
  assert.equal(lib.patternCellClass(null), "pt-empty");
});

test("patternCellTitle states the sample size and how to read the value", () => {
  assert.match(lib.patternCellTitle({ value: 62, n: 99, z: 3.1, stands_out: true, thin: false }),
               /^99 days — differs .* \(z \+3\.1\)$/);
  assert.equal(lib.patternCellTitle({ value: 55, n: 98, z: -0.4, stands_out: false, thin: false }),
               "98 days — within normal variation of the other days");
  assert.equal(lib.patternCellTitle({ value: 100, n: 1, z: null, stands_out: false, thin: true }),
               "1 day — too few to compare");
  assert.equal(lib.patternCellTitle({ value: 33, n: 139, z: null, stands_out: false, thin: false }),
               "139 days");
});

test("fmtPatternValue prints rates with one decimal and averages with two", () => {
  assert.equal(lib.fmtPatternValue(59.375, "rate"), "59.4%");
  assert.equal(lib.fmtPatternValue(-0.8, "plain"), "-0.80%");
  assert.equal(lib.fmtPatternValue(null, "rate"), "—");
});

test("bucketRange closes the last half hour at 16:00", () => {
  const labels = ["09:30", "10:00", "15:30"];
  assert.equal(lib.bucketRange(labels, 0), "09:30–10:00");
  assert.equal(lib.bucketRange(labels, 2), "15:30–16:00");
});

test("heatAlpha scales to the grid's max and never passes the contrast cap", () => {
  assert.equal(lib.heatAlpha(50, 50), 0.6);
  assert.equal(lib.heatAlpha(25, 50), 0.3);
  assert.equal(lib.heatAlpha(80, 50), 0.6, "clamped — a stray value can't wash out the text");
  assert.equal(lib.heatAlpha(0, 50), 0);
  assert.equal(lib.heatAlpha(10, 0), 0, "an all-zero grid stays unshaded");
  assert.equal(lib.heatAlpha(null, 50), 0);
});

test("chanceExpected matches the normal tail at known thresholds", () => {
  /* two-sided: |z| >= 1.96 -> 5%; |z| >= 2.6 -> 0.932% */
  assert.ok(Math.abs(lib.chanceExpected(100, 1.96) - 5.0) < 0.01);
  assert.ok(Math.abs(lib.chanceExpected(185, 2.6) - 1.725) < 0.01);
  assert.equal(lib.chanceExpected(0, 2.6), 0);
});
