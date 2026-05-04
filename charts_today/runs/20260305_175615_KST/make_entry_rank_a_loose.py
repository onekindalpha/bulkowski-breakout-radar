#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
make_entry_rank_a_loose.py

A-mode loose entry ranking:
- Keep: CloseBreak + VolConfirm (from report_v2)
- Drop: intraday hold / retest / hold-confirm requirements
- Rank by breakout distance vs break_level.

Usage:
  python make_entry_rank_a_loose.py
  python make_entry_rank_a_loose.py --report report_v2_YYYYMMDD_HHMMSS_KST.csv
  python make_entry_rank_a_loose.py --prefer-far
  python make_entry_rank_a_loose.py --max-chase 3
"""

from __future__ import annotations

import argparse
from pathlib import Path
from datetime import datetime, timezone, timedelta

import pandas as pd

KST = timezone(timedelta(hours=9))

def now_kst_stamp() -> str:
    return datetime.now(KST).strftime("%Y%m%d_%H%M%S_KST")

def load_report(path: Path) -> pd.DataFrame:
    if not path.exists():
        raise FileNotFoundError(f"Report not found: {path}")
    df = pd.read_csv(path)
    required = ["ticker", "price", "daily_break_level", "breakout_confirmed_close", "breakout_volume_confirmed"]
    missing = [c for c in required if c not in df.columns]
    if missing:
        raise ValueError(f"report missing columns: {missing}\ncols={df.columns.tolist()}")
    return df

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--report", default="report_v2.csv", help="report file (default: report_v2.csv)")
    ap.add_argument("--prefer-far", action="store_true",
                    help="Rank by breakout distance DESC (strongest first). Default ASC (closest-to-level first).")
    ap.add_argument("--only-a", action="store_true",
                    help="Only keep rows whose break_level_src contains 'rolling60d_high'.")
    ap.add_argument("--max-chase", type=float, default=2.0,
                    help="Label as CHASE if breakout_pct_vs_level > this. (default: 2.0)")
    ap.add_argument("--top", type=int, default=20, help="Print top N rows (default: 20)")
    args = ap.parse_args()

    df = load_report(Path(args.report))

    if args.only_a and "break_level_src" in df.columns:
        df = df[df["break_level_src"].astype(str).str.contains("rolling60d_high", na=False)].copy()

    df["daily_break_level"] = pd.to_numeric(df["daily_break_level"], errors="coerce")
    df["price"] = pd.to_numeric(df["price"], errors="coerce")

    df["breakout_pct_vs_level"] = (df["price"] / df["daily_break_level"] - 1.0) * 100.0

    df["CloseBreak"] = df["breakout_confirmed_close"].astype(bool)
    df["VolConfirm"] = df["breakout_volume_confirmed"].astype(bool)
    df["AboveLevel"] = df["breakout_pct_vs_level"] > 0

    # Loose entry condition: confirmed close+vol and price above level
    df["ENTRY_LOOSE_A"] = df["CloseBreak"] & df["VolConfirm"] & df["AboveLevel"]

    def label(row):
        if (not row["CloseBreak"]) or (not row["VolConfirm"]):
            return "SETUP"
        if row["breakout_pct_vs_level"] <= 0:
            return "BELOW"
        if row["breakout_pct_vs_level"] > args.max_chase:
            return "CHASE"
        return "ENTRY"

    df["ENTRY_TAG"] = df.apply(label, axis=1)

    sort_asc = not args.prefer_far
    df_rank = df.sort_values(
        ["ENTRY_LOOSE_A", "ENTRY_TAG", "breakout_pct_vs_level"],
        ascending=[False, True, sort_asc],
        kind="mergesort",
    ).copy()

    df_rank.insert(0, "PRIORITY_RANK", range(1, len(df_rank) + 1))

    out_csv = Path(f"entry_rank_a_loose_{now_kst_stamp()}.csv")
    df_rank.to_csv(out_csv, index=False)

    view_cols = [
        "PRIORITY_RANK", "ticker", "ENTRY_TAG", "price", "daily_break_level", "breakout_pct_vs_level",
        "CloseBreak", "VolConfirm"
    ]
    for optional in ["rsi14", "room_to_weekly_r1_pct", "weekly_r1", "break_level_src",
                     "breakout_confirmed_intraday", "daily_retest"]:
        if optional in df_rank.columns:
            view_cols.append(optional)

    print("\n=== ENTRY RANK (A loose) ===")
    print(f"Source: {args.report}")
    print(f"Saved : {out_csv}  (rows={len(df_rank)})")
    print("Rule  : ENTRY_LOOSE_A = CloseBreak & VolConfirm & Price>Level (NO hold/retest required)")
    print(f"Rank  : {'FAR (DESC)' if args.prefer_far else 'CLOSE (ASC)'} by breakout_pct_vs_level")
    print(f"CHASE : breakout_pct_vs_level > {args.max_chase:.2f}%\n")

    topn = min(args.top, len(df_rank))
    if topn == 0:
        print("(no rows)")
        return

    pd.set_option("display.max_columns", None)
    pd.set_option("display.width", 180)
    print(df_rank[view_cols].head(topn).to_string(index=False))
    print()

if __name__ == "__main__":
    main()
