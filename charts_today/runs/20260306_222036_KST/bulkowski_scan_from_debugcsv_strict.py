#!/usr/bin/env python3
"""
bulkowski_scan_from_debugcsv_strict.py

Stricter replacement for bulkowski_scan_from_debugcsv_yf_compatible.py:
- Uses daily CLOSE breakout, not just "latest close above r60".
- Requires volume confirmation on breakout day (>= VOL_MULT * avg20).
- Marks entry_ready only if a "hold" confirmation is present after breakout:
    - retest day: low <= level*(1+tol) and close >= level
      OR
    - 2 consecutive closes >= level

Outputs:
- prints top N with status
- optionally writes candidates.txt for the next stage

Usage:
  python bulkowski_scan_from_debugcsv_strict.py --debug premarket_auto_debug.csv --top 10 --out candidates.txt
"""

import argparse
import re
import io
from contextlib import redirect_stdout, redirect_stderr
from datetime import datetime
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
import yfinance as yf

LOOKBACK = 60
RECENT = 10
VOL_AVG = 20
VOL_MULT = 1.3
HOLD_WINDOW = 5
CONSEC_CLOSES = 2

ETF_1X = {"XLE", "XOP", "OIH", "XLB", "IYE", "IYM"}
ETF_2X = {"GUSH", "ERX", "UCO", "BOIL", "DIG", "UYM"}

def tol_pct_for_ticker(ticker: str) -> float:
    t = ticker.upper()
    if t in ETF_2X:
        return 4.5
    if t in ETF_1X:
        return 1.75
    return 2.75

def silent_download(symbol: str, period="18mo") -> pd.DataFrame:
    buf = io.StringIO()
    with redirect_stdout(buf), redirect_stderr(buf):
        return yf.download(symbol, period=period, interval="1d", auto_adjust=False, progress=False, threads=False)

def load_universe(debug_csv: str) -> list[str]:
    df = pd.read_csv(debug_csv, comment="#")
    # handle error/note columns
    err_col = "error" if "error" in df.columns else ("note" if "note" in df.columns else None)
    ok_err = (df[err_col].isna() | (df[err_col].astype(str).str.strip()=="")) if err_col else pd.Series([True]*len(df), index=df.index)
    if "premarket" in df.columns:
        ok_px = pd.to_numeric(df["premarket"], errors="coerce").notna()
    else:
        ok_px = pd.Series([True]*len(df), index=df.index)

    df = df[(df.get("group","")=="tickers_core") & ok_err & ok_px]

    sym_col = None
    for c in ["yahoo_symbol","yf_symbol","symbol","ticker"]:
        if c in df.columns:
            sym_col = c
            break
    if sym_col is None:
        raise KeyError(f"No symbol column found in {debug_csv}")

    tickers = df[sym_col].dropna().astype(str).str.upper().unique().tolist()

    # sanity + exclude 2x by default
    exclude = {"ERX","DIG","GUSH","UYM","BOIL","UCO"}
    tickers = [t for t in tickers if t not in exclude]
    tickers = [t for t in tickers if re.match(r"^[A-Z0-9\^\=\.\-\/]{1,20}$", t)]
    return tickers

def prior_level_series(df: pd.DataFrame) -> pd.Series:
    return df["High"].rolling(LOOKBACK).max().shift(1)

def volume_confirmed(df: pd.DataFrame, idx: int) -> bool:
    vol = df["Volume"].astype(float)
    avg = vol.rolling(VOL_AVG).mean().shift(1)
    if pd.isna(avg.iloc[idx]):
        return False
    return bool(vol.iloc[idx] >= VOL_MULT * avg.iloc[idx])

def find_breakout(df: pd.DataFrame, lvl: pd.Series):
    close = df["Close"].astype(float)
    cond = lvl.notna() & (close > lvl)
    start = max(0, len(df)-RECENT)
    sub = cond.iloc[start:]
    if not sub.any():
        return None, None
    idx = int(np.where(sub.values)[0][-1] + start)
    return idx, float(lvl.iloc[idx])

def hold_confirmed(df: pd.DataFrame, breakout_idx: int, level: float, tol_pct: float) -> bool:
    if breakout_idx is None:
        return False
    start = breakout_idx + 1
    end = min(len(df), breakout_idx + 1 + HOLD_WINDOW)
    if start >= end:
        return False
    w = df.iloc[start:end]
    tol = level * (tol_pct/100.0)
    retest = (w["Low"] <= (level + tol)) & (w["Close"] >= level)
    if retest.any():
        return True
    above = (w["Close"] >= level).astype(int).to_numpy()
    if len(above) >= CONSEC_CLOSES:
        for i in range(0, len(above)-CONSEC_CLOSES+1):
            if above[i:i+CONSEC_CLOSES].sum() == CONSEC_CLOSES:
                return True
    return False

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--debug", default="premarket_auto_debug.csv")
    ap.add_argument("--top", type=int, default=10)
    ap.add_argument("--out", default=None, help="write selected symbols to this file (one per line)")
    args = ap.parse_args()

    universe = load_universe(args.debug)
    rows = []

    for sym in universe:
        df = silent_download(sym)
        if df is None or df.empty:
            continue
        df = df.dropna().copy()
        if isinstance(df.columns, pd.MultiIndex):
            lvl1 = df.columns.get_level_values(1)
            if sym in set(lvl1):
                df = df.xs(sym, level=1, axis=1).copy()
        need = {"Open","High","Low","Close","Volume"}
        if not need.issubset(set(df.columns)) or len(df) < (LOOKBACK + VOL_AVG + 10):
            continue

        lvl = prior_level_series(df)
        bidx, blevel = find_breakout(df, lvl)
        tol = tol_pct_for_ticker(sym)

        if bidx is None:
            status = "NO_BREAKOUT"
            vol_ok = False
            hold_ok = False
        else:
            vol_ok = volume_confirmed(df, bidx)
            hold_ok = hold_confirmed(df, bidx, blevel, tol)
            if vol_ok and hold_ok:
                status = "ENTRY_READY"
            elif vol_ok:
                status = "BREAKOUT(vol_ok)_WAIT_HOLD"
            else:
                status = "BREAKOUT_WAIT_VOL"

        last_close = float(df["Close"].iloc[-1])
        rows.append({
            "symbol": sym,
            "status": status,
            "break_level": round(float(blevel) if blevel else np.nan, 4),
            "breakout_date": str(df.index[bidx].date()) if bidx is not None else "",
            "vol_confirmed": bool(vol_ok),
            "hold_confirmed": bool(hold_ok),
            "last_close": round(last_close, 4),
        })

    out = pd.DataFrame(rows)
    if out.empty:
        print("No results.")
        return

    rank = {"ENTRY_READY":0,"BREAKOUT(vol_ok)_WAIT_HOLD":1,"BREAKOUT_WAIT_VOL":2,"NO_BREAKOUT":9}
    out["rank"] = out["status"].map(rank).fillna(9).astype(int)
    out = out.sort_values(["rank","symbol"]).drop(columns=["rank"]).reset_index(drop=True)

    print("\n=== BULKOWSKI STRICT CANDIDATES ===")
    print(out.head(args.top).to_string(index=False))

    if args.out:
        selected = out[out["status"].isin(["ENTRY_READY","BREAKOUT(vol_ok)_WAIT_HOLD","BREAKOUT_WAIT_VOL"])].head(args.top)["symbol"].tolist()
        Path(args.out).write_text("\n".join(selected) + "\n", encoding="utf-8")
        print(f"\nSaved: {args.out}  (count={len(selected)})")

    ts = datetime.now(ZoneInfo("Asia/Seoul")).strftime("%Y%m%d_%H%M%S")
    out.to_csv(f"bulkowski_strict_{ts}_KST.csv", index=False)

if __name__ == "__main__":
    from pathlib import Path
    main()
