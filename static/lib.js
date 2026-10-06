/* Pure helpers shared by the frontend — no DOM, no fetch, no globals touched.
   Kept in its own file so the behavior can be unit-tested under `node --test`
   (see tests/frontend/). app.js loads this first and calls these unqualified. */

/* ---------- formatting ---------- */

function escapeHtml(s) {
  return String(s).replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[c]));
}

function fmtPrice(v) {
  if (v == null || !isFinite(v)) return "—";
  const digits = Math.abs(v) < 1 ? 4 : 2;
  return "$" + v.toLocaleString(undefined, {
    minimumFractionDigits: 2, maximumFractionDigits: digits,
  });
}

function fmtBig(v) {
  if (v == null || !isFinite(v)) return "—";
  const a = Math.abs(v);
  if (a >= 1e12) return (v / 1e12).toFixed(2) + "T";
  if (a >= 1e9) return (v / 1e9).toFixed(2) + "B";
  if (a >= 1e6) return (v / 1e6).toFixed(1) + "M";
  if (a >= 1e3) return (v / 1e3).toFixed(1) + "K";
  return String(v);
}

function relDate(iso, now = Date.now()) {
  if (!iso) return "";
  const days = Math.floor((now - new Date(iso).getTime()) / 86400000);
  if (isNaN(days)) return "";
  return days <= 0 ? "today" : days === 1 ? "1d ago" : `${days}d ago`;
}

// Minimal, safe markdown: escape everything first, then re-introduce a small
// whitelist of formatting (##/###, bullet lists, **bold**, *italic*).
function renderMarkdown(md) {
  const lines = escapeHtml(md).split(/\r?\n/);
  let html = "", inList = false;
  let para = [];
  const flushPara = () => {
    if (para.length) { html += `<p>${para.join(" ")}</p>`; para = []; }
  };
  const closeList = () => { if (inList) { html += "</ul>"; inList = false; } };
  for (const raw of lines) {
    const line = raw.trim();
    if (!line) { flushPara(); closeList(); continue; }
    if (line.startsWith("### ")) { flushPara(); closeList(); html += `<h4>${line.slice(4)}</h4>`; continue; }
    if (line.startsWith("## ")) { flushPara(); closeList(); html += `<h3>${line.slice(3)}</h3>`; continue; }
    if (line.startsWith("# ")) { flushPara(); closeList(); html += `<h3>${line.slice(2)}</h3>`; continue; }
    if (line.startsWith("- ")) {
      flushPara();
      if (!inList) { html += "<ul>"; inList = true; }
      html += `<li>${line.slice(2)}</li>`;
      continue;
    }
    closeList();
    para.push(line);
  }
  flushPara(); closeList();
  return html
    .replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>")
    .replace(/\*([^*]+)\*/g, "<em>$1</em>")
    .replace(/\[\[([^\]]+)\]\]/g, '<span class="wikilink">$1</span>');
}

/* ---------- persisted view state ----------
   Every loader is total: corrupted or hostile localStorage must yield a safe
   default, never throw. These run at module load, so one bad key would
   otherwise leave the page permanently blank. */

function loadCols(read) {
  // {key: bool}; keys not present fall back to each column def's default_on.
  try {
    const saved = JSON.parse(read("mt.cols") || "null");
    if (saved && typeof saved === "object" && !Array.isArray(saved)) return saved;
    // Migrate the legacy hidden-keys list (old default-on model).
    const legacy = JSON.parse(read("mt.hiddenCols") || "null");
    if (Array.isArray(legacy)) {
      const map = {};
      legacy.forEach((k) => { map[k] = false; });
      return map;
    }
  } catch { /* corrupted storage — start fresh */ }
  return {};
}

function loadFilters(read) {
  // [{key, op: "gte"|"lte", value}] — numeric threshold filters for the All view.
  try {
    const saved = JSON.parse(read("mt.filters") || "null");
    if (Array.isArray(saved)) {
      return saved.filter((f) => f && typeof f.key === "string" &&
        (f.op === "gte" || f.op === "lte") &&
        typeof f.value === "number" && isFinite(f.value));
    }
  } catch { /* corrupted storage — start fresh */ }
  return [];
}

function loadCollapsed(read) {
  // Sector names the user has collapsed.
  try {
    const saved = JSON.parse(read("mt.collapsed") || "[]");
    if (Array.isArray(saved)) return saved.filter((n) => typeof n === "string");
  } catch { /* corrupted storage — start fresh */ }
  return [];
}

/* ---------- filtering & sorting ---------- */

const FILTERABLE_TYPES = new Set(["mcap", "price", "pct", "pct_abs", "int"]);

// "10B", "$500m", "-2.5", "3,000" → number; null if unparseable.
// Only currency/grouping decoration is stripped. Internal whitespace is NOT
// removed: "10 20" must be rejected, not silently read as 1020. A single gap
// before the magnitude suffix ("5 k") is still allowed.
function parseNumInput(s) {
  const m = String(s).trim().replace(/[$,%]/g, "").match(/^(-?\d*\.?\d+)\s*([kmbt])?$/i);
  if (!m) return null;
  const mult = { k: 1e3, m: 1e6, b: 1e9, t: 1e12 }[(m[2] || "").toLowerCase()] || 1;
  return Number(m[1]) * mult;
}

// Value of one column for one row. Metric columns live under row.metrics.
function cellValue(row, col) {
  if (row.error) return null;
  return col.metric ? (row.metrics ? row.metrics[col.metric] : undefined) : row[col.key];
}

// `defs` maps filter key -> column def. A filter whose column no longer exists
// is ignored rather than emptying the table; its chip stays removable.
function applyFilters(rows, filters, defs) {
  if (!filters.length) return rows;
  return rows.filter((r) => filters.every((f) => {
    const def = defs[f.key];
    if (!def) return true;
    const v = cellValue(r, def);
    if (typeof v !== "number" || !isFinite(v)) return false;
    return f.op === "gte" ? v >= f.value : v <= f.value;
  }));
}

// Nulls sink to the bottom regardless of sort direction.
function compareRows(a, b, col, dir) {
  const va = cellValue(a, col), vb = cellValue(b, col);
  if (va == null && vb == null) return 0;
  if (va == null) return 1;
  if (vb == null) return -1;
  const cmp = typeof va === "string" ? va.localeCompare(vb) : va - vb;
  return cmp * dir;
}

function sortRows(rows, col, dir) {
  return [...rows].sort((a, b) => compareRows(a, b, col, dir));
}

/* ---------- alert rules ---------- */

const OP_SYMBOL = { gte: "≥", lte: "≤" };

/* Plain-language summary of a rule: what is being watched, over what window,
   and the threshold the owner set. Purely descriptive — it restates the
   condition, never what a met condition might mean. */
/* The window a rule's statistic covers. Pattern (s:) statistics always use
   two years of completed sessions and σ Today (z:) statistics the latest
   session, so the rule's own timeframe applies to neither. */
function ruleWindowLabel(rule) {
  const key = rule.metric_key || "";
  if (key.startsWith("s:")) return "2Y history";
  if (key.startsWith("z:")) return "latest session";
  return rule.period;
}

function describeRule(rule, statLabel) {
  const who = rule.ticker || "Any stock";
  const label = statLabel || rule.metric_key;
  const op = OP_SYMBOL[rule.op] || rule.op;
  return `${who} · ${label} (${ruleWindowLabel(rule)}) ${op} ${rule.value}`;
}

/* Default name for a new rule, so the owner gets a sensible label without
   having to invent one. */
function composeRuleName(rule, statLabel) {
  const who = rule.ticker || "Any stock";
  const label = statLabel || rule.metric_key;
  return `${who}: ${label} ${OP_SYMBOL[rule.op] || rule.op} ${rule.value}`;
}

/* A rule's current state, as one of three distinct outcomes. "unavailable"
   exists so a stat that could not be computed is never rendered as a
   confident "not met". */
function ruleState(rule) {
  if (!rule.enabled) return "paused";
  if (rule.matches && rule.matches.length) return "met";
  if (rule.unavailable && rule.unavailable.length &&
      !(rule.matches && rule.matches.length)) return "unavailable";
  return "unmet";
}

/* ---------- pattern statistics ---------- */

/* A cell is highlighted only when it clears the noise band, and dimmed when
   it rests on too few days to compare at all. */
function patternCellClass(cell) {
  if (!cell || cell.value == null) return "pt-empty";
  if (cell.thin) return "pt-thin";
  return cell.stands_out ? "pt-out" : "";
}

function patternCellTitle(cell) {
  if (!cell || cell.value == null) return "No days in this group";
  const days = `${cell.n} day${cell.n === 1 ? "" : "s"}`;
  if (cell.thin) return `${days} — too few to compare`;
  if (cell.stands_out) {
    const z = `${cell.z > 0 ? "+" : ""}${cell.z}`;
    return `${days} — differs from this stock's other days by more than normal variation (z ${z})`;
  }
  if (cell.z == null) return days;
  return `${days} — within normal variation of the other days`;
}

function fmtPatternValue(value, fmt) {
  if (value == null || !isFinite(value)) return "—";
  if (fmt === "rate") return `${value.toFixed(1)}%`;
  return `${value.toFixed(2)}%`;
}

/* "09:30–10:00" for bucket i; the last bucket runs to the 16:00 close. */
function bucketRange(labels, i) {
  return `${labels[i]}–${i + 1 < labels.length ? labels[i + 1] : "16:00"}`;
}

/* Background strength of a sequential heat cell. Capped at 0.6: measured,
   primary text keeps 5.3:1 contrast on the darkest cell (dim text would drop
   to 2.1:1, so heat cells never use it). */
function heatAlpha(value, max) {
  if (value == null || !(max > 0)) return 0;
  return Math.round(Math.min(1, Math.max(0, value / max)) * 0.6 * 100) / 100;
}

/* How many cells pure chance would flag at a two-sided |z| threshold — the
   number to read the real count against. Normal tail via Abramowitz-Stegun
   7.1.26 (error < 1.5e-7). */
function chanceExpected(tested, zThreshold) {
  const x = zThreshold / Math.SQRT2;
  const t = 1 / (1 + 0.3275911 * x);
  const erfc = t * (0.254829592 + t * (-0.284496736 + t * (1.421413741
    + t * (-1.453152027 + t * 1.061405429)))) * Math.exp(-x * x);
  return tested * erfc;
}

/* Node test harness only — browsers ignore this. */
if (typeof module !== "undefined" && module.exports) {
  module.exports = {
    escapeHtml, fmtPrice, fmtBig, relDate, renderMarkdown,
    loadCols, loadFilters, loadCollapsed,
    FILTERABLE_TYPES, parseNumInput, cellValue, applyFilters,
    compareRows, sortRows,
    OP_SYMBOL, describeRule, composeRuleName, ruleState, ruleWindowLabel,
    patternCellClass, patternCellTitle, fmtPatternValue, bucketRange, heatAlpha,
    chanceExpected,
  };
}
