#!/usr/bin/env python3
"""
bulkowski_scan_from_debugcsv_pattern_v3_korea.py

Strict sieve #1 (candidates selector) with two break-level modes:

- break-mode A: use only fallback break_level = prior 60-trading-day HIGH (shifted by 1)
- break-mode B: use pattern break_level when detected, else fallback

Implemented patterns (B-mode):
- DOUBLE_BOTTOM  -> neckline (peak between the two bottoms)
- ASC_TRIANGLE   -> upper horizontal resistance (swing-high cluster)

NOTE: Head & Shoulders NOT implemented yet.

Outputs:
- candidates.txt (top N tickers)
- candidates_2x.txt (selected 2x tickers)
- candidates_meta.csv (includes break_level and break_level_src)
"""

import argparse
import io
import re
from contextlib import redirect_stdout, redirect_stderr
from datetime import datetime
from zoneinfo import ZoneInfo
from pathlib import Path

import numpy as np
import pandas as pd
import yfinance as yf

from pipeline_config_korea import load_default_groups, union_ordered

KST = ZoneInfo("Asia/Seoul")

LOOKBACK = 60          # prior resistance window (fallback)
RECENT = 10            # breakout must occur within last N bars
VOL_AVG = 20
VOL_MULT = 1.3

HOLD_WINDOW = 5        # days after breakout to consider retest/hold
CONSEC_CLOSES = 2

NEAR_RESIST_PCT = 1.0  # setup near break threshold (optional)

ETF_2X = {"122630.KS", "233160.KS", "233740.KS", "251340.KS", "252670.KS", "267770.KS", "409820.KS"}
ETF_1X = set()

def now_kst_stamp() -> str:
    return datetime.now(KST).strftime("%Y%m%d_%H%M%S")

def silent_download(symbol: str, period="2y") -> pd.DataFrame:
    buf = io.StringIO()
    with redirect_stdout(buf), redirect_stderr(buf):
        return yf.download(symbol, period=period, interval="1d", auto_adjust=False,
                           progress=False, threads=False)

def normalize(df: pd.DataFrame, symbol: str) -> pd.DataFrame:
    """Robust yfinance normalizer (handles tuple/MultiIndex columns)."""
    if df is None or df.empty:
        return pd.DataFrame()
    out = df.copy()

    # Handle MultiIndex columns like ('Open','AAPL') or ('AAPL','Open')
    if isinstance(out.columns, pd.MultiIndex):
        # If ticker is a level value, slice it out
        try:
            lvl0 = set(out.columns.get_level_values(0))
            lvl1 = set(out.columns.get_level_values(1))
            sym = str(symbol)
            if sym in lvl1:
                out = out.xs(sym, level=1, axis=1).copy()
            elif sym in lvl0:
                out = out.xs(sym, level=0, axis=1).copy()
        except Exception:
            # fallback: flatten by taking first element
            out.columns = [c[0] if isinstance(c, tuple) and len(c)>0 else str(c) for c in out.columns]

    # Handle tuple columns (not MultiIndex)
    cols = []
    for c in out.columns:
        if isinstance(c, tuple):
            # pick first string-like token
            c2 = None
            for part in c:
                if isinstance(part, str) and part.strip():
                    c2 = part
                    break
            if c2 is None:
                c2 = str(c[0]) if len(c)>0 else str(c)
            cols.append(str(c2).strip())
        else:
            cols.append(str(c).strip())
    out.columns = [c.title() for c in cols]

    need = {"Open","High","Low","Close","Volume"}
    if not need.issubset(set(out.columns)):
        return pd.DataFrame()

    out = out[list(need)].dropna().sort_index()
    if not isinstance(out.index, pd.DatetimeIndex):
        out.index = pd.to_datetime(out.index, errors="coerce")
        out = out.dropna()
    if isinstance(out.index, pd.DatetimeIndex) and out.index.tz is not None:
        out.index = out.index.tz_localize(None)
    return out
def prior_resistance(df: pd.DataFrame, lookback: int) -> float:
    # shift by 1 so today's high doesn't leak
    hh = df["High"].shift(1).rolling(lookback).max()
    v = float(hh.iloc[-1]) if len(hh) else np.nan
    return v if np.isfinite(v) else float(df["High"].iloc[-1])

def recent_breakout(df: pd.DataFrame, level: float, recent: int) -> int | None:
    # recent breakout by DAILY CLOSE above level
    closes = df["Close"].to_numpy()
    idx0 = max(0, len(df)-recent)
    for i in range(len(df)-1, idx0-1, -1):
        if closes[i] > level:
            return i
    return None

def volume_confirmed(df: pd.DataFrame, bidx: int | None) -> bool:
    if bidx is None: return False
    if bidx < VOL_AVG: return False
    v = df["Volume"].to_numpy()
    avg = float(np.nanmean(v[bidx-VOL_AVG:bidx]))
    return bool(v[bidx] >= VOL_MULT*avg) if avg > 0 else False

def tol_pct(ticker: str) -> float:
    t = ticker.upper()
    if t in ETF_2X:
        return 4.5
    if t in ETF_1X:
        return 1.75
    return 2.75

def hold_ok(df: pd.DataFrame, bidx: int | None, level: float, tol_: float) -> bool:
    if bidx is None: return False
    start = bidx+1
    end = min(len(df), bidx+1+HOLD_WINDOW)
    if start >= end: return False
    w = df.iloc[start:end]
    tolv = level*(tol_/100.0)
    # retest: price dips near/under level and closes back above
    retest = (w["Low"] <= (level+tolv)) & (w["Close"] >= level)
    if bool(retest.any()):
        return True
    # or consecutive closes above level
    above = (w["Close"] >= level).astype(int).to_numpy()
    for i in range(0, len(above)-CONSEC_CLOSES+1):
        if above[i:i+CONSEC_CLOSES].sum() == CONSEC_CLOSES:
            return True
    return False

# --- Pattern helpers (lightweight, heuristic) ---
def _pivot_lows(series: pd.Series, left=3, right=3):
    x = series.to_numpy()
    piv = []
    for i in range(left, len(x)-right):
        if np.all(x[i] < x[i-left:i]) and np.all(x[i] < x[i+1:i+1+right]):
            piv.append(i)
    return piv

def _pivot_highs(series: pd.Series, left=3, right=3):
    x = series.to_numpy()
    piv = []
    for i in range(left, len(x)-right):
        if np.all(x[i] > x[i-left:i]) and np.all(x[i] > x[i+1:i+1+right]):
            piv.append(i)
    return piv

def detect_double_bottom(df: pd.DataFrame):
    # Find two pivot lows within last ~120 bars, with a pivot high between them.
    lows = _pivot_lows(df["Low"], 3, 3)
    highs = _pivot_highs(df["High"], 3, 3)
    if len(lows) < 2 or len(highs) < 1:
        return None
    # focus recent window
    window = 160
    lows = [i for i in lows if i >= max(0, len(df)-window)]
    highs = [i for i in highs if i >= max(0, len(df)-window)]
    if len(lows) < 2 or len(highs) < 1:
        return None
    # take the last two lows that are separated
    l2 = lows[-1]
    # choose l1 as previous low at least 10 bars before l2
    cand_l1 = [i for i in lows[:-1] if i <= l2-10]
    if not cand_l1:
        return None
    l1 = cand_l1[-1]
    # peak between lows
    mids = [h for h in highs if l1 < h < l2]
    if not mids:
        return None
    peak = mids[np.argmax([df["High"].iloc[h] for h in mids])]
    neckline = float(df["High"].iloc[peak])
    # bottoms similarity score
    b1 = float(df["Low"].iloc[l1]); b2 = float(df["Low"].iloc[l2])
    diff_pct = abs(b1-b2)/max(b1,b2)*100 if max(b1,b2)>0 else 999
    score = max(0.0, 10.0 - diff_pct*1.5)  # crude: closer bottoms => higher score
    diag = {"db_l1": l1, "db_l2": l2, "db_peak": peak, "db_diff_pct": round(diff_pct,2)}
    return ("DOUBLE_BOTTOM", score, neckline, "pattern:DOUBLE_BOTTOM_neckline", diag)

def detect_asc_triangle(df: pd.DataFrame):
    # Upper resistance from recent pivot highs cluster (last ~120 bars)
    highs = _pivot_highs(df["High"], 3, 3)
    if len(highs) < 3:
        return None
    window = 160
    highs = [i for i in highs if i >= max(0, len(df)-window)]
    if len(highs) < 3:
        return None
    # take last 6 highs and cluster by proximity
    recent = highs[-6:]
    vals = np.array([float(df["High"].iloc[i]) for i in recent])
    med = float(np.median(vals))
    # require highs are within ~1.2% of median to be "flat"
    spread_pct = float((np.max(vals)-np.min(vals))/med*100) if med>0 else 999
    if spread_pct > 1.2:
        return None
    # simplistic score: tighter spread => higher
    score = max(0.0, 8.0 - spread_pct*3.0)
    diag = {"tri_high_count": len(recent), "tri_spread_pct": round(spread_pct,2)}
    return ("ASC_TRIANGLE", score, med, "pattern:ASC_TRIANGLE_resistance", diag)

def pattern_shape(df: pd.DataFrame, fallback_level: float):
    # Try patterns; return label, score, override_level, src, diag
    out = detect_double_bottom(df)
    if out:
        return out
    out = detect_asc_triangle(df)
    if out:
        return out
    return ("NONE", 0.0, None, "auto_prior60d_high_shifted", {})

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--top", type=int, default=10)
    ap.add_argument("--out", default="candidates_korea.txt")
    ap.add_argument("--max-2x", type=int, default=3)
    ap.add_argument("--groups", default="tickers_core,tickers_leverage2x,finviz_manual,macro_watch_yahoo")
    ap.add_argument("--break-mode", choices=["a","b"], default="b",
                    help="A=prior-60d-high only; B=pattern break_level when detected (else fallback).")
    args = ap.parse_args()

    groups = {g.strip() for g in args.groups.split(",") if g.strip()}
    all_groups = load_default_groups()  # list[GroupTickers]
    use = [g for g in all_groups if (not groups) or (g.group in groups)]
    universe = union_ordered(use)

    rows = []
    for sym in universe:
        df = normalize(silent_download(sym, "2y"), sym)
        if df.empty or len(df) < (LOOKBACK+VOL_AVG+30):
            continue

        fallback = prior_resistance(df, LOOKBACK)

        # Pattern detection (B-mode) OR forced fallback (A-mode)
        label, pscore, override_level, src, diag = pattern_shape(df, fallback)
        if args.break_mode == "a":
            label, pscore, override_level, src, diag = ("FALLBACK_60D_HIGH", 0.0, None, "auto_prior60d_high_shifted", {})

        level = float(override_level) if override_level is not None else float(fallback)

        bidx = recent_breakout(df, level, recent=RECENT)
        vol_ok = volume_confirmed(df, bidx) if bidx is not None else False
        h_ok = hold_ok(df, bidx, level, tol_pct(sym)) if bidx is not None else False

        status, rank = "NO_SIGNAL", 9
        if h_ok and vol_ok:
            status, rank = "BREAKOUT_VOL_OK_HOLD_OK", 0
        elif bidx is not None and vol_ok:
            status, rank = "BREAKOUT_VOL_OK_WAIT_HOLD", 1
        elif bidx is not None:
            status, rank = "BREAKOUT_WAIT_VOL", 2
        else:
            last_close = float(df["Close"].iloc[-1])
            dist = (level/last_close - 1.0)*100 if last_close>0 else 999
            if 0 <= dist <= NEAR_RESIST_PCT and pscore >= 2.5:
                status, rank = "SETUP_NEAR_BREAK", 3

        last_close = float(df["Close"].iloc[-1])
        breakout_strength = (last_close - level) / level if level > 0 else -999

        rows.append({
            "ticker": sym,
            "is_2x": sym.upper() in ETF_2X,
            "status": status,
            "status_rank": rank,
            "pattern": label,
            "pattern_score": round(pscore, 2),
            "break_level": round(level, 3),
            "break_level_src": src,
            "breakout_date": str(df.index[bidx].date()) if bidx is not None else "",
            "vol_confirmed": bool(vol_ok),
            "hold_confirmed": bool(h_ok),
            "last_close": round(last_close, 3),
            "breakout_strength": round(float(breakout_strength), 6),
            **{f"diag_{k}": v for k, v in (diag or {}).items()},
        })

    out = pd.DataFrame(rows)
    if out.empty:
        print("No results.")
        return

    # Rank: status first (best = 0), then pattern score (B-mode), then breakout strength (helps A-mode)
    out = out.sort_values(
        ["status_rank", "pattern_score", "breakout_strength", "is_2x", "ticker"],
        ascending=[True, False, False, True, True],
    ).reset_index(drop=True)

    selected = []
    cnt2x = 0
    for _, r in out.iterrows():
        if bool(r["is_2x"]) and cnt2x >= args.max_2x:
            continue
        selected.append(r)
        if bool(r["is_2x"]):
            cnt2x += 1
        if len(selected) >= args.top:
            break

    sel = pd.DataFrame(selected).reset_index(drop=True)

    mode_label = "A(60d_high)" if args.break_mode == "a" else "B(pattern)"
    print(f"\n=== BULKOWSKI PATTERN SIEVE (v3 / {mode_label}) ===")
    show = ["ticker","status","pattern","pattern_score","break_level","break_level_src","breakout_date","vol_confirmed","hold_confirmed","last_close","is_2x"]
    print(sel[show].to_string(index=False))

    Path(args.out).write_text("\n".join(sel["ticker"].tolist()) + "\n", encoding="utf-8")
    Path("candidates_2x_korea.txt").write_text("\n".join(sel[sel["is_2x"]]["ticker"].tolist()) + "\n", encoding="utf-8")

    meta = sel.copy()
    meta.to_csv("candidates_meta_korea.csv", index=False)
    meta.to_csv(f"candidates_meta_korea_{now_kst_stamp()}_KST.csv", index=False)

    print(f"\nSaved: {args.out}  (count={len(sel)}, included_2x={int(sel['is_2x'].sum())})")
    print("Saved: candidates_2x_korea.txt")
    print("Saved: candidates_meta_korea.csv")

if __name__ == "__main__":
    main()
