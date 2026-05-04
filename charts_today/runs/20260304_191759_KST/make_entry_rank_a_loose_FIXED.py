#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
make_entry_rank_a_loose.py (FIXED)

Fixes:
- report_v2.csv contains header comment lines starting with '#', which breaks pandas default CSV parser.
  This version reads with comment='#' and engine='python'.

Purpose (C-mode helper):
- "A-loose entry ranking": require CloseBreak + VolConfirm only (no hold/retest),
  then rank by breakout distance vs break level (avoid chasing).

Default behavior:
- Filters rows with:
    breakout_confirmed_close == True
    breakout_volume_confirmed == True
    breakout_pct_vs_level between 0 and max_chase
- If --only-a is set, keeps only rows whose break_level_src indicates prior-60d-high (rolling60d_high).

Outputs:
- entry_rank_a_loose_<KSTSTAMP>.csv
- prints a terminal block with the ranked candidates.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd

KST = ZoneInfo("Asia/Seoul")

def now_kst_stamp() -> str:
    return datetime.now(KST).strftime("%Y%m%d_%H%M%S_KST")

def load_report(path: Path) -> pd.DataFrame:
    # Robust read: ignore our header comment lines
    df = pd.read_csv(path, comment="#", engine="python")
    # normalize ticker col
    if "ticker" not in df.columns:
        for alt in ["symbol", "yf_symbol", "yahoo_symbol"]:
            if alt in df.columns:
                df["ticker"] = df[alt]
                break
    if "ticker" not in df.columns:
        raise ValueError(f"No ticker column found in {path}. Columns={list(df.columns)}")
    df["ticker"] = df["ticker"].astype(str).str.strip()
    return df

def compute_breakout_pct(df: pd.DataFrame) -> pd.Series:
    if "price" not in df.columns:
        raise ValueError("Missing column: price")
    # break level column name varies across versions; prefer daily_break_level
    lvl_col = None
    for c in ["daily_break_level", "break_level", "level", "break_level_value"]:
        if c in df.columns:
            lvl_col = c
            break
    if lvl_col is None:
        raise ValueError("Missing break level column (expected daily_break_level or break_level)")
    lvl = pd.to_numeric(df[lvl_col], errors="coerce")
    px = pd.to_numeric(df["price"], errors="coerce")
    return (px / lvl - 1.0) * 100.0

def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--report", type=str, default="report_v2.csv", help="report_v2*.csv path")
    ap.add_argument("--only-a", action="store_true", help="Keep only A-mode (prior-60d-high) break_level_src rows")
    ap.add_argument("--max-chase", type=float, default=1.0, help="Max breakout distance (%) above level to allow")
    ap.add_argument("--top", type=int, default=20, help="How many ranked rows to print/save")
    args = ap.parse_args()

    path = Path(args.report)
    if not path.exists():
        raise FileNotFoundError(path)

    df = load_report(path)

    # Basic required booleans
    for col in ["breakout_confirmed_close", "breakout_volume_confirmed"]:
        if col not in df.columns:
            raise ValueError(f"Missing column: {col} (columns={list(df.columns)})")

    df = df.copy()
    df["breakout_pct_vs_level"] = compute_breakout_pct(df)

    # A-only filter by source (your A-mode uses rolling60d_high)
    if args.only_a and "break_level_src" in df.columns:
        df = df[df["break_level_src"].astype(str).str.contains("rolling60d_high", na=False)]

    # Loose entry conditions: close break + volume confirm
    df = df[(df["breakout_confirmed_close"] == True) & (df["breakout_volume_confirmed"] == True)]

    # Avoid chasing: keep only near-level breakouts
    df = df[(df["breakout_pct_vs_level"] >= 0.0) & (df["breakout_pct_vs_level"] <= float(args.max_chase))]

    if df.empty:
        print("=== A-LOOSE ENTRY RANK (none) ===")
        print("No tickers met CloseBreak+VolConfirm within max-chase.")
        return

    # Rank: closer to break level first (smaller breakout distance)
    df = df.sort_values(["breakout_pct_vs_level"], ascending=True)

    df["RANK"] = range(1, len(df) + 1)

    # Pick display columns
    cols = ["RANK", "ticker", "price"]
    if "daily_break_level" in df.columns:
        cols.append("daily_break_level")
    elif "break_level" in df.columns:
        cols.append("break_level")
    cols += ["breakout_pct_vs_level"]

    # Optional context columns
    for c in ["rsi14", "room_to_weekly_r1_pct", "px_vs_sma50", "px_vs_sma200", "grade", "score", "break_level_src"]:
        if c in df.columns:
            cols.append(c)

    out_df = df[cols].head(args.top).reset_index(drop=True)

    ts = now_kst_stamp()
    out_path = Path(f"entry_rank_a_loose_{ts}.csv")
    out_df.to_csv(out_path, index=False)
    Path("entry_rank_a_loose_latest.txt").write_text(str(out_path) + "\n", encoding="utf-8")

    print("\n=== A-LOOSE ENTRY RANK (LAST BLOCK) ===")
    print(f"Source: {path.name}")
    print(f"Saved: {out_path.name}  (rows={len(out_df)})")
    with pd.option_context("display.max_rows", 200, "display.width", 200):
        print(out_df.to_string(index=False))

if __name__ == "__main__":
    main()
