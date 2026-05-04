#!/usr/bin/env python3
"""bulkowski_scan_from_debugcsv_pattern_v2.py  (B-mode levels)

- DOUBLE_BOTTOM -> neckline (peak between two bottoms)
- ASC_TRIANGLE  -> flat resistance (clustered swing highs)
- fallback      -> prior 60d high (shifted)

Outputs:
  candidates.txt
  candidates_2x.txt
  candidates_meta.csv + candidates_meta_<ts>_KST.csv
"""
from __future__ import annotations
import argparse, io
from contextlib import redirect_stdout, redirect_stderr
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo
import numpy as np
import pandas as pd
import yfinance as yf
from pipeline_config import load_default_groups, union_ordered

KST = ZoneInfo("Asia/Seoul")
LOOKBACK = 60
VOL_AVG = 20
VOL_MULT = 1.3
HOLD_WINDOW = 8
CONSEC_CLOSES = 2
NEAR_RESIST_PCT = 3.0
ETF_2X = {"GUSH","ERX","UCO","BOIL","DIG","UYM"}

def now_kst_stamp():
    return datetime.now(KST).strftime("%Y%m%d_%H%M%S")

def silent_download(symbol: str, period="2y") -> pd.DataFrame:
    buf = io.StringIO()
    with redirect_stdout(buf), redirect_stderr(buf):
        return yf.download(symbol, period=period, interval="1d", auto_adjust=False, progress=False, threads=False)

def normalize(raw: pd.DataFrame, ticker: str) -> pd.DataFrame:
    if raw is None or raw.empty: return pd.DataFrame()
    df = raw.copy()
    if isinstance(df.columns, pd.MultiIndex):
        lvl1 = df.columns.get_level_values(1)
        if ticker in set(lvl1): df = df.xs(ticker, level=1, axis=1).copy()
    df.columns = [str(c).strip() for c in df.columns]
    need = {"Open","High","Low","Close","Volume"}
    if not need.issubset(df.columns): return pd.DataFrame()
    df = df[list(need)].dropna().sort_index()
    if not isinstance(df.index, pd.DatetimeIndex):
        df.index = pd.to_datetime(df.index, errors="coerce"); df = df.dropna()
    if isinstance(df.index, pd.DatetimeIndex) and df.index.tz is not None:
        df.index = df.index.tz_localize(None)
    return df

def prior_resistance(df: pd.DataFrame, lookback=LOOKBACK) -> float:
    s = df["High"].rolling(lookback).max().shift(1)
    v = s.iloc[-1]
    return float(v) if pd.notna(v) else float(df["High"].tail(lookback).max())

def volume_confirmed(df: pd.DataFrame, idx: int) -> bool:
    if idx is None or idx <= VOL_AVG: return False
    vol = df["Volume"].astype(float)
    avg = vol.rolling(VOL_AVG).mean().shift(1)
    if pd.isna(avg.iloc[idx]): return False
    return bool(vol.iloc[idx] >= VOL_MULT * avg.iloc[idx])

def tol_pct(sym: str) -> float:
    return 4.5 if sym.upper() in ETF_2X else 2.75

def pivot_points(df: pd.DataFrame, pivot=2):
    h = df["High"].to_numpy(); l = df["Low"].to_numpy()
    ph, pl = [], []
    for i in range(pivot, len(df)-pivot):
        if h[i] == np.max(h[i-pivot:i+pivot+1]): ph.append(i)
        if l[i] == np.min(l[i-pivot:i+pivot+1]): pl.append(i)
    return ph, pl

def detect_double_bottom(df: pd.DataFrame, window=180, pivot=2, tol_pct_=6.0, min_sep=10):
    d = df.tail(min(window, len(df))).copy()
    ph, pl = pivot_points(d, pivot=pivot)
    if len(pl) < 2: return None, 0.0, {"db":"no_two_pivot_lows"}
    lows = d["Low"].to_numpy()
    cand = pl[-10:]
    best = None
    for a in range(len(cand)-1):
        for b in range(a+1, len(cand)):
            i1, i2 = cand[a], cand[b]
            if (i2-i1) < min_sep: continue
            lo1, lo2 = lows[i1], lows[i2]
            if lo1 <= 0 or lo2 <= 0: continue
            diff = abs(lo1-lo2)/min(lo1,lo2)*100
            if diff > tol_pct_: continue
            neck = float(d["High"].iloc[i1:i2+1].max())
            depth = (neck/min(lo1,lo2) - 1.0)*100
            if depth < 8: continue
            score = (10-diff) + min(depth/5, 6) + (i2/len(d))*2
            if best is None or score > best[0]:
                best = (score, neck, lo1, lo2, diff, depth)
    if best is None: return None, 0.0, {"db":"no_valid_pair"}
    score, neck, lo1, lo2, diff, depth = best
    diag = {"db_lo1": round(float(lo1),3), "db_lo2": round(float(lo2),3), "db_diff_pct": round(float(diff),2),
            "db_depth_pct": round(float(depth),2), "db_neck": round(float(neck),3)}
    return float(neck), float(score), diag

def detect_asc_triangle(df: pd.DataFrame, window=180, pivot=2, flat_tol_pct=3.0):
    d = df.tail(min(window, len(df))).copy()
    ph, pl = pivot_points(d, pivot=pivot)
    if len(ph) < 2 or len(pl) < 2: return None, 0.0, {"tri":"not_enough_pivots"}
    highs = d["High"].to_numpy(); lows = d["Low"].to_numpy()
    hidx = ph[-8:]
    hvals = [highs[i] for i in hidx]
    top = float(np.median(hvals))
    flat = max(abs(h-top)/top*100 for h in hvals)
    if flat > flat_tol_pct: return None, 0.0, {"tri":"highs_not_flat", "tri_flat_pct": round(float(flat),2)}
    lidx = pl[-8:]
    x = np.array(lidx, dtype=float); y = np.array([lows[i] for i in lidx], dtype=float)
    if len(x) < 2: return None, 0.0, {"tri":"no_low_fit"}
    A = np.vstack([x, np.ones(len(x))]).T
    m, c = np.linalg.lstsq(A, y, rcond=None)[0]
    if m <= 0: return None, 0.0, {"tri":"lows_not_rising", "tri_slope": round(float(m),6)}
    score = 6.0 + max(0, (flat_tol_pct-flat)) + min((m/top)*1000, 6)
    diag = {"tri_res": round(float(top),3), "tri_flat_pct": round(float(flat),2), "tri_slope": round(float(m),6)}
    return float(top), float(score), diag

def pattern_shape(df: pd.DataFrame, fallback_res: float):
    neck, sdb, ddb = detect_double_bottom(df)
    if neck is not None and sdb >= 6.0: return "DOUBLE_BOTTOM", sdb, ddb, neck
    res, stri, dtri = detect_asc_triangle(df)
    if res is not None and stri >= 6.0: return "ASC_TRIANGLE", stri, dtri, res
    return "NONE", 0.0, {"fallback_res": round(float(fallback_res),3)}, None

def recent_breakout(df: pd.DataFrame, level: float, recent=10):
    c = df["Close"].astype(float)
    s = max(0, len(df)-recent)
    cond = c.iloc[s:] > float(level)
    if not cond.any(): return None
    return int(np.where(cond.values)[0][-1] + s)

def hold_ok(df: pd.DataFrame, idx: int, level: float, tol_: float):
    if idx is None: return False
    start = idx+1; end = min(len(df), idx+1+HOLD_WINDOW)
    if start >= end: return False
    w = df.iloc[start:end]
    tolv = level*(tol_/100.0)
    retest = (w["Low"] <= (level+tolv)) & (w["Close"] >= level)
    if retest.any(): return True
    above = (w["Close"] >= level).astype(int).to_numpy()
    for i in range(0, len(above)-CONSEC_CLOSES+1):
        if above[i:i+CONSEC_CLOSES].sum() == CONSEC_CLOSES: return True
    return False

def main():
    ap = argparse.ArgumentParser()

ap.add_argument("--break-mode", choices=["a","b"], default="b",
                help="A=use fallback prior-60d-high break_level only; B=use pattern break_level when detected (double bottom neckline / ascending triangle resistance).")
    ap.add_argument("--top", type=int, default=10)
    ap.add_argument("--out", default="candidates.txt")
    ap.add_argument("--max-2x", type=int, default=3)
    ap.add_argument("--groups", default="tickers_core,tickers_leverage2x,finviz_manual,macro_watch_yahoo")
    args = ap.parse_args()
    break_mode = args.break_mode
    groups = {g.strip() for g in args.groups.split(",") if g.strip()}
    all_groups = load_default_groups()  # list[GroupTickers]
    use = [g for g in all_groups if (not groups) or (g.group in groups)]
    universe = union_ordered(use)

    rows = []
    for sym in universe:
        df = normalize(silent_download(sym, "2y"), sym)
        if df.empty or len(df) < (LOOKBACK+VOL_AVG+30): continue
        fallback = prior_resistance(df, LOOKBACK)
        label, pscore, diag, override = pattern_shape(df, fallback)
        level = float(override) if override is not None else float(fallback)

        bidx = recent_breakout(df, level, recent=10)
        vol_ok = volume_confirmed(df, bidx) if bidx is not None else False
        h_ok = hold_ok(df, bidx, level, tol_pct(sym)) if bidx is not None else False

        status, rank = "NO_SIGNAL", 9
        if h_ok and vol_ok: status, rank = "BREAKOUT_VOL_OK_HOLD_OK", 0
        elif bidx is not None and vol_ok: status, rank = "BREAKOUT_VOL_OK_WAIT_HOLD", 1
        elif bidx is not None: status, rank = "BREAKOUT_WAIT_VOL", 2
        else:
            last_close = float(df["Close"].iloc[-1])
            dist = (level/last_close - 1.0)*100 if last_close>0 else 999
            if 0 <= dist <= NEAR_RESIST_PCT and pscore >= 2.5:
                status, rank = "SETUP_NEAR_BREAK", 3


# If break-mode A, ignore detected pattern and use fallback (prior 60d high shifted) as break_level.

if break_mode == "a":

    pattern = "FALLBACK_60D_HIGH"

    pattern_score = 0.0

    # keep break_level as fallback (it may already be fallback); ensure src reflects that.

    break_level_src = "auto_prior60d_high_shifted"


        rows.append({
            "ticker": sym, "is_2x": sym.upper() in ETF_2X, "status": status, "status_rank": rank,
            "pattern": label, "pattern_score": round(pscore,2), "break_level": round(level,3),
            "breakout_date": str(df.index[bidx].date()) if bidx is not None else "",
            "vol_confirmed": bool(vol_ok), "hold_confirmed": bool(h_ok), "last_close": round(float(df["Close"].iloc[-1]),3),
            **{f"diag_{k}": v for k,v in diag.items()}
        })

    out = pd.DataFrame(rows)
    if out.empty:
        print("No results."); return
    out = out.sort_values(["status_rank","pattern_score","is_2x","ticker"], ascending=[True, False, True, True]).reset_index(drop=True)

    selected=[]; cnt2x=0
    for _, r in out.iterrows():
        if bool(r["is_2x"]) and cnt2x >= args.max_2x: continue
        selected.append(r)
        if bool(r["is_2x"]): cnt2x += 1
        if len(selected) >= args.top: break
    sel = pd.DataFrame(selected).reset_index(drop=True)

    print("\n=== BULKOWSKI PATTERN SIEVE (v2 / B-mode) ===")
    show=["ticker","status","pattern","pattern_score","break_level","breakout_date","vol_confirmed","hold_confirmed","last_close","is_2x"]
    print(sel[show].to_string(index=False))

    Path(args.out).write_text("\n".join(sel["ticker"].tolist())+"\n", encoding="utf-8")
    Path("candidates_2x.txt").write_text("\n".join(sel[sel["is_2x"]]["ticker"].tolist())+"\n", encoding="utf-8")

    meta = sel.copy()
    meta.to_csv("candidates_meta.csv", index=False)
    meta.to_csv(f"candidates_meta_{now_kst_stamp()}_KST.csv", index=False)
    print(f"\nSaved: {args.out}  (count={len(sel)}, included_2x={int(sel['is_2x'].sum())})")
    print("Saved: candidates_2x.txt")
    print("Saved: candidates_meta.csv")

if __name__ == "__main__":
    main()
