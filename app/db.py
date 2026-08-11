"""SQLite persistence for tracked sectors and stocks."""
from __future__ import annotations

import sqlite3
from contextlib import closing
from pathlib import Path
from typing import Optional

DB_PATH = Path(__file__).resolve().parent.parent / "data" / "tracker.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS sectors (
    id   INTEGER PRIMARY KEY,
    name TEXT NOT NULL UNIQUE COLLATE NOCASE
);
CREATE TABLE IF NOT EXISTS stocks (
    id         INTEGER PRIMARY KEY,
    ticker     TEXT NOT NULL UNIQUE,
    name       TEXT,
    sector_id  INTEGER NOT NULL REFERENCES sectors(id),
    created_at TEXT DEFAULT (datetime('now'))
);
-- Owner-authored watch conditions over the tracked statistics. A NULL ticker
-- means "any tracked stock" (screener style). Descriptive only: a rule states
-- a threshold the owner chose, and the app reports whether it currently holds.
CREATE TABLE IF NOT EXISTS alerts (
    id         INTEGER PRIMARY KEY,
    name       TEXT NOT NULL,
    ticker     TEXT,
    period     TEXT NOT NULL,
    metric_key TEXT NOT NULL,
    op         TEXT NOT NULL CHECK (op IN ('gte', 'lte')),
    value      REAL NOT NULL,
    enabled    INTEGER NOT NULL DEFAULT 1,
    created_at TEXT DEFAULT (datetime('now'))
);
"""

# First-run seed: the user's portfolio (June 2026), thematically sectored.
# Names are Yahoo's official longNames, captured at seed time. The user's
# list contained "OKLA", which has no Yahoo data — seeded as OKLO (Oklo Inc.).
SEED_PORTFOLIO: dict[str, list[tuple[str, str]]] = {
    "Semiconductors": [
        ("NVDA", "NVIDIA Corporation"),
        ("TSM", "Taiwan Semiconductor Manufacturing Company Limited"),
        ("INTC", "Intel Corporation"),
        ("MRVL", "Marvell Technology, Inc."),
        ("AMAT", "Applied Materials, Inc."),
    ],
    "Networking & Optical": [
        ("NOK", "Nokia Oyj"),
        ("AAOI", "Applied Optoelectronics, Inc."),
        ("POET", "POET Technologies Inc."),
    ],
    "AI Infrastructure": [
        ("SMCI", "Super Micro Computer, Inc."),
        ("HPE", "Hewlett Packard Enterprise Company"),
        ("NBIS", "Nebius Group N.V."),
        ("IREN", "IREN Limited"),
    ],
    "Software & Cloud": [
        ("MSFT", "Microsoft Corporation"),
        ("NOW", "ServiceNow, Inc."),
        ("IBM", "International Business Machines Corporation"),
    ],
    "Consumer & Fintech": [
        ("TTWO", "Take-Two Interactive Software, Inc."),
        ("HOOD", "Robinhood Markets, Inc."),
        ("BYND", "Beyond Meat, Inc."),
    ],
    "Frontier Tech": [
        ("RGTI", "Rigetti Computing, Inc."),
        ("ASTS", "AST SpaceMobile, Inc."),
        ("OKLO", "Oklo Inc."),
    ],
    "Biotech": [
        ("ARTV", "Artiva Biotherapeutics, Inc."),
        ("IKT", "Inhibikase Therapeutics, Inc."),
        ("PRQR", "ProQR Therapeutics N.V."),
    ],
    "Cannabis": [
        ("CGC", "Canopy Growth Corporation"),
        ("TLRY", "Tilray Brands, Inc."),
    ],
    "Materials": [
        ("MP", "MP Materials Corp."),
    ],
}


def connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def _migrate(conn: sqlite3.Connection) -> None:
    columns = {row["name"] for row in conn.execute("PRAGMA table_info(stocks)")}
    if "analysis" not in columns:
        conn.execute("ALTER TABLE stocks ADD COLUMN analysis TEXT")
    if "analysis_at" not in columns:
        conn.execute("ALTER TABLE stocks ADD COLUMN analysis_at TEXT")
    if "shares" not in columns:
        # Effective shares outstanding (Yahoo marketCap / price at capture
        # time, so ADR ratios come out right). Market cap renders as
        # shares × live price; re-synced whenever a detail view is opened.
        conn.execute("ALTER TABLE stocks ADD COLUMN shares REAL")


def init() -> None:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    with closing(connect()) as conn, conn:
        conn.executescript(SCHEMA)
        _migrate(conn)
        empty = conn.execute("SELECT COUNT(*) FROM sectors").fetchone()[0] == 0
        if empty:
            for sector, stocks in SEED_PORTFOLIO.items():
                cur = conn.execute("INSERT INTO sectors (name) VALUES (?)", (sector,))
                conn.executemany(
                    "INSERT INTO stocks (ticker, name, sector_id) VALUES (?, ?, ?)",
                    [(t, n, cur.lastrowid) for t, n in stocks],
                )


def sectors_with_stocks() -> list[dict]:
    with closing(connect()) as conn:
        secs = conn.execute("SELECT id, name FROM sectors ORDER BY name").fetchall()
        stocks = conn.execute(
            "SELECT id, ticker, name, sector_id, shares FROM stocks ORDER BY ticker"
        ).fetchall()
    by_sector: dict[int, list[dict]] = {}
    for s in stocks:
        by_sector.setdefault(s["sector_id"], []).append(
            {"id": s["id"], "ticker": s["ticker"], "name": s["name"], "shares": s["shares"]}
        )
    return [
        {"id": s["id"], "name": s["name"], "stocks": by_sector.get(s["id"], [])}
        for s in secs
    ]


def list_sectors() -> list[dict]:
    with closing(connect()) as conn:
        rows = conn.execute(
            """SELECT s.id, s.name, COUNT(st.id) AS stock_count
               FROM sectors s LEFT JOIN stocks st ON st.sector_id = s.id
               GROUP BY s.id ORDER BY s.name"""
        ).fetchall()
    return [dict(r) for r in rows]


def get_or_create_sector(name: str) -> int:
    with closing(connect()) as conn, conn:
        row = conn.execute("SELECT id FROM sectors WHERE name = ?", (name,)).fetchone()
        if row:
            return row["id"]
        return conn.execute("INSERT INTO sectors (name) VALUES (?)", (name,)).lastrowid


def sector_exists(sector_id: int) -> bool:
    with closing(connect()) as conn:
        return conn.execute("SELECT 1 FROM sectors WHERE id = ?", (sector_id,)).fetchone() is not None


def ticker_exists(ticker: str) -> bool:
    with closing(connect()) as conn:
        return conn.execute("SELECT 1 FROM stocks WHERE ticker = ?", (ticker,)).fetchone() is not None


def add_stock(ticker: str, name: Optional[str], sector_id: int) -> int:
    with closing(connect()) as conn, conn:
        return conn.execute(
            "INSERT INTO stocks (ticker, name, sector_id) VALUES (?, ?, ?)",
            (ticker, name, sector_id),
        ).lastrowid


def move_stock(stock_id: int, sector_id: int) -> bool:
    with closing(connect()) as conn, conn:
        cur = conn.execute(
            "UPDATE stocks SET sector_id = ? WHERE id = ?", (sector_id, stock_id)
        )
        return cur.rowcount > 0


def delete_stock(stock_id: int) -> bool:
    with closing(connect()) as conn, conn:
        return conn.execute("DELETE FROM stocks WHERE id = ?", (stock_id,)).rowcount > 0


def all_tickers() -> list[str]:
    with closing(connect()) as conn:
        return [r["ticker"] for r in conn.execute("SELECT ticker FROM stocks ORDER BY ticker")]


def get_stock_by_ticker(ticker: str) -> Optional[dict]:
    with closing(connect()) as conn:
        row = conn.execute(
            """SELECT st.id, st.ticker, st.name, st.analysis, st.analysis_at,
                      se.name AS sector
               FROM stocks st JOIN sectors se ON se.id = st.sector_id
               WHERE st.ticker = ?""",
            (ticker,),
        ).fetchone()
    return dict(row) if row else None


def set_shares(ticker: str, shares: Optional[float]) -> None:
    if not shares or shares <= 0:
        return
    with closing(connect()) as conn, conn:
        conn.execute("UPDATE stocks SET shares = ? WHERE ticker = ?", (shares, ticker))


def set_analysis(ticker: str, analysis_md: str, timestamp: str) -> bool:
    with closing(connect()) as conn, conn:
        cur = conn.execute(
            "UPDATE stocks SET analysis = ?, analysis_at = ? WHERE ticker = ?",
            (analysis_md, timestamp, ticker),
        )
        return cur.rowcount > 0


def list_alerts(enabled_only: bool = False) -> list[dict]:
    query = ("SELECT id, name, ticker, period, metric_key, op, value, enabled, created_at "
             "FROM alerts")
    if enabled_only:
        query += " WHERE enabled = 1"
    query += " ORDER BY id"
    with closing(connect()) as conn:
        rows = conn.execute(query).fetchall()
    return [{**dict(r), "enabled": bool(r["enabled"])} for r in rows]


def add_alert(name: str, ticker: Optional[str], period: str, metric_key: str,
              op: str, value: float) -> int:
    with closing(connect()) as conn, conn:
        return conn.execute(
            """INSERT INTO alerts (name, ticker, period, metric_key, op, value)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (name, ticker, period, metric_key, op, value),
        ).lastrowid


def set_alert_enabled(alert_id: int, enabled: bool) -> bool:
    with closing(connect()) as conn, conn:
        cur = conn.execute("UPDATE alerts SET enabled = ? WHERE id = ?",
                           (1 if enabled else 0, alert_id))
        return cur.rowcount > 0


def delete_alert(alert_id: int) -> bool:
    with closing(connect()) as conn, conn:
        return conn.execute("DELETE FROM alerts WHERE id = ?", (alert_id,)).rowcount > 0


def dump_tables() -> dict:
    """Read-only dump of every row/column, for the DB view."""
    out = {}
    with closing(connect()) as conn:
        for table in ("sectors", "stocks", "alerts"):
            columns = [r["name"] for r in conn.execute(f"PRAGMA table_info({table})")]
            rows = [dict(r) for r in conn.execute(f"SELECT * FROM {table}")]
            out[table] = {"columns": columns, "rows": rows}
    return out


def delete_sector(sector_id: int) -> str:
    """Returns 'deleted', 'not_found', or 'not_empty'."""
    with closing(connect()) as conn, conn:
        if conn.execute("SELECT 1 FROM stocks WHERE sector_id = ?", (sector_id,)).fetchone():
            return "not_empty"
        cur = conn.execute("DELETE FROM sectors WHERE id = ?", (sector_id,))
        return "deleted" if cur.rowcount else "not_found"
