#!/usr/bin/env python3
"""
close_position.py

Move a sold position from positions.csv (OPEN) -> positions_closed.csv (HISTORY),
so monitors stop alerting on it.

Usage:
  python close_position.py UYM 31.21 --shares 10
  python close_position.py UYM 31.21                 # shares defaults to the shares in positions.csv
  python close_position.py UYM 31.21 --date 2026-03-03

Files:
  - positions.csv (open positions)  [required]
  - positions_closed.csv            [created/appended]

Behavior:
  - finds ticker in positions.csv (case-insensitive)
  - writes one row into positions_closed.csv with:
      ticker, entry_price, shares, custom_break_level, entry_date, entry_ts_kr,
      exit_price, exit_date, exit_ts_kr, pnl_pct, notes
  - removes that ticker from positions.csv and overwrites positions.csv

Notes:
  - This is bookkeeping only; it does NOT place any trades.
"""

from __future__ import annotations

import argparse
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd

KST = ZoneInfo("Asia/Seoul")


def now_date_kst() -> str:
    return datetime.now(KST).strftime("%Y-%m-%d")


def now_ts_kst() -> str:
    return datetime.now(KST).strftime("%Y-%m-%d %H:%M:%S")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("ticker", help="ticker to close (e.g., UYM)")
    ap.add_argument("exit_price", type=float, help="exit price")
    ap.add_argument("--shares", type=float, default=None, help="shares sold (default: from positions.csv)")
    ap.add_argument("--date", default="", help="exit date YYYY-MM-DD (default: today KST)")
    ap.add_argument("--notes", default="", help="optional notes")
    ap.add_argument("--positions", default="positions.csv")
    ap.add_argument("--closed", default="positions_closed.csv")
    args = ap.parse_args()

    pos_path = Path(args.positions)
    if not pos_path.exists():
        raise SystemExit(f"Not found: {args.positions}")

    df = pd.read_csv(pos_path)
    df.columns = [c.strip() for c in df.columns]
    if "ticker" not in df.columns:
        raise SystemExit("positions.csv must have 'ticker' column")

    df["ticker"] = df["ticker"].astype(str).str.strip().str.upper()
    t = args.ticker.strip().upper()

    if (df["ticker"] == t).sum() == 0:
        raise SystemExit(f"Ticker not found in {args.positions}: {t}")
    if (df["ticker"] == t).sum() > 1:
        raise SystemExit(f"Duplicate ticker rows in {args.positions}: {t} (fix duplicates first)")

    row = df.loc[df["ticker"] == t].iloc[0].to_dict()

    entry_price = float(row.get("entry_price", row.get("entry", float("nan"))))
    shares = float(args.shares) if args.shares is not None else float(row.get("shares", float("nan")))
    if pd.isna(entry_price) or pd.isna(shares):
        raise SystemExit("positions.csv must include numeric entry_price and shares for the ticker")

    exit_price = float(args.exit_price)
    pnl_pct = (exit_price / entry_price - 1.0) * 100.0

    exit_date = args.date.strip() or now_date_kst()
    exit_ts = now_ts_kst()

    closed_row = {
        "ticker": t,
        "entry_price": entry_price,
        "shares": shares,
        "custom_break_level": row.get("custom_break_level", row.get("break_level", "")),
        "entry_date": row.get("entry_date", ""),
        "entry_ts_kr": row.get("entry_ts_kr", ""),
        "exit_price": exit_price,
        "exit_date": exit_date,
        "exit_ts_kr": exit_ts,
        "pnl_pct": round(pnl_pct, 2),
        "notes": args.notes,
    }

    closed_path = Path(args.closed)
    if closed_path.exists():
        closed_df = pd.read_csv(closed_path)
        closed_df.columns = [c.strip() for c in closed_df.columns]
        closed_df = pd.concat([closed_df, pd.DataFrame([closed_row])], ignore_index=True)
    else:
        closed_df = pd.DataFrame([closed_row])

    # Write closed history
    closed_df.to_csv(closed_path, index=False)

    # Remove from open positions and overwrite
    df_open = df[df["ticker"] != t].copy()
    df_open.to_csv(pos_path, index=False)

    print(f"Closed: {t} @ {exit_price}  (entry={entry_price}, pnl={round(pnl_pct,2)}%)")
    print(f"Updated: {args.positions} (open positions now {len(df_open)})")
    print(f"Appended: {args.closed} (rows {len(closed_df)})")


if __name__ == "__main__":
    main()
