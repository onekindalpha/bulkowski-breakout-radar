#!/usr/bin/env python3
"""
bulkowski_scan_from_debugcsv_pattern_v1.py

Purpose (sieve #1, pattern-aware):
- Build top-N candidates using BOTH:
  (A) Bulkowski-style breakout confirmation (daily CLOSE breakout + volume confirmation + optional hold)
  (B) Pre-breakout "setup" patterns (ascending triangle / rectangle / double bottom-ish) near resistance

Why this exists:
- Your earlier strict scripts mostly rank "breakout + volume" but not the *shape* of the base.
- This adds a lightweight, reproducible shape score so the first sieve isn't "breakout-only".

Universe:
- Reads premarket_auto_debug.csv and includes groups by default:
    tickers_core, tickers_leverage2x, finviz_manual
- Includes 2x tickers, but caps how many 2x can appear in candidates.txt (default max_2x=3).

Outputs:
- prints top-N table
- writes candidates.txt (default) with selected symbols
- writes candidates_2x.txt with ranked 2x subset (if any)

Usage:
  python bulkowski_scan_from_debugcsv_pattern_v1.py
  python bulkowski_scan_from_debugcsv_pattern_v1.py --top 10 --out candidates.txt --max-2x 3
"""

import argparse
import io
import re
from contextlib import redirect_stdout, redirect_stderr
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
import yfinance as yf

# --- parameters (tune once, keep stable) ---
LOOKBACK = 60              # base window
RECENT_BREAKOUT = 10       # breakout must be within last N sessions to be considered "fresh"
VOL_AVG = 20
VOL_MULT = 1.3             # breakout volume confirmation
HOLD_WINDOW = 5
CONSEC_CLOSES = 2

# setup detection
NEAR_RESIST_PCT = 1.0      # "setup" if last close within this % below resistance
TOUCH_PCT = 0.8            # touch counting tolerance (%)
MIN_TOUCHES = 3

# 2x sets
ETF_2X = {"GUSH", "ERX", "UCO", "BOIL", "DIG", "UYM"}
ETF_1X = {"XLE", "XOP", "OIH", "XLB", "IYE", "IYM"}

TICKER_RE = re.compile(r"^[A-Z0-9\^\=\.\-\/]{1,20}$")
KST = ZoneInfo("Asia/Seoul")


def kst_ts():
    return datetime.now(KST).strftime("%Y%m%d_%H%M%S")


def silent_download(symbol: str, period="2y") -> pd.DataFrame:
    buf = io.StringIO()
    with redirect_stdout(buf), redirect_stderr(buf):
        return yf.download(symbol, period=period, interval="1d", auto_adjust=False, progress=False, threads=False)


def normalize(df: pd.DataFrame, sym: str) -> pd.DataFrame:
    if df is None or df.empty:
        return pd.DataFrame()
    out = df.copy()
    if isinstance(out.columns, pd.MultiIndex):
        lvl1 = out.columns.get_level_values(1)
        lvl0 = out.columns.get_level_values(0)
        if sym in set(lvl1):
            out = out.xs(sym, level=1, axis=1).copy()
        elif sym in set(lvl0):
            out = out[sym].copy()
    out.columns = [str(c).strip() for c in out.columns]
    need = {"Open","High","Low","Close","Volume"}
    if not need.issubset(set(out.columns)):
        return pd.DataFrame()
    out = out[list(need)].dropna().sort_index()
    if not isinstance(out.index, pd.DatetimeIndex):
        out.index = pd.to_datetime(out.index, errors="coerce")
        out = out.dropna()
    if out.index.tz is not None:
        out.index = out.index.tz_localize(None)
    return out


def load_universe(debug_csv: str, groups: set[str]) -> list[str]:
    df = pd.read_csv(debug_csv, comment="#")
    sym_col = None
    for c in ["yahoo_symbol","yf_symbol","symbol","ticker"]:
        if c in df.columns:
            sym_col = c
            break
    if sym_col is None:
        raise KeyError("No symbol column in debug csv")

    if "group" in df.columns:
        df = df[df["group"].astype(str).isin(groups)].copy()

    # drop rows with explicit errors if present
    err_col = "error" if "error" in df.columns else ("note" if "note" in df.columns else None)
    if err_col:
        ok = df[err_col].isna() | (df[err_col].astype(str).str.strip() == "")
        df = df[ok].copy()

    syms = df[sym_col].dropna().astype(str).str.upper().unique().tolist()
    syms = [s for s in syms if TICKER_RE.match(s)]
    return syms


def prior_resistance_series(df: pd.DataFrame, lookback=LOOKBACK) -> pd.Series:
    return df["High"].rolling(lookback).max().shift(1)


def volume_confirmed(df: pd.DataFrame, idx: int) -> bool:
    vol = df["Volume"].astype(float)
    avg = vol.rolling(VOL_AVG).mean().shift(1)
    if idx is None or idx < 0 or idx >= len(df) or pd.isna(avg.iloc[idx]):
        return False
    return bool(vol.iloc[idx] >= VOL_MULT * avg.iloc[idx])


def find_recent_breakout(df: pd.DataFrame, lvl: pd.Series) -> tuple[int | None, float | None]:
    close = df["Close"].astype(float)
    cond = lvl.notna() & (close > lvl)
    start = max(0, len(df) - RECENT_BREAKOUT)
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


def tol_pct_for(sym: str) -> float:
    s = sym.upper()
    if s in ETF_2X:
        return 4.5
    if s in ETF_1X:
        return 1.75
    return 2.75


def regression_slope(y: np.ndarray) -> float:
    x = np.arange(len(y), dtype=float)
    if len(y) < 5:
        return 0.0
    # polyfit slope
    return float(np.polyfit(x, y, 1)[0])


def double_bottom_score(df: pd.DataFrame, window=120) -> float:
    if len(df) < window + 20:
        return 0.0
    low = df["Low"].astype(float).iloc[-window:].to_numpy()
    close = df["Close"].astype(float).to_numpy()

    idx = np.argsort(low)[:12]
    idx = np.sort(idx)
    best = 0.0
    for i in range(len(idx)):
        for j in range(i+1, len(idx)):
            a, b = idx[i], idx[j]
            if b - a < 15:
                continue
            la, lb = low[a], low[b]
            if abs(la - lb) / max(la, 1e-9) > 0.03:
                continue
            # mid peak (neckline)
            # map to full-series indices for close extraction
            base_start = len(df) - window
            mid_peak = float(np.max(close[base_start + a : base_start + b]))
            last = float(close[-1])
            sc = 2.0
            if last >= mid_peak:
                sc += 1.0
            best = max(best, sc)
    return best


def pattern_shape_score(df: pd.DataFrame, resistance: float) -> tuple[str, float, dict]:
    """
    Lightweight base-shape scoring around a resistance line (prior 60d high).
    Returns (pattern_label, score, diagnostics)
    """
    d = df.tail(LOOKBACK).copy()
    high = d["High"].astype(float).to_numpy()
    low = d["Low"].astype(float).to_numpy()
    close = d["Close"].astype(float).to_numpy()

    # touches near resistance
    tol = resistance * (TOUCH_PCT/100.0)
    touches = int(np.sum(np.abs(high - resistance) <= tol))

    # higher lows slope
    slope = regression_slope(low)

    # base depth
    depth = float((resistance - np.min(low)) / resistance) if resistance > 0 else 1.0

    # volatility contraction (last 10 vs last 60)
    ret = pd.Series(close).pct_change().dropna()
    vol60 = float(ret.tail(LOOKBACK-1).std()) if len(ret) >= 20 else np.nan
    vol10 = float(ret.tail(10).std()) if len(ret) >= 12 else np.nan
    vol_ratio = float(vol10/vol60) if (np.isfinite(vol60) and vol60 > 0 and np.isfinite(vol10)) else np.nan

    # score components
    score = 0.0
    if touches >= MIN_TOUCHES:
        score += 2.0
    elif touches == 2:
        score += 1.0

    if slope > 0:
        score += 1.5
    elif slope < 0:
        score -= 1.0

    # prefer "not too deep, not too shallow" (bulkowski-ish bases)
    if 0.08 <= depth <= 0.30:
        score += 2.0
    elif depth < 0.05:
        score -= 0.5
    elif depth > 0.40:
        score -= 1.0

    if np.isfinite(vol_ratio):
        if vol_ratio < 0.9:
            score += 1.0  # contraction
        elif vol_ratio > 1.2:
            score -= 0.5

    # pick label
    label = "BASE"
    if touches >= MIN_TOUCHES and slope > 0:
        label = "ASC_TRIANGLE"
    elif touches >= MIN_TOUCHES:
        label = "RECTANGLE"

    # add double bottom as overlay
    db = double_bottom_score(df, 120)
    if db >= 2.5:
        label = "DOUBLE_BOTTOM"
        score += min(2.5, db)

    diag = {
        "touches": touches,
        "slope_low": round(slope, 6),
        "depth": round(depth, 4),
        "vol_ratio_10_60": round(vol_ratio, 3) if np.isfinite(vol_ratio) else np.nan,
        "db_score": round(db, 2),
    }
    return label, float(score), diag


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--debug", default="premarket_auto_debug.csv")
    ap.add_argument("--top", type=int, default=10)
    ap.add_argument("--out", default="candidates.txt")
    ap.add_argument("--groups", default="tickers_core,tickers_leverage2x,finviz_manual")
    ap.add_argument("--max-2x", type=int, default=3)
    args = ap.parse_args()

    groups = {g.strip() for g in args.groups.split(",") if g.strip()}
    universe = load_universe(args.debug, groups)

    rows = []
    for sym in universe:
        df_raw = silent_download(sym, period="2y")
        df = normalize(df_raw, sym)
        if df.empty or len(df) < (LOOKBACK + VOL_AVG + 30):
            continue

        lvl = prior_resistance_series(df, LOOKBACK)
        resistance = float(lvl.iloc[-1]) if pd.notna(lvl.iloc[-1]) else float(df["High"].tail(LOOKBACK).max())
        last_close = float(df["Close"].iloc[-1])

        # pattern shape score (base)
        label, pscore, diag = pattern_shape_score(df, resistance)

        # breakout logic
        bidx, blevel = find_recent_breakout(df, lvl)
        tol = tol_pct_for(sym)
        vol_ok = volume_confirmed(df, bidx) if bidx is not None else False
        hold_ok = hold_confirmed(df, bidx, blevel, tol) if (bidx is not None and blevel is not None) else False

        status = "NO_SIGNAL"
        status_rank = 9
        if bidx is not None and vol_ok and hold_ok:
            status = "ENTRY_READY"
            status_rank = 0
        elif bidx is not None and vol_ok:
            status = "BREAKOUT_VOL_OK_WAIT_HOLD"
            status_rank = 1
        elif bidx is not None:
            status = "BREAKOUT_WAIT_VOL"
            status_rank = 2
        else:
            # setup near resistance (pre-breakout)
            dist_pct = (resistance / last_close - 1.0) * 100 if last_close > 0 else 999
            if 0 <= dist_pct <= NEAR_RESIST_PCT and pscore >= 2.5:
                status = "SETUP_NEAR_BREAK"
                status_rank = 3

        rows.append({
            "symbol": sym,
            "is_2x": sym in ETF_2X,
            "status": status,
            "status_rank": status_rank,
            "pattern": label,
            "pattern_score": round(pscore, 2),
            "break_level": round(float(blevel) if blevel is not None else resistance, 3),
            "breakout_date": str(df.index[bidx].date()) if bidx is not None else "",
            "vol_confirmed": bool(vol_ok),
            "hold_confirmed": bool(hold_ok),
            "last_close": round(last_close, 3),
            **{f"diag_{k}": v for k, v in diag.items()},
        })

    out = pd.DataFrame(rows)
    if out.empty:
        print("No results.")
        return

    # sort: status_rank (lower better), then pattern_score desc, then non-2x first, then symbol
    out = out.sort_values(["status_rank", "pattern_score", "is_2x", "symbol"], ascending=[True, False, True, True]).reset_index(drop=True)

    print("\n=== BULKOWSKI PATTERN SIEVE (v1) ===")
    cols = ["symbol","status","pattern","pattern_score","break_level","breakout_date","vol_confirmed","hold_confirmed","last_close","is_2x"]
    print(out[cols].head(args.top).to_string(index=False))

    # write candidates with 2x cap
    selected = []
    two_x = 0
    for _, r in out.iterrows():
        if len(selected) >= args.top:
            break
        if r["status"] == "NO_SIGNAL":
            continue
        if bool(r["is_2x"]):
            if two_x >= args.max_2x:
                continue
            two_x += 1
        selected.append(str(r["symbol"]))

    Path(args.out).write_text("\n".join(selected) + "\n", encoding="utf-8")
    print(f"\nSaved: {args.out}  (count={len(selected)}, included_2x={two_x})")

    # 2x list for reference
    out_2x = out[out["is_2x"]].copy()
    if not out_2x.empty:
        Path("candidates_2x.txt").write_text("\n".join(out_2x["symbol"].tolist()) + "\n", encoding="utf-8")
        print("Saved: candidates_2x.txt")

    # snapshot
    out.to_csv(f"bulkowski_pattern_sieve_{kst_ts()}_KST.csv", index=False)


if __name__ == "__main__":
    main()
