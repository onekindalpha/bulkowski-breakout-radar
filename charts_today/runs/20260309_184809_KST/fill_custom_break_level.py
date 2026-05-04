#!/usr/bin/env python3
"""
fill_custom_break_level.py

If you don't remember the breakout line, this script can fill (or overwrite) custom_break_level
using a reproducible rule from Yahoo daily bars.

Default rule (breakout-line proxy):
  custom_break_level := prior 60-trading-day HIGH (rolling max of High, shifted by 1)
computed on the latest daily bar (or on entry_date if you provide it).

Input: positions.csv with columns:
  ticker, entry_price, shares, [custom_break_level], [entry_date]

- If entry_date exists and is valid (YYYY-MM-DD), we compute the level on that date
  (nearest previous trading day in the daily series).
- Otherwise we compute using the latest available daily bar.

Output:
  positions_filled.csv

Optional:
  --inplace    overwrite positions.csv

Usage:
  python fill_custom_break_level.py
  python fill_custom_break_level.py --inplace
"""

from __future__ import annotations

import argparse
import io
from contextlib import redirect_stdout, redirect_stderr
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd
import yfinance as yf


LOOKBACK = 60


def safe_download(symbol: str, period="2y") -> pd.DataFrame:
    buf = io.StringIO()
    with redirect_stdout(buf), redirect_stderr(buf):
        return yf.download(symbol, period=period, interval="1d", auto_adjust=False, progress=False, threads=False)


def normalize(df: pd.DataFrame, sym: str) -> pd.DataFrame:
    if df is None or df.empty:
        return pd.DataFrame()
    out = df.copy()
    if isinstance(out.columns, pd.MultiIndex):
        lvl0 = out.columns.get_level_values(0)
        lvl1 = out.columns.get_level_values(1)
        if sym in set(lvl1):
            out = out.xs(sym, level=1, axis=1).copy()
        elif sym in set(lvl0):
            out = out[sym].copy()
    out.columns = [str(c).strip() for c in out.columns]
    need = {"High", "Close"}
    if not need.issubset(set(out.columns)):
        return pd.DataFrame()
    out = out[list(need)].dropna().sort_index()
    if not isinstance(out.index, pd.DatetimeIndex):
        out.index = pd.to_datetime(out.index, errors="coerce")
        out = out.dropna()
    if out.index.tz is not None:
        out.index = out.index.tz_localize(None)
    return out


def prior_60d_high_shifted(df: pd.DataFrame, lookback=LOOKBACK) -> pd.Series:
    return df["High"].rolling(lookback).max().shift(1)


def pick_index_for_date(idx: pd.DatetimeIndex, target: pd.Timestamp) -> int:
    """
    Find nearest index <= target (previous trading day).
    Returns integer position, or last index if target after last.
    """
    if len(idx) == 0:
        return -1
    if target >= idx[-1]:
        return len(idx) - 1
    # searchsorted gives insertion point
    pos = idx.searchsorted(target, side="right") - 1
    return int(max(0, pos))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--positions", default="positions.csv")
    ap.add_argument("--out", default="positions_filled.csv")
    ap.add_argument("--inplace", action="store_true")
    args = ap.parse_args()

    p = Path(args.positions)
    if not p.exists():
        raise SystemExit(f"{args.positions} not found")

    pos = pd.read_csv(p)
    pos.columns = [c.strip() for c in pos.columns]
    if "ticker" not in pos.columns:
        raise SystemExit("positions.csv must have 'ticker' column")

    # normalize
    pos["ticker"] = pos["ticker"].astype(str).str.strip().str.upper()

    # load per ticker
    filled = []
    for _, r in pos.iterrows():
        t = str(r["ticker"]).strip().upper()
        if not t:
            continue

        raw = safe_download(t, period="2y")
        df = normalize(raw, t)
        if df.empty or len(df) < (LOOKBACK + 5):
            filled.append(np.nan)
            continue

        lvl = prior_60d_high_shifted(df, LOOKBACK)

        # choose date
        if "entry_date" in pos.columns and pd.notna(r.get("entry_date")) and str(r.get("entry_date")).strip():
            try:
                target = pd.to_datetime(str(r["entry_date"]).strip()).tz_localize(None)
            except Exception:
                target = df.index[-1]
        else:
            target = df.index[-1]

        i = pick_index_for_date(df.index, target)
        val = float(lvl.iloc[i]) if (i >= 0 and pd.notna(lvl.iloc[i])) else float(df["High"].tail(LOOKBACK).max())
        filled.append(round(val, 4))

    pos["custom_break_level_filled"] = filled

    # if custom_break_level missing, create it
    if "custom_break_level" not in pos.columns:
        pos["custom_break_level"] = pos["custom_break_level_filled"]
    else:
        # overwrite only where empty/NaN
        cur = pd.to_numeric(pos["custom_break_level"], errors="coerce")
        mask = cur.isna()
        pos.loc[mask, "custom_break_level"] = pos.loc[mask, "custom_break_level_filled"]

    pos.to_csv(args.out, index=False)
    print(f"Saved: {args.out}")

    if args.inplace:
        pos.drop(columns=["custom_break_level_filled"]).to_csv(args.positions, index=False)
        print(f"Overwrote: {args.positions}")


if __name__ == "__main__":
    main()
