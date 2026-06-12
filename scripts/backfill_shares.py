"""Backfill/refresh effective shares outstanding for all tracked stocks.

Market cap in the overview renders as shares × live price, so rerun this after
stock splits or major dilution to true things up:

    venv/bin/python scripts/backfill_shares.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import yfinance as yf  # noqa: E402

from app import db  # noqa: E402
from app.prices import effective_shares  # noqa: E402


def main() -> None:
    db.init()
    for ticker in db.all_tickers():
        try:
            info = yf.Ticker(ticker).info or {}
            shares = effective_shares(info.get("marketCap"),
                                      info.get("regularMarketPrice"),
                                      info.get("sharesOutstanding"))
        except Exception as exc:
            print(f"{ticker:6} FAILED: {type(exc).__name__}")
            continue
        if shares:
            db.set_shares(ticker, shares)
            mcap = info.get("marketCap") or 0
            print(f"{ticker:6} shares={shares:,.0f}  (Yahoo mcap ${mcap / 1e9:,.1f}B)")
        else:
            print(f"{ticker:6} no shares data on Yahoo")


if __name__ == "__main__":
    main()
