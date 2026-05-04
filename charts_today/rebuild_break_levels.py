#!/usr/bin/env python3
"""
rebuild_break_levels.py

Fix "custom_break_level is arbitrary" by rebuilding it from a reproducible rule.

Priority (per ticker):
  1) If a recent report exists (report_v2.csv or newest report_v2_*_KST.csv) AND contains daily_break_level for the ticker:
       break_level := report.daily_break_level   (best proxy for "the breakout line you saw in scan")
  2) Else use Yahoo daily bars:
       break_level := prior 60-trading-day HIGH (rolling max High shifted by 1)
       evaluated on entry_date if provided, else latest bar.

Modes:
  --overwrite all    (default) : overwrite every custom_break_level (even if currently filled)
  --overwrite missing          : only fill blanks/NaN

Outputs:
  - positions_rebuilt.csv (default)
  - optionally overwrite positions.csv with --inplace
  - positions_level_audit_<ts>.csv (shows old vs new, diff%)

positions.csv columns required:
  ticker,entry_price,shares
optional:
  custom_break_level, entry_date (YYYY-MM-DD)

Usage:
  python rebuild_break_levels.py
  python rebuild_break_levels.py --overwrite missing
  python rebuild_break_levels.py --inplace
"""

from __future__ import annotations

import argparse
import io
from contextlib import redirect_stdout, redirect_stderr
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
import yfinance as yf

KST = ZoneInfo("Asia/Seoul")
LOOKBACK = 60


def kst_stamp():
    return datetime.now(KST).strftime("%Y%m%d_%H%M%S")


def silent_download(*args, **kwargs) -> pd.DataFrame:
    buf = io.StringIO()
    with redirect_stdout(buf), redirect_stderr(buf):
        return yf.download(*args, **kwargs)


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
    return out


def newest_report_path() -> Path | None:
    stamped = sorted(Path(".").glob("report_v2_*_KST.csv"), key=lambda p: p.stat().st_mtime, reverse=True)
    if stamped:
        return stamped[0]
    p = Path("report_v2.csv")
    return p if p.exists() else None


def load_break_levels_from_report() -> dict[str, float]:
    rp = newest_report_path()
    if rp is None:
        return {}
    try:
        df = pd.read_csv(rp, comment="#")
    except Exception:
        return {}
    if not {"ticker", "daily_break_level"}.issubset(df.columns):
        return {}
    out = {}
    for _, r in df.dropna(subset=["ticker", "daily_break_level"]).iterrows():
        t = str(r["ticker"]).strip().upper()
        try:
            out[t] = float(r["daily_break_level"])
        except Exception:
            pass
    return out


def download_daily(sym: str, period="2y") -> pd.DataFrame:
    df = silent_download(sym, period=period, interval="1d", auto_adjust=False, progress=False, threads=False)
    df = normalize(df, sym)
    need = {"High", "Close"}
    if df.empty or not need.issubset(set(df.columns)):
        return pd.DataFrame()
    df = df[list(need)].dropna().sort_index()
    if not isinstance(df.index, pd.DatetimeIndex):
        df.index = pd.to_datetime(df.index, errors="coerce")
        df = df.dropna()
    if isinstance(df.index, pd.DatetimeIndex) and df.index.tz is not None:
        df.index = df.index.tz_localize(None)
    return df


def prior_60d_high_shifted(df: pd.DataFrame, lookback=LOOKBACK) -> pd.Series:
    return df["High"].rolling(lookback).max().shift(1)


def pick_index_for_date(idx: pd.DatetimeIndex, target: pd.Timestamp) -> int:
    if len(idx) == 0:
        return -1
    if target >= idx[-1]:
        return len(idx) - 1
    pos = idx.searchsorted(target, side="right") - 1
    return int(max(0, pos))


def compute_level_from_daily(df: pd.DataFrame, entry_date: str) -> float:
    lvl = prior_60d_high_shifted(df, LOOKBACK)
    if entry_date:
        try:
            target = pd.to_datetime(entry_date).tz_localize(None)
            i = pick_index_for_date(df.index, target)
        except Exception:
            i = len(df) - 1
    else:
        i = len(df) - 1
    val = lvl.iloc[i] if (0 <= i < len(lvl)) else np.nan
    if pd.isna(val):
        return float(df["High"].tail(LOOKBACK).max())
    return float(val)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--positions", default="positions.csv")
    ap.add_argument("--out", default="positions_rebuilt.csv")
    ap.add_argument("--inplace", action="store_true", help="overwrite positions.csv too")
    ap.add_argument("--overwrite", choices=["all", "missing"], default="all",
                    help="overwrite all existing levels or fill only missing (default all)")
    args = ap.parse_args()

    p = Path(args.positions)
    if not p.exists():
        raise SystemExit(f"{args.positions} not found")

    df = pd.read_csv(p)
    df.columns = [c.strip() for c in df.columns]
    need = {"ticker", "entry_price", "shares"}
    miss = need - set(df.columns)
    if miss:
        raise SystemExit(f"positions.csv missing columns: {sorted(miss)}")

    df["ticker"] = df["ticker"].astype(str).str.strip().str.upper()
    df["entry_price"] = pd.to_numeric(df["entry_price"], errors="coerce")
    df["shares"] = pd.to_numeric(df["shares"], errors="coerce")

    if "custom_break_level" not in df.columns:
        df["custom_break_level"] = np.nan
    else:
        df["custom_break_level"] = pd.to_numeric(df["custom_break_level"], errors="coerce")

    if "entry_date" not in df.columns:
        df["entry_date"] = ""
    else:
        df["entry_date"] = df["entry_date"].astype(str).str.strip()

    if df["ticker"].duplicated().any():
        dups = sorted(set(df[df["ticker"].duplicated(keep=False)]["ticker"].tolist()))
        raise SystemExit(f"Duplicate tickers in positions.csv: {dups}")

    report_levels = load_break_levels_from_report()

    new_levels = []
    srcs = []
    for _, r in df.iterrows():
        t = r["ticker"]
        cur = r["custom_break_level"]
        if args.overwrite == "missing" and np.isfinite(cur):
            new_levels.append(float(cur))
            srcs.append("kept_existing")
            continue

        if t in report_levels and np.isfinite(report_levels[t]):
            new_levels.append(float(report_levels[t]))
            srcs.append("report_daily_break_level")
            continue

        ddf = download_daily(t, period="2y")
        if ddf.empty:
            new_levels.append(np.nan)
            srcs.append("missing_daily")
            continue
        new_levels.append(round(compute_level_from_daily(ddf, r["entry_date"]), 4))
        srcs.append("auto_prior60d_high_shifted")

    audit = df[["ticker", "entry_price", "custom_break_level"]].copy()
    audit["rebuilt_break_level"] = new_levels
    audit["rebuilt_src"] = srcs
    audit["diff_pct_vs_old"] = np.where(
        np.isfinite(audit["custom_break_level"]),
        (audit["rebuilt_break_level"] / audit["custom_break_level"] - 1.0) * 100.0,
        np.nan
    ).round(2)

    df["custom_break_level"] = new_levels

    audit_name = f"positions_level_audit_{kst_stamp()}_KST.csv"
    audit.to_csv(audit_name, index=False)
    df.to_csv(args.out, index=False)

    print(f"Saved: {args.out}")
    print(f"Saved: {audit_name}")
    if args.inplace:
        df.to_csv(args.positions, index=False)
        print(f"Overwrote: {args.positions}")


if __name__ == "__main__":
    main()
