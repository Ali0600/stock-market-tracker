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

/* Node test harness only — browsers ignore this. */
if (typeof module !== "undefined" && module.exports) {
  module.exports = {
    escapeHtml, fmtPrice, fmtBig, relDate, renderMarkdown,
    loadCols, loadFilters, loadCollapsed,
    FILTERABLE_TYPES, parseNumInput, cellValue, applyFilters,
    compareRows, sortRows,
  };
}
