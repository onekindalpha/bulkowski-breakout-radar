#!/usr/bin/env python3
# -*- coding: utf-8 -*-
from __future__ import annotations
# ...existing code...
# (파일 중간에 있던 `from __future__ import annotations` 항목은 삭제하세요)
# ...existing code...

# --- KRX: offline name mapping for terminal display ---
import re as _re
from pathlib import Path as _Path

_NAME_SOURCES = [
    "tickers_core_korea.txt",
    "tickers_leverage2x_korea.txt",
    "finviz_manual_korea.txt",
    "macro_watch_yahoo_korea.txt",
    "finviz_manul_korea.txt",
]

def _load_name_map_korea() -> dict[str, str]:
    m: dict[str, str] = {}
    for fn in _NAME_SOURCES:
        p = _Path(fn)
        if not p.exists():
            continue
        for line in p.read_text(encoding="utf-8", errors="ignore").splitlines():
            s = line.strip()
            if not s or s.startswith("#"):
                continue
            name = ""
            if "#" in s:
                left, right = s.split("#", 1)
                name = right.strip()
                s = left.strip()
            if not s:
                continue
            t = _re.split(r"[\s,;]+", s)[0].strip().upper()
            if t.startswith("A") and len(t) > 1 and t[1:].isdigit():
                t = t[1:]
            if t and name and t not in m:
                m[t] = name
    return m

def _ticker_disp(t: str, m: dict[str, str]) -> str:
    t = str(t).strip()
    nm = m.get(t, "")
    return f"{t} ({nm})" if nm else t
# --- end name mapping ---

#!/usr/bin/env python3
"""
scan_candidates_v2_safe_v7_strict_v2d_korea.py

Drop-in "strict" scanner with:
- Default universe = ALL ticker txts from pipeline_config (macro/core/2x/finviz_manual), unioned.
- If candidates.txt exists, it is used automatically (fast path) unless --ignore-candidates is set.
- Counts everywhere (terminal + report header lines) to detect missing tickers quickly.
- Optional intraday confirmation (5m hold bars) for regular-session decision support.

Outputs:
  - report_v2_<YYYYMMDD_HHMMSS>_KST.csv  (snapshot with header lines)
  - report_v2.csv                       (latest, also with header lines)
  - scan_skipped.log                    (why a ticker was skipped)

Usage:
  python scan_candidates_v2_safe_v7_strict_v2d_korea.py
  python scan_candidates_v2_safe_v7_strict_v2d_korea.py --intraday --hold-bars 3
  python scan_candidates_v2_safe_v7_strict_v2d_korea.py --ignore-candidates
"""

import re
import argparse
import io
from contextlib import redirect_stdout, redirect_stderr
from pathlib import Path
from datetime import datetime
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
import yfinance as yf

from pipeline_config_korea import load_default_groups, union_ordered, print_group_counts, now_kst_str, write_header_lines, read_tickers_from_file

LOOKBACK = 60
VOL_AVG = 20
VOL_MULT = 1.3
INTRADAY_INTERVAL = "5m"

ETF_1X = set()
ETF_2X = {"122630.KS", "233160.KS", "233740.KS", "251340.KS", "252670.KS", "267770.KS", "409820.KS"}

def tol_pct_for_ticker(ticker: str) -> float:
    t = ticker.upper()
    if t in ETF_2X:
        return 4.5
    if t in ETF_1X:
        return 1.75
    return 2.75

def safe_download(symbol: str, period="5y", interval="1d", auto_adjust=False, prepost=False) -> pd.DataFrame:
    buf = io.StringIO()
    with redirect_stdout(buf), redirect_stderr(buf):
        return yf.download(
            symbol,
            period=period,
            interval=interval,
            auto_adjust=auto_adjust,
            progress=False,
            threads=False,
            prepost=prepost,
        )

def rsi(series: pd.Series, period=14) -> pd.Series:
    delta = series.diff()
    gain = delta.clip(lower=0).rolling(period).mean()
    loss = (-delta.clip(upper=0)).rolling(period).mean()
    rs = gain / loss.replace(0, np.nan)
    return 100 - (100 / (1 + rs))

def swing_levels(df: pd.DataFrame, lookback=180, pivot=2):
    d = df.tail(min(lookback, len(df))).copy()
    highs = d["High"].to_numpy()
    lows = d["Low"].to_numpy()
    sh, sl = [], []
    for i in range(pivot, len(d) - pivot):
        if highs[i] == np.max(highs[i - pivot : i + pivot + 1]):
            sh.append(highs[i])
        if lows[i] == np.min(lows[i - pivot : i + pivot + 1]):
            sl.append(lows[i])
    r1 = float(sh[-1]) if sh else float(d["High"].max())
    s1 = float(sl[-1]) if sl else float(d["Low"].min())
    return r1, s1

def breakout_level_prior(df: pd.DataFrame, lookback=LOOKBACK) -> pd.Series:
    return df["High"].rolling(lookback).max().shift(1)

def volume_confirmed(df: pd.DataFrame, idx: int) -> bool:
    if "Volume" not in df.columns or len(df) < (VOL_AVG + 5):
        return False
    vol = df["Volume"].astype(float)
    avg = vol.rolling(VOL_AVG).mean().shift(1)
    if pd.isna(avg.iloc[idx]):
        return False
    return bool(vol.iloc[idx] >= VOL_MULT * avg.iloc[idx])

def find_last_breakout_close(df: pd.DataFrame, lvl_series: pd.Series, recent=10):
    close = df["Close"].astype(float)
    cond = lvl_series.notna() & (close > lvl_series)
    start = max(0, len(df) - recent)
    sub = cond.iloc[start:]
    if not sub.any():
        return None, None
    idx = int(np.where(sub.values)[0][-1] + start)
    return idx, float(lvl_series.iloc[idx])

def intraday_hold_confirm(symbol: str, level: float, hold_bars: int, prepost: bool) -> bool:
    try:
        intr = safe_download(symbol, period="5d", interval=INTRADAY_INTERVAL, auto_adjust=False, prepost=prepost)
        if intr is None or intr.empty:
            return False
        df = intr.copy()
        if isinstance(df.columns, pd.MultiIndex):
            lvl1 = df.columns.get_level_values(1)
            if symbol in set(lvl1):
                df = df.xs(symbol, level=1, axis=1).copy()
        if "Close" not in df.columns:
            return False
        close = df["Close"].astype(float).dropna()
        if len(close) < hold_bars:
            return False
        return bool((close.iloc[-hold_bars:] > level).all())
    except Exception:
        return False

def normalize_ohlcv(raw: pd.DataFrame, ticker: str) -> pd.DataFrame:
    if raw is None or raw.empty:
        return pd.DataFrame()
    df = raw.copy()
    if isinstance(df.columns, pd.MultiIndex):
        lvl0 = df.columns.get_level_values(0)
        lvl1 = df.columns.get_level_values(1)
        if ticker in set(lvl1):
            df = df.xs(ticker, level=1, axis=1).copy()
        elif ticker in set(lvl0):
            df = df[ticker].copy()
    df.columns = [str(c).strip() for c in df.columns]
    need = {"Open","High","Low","Close","Volume"}
    if not need.issubset(set(df.columns)):
        return pd.DataFrame()
    df = df[list(need)].dropna().sort_index()
    if not isinstance(df.index, pd.DatetimeIndex):
        df.index = pd.to_datetime(df.index, errors="coerce")
        df = df.dropna()
    if isinstance(df.index, pd.DatetimeIndex) and df.index.tz is not None:
        df.index = df.index.tz_localize(None)
    return df

def load_premarket(path="premarket_korea.csv") -> dict[str, float]:
    p = Path(path)
    if not p.exists():
        return {}
    try:
        pm = pd.read_csv(p, comment="#")
    except Exception:
        return {}
    if "ticker_display" not in pm.columns or "premarket" not in pm.columns:
        return {}
    out: dict[str, float] = {}
    for _, r in pm.dropna().iterrows():
        t = str(r["ticker_display"]).strip().upper()
        try:
            out[t] = float(r["premarket"])
        except Exception:
            pass
    return out


def load_candidates_meta(path: str = "candidates_meta_korea.csv") -> dict[str, dict]:
    """Load per-ticker pattern break levels (Bulkowski B-mode).
    Expected columns: ticker, pattern, break_level
    """
    p = Path(path)
    if not p.exists():
        return {}
    try:
        df = pd.read_csv(p)
    except Exception:
        return {}
    if "ticker_display" not in df.columns:
        return {}
    out: dict[str, dict] = {}
    for _, r in df.iterrows():
        t = str(r.get("ticker_display","")).strip().upper()
        if not t:
            continue
        item = {}
        if "pattern" in df.columns:
            item["pattern"] = str(r.get("pattern","")).strip()
        if "break_level" in df.columns:
            try:
                item["break_level"] = float(r.get("break_level"))
            except Exception:
                pass
        out[t] = item
    return out

def grade(row):
    room = row["room_to_weekly_r1_pct"]
    gap = abs(row["gap_pct"])
    in_middle = row["in_daily_box_middle"]
    if (not row["breakout_confirmed_close"]) and (not row["breakout_confirmed_intraday"]):
        return "C"
    if in_middle and gap < 2:
        return "C"
    if (room >= 2.0) and (row["daily_breakout"] or row["daily_retest"]) and (room - max(gap, 0) >= 0.8):
        return "A"
    if (room >= 0.8) and (gap >= 1.0 or row["daily_breakout"] or row["daily_retest"]):
        return "B"
    return "C"

def score(row):
    s = 0.0
    s += 2.0 if row["weekly_up"] else 0.0
    s += 1.0 if row["px_vs_sma200"] > 0 else 0.0
    s += 1.0 if row["px_vs_sma50"] > 0 else 0.0
    if 50 <= row["rsi14"] <= 65:
        s += 3.0
    elif 65 < row["rsi14"] <= 70:
        s += 1.0
    elif row["rsi14"] > 70:
        s -= 3.0
    if abs(row["gap_pct"]) >= 4:
        s -= 2.0
    elif abs(row["gap_pct"]) >= 2:
        s -= 1.0
    if row["daily_breakout"]:
        s += 2.0
    if row["daily_retest"]:
        s += 1.0
    if row["room_to_weekly_r1_pct"] >= 3:
        s += 2.0
    elif row["room_to_weekly_r1_pct"] >= 1.5:
        s += 1.0
    elif row["room_to_weekly_r1_pct"] < 0.7:
        s -= 2.0
    if row["breakout_volume_confirmed"]:
        s += 1.0
    return s

def load_candidates_if_any(ignore: bool) -> list[str] | None:
    if ignore:
        return None
    p = Path("candidates_korea.txt")
    if not p.exists():
        return None
    return read_tickers_from_file(str(p))

def main():
    name_map = _load_name_map_korea()
    ap = argparse.ArgumentParser()
    ap.add_argument('--break-mode', choices=['a','b'], default='b', help='A=use auto prior-60d-high break_level only (ignore pattern override); B=use pattern break_level from candidates_meta.csv when available.')
    ap.add_argument("--premarket", default="premarket_korea.csv")
    ap.add_argument("--intraday", action="store_true")
    ap.add_argument("--hold-bars", type=int, default=3)
    ap.add_argument("--prepost", action="store_true")
    ap.add_argument("--ignore-candidates", action="store_true", help="ignore candidates.txt even if it exists")
    args = ap.parse_args()

    break_mode = args.break_mode
    groups = load_default_groups()
    print_group_counts(groups, title="INPUT TXT COUNTS (ALL GROUPS)")

    candidates = load_candidates_if_any(args.ignore_candidates)
    if candidates is not None and len(candidates) > 0:
        base = candidates
        print(f"USING candidates.txt: {len(base)} tickers")
    else:
        base = union_ordered(groups)
        print(f"USING UNION of txts: {len(base)} tickers")

    premarket = load_premarket(args.premarket)
    print(f"premarket.csv tickers: {len(premarket)}  (manual overrides included if merged)")

    # Load optional pattern break-level overrides (created by bulkowski_scan_from_debugcsv_pattern_v2.py)
    cand_meta = {}
    if break_mode == "b":
        cand_meta = load_candidates_meta("candidates_meta_korea.csv")
        if cand_meta:
            print(f"candidates_meta_korea.csv: {len(cand_meta)} tickers (pattern break_level overrides enabled)")
        else:
            print("candidates_meta.csv: (none)")
    else:
        print("break-mode=A: pattern override disabled (using prior-60d-high only)")

    # Universe:
    # - If candidates.txt exists: STRICT candidates-only (do NOT expand with old manual leftovers)
    # - Else: union txts + premarket additions
    seen = set()
    tickers: list[str] = []
    for t in base:
        if t not in seen:
            seen.add(t)
            tickers.append(t)

    only_extend_with_premarket = (candidates is None or len(candidates) == 0)
    if only_extend_with_premarket:
        for t in premarket.keys():
            if t not in seen:
                seen.add(t)
                tickers.append(t)

    print(f"FINAL_UNIVERSE (after base + premarket(optional)): {len(tickers)}")
    print(f"KST_NOW: {now_kst_str()}\n")

    skipped, rows = [], []
    for t in tickers:
        try:
            raw = safe_download(t, period="5y", interval="1d", auto_adjust=False)
            df = normalize_ohlcv(raw, t)
            if df.empty:
                skipped.append((t, "no_ohlcv_or_columns"))
                continue
            if len(df) < 260:
                skipped.append((t, f"too_short(len={len(df)})"))
                continue

            close = df["Close"].astype(float)
            last_close = float(close.iloc[-1])
            px = float(premarket.get(t, last_close))
            gap_pct = (px / last_close - 1.0) * 100.0 if t in premarket else 0.0

            sma50 = close.rolling(50).mean()
            sma200 = close.rolling(200).mean()
            rsi14 = rsi(close, 14)

            w = df.resample("W-FRI").agg({"Open":"first","High":"max","Low":"min","Close":"last","Volume":"sum"}).dropna()
            if len(w) < 60:
                skipped.append((t, f"weekly_too_short(len={len(w)})"))
                continue
            wclose = w["Close"].astype(float)
            w_sma20 = wclose.rolling(20).mean()
            w_sma50 = wclose.rolling(50).mean()
            weekly_up = bool((wclose.iloc[-1] > w_sma20.iloc[-1]) and (w_sma20.iloc[-1] > w_sma50.iloc[-1]))

            w_r1, w_s1 = swing_levels(w, lookback=min(180, len(w)), pivot=2)
            room_to_weekly_r1_pct = ((w_r1 / px) - 1) * 100 if px > 0 else np.nan

            lvl_series = breakout_level_prior(df, lookback=LOOKBACK)
            level = float(lvl_series.iloc[-1]) if pd.notna(lvl_series.iloc[-1]) else float(df["High"].tail(LOOKBACK).max())
            break_level_src = "rolling60d_high"
            # Pattern-specific override (Bulkowski B-mode): use neckline/resistance from candidates_meta.csv
            if t in cand_meta and isinstance(cand_meta[t], dict) and "break_level" in cand_meta[t]:
                try:
                    level = float(cand_meta[t]["break_level"])
                    break_level_src = f"pattern:{cand_meta[t].get('pattern','')}".strip(":")
                except Exception:
                    pass
            tol = tol_pct_for_ticker(t)

            # Close confirmation: any recent (<=10 bars) daily close above the chosen level
            close_s = df["Close"].astype(float)
            recent_n = 10
            start_i = max(0, len(df) - recent_n)
            cond = (close_s.iloc[start_i:] > float(level))
            if cond.any():
                rel = int(cond.values.nonzero()[0][-1])
                bidx = start_i + rel
            else:
                bidx = None
            blevel = float(level) if bidx is not None else None
            breakout_confirmed_close = bool(bidx is not None)
            breakout_volume_confirmed = bool(volume_confirmed(df, bidx)) if bidx is not None else False

            breakout_confirmed_intraday = False
            if args.intraday and (px > level):
                breakout_confirmed_intraday = intraday_hold_confirm(t, level, args.hold_bars, args.prepost)

            daily_breakout = bool((breakout_confirmed_close and breakout_volume_confirmed) or breakout_confirmed_intraday)
            daily_retest = bool(daily_breakout and (abs(px - level) / level * 100) <= tol)

            lo60 = float(df["Low"].rolling(60).min().iloc[-1])
            hi60 = float(df["High"].rolling(60).max().iloc[-1])
            mid60 = (lo60 + hi60) / 2
            in_daily_box_middle = (abs(px - mid60) / mid60 * 100) < 3.0

            row = {
                "ticker_display": t,
                "price": px,
                "gap_pct": gap_pct,
                "rsi14": float(rsi14.iloc[-1]),
                "px_vs_sma50": (px / float(sma50.iloc[-1]) - 1) * 100,
                "px_vs_sma200": (px / float(sma200.iloc[-1]) - 1) * 100,
                "weekly_up": weekly_up,
                "weekly_r1": float(w_r1),
                "weekly_s1": float(w_s1),
                "room_to_weekly_r1_pct": float(room_to_weekly_r1_pct),
                "daily_break_level": float(level),
                "break_level_src": break_level_src,
                "daily_breakout": bool(daily_breakout),
                "daily_retest": bool(daily_retest),
                "in_daily_box_middle": bool(in_daily_box_middle),
                "breakout_confirmed_close": bool(breakout_confirmed_close),
                "breakout_volume_confirmed": bool(breakout_volume_confirmed),
                "breakout_confirmed_intraday": bool(breakout_confirmed_intraday),
            }
            row["grade"] = grade(row)
            row["score"] = score(row)
            rows.append(row)

        except Exception as e:
            skipped.append((t, f"exception:{type(e).__name__}"))
            continue

    if skipped:
        Path("scan_skipped.log").write_text("\n".join([f"{t}\t{r}" for t, r in skipped]), encoding="utf-8")

    out = pd.DataFrame(rows)
    if out.empty:
        print("No usable results. See scan_skipped.log")
        return

    grade_rank = {"A":0,"B":1,"C":2}
    out["grade_rank"] = out["grade"].map(grade_rank).fillna(9).astype(int)
    out = out.sort_values(["grade_rank","score","gap_pct"], ascending=[True, False, True])

    for c in ["price","gap_pct","rsi14","px_vs_sma50","px_vs_sma200","room_to_weekly_r1_pct","weekly_r1","weekly_s1","daily_break_level","score"]:
        out[c] = out[c].astype(float).round(2)

    cols = [
        "ticker_display","grade","score","price","gap_pct","rsi14",
        "room_to_weekly_r1_pct","weekly_r1",
        "daily_breakout","daily_retest","daily_break_level","break_level_src",
        "breakout_confirmed_close","breakout_volume_confirmed","breakout_confirmed_intraday",
        "px_vs_sma50","px_vs_sma200",
    ]

    print("\n=== WATCHLIST (STRICT v2) ===")
    print(out[cols].head(25).to_string(index=False))

    ts = datetime.now(ZoneInfo("Asia/Seoul")).strftime("%Y%m%d_%H%M%S")
    stamped = f"report_v2_korea_{ts}_KST.csv"

    # header lines with counts for debugging missing tickers
    header = [
        f"saved_at_kr,{now_kst_str()}",
        f"used_candidates_txt,{bool(candidates is not None and len(candidates)>0)}",
        f"used_candidates_meta,{bool(cand_meta) if break_mode=='b' else False}",
        f"count_base,{len(base)}",
        f"count_premarket,{len(premarket)}",
        f"count_final_universe,{len(tickers)}",
        f"count_scored,{len(out)}",
        f"count_skipped,{len(skipped)}",
    ]

    body = out.drop(columns=["grade_rank"]).to_csv(index=False)
    write_header_lines(stamped, header, body)
    write_header_lines("report_v2_korea.csv", header, body)

    print(f"\nSaved: {stamped}")
    print("Saved: report_v2_korea.csv (latest)")
    if skipped:
        print("Saved: scan_skipped.log")

if __name__ == "__main__":
    main()