"""Refresh the "Price Since Publication" table in every vault article note.

Rewrites only the block between <!-- prices:start --> and <!-- prices:end -->
(adds the section if an article note has frontmatter prices but no markers).
Idempotent; safe to run any time:

    venv/bin/python scripts/refresh_article_prices.py
"""
import re
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import prices  # noqa: E402
from app.vault import VAULT_PATH, _split_frontmatter  # noqa: E402

MARKER = re.compile(r"<!-- prices:start -->.*?<!-- prices:end -->", re.S)


def fmt_price(value: float) -> str:
    return f"${value:,.4f}" if abs(value) < 1 else f"${value:,.2f}"


def main() -> None:
    articles_dir = VAULT_PATH / "Articles"
    if not articles_dir.is_dir():
        print(f"No Articles directory at {articles_dir}")
        return

    notes = []
    tickers: set[str] = set()
    for path in sorted(articles_dir.glob("*.md")):
        meta, _ = _split_frontmatter(path.read_text(encoding="utf-8"))
        raw = meta.get("prices")
        price_map = ({k.upper(): v for k, v in raw.items() if isinstance(v, (int, float))}
                     if isinstance(raw, dict) else {})
        if price_map:
            notes.append((path, price_map))
            tickers |= set(price_map)
    if not notes:
        print("No article notes with frontmatter prices found.")
        return

    frames, _, _, fetch_error = prices.get_daily(sorted(tickers), "1D")
    if fetch_error:
        print(f"Warning: {fetch_error} — keeping existing tables where current price is unknown.")
    current: dict[str, float] = {}
    for t in tickers:
        frame = frames.get(t)
        if frame is not None and not frame.empty:
            closes = frame["Close"].dropna()
            if not closes.empty:
                current[t] = float(closes.iloc[-1])

    today = date.today().isoformat()
    for path, price_map in notes:
        rows = []
        for t in sorted(price_map):
            now = current.get(t)
            change = prices._pct(now, price_map[t]) if now else None
            if change is not None and change == 0:
                change = 0.0  # normalize -0.0 so the table never shows "-0.00%"
            rows.append(f"| {t} | {fmt_price(price_map[t])} "
                        f"| {fmt_price(now) if now else '—'} "
                        f"| {f'{change:+.2f}%' if change is not None else '—'} |")
        block = ("<!-- prices:start -->\n"
                 "| Ticker | At publication | Now | Since |\n"
                 "| --- | --- | --- | --- |\n"
                 + "\n".join(rows)
                 + f"\n\n_Prices refreshed {today}._\n"
                 "<!-- prices:end -->")

        text = path.read_text(encoding="utf-8")
        if MARKER.search(text):
            # A replacement FUNCTION (not a string) so backslashes and \1-style
            # sequences inside the generated table are inserted literally rather
            # than read as group references. sub() calls it immediately, within
            # this iteration, so the late-binding B023 warns about cannot occur.
            updated = MARKER.sub(lambda _: block, text)  # noqa: B023
        else:
            updated = text.rstrip() + "\n\n## Price Since Publication\n" + block + "\n"
        if updated != text:
            path.write_text(updated, encoding="utf-8")
            print(f"updated   {path.name}")
        else:
            print(f"unchanged {path.name}")


if __name__ == "__main__":
    main()
