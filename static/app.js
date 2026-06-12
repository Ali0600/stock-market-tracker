/* Market Tracker frontend — renders the overview, handles period switching,
   the Things-to-Track column picker, and stock/sector management. */

const PERIODS = ["1D", "5D", "1M", "3M", "YTD", "1Y"];

// Period-aware labels for the core columns.
const PERIOD_LABELS = {
  "1D": { ago: "Prev Close", pct: "1D %", high: "Day High", low: "Day Low" },
  "5D": { ago: "5D Ago", pct: "5D %", high: "5D High", low: "5D Low" },
  "1M": { ago: "1M Ago", pct: "1M %", high: "1M High", low: "1M Low" },
  "3M": { ago: "3M Ago", pct: "3M %", high: "3M High", low: "3M Low" },
  "YTD": { ago: "Year Start", pct: "YTD %", high: "YTD High", low: "YTD Low" },
  "1Y": { ago: "1Y Ago", pct: "1Y %", high: "1Y High", low: "1Y Low" },
};

function loadCols() {
  // {key: bool}; keys not present fall back to each column def's default_on.
  try {
    const saved = JSON.parse(localStorage.getItem("mt.cols") || "null");
    if (saved && typeof saved === "object" && !Array.isArray(saved)) return saved;
    // Migrate the legacy hidden-keys list (old default-on model).
    const legacy = JSON.parse(localStorage.getItem("mt.hiddenCols") || "null");
    if (Array.isArray(legacy)) {
      const map = {};
      legacy.forEach((k) => { map[k] = false; });
      return map;
    }
  } catch { /* corrupted storage — start fresh */ }
  return {};
}

function loadFilters() {
  // [{key, op: "gte"|"lte", value}] — numeric threshold filters for the All view.
  try {
    const saved = JSON.parse(localStorage.getItem("mt.filters") || "null");
    if (Array.isArray(saved)) {
      return saved.filter((f) => f && typeof f.key === "string" &&
        (f.op === "gte" || f.op === "lte") &&
        typeof f.value === "number" && isFinite(f.value));
    }
  } catch { /* corrupted storage — start fresh */ }
  return [];
}

const state = {
  period: localStorage.getItem("mt.period") || "3M",
  grouping: localStorage.getItem("mt.grouping") === "all" ? "all" : "sector",
  cols: loadCols(),
  filters: loadFilters(),
  collapsed: new Set(JSON.parse(localStorage.getItem("mt.collapsed") || "[]")),
  sort: { key: "ticker", dir: 1 },
  data: null,
};

function colVisible(def) {
  const explicit = state.cols[def.key];
  return explicit !== undefined ? explicit : def.default_on !== false;
}
if (!PERIODS.includes(state.period)) state.period = "3M";

// Optional ?period=1D in the URL overrides the saved selection (bookmarkable views).
const urlPeriod = (new URLSearchParams(location.search).get("period") || "").toUpperCase();
if (PERIODS.includes(urlPeriod)) state.period = urlPeriod;

// Optional ?group=all|sector overrides the saved grouping the same way.
const urlGroup = (new URLSearchParams(location.search).get("group") || "").toLowerCase();
if (urlGroup === "all" || urlGroup === "sector") state.grouping = urlGroup;

const $ = (id) => document.getElementById(id);
const tableWrap = document.querySelector(".table-wrap");

/* ---------- formatting ---------- */

function fmtPrice(v) {
  if (v == null || !isFinite(v)) return "—";
  const digits = Math.abs(v) < 1 ? 4 : 2;
  return "$" + v.toLocaleString(undefined, {
    minimumFractionDigits: 2, maximumFractionDigits: digits,
  });
}

function pctChip(v) {
  if (v == null || !isFinite(v)) return '<span class="muted">—</span>';
  const cls = v > 0.005 ? "up" : v < -0.005 ? "down" : "flat";
  const sign = v > 0 ? "+" : "";
  return `<span class="pct ${cls}">${sign}${v.toFixed(2)}%</span>`;
}

function fmtMetric(v, fmt) {
  if (v == null) return '<span class="muted">—</span>';
  if (fmt === "int") return String(v);
  return escapeHtml(String(v));
}

function escapeHtml(s) {
  return s.replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[c]));
}

/* ---------- data ---------- */

async function load(refresh = false) {
  tableWrap.classList.add("loading");
  document.body.classList.add("loading");
  $("refreshBtn").disabled = true;
  try {
    const res = await fetch(`/api/overview?period=${state.period}&refresh=${refresh}`);
    if (!res.ok) throw new Error((await res.json()).detail || res.statusText);
    state.data = await res.json();
    render();
  } catch (err) {
    showBanner(`Failed to load data: ${err.message}`);
  } finally {
    tableWrap.classList.remove("loading");
    document.body.classList.remove("loading");
    $("refreshBtn").disabled = false;
  }
}

function showBanner(msg) {
  const el = $("banner");
  el.textContent = msg;
  el.classList.toggle("hidden", !msg);
}

/* ---------- rendering ---------- */

function render() {
  const d = state.data;
  if (!d) return;

  const asOf = new Date(d.as_of);
  $("asOf").textContent = `as of ${asOf.toLocaleString([], {
    month: "short", day: "numeric", hour: "numeric", minute: "2-digit",
  })}`;
  $("staleBadge").classList.toggle("hidden", !d.stale);
  showBanner(d.fetch_error && d.stale
    ? `Yahoo Finance fetch failed — showing cached data from ${asOf.toLocaleTimeString()}`
    : d.fetch_error ? `Yahoo Finance fetch failed: ${d.fetch_error}` : "");

  // The table also lists empty sectors (so they can be deleted); the empty
  // state only shows when nothing exists at all.
  const hasContent = d.sectors.length > 0;
  const hasStocks = d.sectors.some((s) => s.stocks.length);
  $("emptyState").classList.toggle("hidden", hasContent);
  tableWrap.classList.toggle("hidden", !hasContent);
  $("summary").classList.toggle("hidden", !hasStocks);

  renderSummary();
  renderColsMenu();
  if (hasContent) renderTable();
}

function renderSummary() {
  const d = state.data;
  const allPcts = d.sectors.flatMap((s) => s.stocks.map((r) => r.pct).filter((p) => p != null));
  const marketAvg = allPcts.length
    ? allPcts.reduce((a, b) => a + b, 0) / allPcts.length : null;

  const card = (name, pct, count, extra = "", attrs = "") => `
    <div class="card ${extra}" ${attrs}>
      <div class="card-name">${escapeHtml(name)}</div>
      <div class="card-pct">${pctChip(pct)}</div>
      <div class="card-count">${count} stock${count === 1 ? "" : "s"} · ${state.period}</div>
    </div>`;

  $("summary").innerHTML =
    card("Market", marketAvg == null ? null : Math.round(marketAvg * 100) / 100,
         d.sectors.reduce((n, s) => n + s.stocks.length, 0), "market") +
    d.sectors.filter((s) => s.stocks.length)
      .map((s) => card(s.name, s.avg_pct, s.stocks.length, "sector-card",
                       `data-sector-id="${s.id}" title="Open the ${escapeHtml(s.name)} vault note"`))
      .join("");
}

function columnUniverse() {
  // Every column that can exist for the current period, ignoring visibility.
  // Filters resolve against this so they keep working when a column is
  // toggled off in the picker.
  const L = PERIOD_LABELS[state.period];
  const core = [
    { key: "ticker", label: "Stock", group: "", type: "stock" },
    { key: "sector", label: "Sector", group: "", type: "sector", allOnly: true },
    { key: "market_cap", label: "Mkt Cap", group: "Price", type: "mcap",
      title: "Live price × cached shares outstanding (re-synced when you open the stock's detail view)" },
    { key: "current", label: "Current", group: "Price", type: "price" },
    { key: "ago", label: L.ago, group: "perf", type: "price" },
    { key: "pct", label: L.pct, group: "perf", type: "pct" },
    { key: "high", label: L.high, group: "perf", type: "price" },
    { key: "high_pct", label: "Off High", group: "perf", type: "pct",
      title: "Current price vs the period high" },
    { key: "low", label: L.low, group: "perf", type: "price" },
    { key: "low_pct", label: "Off Low", group: "perf", type: "pct",
      title: "Current price vs the period low" },
  ];
  const extras = (state.data.metric_defs || []).map((m) => ({
    key: "m:" + m.key, label: m.label, group: "Things to Track",
    type: m.fmt, metric: m.key, title: m.description, def: m,
  }));
  return [...core, ...extras];
}

function visibleColumns() {
  return columnUniverse().filter((c) =>
    (!c.allOnly || state.grouping === "all") && (!c.def || colVisible(c.def)));
}

function cellValue(row, col) {
  if (row.error) return null;
  return col.metric ? row.metrics?.[col.metric] : row[col.key];
}

/* ---------- numeric filters (All view) ---------- */

const FILTERABLE_TYPES = new Set(["mcap", "price", "pct", "pct_abs", "int"]);

// "10B", "$500m", "-2.5", "3,000" → number; null if unparseable.
function parseNumInput(s) {
  const m = String(s).trim().replace(/[$,%\s]/g, "").match(/^(-?\d*\.?\d+)([kmbt])?$/i);
  if (!m) return null;
  const mult = { k: 1e3, m: 1e6, b: 1e9, t: 1e12 }[(m[2] || "").toLowerCase()] || 1;
  return Number(m[1]) * mult;
}

function filterableColumns() {
  return columnUniverse().filter((c) => FILTERABLE_TYPES.has(c.type));
}

function saveFilters() {
  localStorage.setItem("mt.filters", JSON.stringify(state.filters));
}

function applyFilters(rows) {
  if (!state.filters.length) return rows;
  const defs = Object.fromEntries(filterableColumns().map((c) => [c.key, c]));
  return rows.filter((r) => state.filters.every((f) => {
    const def = defs[f.key];
    if (!def) return true; // metric no longer exists — ignore; chip stays removable
    const v = cellValue(r, def);
    if (typeof v !== "number" || !isFinite(v)) return false;
    return f.op === "gte" ? v >= f.value : v <= f.value;
  }));
}

function fmtFilterValue(f, def) {
  if (def?.type === "mcap") return "$" + fmtBig(f.value);
  if (def?.type === "price") return fmtPrice(f.value);
  if (def?.type === "pct" || def?.type === "pct_abs") return `${f.value}%`;
  return String(f.value);
}

function renderFilterChips(total, shown) {
  const inAll = state.grouping === "all";
  $("filterWrap").classList.toggle("hidden", !inAll);
  const wrap = $("filterChips");
  if (!inAll || !state.filters.length) {
    wrap.classList.add("hidden");
    wrap.innerHTML = "";
    return;
  }
  const defs = Object.fromEntries(filterableColumns().map((c) => [c.key, c]));
  const chips = state.filters.map((f, i) => {
    const def = defs[f.key];
    return `<span class="fchip">${escapeHtml(def ? def.label : f.key)}
      ${f.op === "gte" ? "≥" : "≤"} ${escapeHtml(fmtFilterValue(f, def))}
      <button class="fchip-x" data-fdel="${i}" title="Remove filter">✕</button></span>`;
  }).join("");
  wrap.innerHTML = chips +
    `<span class="fcount">showing ${shown} of ${total} stock${total === 1 ? "" : "s"}</span>`;
  wrap.classList.remove("hidden");
}

function renderTable() {
  const cols = visibleColumns();
  const perfLabel = `${state.period} Performance`;

  // Two-row header: group labels over column names.
  const groups = [];
  for (const c of cols) {
    const name = c.group === "perf" ? perfLabel : c.group;
    const last = groups[groups.length - 1];
    if (last && last.name === name) last.span++;
    else groups.push({ name, span: 1 });
  }
  const groupRow = groups.map((g) =>
    `<th colspan="${g.span}">${escapeHtml(g.name)}</th>`).join("") + "<th></th>";

  const colRow = cols.map((c) => {
    const sorted = state.sort.key === c.key;
    const arrow = sorted ? `<span class="arrow">${state.sort.dir > 0 ? "▲" : "▼"}</span>` : "";
    const cls = (c.type === "stock" ? "col-stock " : "") + "sortable";
    return `<th class="${cls}" data-sort="${c.key}" title="${escapeHtml(c.title || "")}">
              ${escapeHtml(c.label)} ${arrow}</th>`;
  }).join("") + "<th></th>";

  document.querySelector("#grid thead").innerHTML =
    `<tr class="group-row">${groupRow}</tr><tr class="col-row">${colRow}</tr>`;

  const tbody = document.querySelector("#grid tbody");

  if (state.grouping === "all") {
    // Flat cross-sector list: filter, then rank the whole portfolio.
    const all = state.data.sectors.flatMap((sec) =>
      sec.stocks.map((r) => ({ ...r, sector: sec.name })));
    const rows = sortRows(applyFilters(all), cols);
    tbody.innerHTML = rows.map((r) => stockRow(r, cols)).join("");
    renderFilterChips(all.length, rows.length);
    return;
  }

  renderFilterChips();
  tbody.innerHTML = state.data.sectors.map((sec) => {
    const collapsed = state.collapsed.has(sec.name);
    const delBtn = sec.stocks.length ? "" :
      `<button class="sec-del" data-id="${sec.id}" title="Delete this empty sector">✕ remove</button>`;
    const header = `
      <tr class="sector-row ${collapsed ? "collapsed" : ""}" data-sector="${escapeHtml(sec.name)}">
        <td colspan="${cols.length + 1}"><span class="sec-inner">
          <span class="chev">▾</span>${escapeHtml(sec.name)}
          ${sec.stocks.length ? pctChip(sec.avg_pct) : ""}
          <span class="sec-count">${sec.stocks.length} stock${sec.stocks.length === 1 ? "" : "s"}</span>
          ${delBtn}
        </span></td>
      </tr>`;
    if (collapsed || !sec.stocks.length) return header;

    const rows = sortRows(sec.stocks, cols);
    return header + rows.map((r) => stockRow(r, cols)).join("");
  }).join("");
}

function sortRows(rows, cols) {
  const col = cols.find((c) => c.key === state.sort.key) || cols[0];
  return [...rows].sort((a, b) => {
    const va = cellValue(a, col), vb = cellValue(b, col);
    if (va == null && vb == null) return 0;
    if (va == null) return 1;          // nulls sink regardless of direction
    if (vb == null) return -1;
    const cmp = typeof va === "string"
      ? va.localeCompare(vb) : va - vb;
    return cmp * state.sort.dir;
  });
}

function stockRow(r, cols) {
  const stockCell = `
    <td class="cell-stock clickable" data-ticker="${escapeHtml(r.ticker)}"
        title="Open ${escapeHtml(r.ticker)} details & AI analysis">
      <div class="tick">${escapeHtml(r.ticker)}</div>
      <div class="tick-name">${escapeHtml(r.name || "")}</div>
    </td>`;

  const actions = `
    <td class="cell-actions">
      <button class="iconbtn move" data-id="${r.id}" data-ticker="${escapeHtml(r.ticker)}"
              title="Move to another sector">⇄</button>
      <button class="iconbtn del" data-id="${r.id}" data-ticker="${escapeHtml(r.ticker)}"
              title="Stop tracking">✕</button>
    </td>`;

  if (r.error) {
    return `<tr class="stock-row error-row">${stockCell}
      <td class="cell-error" colspan="${cols.length - 1}">⚠ ${escapeHtml(r.error)}</td>
      ${actions}</tr>`;
  }

  const cells = cols.slice(1).map((c) => {
    const v = cellValue(r, c);
    if (c.type === "sector") {
      return `<td class="cell-sector">${escapeHtml(r.sector || "")}</td>`;
    }
    if (c.type === "mcap") {
      return `<td>${v == null ? '<span class="muted">—</span>' : "$" + fmtBig(v)}</td>`;
    }
    if (c.type === "pct_abs") {
      return `<td><span class="muted">${v == null ? "—" : v.toFixed(2) + "%"}</span></td>`;
    }
    if (c.type === "pct") return `<td>${pctChip(v)}</td>`;
    if (c.type === "price") {
      const partial = c.key === "ago" && r.partial_since
        ? `<sup class="partial" title="History starts ${r.partial_since} — younger than the selected period">*</sup>`
        : "";
      return `<td>${fmtPrice(v)}${partial}</td>`;
    }
    return `<td>${fmtMetric(v, c.type)}</td>`;
  }).join("");

  return `<tr class="stock-row">${stockCell}${cells}${actions}</tr>`;
}

function renderColsMenu() {
  let html = "", lastGroup = null;
  for (const m of state.data.metric_defs || []) {
    const group = m.group || "Things to Track";
    if (group !== lastGroup) {
      html += `<div class="menu-group">${escapeHtml(group)}</div>`;
      lastGroup = group;
    }
    html += `
      <label title="${escapeHtml(m.description || "")}">
        <input type="checkbox" data-col="${m.key}" ${colVisible(m) ? "checked" : ""}>
        ${escapeHtml(m.label)}
      </label>`;
  }
  $("colsMenu").innerHTML = html;
}

/* ---------- interactions ---------- */

document.querySelectorAll("#periodCtl button").forEach((btn) => {
  btn.classList.toggle("active", btn.dataset.period === state.period);
  btn.addEventListener("click", () => {
    state.period = btn.dataset.period;
    localStorage.setItem("mt.period", state.period);
    document.querySelectorAll("#periodCtl button").forEach((b) =>
      b.classList.toggle("active", b === btn));
    load();
  });
});

document.querySelectorAll("#groupCtl button").forEach((btn) => {
  btn.classList.toggle("active", btn.dataset.group === state.grouping);
  btn.addEventListener("click", () => {
    state.grouping = btn.dataset.group;
    localStorage.setItem("mt.grouping", state.grouping);
    document.querySelectorAll("#groupCtl button").forEach((b) =>
      b.classList.toggle("active", b === btn));
    $("filterMenu").classList.add("hidden");
    if (state.data) renderTable();
  });
});

$("refreshBtn").addEventListener("click", () => load(true));

$("colsBtn").addEventListener("click", (e) => {
  e.stopPropagation();
  $("filterMenu").classList.add("hidden");
  $("colsMenu").classList.toggle("hidden");
});
$("colsMenu").addEventListener("click", (e) => e.stopPropagation());
$("colsMenu").addEventListener("change", (e) => {
  const key = e.target.dataset.col;
  if (!key) return;
  state.cols[key] = e.target.checked;
  localStorage.setItem("mt.cols", JSON.stringify(state.cols));
  renderTable();
});
document.addEventListener("click", () => {
  $("colsMenu").classList.add("hidden");
  $("filterMenu").classList.add("hidden");
});

function renderFilterForm() {
  // Offer the currently visible numeric columns (labels are period-aware).
  $("fCol").innerHTML = visibleColumns()
    .filter((c) => FILTERABLE_TYPES.has(c.type))
    .map((c) => `<option value="${escapeHtml(c.key)}">${escapeHtml(c.label)}</option>`)
    .join("");
}

$("filterBtn").addEventListener("click", (e) => {
  e.stopPropagation();
  $("colsMenu").classList.add("hidden");
  const menu = $("filterMenu");
  if (menu.classList.contains("hidden")) renderFilterForm();
  menu.classList.toggle("hidden");
});
$("filterMenu").addEventListener("click", (e) => e.stopPropagation());

$("fAdd").addEventListener("click", () => {
  const value = parseNumInput($("fVal").value);
  if (value == null) {
    $("fVal").classList.add("invalid");
    $("fVal").focus();
    return;
  }
  $("fVal").classList.remove("invalid");
  state.filters.push({ key: $("fCol").value, op: $("fOp").value, value });
  saveFilters();
  $("fVal").value = "";
  $("filterMenu").classList.add("hidden");
  renderTable();
});
$("fVal").addEventListener("keydown", (e) => {
  if (e.key === "Enter") { e.preventDefault(); $("fAdd").click(); }
});

$("filterChips").addEventListener("click", (e) => {
  const x = e.target.closest("button[data-fdel]");
  if (!x) return;
  state.filters.splice(Number(x.dataset.fdel), 1);
  saveFilters();
  renderTable();
});

document.querySelector("#grid thead").addEventListener("click", (e) => {
  const th = e.target.closest("th[data-sort]");
  if (!th) return;
  const key = th.dataset.sort;
  state.sort = {
    key,
    dir: state.sort.key === key ? -state.sort.dir : (key === "ticker" ? 1 : -1),
  };
  renderTable();
});

document.querySelector("#grid tbody").addEventListener("click", async (e) => {
  const secDel = e.target.closest("button.sec-del");
  if (secDel) {
    if (!confirm("Delete this empty sector?")) return;
    const res = await fetch(`/api/sectors/${secDel.dataset.id}`, { method: "DELETE" });
    if (!res.ok) showBanner((await res.json()).detail || "Failed to delete sector");
    load();
    return;
  }

  const sectorRow = e.target.closest("tr.sector-row");
  if (sectorRow) {
    const name = sectorRow.dataset.sector;
    state.collapsed.has(name) ? state.collapsed.delete(name) : state.collapsed.add(name);
    localStorage.setItem("mt.collapsed", JSON.stringify([...state.collapsed]));
    renderTable();
    return;
  }

  const del = e.target.closest("button.del");
  if (del) {
    if (!confirm(`Stop tracking ${del.dataset.ticker}?`)) return;
    const res = await fetch(`/api/stocks/${del.dataset.id}`, { method: "DELETE" });
    if (!res.ok) showBanner("Failed to remove stock");
    load();
    return;
  }

  const move = e.target.closest("button.move");
  if (move) {
    openMoveModal(move.dataset.id, move.dataset.ticker);
    return;
  }

  const stockCell = e.target.closest("td.cell-stock[data-ticker]");
  if (stockCell) {
    detailOpenedFromApp = true;
    location.hash = "#/stock/" + stockCell.dataset.ticker;
  }
});

/* ---------- add / move modals ---------- */

const addModal = $("addModal");
const moveModal = $("moveModal");

async function fetchSectors() {
  const res = await fetch("/api/sectors");
  return res.json();
}

async function openAddModal() {
  const sectors = await fetchSectors();
  $("addSector").innerHTML =
    sectors.map((s) => `<option value="${s.id}">${escapeHtml(s.name)}</option>`).join("") +
    `<option value="__new__">➕ New sector…</option>`;
  if (!sectors.length) $("addSector").value = "__new__";
  $("newSectorRow").classList.toggle("hidden", $("addSector").value !== "__new__");
  $("addError").classList.add("hidden");
  $("addForm").reset();
  $("addSector").value = sectors.length ? sectors[0].id : "__new__";
  $("newSectorRow").classList.toggle("hidden", $("addSector").value !== "__new__");
  addModal.showModal();
  $("addTicker").focus();
}

$("addBtn").addEventListener("click", openAddModal);

$("addSector").addEventListener("change", () => {
  $("newSectorRow").classList.toggle("hidden", $("addSector").value !== "__new__");
});

$("addForm").addEventListener("submit", async (e) => {
  e.preventDefault();
  const ticker = $("addTicker").value.trim().toUpperCase();
  const sectorVal = $("addSector").value;
  const body = { ticker };
  if (sectorVal === "__new__") body.new_sector_name = $("addNewSector").value.trim();
  else body.sector_id = Number(sectorVal);

  $("addSubmit").disabled = true;
  $("addError").classList.add("hidden");
  try {
    const res = await fetch("/api/stocks", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    if (!res.ok) {
      const detail = (await res.json()).detail;
      throw new Error(typeof detail === "string" ? detail : "Invalid input");
    }
    addModal.close();
    load();
  } catch (err) {
    $("addError").textContent = err.message;
    $("addError").classList.remove("hidden");
  } finally {
    $("addSubmit").disabled = false;
  }
});

async function openMoveModal(stockId, ticker) {
  const sectors = await fetchSectors();
  $("moveTitle").textContent = `Move ${ticker} to…`;
  $("moveSector").innerHTML =
    sectors.map((s) => `<option value="${s.id}">${escapeHtml(s.name)}</option>`).join("");
  $("moveError").classList.add("hidden");
  moveModal.dataset.stockId = stockId;
  moveModal.showModal();
}

$("moveForm").addEventListener("submit", async (e) => {
  e.preventDefault();
  try {
    const res = await fetch(`/api/stocks/${moveModal.dataset.stockId}`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ sector_id: Number($("moveSector").value) }),
    });
    if (!res.ok) throw new Error((await res.json()).detail || "Move failed");
    moveModal.close();
    load();
  } catch (err) {
    $("moveError").textContent = err.message;
    $("moveError").classList.remove("hidden");
  }
});

/* ---------- stock detail view ---------- */

const detailModal = $("detailModal");
let detailOpenedFromApp = false;
let detailTicker = null;
let detailPeriod = null; // overlay-local timeframe; never touches the dashboard's

function fmtBig(v) {
  if (v == null || !isFinite(v)) return "—";
  const a = Math.abs(v);
  if (a >= 1e12) return (v / 1e12).toFixed(2) + "T";
  if (a >= 1e9) return (v / 1e9).toFixed(2) + "B";
  if (a >= 1e6) return (v / 1e6).toFixed(1) + "M";
  if (a >= 1e3) return (v / 1e3).toFixed(1) + "K";
  return String(v);
}

function relDate(iso) {
  if (!iso) return "";
  const days = Math.floor((Date.now() - new Date(iso).getTime()) / 86400000);
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

function chartSVG(points) {
  if (!points || points.length < 2) {
    return '<p class="muted">Not enough price history for a chart.</p>';
  }
  const W = 720, H = 180, P = 6;
  const vals = points.map((p) => p[1]);
  const min = Math.min(...vals), max = Math.max(...vals);
  const span = max - min || 1;
  const x = (i) => P + (i * (W - 2 * P)) / (points.length - 1);
  const y = (v) => H - P - ((v - min) * (H - 2 * P)) / span;
  const line = vals.map((v, i) => `${i ? "L" : "M"}${x(i).toFixed(1)},${y(v).toFixed(1)}`).join("");
  const area = `${line}L${x(vals.length - 1).toFixed(1)},${H - P}L${x(0).toFixed(1)},${H - P}Z`;
  const trend = vals[vals.length - 1] >= vals[0] ? "up" : "down";
  return `
    <svg viewBox="0 0 ${W} ${H}" class="chart ${trend}" preserveAspectRatio="none" role="img"
         aria-label="price chart for the selected timeframe">
      <path class="chart-area" d="${area}"/>
      <path class="chart-line" d="${line}"/>
    </svg>
    <div class="chart-meta">
      <span>${points[0][0]}</span>
      <span class="muted">low ${fmtPrice(min)} · high ${fmtPrice(max)}</span>
      <span>${points[points.length - 1][0]}</span>
    </div>`;
}

function statsGrid(s) {
  if (!s) return '<p class="muted">Fundamentals unavailable for this ticker.</p>';
  const frac = (v) => (v == null ? "—" : (v * 100).toFixed(1) + "%");
  const num = (v, d = 2) => (v == null ? "—" : Number(v).toFixed(d));
  const rec = s.recommendation
    ? s.recommendation.replace(/_/g, " ").replace(/\b\w/g, (c) => c.toUpperCase()) : null;
  const analyst = rec
    ? `${rec}${s.analyst_count ? ` · ${s.analyst_count} analysts` : ""}` : "—";
  const rows = [
    ["Market Cap", s.market_cap == null ? "—" : "$" + fmtBig(s.market_cap)],
    ["P/E (ttm / fwd)", `${num(s.trailing_pe, 1)} / ${num(s.forward_pe, 1)}`],
    ["EPS (ttm)", s.eps_ttm == null ? "—" : "$" + num(s.eps_ttm)],
    ["Beta", num(s.beta)],
    ["Dividend Yield", s.dividend_yield == null ? "—" : num(s.dividend_yield) + "%"],
    ["Profit Margin", frac(s.profit_margin)],
    ["Revenue Growth (yoy)", frac(s.revenue_growth)],
    ["52W Range", s.year_low == null ? "—" : `${fmtPrice(s.year_low)} – ${fmtPrice(s.year_high)}`],
    ["Avg Volume", fmtBig(s.avg_volume)],
    ["Analyst Consensus", analyst],
    ["Mean Price Target", s.target_mean == null ? "—" : fmtPrice(s.target_mean)],
    ["Earnings Date", s.next_earnings || "—"],
    ["Industry", s.industry || "—"],
  ];
  return `<div class="stats-grid">${rows.map(([k, v]) =>
    `<div class="stat"><div class="stat-k">${escapeHtml(k)}</div>
     <div class="stat-v">${typeof v === "string" ? v : escapeHtml(String(v))}</div></div>`).join("")}</div>`;
}

function vaultHtml(v) {
  if (!v) return "";
  // The note's own H1 duplicates the overlay header — drop it.
  const noteBody = (v.note_md || "").replace(/^#\s[^\n]*\n?/, "");
  const note = noteBody
    ? `<div class="analysis vault-note">${renderMarkdown(noteBody)}</div>` : "";
  const articles = (v.articles && v.articles.length) ? `
    <table class="vault-articles">
      <thead><tr><th>Article</th><th>Published</th><th>Price then</th><th>Since</th></tr></thead>
      <tbody>${v.articles.map((a) => `
        <tr>
          <td>${a.url
            ? `<a href="${escapeHtml(a.url)}" target="_blank" rel="noopener noreferrer">${escapeHtml(a.title)}</a>`
            : escapeHtml(a.title)}</td>
          <td>${escapeHtml(a.published || "—")}</td>
          <td>${fmtPrice(a.price_at)}</td>
          <td>${pctChip(a.change_pct)}</td>
        </tr>`).join("")}
      </tbody>
    </table>` : "";
  const meta = `<p class="analysis-meta">From the Stocks Vault${
    v.updated ? ` · updated ${escapeHtml(String(v.updated))}` : ""} · edit in Obsidian</p>`;
  return `<section class="detail-section"><h3>Vault Notes</h3>${note}${articles}${meta}</section>`;
}

function rangeMatrix(r) {
  if (!r || !r.metrics || !r.metrics.length) return "";
  const days = Math.max(...r.metrics.map((m) => m.days || 0));
  const rows = r.metrics.map((m) => `
    <tr>
      <th>${escapeHtml(m.label)}</th>
      <td>${pctChip(m.avg)}</td>
      <td>${pctChip(m.med)}</td>
      <td title="on ${escapeHtml(m.min.date)}">${pctChip(m.min.value)}</td>
      <td title="on ${escapeHtml(m.max.date)}">${pctChip(m.max.value)}</td>
      <td class="muted">${m.std == null ? "—" : m.std.toFixed(2) + "%"}</td>
    </tr>`).join("");
  return `
    <section class="detail-section">
      <h3>Daily Range Stats · ${escapeHtml(r.period)}
        <span class="muted">— ${days} trading day${days === 1 ? "" : "s"}; hover min/max for the date</span></h3>
      <table class="range-matrix">
        <thead><tr><th></th><th>Avg</th><th>Median</th><th>Min</th><th>Max</th><th>σ</th></tr></thead>
        <tbody>${rows}</tbody>
      </table>
    </section>`;
}

function newsList(items) {
  if (!items || !items.length) return "";
  return `
    <section class="detail-section">
      <h3>Recent News</h3>
      <ul class="news-list">${items.map((n) => `
        <li><a href="${escapeHtml(n.link)}" target="_blank" rel="noopener noreferrer">
          ${escapeHtml(n.title)}</a>
          <span class="news-meta">${escapeHtml(n.publisher || "")} ${relDate(n.published)}</span>
        </li>`).join("")}
      </ul>
    </section>`;
}

async function openDetail(ticker, period) {
  const freshOpen = detailTicker !== ticker || !detailModal.open;
  detailTicker = ticker;
  detailPeriod = period || (freshOpen ? state.period : detailPeriod) || state.period;
  const requested = `${ticker}:${detailPeriod}`;
  if (freshOpen) {
    $("detailBody").innerHTML = `
      <div class="detail-head">
        <div><span class="detail-tick">${escapeHtml(ticker)}</span></div>
        <button class="iconbtn detail-close" onclick="detailModal.close()" title="Close">✕</button>
      </div>
      <p class="muted">Loading…</p>`;
  }
  if (!detailModal.open) detailModal.showModal();

  let d;
  try {
    const res = await fetch(`/api/stocks/${ticker}/detail?period=${detailPeriod}`);
    if (!res.ok) throw new Error((await res.json()).detail || res.statusText);
    d = await res.json();
  } catch (err) {
    $("detailBody").innerHTML = `
      <div class="detail-head">
        <div><span class="detail-tick">${escapeHtml(ticker)}</span></div>
        <button class="iconbtn detail-close" onclick="detailModal.close()" title="Close">✕</button>
      </div>
      <p class="form-error">${escapeHtml(err.message)}</p>`;
    return;
  }
  // user moved on (closed, other ticker, or clicked another timeframe chip)
  if (`${detailTicker}:${detailPeriod}` !== requested || !detailModal.open) return;

  const analysis = d.analysis_md
    ? `${renderMarkdown(d.analysis_md)}
       <p class="analysis-meta">Generated ${escapeHtml((d.analysis_at || "").slice(0, 10))} ·
       informational research notes only — not investment advice.</p>`
    : '<p class="muted">No analysis written for this stock yet.</p>';

  $("detailBody").innerHTML = `
    <div class="detail-head">
      <div>
        <span class="detail-tick">${escapeHtml(d.ticker)}</span>
        <span class="detail-name">${escapeHtml(d.name || "")}</span>
        <span class="sector-chip">${escapeHtml(d.sector)}</span>
      </div>
      <div class="detail-price">
        <span class="detail-current">${fmtPrice(d.current)}</span>
        ${pctChip(d.day_pct)}
        ${d.stale ? '<span class="badge stale">cached</span>' : ""}
      </div>
      <button class="iconbtn detail-close" onclick="detailModal.close()" title="Close">✕</button>
    </div>
    <div class="detail-periods">${PERIODS.map((p) =>
      `<button class="chip ${p === detailPeriod ? "active" : ""}" data-detail-period="${p}">${p}</button>`).join("")}
    </div>
    <section class="detail-section">${chartSVG(d.chart)}</section>
    <section class="detail-section"><h3>Key Stats</h3>${statsGrid(d.stats)}</section>
    ${rangeMatrix(d.range)}
    ${newsList(d.news)}
    ${vaultHtml(d.vault)}
    <section class="detail-section">
      <h3>AI Analysis</h3>
      <div class="analysis">${analysis}</div>
    </section>`;
}

async function openSectorNote(sectorId) {
  detailTicker = "sector:" + sectorId;
  $("detailBody").innerHTML = `
    <div class="detail-head">
      <div><span class="detail-tick">Sector</span></div>
      <button class="iconbtn detail-close" onclick="detailModal.close()" title="Close">✕</button>
    </div>
    <p class="muted">Loading…</p>`;
  if (!detailModal.open) detailModal.showModal();

  try {
    const res = await fetch(`/api/sectors/${sectorId}/note`);
    if (!res.ok) throw new Error((await res.json()).detail || res.statusText);
    const d = await res.json();
    if (detailTicker !== "sector:" + sectorId || !detailModal.open) return;
    const body = (d.note_md || "").replace(/^#\s[^\n]*\n?/, "");
    $("detailBody").innerHTML = `
      <div class="detail-head">
        <div>
          <span class="detail-tick">${escapeHtml(d.name)}</span>
          <span class="sector-chip">Sector · ${d.stock_count} stock${d.stock_count === 1 ? "" : "s"}</span>
        </div>
        <button class="iconbtn detail-close" onclick="detailModal.close()" title="Close">✕</button>
      </div>
      <div class="analysis vault-note">${renderMarkdown(body)}</div>
      <p class="analysis-meta">From the Stocks Vault${
        d.updated ? ` · updated ${escapeHtml(String(d.updated))}` : ""} · edit in Obsidian</p>`;
  } catch (err) {
    $("detailBody").innerHTML = `
      <div class="detail-head">
        <div><span class="detail-tick">Sector</span></div>
        <button class="iconbtn detail-close" onclick="detailModal.close()" title="Close">✕</button>
      </div>
      <p class="form-error">${escapeHtml(err.message)}</p>`;
  }
}

/* ---------- database view (read-only) ---------- */

const dbState = {
  data: null,
  sort: { key: "ticker", dir: 1 },
  filter: "",
  expanded: null, // { ticker, period, rows | null while loading }
};

function fmtAge(seconds) {
  if (seconds == null) return "—";
  return seconds < 90 ? `${seconds}s` : `${Math.round(seconds / 60)}m`;
}

const PAGE_VIEWS = ["dbView", "btView", "devView"];

function showPage(sectionId) {
  document.body.classList.add("db-mode");
  for (const id of PAGE_VIEWS) $(id).classList.toggle("hidden", id !== sectionId);
}

function hidePages() {
  document.body.classList.remove("db-mode");
  for (const id of PAGE_VIEWS) $(id).classList.add("hidden");
}

async function enterDbView() {
  showPage("dbView");
  const view = $("dbView");
  if (!dbState.data) view.innerHTML = '<p class="muted db-loading">Loading database…</p>';
  try {
    const res = await fetch("/api/db");
    if (!res.ok) throw new Error((await res.json()).detail || res.statusText);
    dbState.data = await res.json();
  } catch (err) {
    view.innerHTML = `<p class="form-error db-loading">Failed to load: ${escapeHtml(err.message)}</p>`;
    return;
  }
  if (location.hash === "#/db") renderDbView();
}

function exitDbView() {
  hidePages();
}

function dbCard(name, value, sub) {
  return `<div class="card">
    <div class="card-name">${escapeHtml(name)}</div>
    <div class="card-pct db-card-value">${value}</div>
    <div class="card-count">${sub}</div>
  </div>`;
}

function renderDbView() {
  const d = dbState.data;
  const oldest = d.caches.length ? Math.max(...d.caches.map((c) => c.age_seconds)) : null;
  const cacheMeta = d.caches.length
    ? d.caches.map((c) =>
        `${escapeHtml(c.slot)} — ${c.items} item${c.items === 1 ? "" : "s"}, age ${fmtAge(c.age_seconds)} / ttl ${fmtAge(c.ttl_seconds)}`)
        .join(" · ")
    : "none warm yet (open the dashboard to populate)";

  $("dbView").innerHTML = `
    <div class="db-head">
      <h2>Database <span class="muted">· read-only</span></h2>
      <input id="dbFilter" placeholder="Filter ticker / name / sector…"
             value="${escapeHtml(dbState.filter)}" autocomplete="off" spellcheck="false">
      <button class="btn" id="dbBack">← Dashboard</button>
    </div>
    <div class="summary db-cards">
      ${dbCard("SQLite", d.db.size_bytes != null ? (d.db.size_bytes / 1024).toFixed(0) + " KB" : "—",
               escapeHtml(d.db.path))}
      ${dbCard("stocks table", `${d.tables.stocks.rows.length} rows`,
               `${d.tables.stocks.columns.length} columns`)}
      ${dbCard("sectors table", `${d.tables.sectors.rows.length} rows`,
               `${d.tables.sectors.columns.length} columns`)}
      ${dbCard("Fetch caches", `${d.caches.length} slot${d.caches.length === 1 ? "" : "s"}`,
               d.caches.length ? `oldest ${fmtAge(oldest)}` : "cold")}
      ${dbCard("Vault", d.vault.available ? `${d.vault.stocks + d.vault.sectors} notes` : "missing",
               d.vault.available ? `${d.vault.articles} article${d.vault.articles === 1 ? "" : "s"} · ${escapeHtml(d.vault.path)}`
                                 : escapeHtml(d.vault.path))}
    </div>
    <h3 class="db-sub">sectors</h3>
    <div class="db-scroll">${dbSectorsTable()}</div>
    <h3 class="db-sub">stocks <span class="muted">— click a ticker for its detail view, ▦ for raw price bars</span></h3>
    <div id="dbStocksWrap" class="db-scroll">${dbStocksTable()}</div>
    <p class="analysis-meta">Caches: ${cacheMeta}</p>`;

  $("dbBack").addEventListener("click", () => { location.hash = ""; });
  $("dbFilter").addEventListener("input", (e) => {
    dbState.filter = e.target.value;
    refreshDbStocks();
  });
}

function refreshDbStocks() {
  const wrap = $("dbStocksWrap");
  if (wrap) wrap.innerHTML = dbStocksTable();
}

function dbSectorsTable() {
  const d = dbState.data;
  const head = d.tables.sectors.columns.map((c) => `<th>${escapeHtml(c)}</th>`).join("");
  const body = d.tables.sectors.rows.map((r) =>
    `<tr>${d.tables.sectors.columns.map((c) =>
      `<td>${escapeHtml(String(r[c] ?? "—"))}</td>`).join("")}</tr>`).join("");
  return `<table class="db-table"><thead><tr>${head}</tr></thead><tbody>${body}</tbody></table>`;
}

const DB_STOCK_COLS = [
  { key: "id", label: "id" },
  { key: "ticker", label: "ticker" },
  { key: "name", label: "name" },
  { key: "sector", label: "sector" },
  { key: "shares", label: "shares", fmt: (v) => (v == null ? "—" : fmtBig(v)) },
  { key: "created_at", label: "created_at" },
  { key: "analysis_at", label: "analysis_at", fmt: (v) => v || "—" },
  { key: "analysis_chars", label: "analysis",
    fmt: (v) => (v ? `${v.toLocaleString()} chars` : "—") },
];

function dbStocksTable() {
  const d = dbState.data;
  const sectorName = Object.fromEntries(d.tables.sectors.rows.map((r) => [r.id, r.name]));
  let rows = d.tables.stocks.rows.map((r) => ({ ...r, sector: sectorName[r.sector_id] || "" }));

  const q = dbState.filter.trim().toLowerCase();
  if (q) {
    rows = rows.filter((r) =>
      `${r.ticker} ${r.name || ""} ${r.sector}`.toLowerCase().includes(q));
  }
  const { key, dir } = dbState.sort;
  rows.sort((a, b) => {
    const va = a[key], vb = b[key];
    if (va == null) return 1;
    if (vb == null) return -1;
    return (typeof va === "string" ? va.localeCompare(vb) : va - vb) * dir;
  });

  const head = DB_STOCK_COLS.map((c) => {
    const arrow = key === c.key ? `<span class="arrow">${dir > 0 ? "▲" : "▼"}</span>` : "";
    return `<th class="sortable" data-dbsort="${c.key}">${escapeHtml(c.label)} ${arrow}</th>`;
  }).join("") + "<th></th>";

  const body = rows.map((r) => {
    const cells = DB_STOCK_COLS.map((c) => {
      const value = c.fmt ? c.fmt(r[c.key]) : escapeHtml(String(r[c.key] ?? "—"));
      const extra = c.key === "ticker"
        ? ` class="db-tick" data-goto-stock="${escapeHtml(r.ticker)}" title="Open detail view"`
        : c.key === "analysis_chars" && r.analysis_preview
          ? ` title="${escapeHtml(r.analysis_preview)}"` : "";
      return `<td${extra}>${value}</td>`;
    }).join("");
    let html = `<tr>${cells}
      <td><button class="iconbtn db-bars" data-bars="${escapeHtml(r.ticker)}"
          title="Raw daily OHLCV bars">▦</button></td></tr>`;
    if (dbState.expanded && dbState.expanded.ticker === r.ticker) {
      html += dbExpansionRow();
    }
    return html;
  }).join("");

  return `<table class="db-table"><thead><tr>${head}</tr></thead><tbody>${body}</tbody></table>`;
}

function dbExpansionRow() {
  const ex = dbState.expanded;
  const chips = PERIODS.map((p) =>
    `<button class="chip ${p === ex.period ? "active" : ""}" data-bars-period="${p}">${p}</button>`).join("");
  let table = '<p class="muted">Loading bars…</p>';
  if (ex.rows) {
    if (!ex.rows.length) {
      table = '<p class="muted">No price data.</p>';
    } else {
      const body = [...ex.rows].reverse().map((b) => `
        <tr><td>${b.date}</td><td>${fmtPrice(b.open)}</td><td>${fmtPrice(b.high)}</td>
        <td>${fmtPrice(b.low)}</td><td>${fmtPrice(b.close)}</td>
        <td>${b.volume == null ? "—" : fmtBig(b.volume)}</td></tr>`).join("");
      table = `<div class="db-bars-scroll"><table class="db-table db-bars-table">
        <thead><tr><th>date</th><th>open</th><th>high</th><th>low</th><th>close</th><th>volume</th></tr></thead>
        <tbody>${body}</tbody></table></div>`;
    }
  }
  return `<tr class="db-expand"><td colspan="${DB_STOCK_COLS.length + 1}">
    <div class="db-expand-head">${escapeHtml(ex.ticker)} daily bars ${chips}
      <span class="muted">${ex.rows ? ex.rows.length + " rows" : ""}</span></div>
    ${table}</td></tr>`;
}

async function loadBars(ticker, period) {
  dbState.expanded = { ticker, period, rows: null };
  refreshDbStocks();
  try {
    const res = await fetch(`/api/db/prices/${ticker}?period=${period}`);
    if (!res.ok) throw new Error();
    const d = await res.json();
    if (!dbState.expanded || dbState.expanded.ticker !== ticker) return;
    dbState.expanded.rows = d.rows || [];
  } catch {
    if (dbState.expanded && dbState.expanded.ticker === ticker) dbState.expanded.rows = [];
  }
  refreshDbStocks();
}

$("dbView").addEventListener("click", (e) => {
  const sortTh = e.target.closest("th[data-dbsort]");
  if (sortTh) {
    const k = sortTh.dataset.dbsort;
    dbState.sort = { key: k, dir: dbState.sort.key === k ? -dbState.sort.dir : 1 };
    refreshDbStocks();
    return;
  }
  const tick = e.target.closest("td[data-goto-stock]");
  if (tick) {
    detailOpenedFromApp = true;
    location.hash = "#/stock/" + tick.dataset.gotoStock;
    return;
  }
  const bars = e.target.closest("button[data-bars]");
  if (bars) {
    const t = bars.dataset.bars;
    if (dbState.expanded && dbState.expanded.ticker === t) {
      dbState.expanded = null;
      refreshDbStocks();
    } else {
      loadBars(t, "3M");
    }
    return;
  }
  const chip = e.target.closest("button[data-bars-period]");
  if (chip && dbState.expanded) loadBars(dbState.expanded.ticker, chip.dataset.barsPeriod);
});

$("brand").addEventListener("click", () => { location.hash = ""; });
$("dbBtn").addEventListener("click", () => { location.hash = "#/db"; });
$("btBtn").addEventListener("click", () => { location.hash = "#/backtest"; });
$("devBtn").addEventListener("click", () => { location.hash = "#/deviations"; });

/* ---------- backtest page (historical simulation — descriptive only) ---------- */

const BT_DEFAULTS = {
  tickers: "all", entry_mode: "pct", entry_value: 2, avg_in_enabled: false,
  avg_in_level: 4, tp_mode: "pct", tp_value: 3, stop_pct: 5,
  max_hold_days: 10, cost_bps: 5, lookback: 63,
};

const btState = {
  rule: (() => {
    try { return { ...BT_DEFAULTS, ...JSON.parse(localStorage.getItem("mt.btRule") || "{}") }; }
    catch { return { ...BT_DEFAULTS }; }
  })(),
  data: null,
  running: false,
  expanded: null,
};

function btField(id, label, input) {
  return `<label class="bt-field">${escapeHtml(label)}${input.replace("{id}", id)}</label>`;
}

function renderBtForm() {
  const r = btState.rule;
  const tickers = (state.data ? state.data.sectors.flatMap((s) => s.stocks.map((x) => x.ticker)) : [])
    .sort();
  const options = ['<option value="all">All portfolio</option>']
    .concat(tickers.map((t) => `<option value="${t}" ${r.tickers === t ? "selected" : ""}>${t}</option>`))
    .join("");
  return `
    <div class="bt-form">
      ${btField("bt-tickers", "Tickers", `<select id="{id}">${options}</select>`)}
      ${btField("bt-entry-mode", "Entry trigger", `<select id="{id}">
        <option value="pct" ${r.entry_mode === "pct" ? "selected" : ""}>% below prev close</option>
        <option value="sigma" ${r.entry_mode === "sigma" ? "selected" : ""}>σ beyond avg dip</option>
      </select>`)}
      ${btField("bt-entry-value", "Entry value", `<input id="{id}" type="number" step="0.1" min="0.1" value="${r.entry_value}">`)}
      ${btField("bt-avg-in", "Average in", `<select id="{id}">
        <option value="no" ${!r.avg_in_enabled ? "selected" : ""}>off</option>
        <option value="yes" ${r.avg_in_enabled ? "selected" : ""}>2nd tranche</option>
      </select>`)}
      ${btField("bt-avg-level", "…at % below PC", `<input id="{id}" type="number" step="0.5" min="0.5" value="${r.avg_in_level}">`)}
      ${btField("bt-tp-mode", "Target", `<select id="{id}">
        <option value="pct" ${r.tp_mode === "pct" ? "selected" : ""}>% above avg cost</option>
        <option value="avg_range" ${r.tp_mode === "avg_range" ? "selected" : ""}>× avg daily range</option>
      </select>`)}
      ${btField("bt-tp-value", "Target value", `<input id="{id}" type="number" step="0.1" min="0.1" value="${r.tp_value}">`)}
      ${btField("bt-stop", "Stop %", `<input id="{id}" type="number" step="0.5" min="0.5" value="${r.stop_pct}">`)}
      ${btField("bt-days", "Max hold days", `<input id="{id}" type="number" step="1" min="1" value="${r.max_hold_days}">`)}
      ${btField("bt-cost", "Cost bps/side", `<input id="{id}" type="number" step="1" min="0" value="${r.cost_bps}">`)}
      ${btField("bt-lookback", "Stats lookback", `<input id="{id}" type="number" step="1" min="20" max="200" value="${r.lookback}">`)}
      <button id="btRun" class="btn primary" ${btState.running ? "disabled" : ""}>
        ${btState.running ? "Running…" : "Run simulation"}</button>
    </div>`;
}

function btPct(v, signed = true) {
  if (v == null) return '<span class="muted">—</span>';
  return pctChip(signed ? v : Math.abs(v));
}

function renderBtResults() {
  const d = btState.data;
  if (!d) return "";
  const ru = d.rollup;
  const cards = `
    <div class="summary db-cards">
      ${dbCard("Tickers", `${ru.tickers_tested} tested`, ru.tickers_skipped ? `${ru.tickers_skipped} skipped (thin history)` : "full coverage")}
      ${dbCard("Trades", String(ru.total_trades), "closed, across all tested")}
      ${dbCard("Median expectancy", btPct(ru.median_expectancy_pct), "per trade, net of costs")}
      ${dbCard("Net-negative tickers", `${ru.net_negative_tickers} / ${ru.tickers_tested}`, "lost money after costs")}
      ${dbCard("Beat buy & hold", `${ru.beat_buy_hold} / ${ru.tickers_tested}`, "vs holding the same span")}
      ${ru.best ? dbCard("Best / worst", `${ru.best[0]} ${ru.best[1] > 0 ? "+" : ""}${ru.best[1]}%`,
                         `worst: ${ru.worst[0]} ${ru.worst[1]}%`) : ""}
    </div>`;

  const rows = Object.entries(d.results).map(([t, r]) => {
    if (r.error) {
      return `<tr class="bt-skip"><td>${t}</td><td colspan="8" class="muted">${escapeHtml(r.error)}</td></tr>`;
    }
    const s = r.summary;
    const delta = (s.cumulative_pct != null && s.buy_hold_pct != null)
      ? s.cumulative_pct - s.buy_hold_pct : null;
    let html = `
      <tr class="bt-row" data-bt-ticker="${t}">
        <td class="db-tick">${t} ${r.trades.length ? `<span class="muted">▸</span>` : ""}</td>
        <td>${s.trades}${r.open_trade ? ' <span class="muted" title="plus one still-open position">+1 open</span>' : ""}</td>
        <td>${s.hit_rate == null ? "—" : s.hit_rate + "%"}</td>
        <td>${btPct(s.expectancy_pct)}</td>
        <td>${btPct(s.cumulative_pct)}</td>
        <td class="muted">${s.max_drawdown_pct == null ? "—" : "−" + s.max_drawdown_pct + "%"}</td>
        <td>${btPct(s.buy_hold_pct)}</td>
        <td>${btPct(delta == null ? null : Math.round(delta * 100) / 100)}</td>
        <td class="muted">${s.time_in_market_pct == null ? "—" : s.time_in_market_pct + "%"}</td>
      </tr>`;
    if (btState.expanded === t && r.trades.length) {
      const tradeRows = r.trades.map((tr) => `
        <tr><td>${tr.entry_date}</td>
        <td>${tr.tranches.map((x) => fmtPrice(x.price)).join(" + ")}</td>
        <td>${tr.exit_date}</td><td>${fmtPrice(tr.exit_price)}</td>
        <td>${escapeHtml(tr.reason)}</td><td>${tr.held_days}d</td>
        <td>${btPct(tr.pnl_pct)}</td></tr>`).join("");
      html += `
        <tr class="db-expand"><td colspan="9">
          <div class="db-bars-scroll"><table class="db-table db-bars-table">
            <thead><tr><th>entry</th><th>fills</th><th>exit</th><th>price</th>
            <th>reason</th><th>held</th><th>P&L</th></tr></thead>
            <tbody>${tradeRows}</tbody></table></div>
        </td></tr>`;
    }
    return html;
  }).join("");

  return `
    ${cards}
    <h3 class="db-sub">per ticker <span class="muted">— click a row for its trades</span></h3>
    <div class="db-scroll"><table class="db-table">
      <thead><tr><th>ticker</th><th>trades</th><th>hit rate</th><th>expectancy</th>
      <th>cumulative</th><th>max DD</th><th>buy&nbsp;&amp;&nbsp;hold</th><th>Δ vs B&amp;H</th><th>in market</th></tr></thead>
      <tbody>${rows}</tbody></table></div>
    <div class="bt-assumptions">
      <strong>Assumptions & limits</strong>
      <ul>${(d.assumptions || []).map((a) => `<li>${escapeHtml(a)}</li>`).join("")}</ul>
    </div>`;
}

function renderBtView() {
  $("btView").innerHTML = `
    <div class="db-head">
      <h2>Rule Backtest <span class="muted">· historical simulation — not advice</span></h2>
      <button class="btn" id="btBack" style="margin-left:auto">← Dashboard</button>
    </div>
    ${renderBtForm()}
    <div id="btResults">${renderBtResults()}</div>`;
  $("btBack").addEventListener("click", () => { location.hash = ""; });
  $("btRun").addEventListener("click", runBacktest);
}

async function runBacktest() {
  const val = (id) => $(id).value;
  btState.rule = {
    tickers: val("bt-tickers"),
    entry_mode: val("bt-entry-mode"),
    entry_value: Number(val("bt-entry-value")),
    avg_in_enabled: val("bt-avg-in") === "yes",
    avg_in_level: Number(val("bt-avg-level")),
    tp_mode: val("bt-tp-mode"),
    tp_value: Number(val("bt-tp-value")),
    stop_pct: Number(val("bt-stop")),
    max_hold_days: Number(val("bt-days")),
    cost_bps: Number(val("bt-cost")),
    lookback: Number(val("bt-lookback")),
  };
  localStorage.setItem("mt.btRule", JSON.stringify(btState.rule));
  btState.running = true;
  btState.expanded = null;
  renderBtView();
  try {
    const { tickers, ...rule } = btState.rule;
    const res = await fetch("/api/backtest", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ tickers: tickers === "all" ? "all" : [tickers], rule }),
    });
    if (!res.ok) {
      const detail = (await res.json()).detail;
      throw new Error(typeof detail === "string" ? detail : "Invalid rule parameters");
    }
    btState.data = await res.json();
  } catch (err) {
    btState.data = null;
    showBanner(`Backtest failed: ${err.message}`);
  } finally {
    btState.running = false;
  }
  if (location.hash === "#/backtest") renderBtView();
}

$("btView").addEventListener("click", (e) => {
  const row = e.target.closest("tr[data-bt-ticker]");
  if (row) {
    const t = row.dataset.btTicker;
    btState.expanded = btState.expanded === t ? null : t;
    $("btResults").innerHTML = renderBtResults();
  }
});

/* ---------- deviations page (today vs typical) ---------- */

const devState = { data: null, sort: "pc_close" };
const DEV_COLS = [
  ["pc_close", "Day %"], ["pc_low", "Dip"], ["pc_high", "Stretch"], ["l_high", "Range"],
];

function zChip(metric) {
  if (!metric) return '<span class="muted">—</span>';
  const v = metric.today;
  const cls = v > 0.005 ? "up" : v < -0.005 ? "down" : "flat";
  const hot = Math.abs(metric.z) >= 2 ? " z-hot" : "";
  const zText = `${metric.z > 0 ? "+" : ""}${metric.z.toFixed(1)}σ`;
  const title = `${zText} vs its typical day (avg ${metric.avg}% ± ${metric.sigma})`;
  return `<span class="pct ${cls}${hot}" title="${escapeHtml(title)}">` +
         `${v > 0 ? "+" : ""}${v.toFixed(2)}%<small class="z-tag">${zText}</small></span>`;
}

async function enterDevView() {
  showPage("devView");
  if (!devState.data) $("devView").innerHTML = '<p class="muted db-loading">Computing deviations…</p>';
  try {
    const res = await fetch("/api/deviations");
    if (!res.ok) throw new Error(res.statusText);
    devState.data = await res.json();
  } catch (err) {
    $("devView").innerHTML = `<p class="form-error db-loading">Failed: ${escapeHtml(err.message)}</p>`;
    return;
  }
  if (location.hash === "#/deviations") renderDevView();
}

function renderDevView() {
  const d = devState.data;
  const session = d.stocks.length ? d.stocks[0].session_date : "";
  const sorted = [...d.stocks].sort((a, b) => {
    const va = a.metrics[devState.sort] ? Math.abs(a.metrics[devState.sort].today) : -1;
    const vb = b.metrics[devState.sort] ? Math.abs(b.metrics[devState.sort].today) : -1;
    return vb - va;
  });
  const head = DEV_COLS.map(([key, label]) =>
    `<th class="sortable" data-dev-sort="${key}">${label}
       ${devState.sort === key ? '<span class="arrow">▼</span>' : ""}</th>`).join("");
  const rows = sorted.map((s) => `
    <tr>
      <td class="db-tick" data-goto-stock="${s.ticker}">${s.ticker}
        <div class="tick-name">${escapeHtml(s.sector)}</div></td>
      <td>${fmtPrice(s.current)}</td>
      ${DEV_COLS.map(([key]) => `<td>${zChip(s.metrics[key])}</td>`).join("")}
    </tr>`).join("");

  $("devView").innerHTML = `
    <div class="db-head">
      <h2>σ Today <span class="muted">· session ${escapeHtml(session)}${d.stale ? " · cached" : ""}</span></h2>
      <button class="btn" id="devBack" style="margin-left:auto">← Dashboard</button>
    </div>
    <p class="muted dev-note">Today's moves in %, each tagged with how unusual it is for that
      stock (the small σ = distance from its own trailing 63-day average; ±2σ or more lights
      up). Statistical context only — it describes the day; it doesn't prescribe anything.</p>
    <div class="db-scroll"><table class="db-table dev-table">
      <thead><tr><th>stock</th><th>current</th>${head}</tr></thead>
      <tbody>${rows}</tbody></table></div>
    <p class="analysis-meta">σ tag = (today − trailing avg) / trailing σ, computed per stock from
      the 63 sessions before today. Hover a chip for the averages behind it.</p>`;
  $("devBack").addEventListener("click", () => { location.hash = ""; });
}

$("devView").addEventListener("click", (e) => {
  const th = e.target.closest("th[data-dev-sort]");
  if (th) {
    devState.sort = th.dataset.devSort;
    renderDevView();
    return;
  }
  const tick = e.target.closest("td[data-goto-stock]");
  if (tick) {
    detailOpenedFromApp = true;
    location.hash = "#/stock/" + tick.dataset.gotoStock;
  }
});

function handleRoute() {
  if (location.hash === "#/db") {
    if (detailModal.open) detailModal.close();
    enterDbView();
    return;
  }
  if (location.hash === "#/backtest") {
    if (detailModal.open) detailModal.close();
    showPage("btView");
    renderBtView();
    return;
  }
  if (location.hash === "#/deviations") {
    if (detailModal.open) detailModal.close();
    enterDevView();
    return;
  }
  exitDbView();
  const stockMatch = location.hash.match(/^#\/stock\/([A-Za-z0-9.\-]+)$/);
  const sectorMatch = location.hash.match(/^#\/sector\/(\d+)$/);
  if (stockMatch) {
    openDetail(stockMatch[1].toUpperCase());
  } else if (sectorMatch) {
    openSectorNote(Number(sectorMatch[1]));
  } else if (detailModal.open) {
    detailModal.close();
  }
}

$("summary").addEventListener("click", (e) => {
  const card = e.target.closest(".card[data-sector-id]");
  if (card) {
    detailOpenedFromApp = true;
    location.hash = "#/sector/" + card.dataset.sectorId;
  }
});

// Native dialog close (Esc, ✕) → keep the URL hash in sync.
detailModal.addEventListener("close", () => {
  detailTicker = null;
  if (location.hash.startsWith("#/stock/")) {
    if (detailOpenedFromApp) history.back();
    else history.replaceState(null, "", location.pathname + location.search);
  }
  detailOpenedFromApp = false;
});
detailModal.addEventListener("click", (e) => {
  const chip = e.target.closest("button[data-detail-period]");
  if (chip && detailTicker) {
    openDetail(detailTicker, chip.dataset.detailPeriod);
    return;
  }
  if (e.target === detailModal) detailModal.close(); // backdrop click
});

window.addEventListener("hashchange", handleRoute);

/* ---------- boot ---------- */

load();
handleRoute();
