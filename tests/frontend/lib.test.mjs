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
