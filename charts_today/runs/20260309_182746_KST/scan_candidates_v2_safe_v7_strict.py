#!/usr/bin/env python3
"""
scan_candidates_v2_safe_v7_strict.py

What this fixes vs v6:
- By default, "breakout/retest" is evaluated on LAST DAILY CLOSE (EOD-confirmable),
  not on premarket/manual px. This avoids "premarket-only breakout" false positives.
- Optional intraday confirmation: --intraday will use 5m bars to require
  N consecutive 5m closes above the breakout level before treating as breakout.
- Adds volume confirmation for breakout day: volume >= VOL_MULT * avg20 volume.

Outputs:
- report_v2_<YYYYMMDD_HHMMSS>_KST.csv (snapshot)
- report_v2.csv (latest)

Usage examples:
  python scan_candidates_v2_safe_v7_strict.py
  python scan_candidates_v2_safe_v7_strict.py --intraday --hold-bars 3
  python scan_candidates_v2_safe_v7_strict.py --universe candidates.txt --intraday
"""

import re
import argparse
from pathlib import Path
from datetime import datetime
from zoneinfo import ZoneInfo
import io
from contextlib import redirect_stdout, redirect_stderr

import numpy as np
import pandas as pd
import yfinance as yf


LOOKBACK = 60
VOL_AVG = 20
VOL_MULT = 1.3  # volume confirmation threshold
INTRADAY_INTERVAL = "5m"


# ---------- silent yfinance ----------
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


# ---------- indicators ----------
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


# ---------- breakout logic ----------
ETF_1X = {"XLE", "XOP", "OIH", "XLB", "IYE", "IYM"}
ETF_2X = {"GUSH", "ERX", "UCO", "BOIL", "DIG", "UYM"}

def tol_pct_for_ticker(ticker: str) -> float:
    t = ticker.upper()
    if t in ETF_2X:
        return 4.5
    if t in ETF_1X:
        return 1.75
    return 2.75


def breakout_level_prior(df: pd.DataFrame, lookback=LOOKBACK) -> pd.Series:
    # Prior resistance (exclude current bar): rolling max shifted by 1
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
    """
    Find most recent breakout DAY (close > prior level) within last `recent` sessions.
    Returns (idx, level_at_idx) or (None, None)
    """
    close = df["Close"].astype(float)
    cond = lvl_series.notna() & (close > lvl_series)
    start = max(0, len(df) - recent)
    sub = cond.iloc[start:]
    if not sub.any():
        return None, None
    idx = int(np.where(sub.values)[0][-1] + start)
    return idx, float(lvl_series.iloc[idx])


def intraday_hold_confirm(symbol: str, level: float, hold_bars: int, prepost: bool) -> bool:
    """
    Intraday confirmation:
      - last `hold_bars` consecutive 5m closes are above `level`
    This is intentionally strict to reduce false positives.
    """
    try:
        intr = safe_download(symbol, period="5d", interval=INTRADAY_INTERVAL, auto_adjust=False, prepost=prepost)
        if intr is None or intr.empty:
            return False
        # normalize columns if MultiIndex
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


# ---------- IO ----------
def load_tickers_file(path: str) -> list[str]:
    p = Path(path)
    if not p.exists():
        return []
    ticker_re = re.compile(r"^[A-Z0-9\^=\.\-\/]{1,20}$")
    out, seen = [], set()
    for line in p.read_text(encoding="utf-8", errors="ignore").splitlines():
        raw = line.strip()
        if not raw or raw.startswith("#"):
            continue
        if "#" in raw:
            raw = raw.split("#", 1)[0].strip()
        if not raw:
            continue
        for tok in re.split(r"[\s,;]+", raw):
            tok = tok.strip().upper()
            if not tok or not ticker_re.match(tok):
                continue
            if tok not in seen:
                seen.add(tok)
                out.append(tok)
    return out


def load_premarket(path="premarket.csv") -> dict[str, float]:
    p = Path(path)
    if not p.exists():
        return {}
    try:
        pm = pd.read_csv(p)
    except Exception:
        return {}
    if "ticker" not in pm.columns or "premarket" not in pm.columns:
        return {}
    out: dict[str, float] = {}
    for _, r in pm.dropna().iterrows():
        t = str(r["ticker"]).strip().upper()
        try:
            out[t] = float(r["premarket"])
        except Exception:
            pass
    return out


def build_universe(args, premarket: dict[str, float]) -> list[str]:
    if args.universe:
        base = load_tickers_file(args.universe)
    else:
        base = load_tickers_file(args.core) + load_tickers_file(args.lev2)
        if not base:
            base = load_tickers_file("tickers.txt")
    if args.extra:
        base += load_tickers_file(args.extra)

    # union + keep order
    seen, out = set(), []
    for t in base:
        if t not in seen:
            seen.add(t)
            out.append(t)
    # include manual-only tickers present in premarket.csv
    for t in premarket.keys():
        if t not in seen:
            seen.add(t)
            out.append(t)
    return out


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


# ---------- grading ----------
def grade(row):
    room = row["room_to_weekly_r1_pct"]
    gap = abs(row["gap_pct"])
    in_middle = row["in_daily_box_middle"]

    # strict: if not close-confirmed and not intraday-confirmed, downgrade to C
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

    # strict bonus for volume-confirmed breakout day
    if row["breakout_volume_confirmed"]:
        s += 1.0
    return s


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--premarket", default="premarket.csv")
    ap.add_argument("--universe", default=None)
    ap.add_argument("--core", default="tickers_core.txt")
    ap.add_argument("--lev2", default="tickers_leverage2x.txt")
    ap.add_argument("--extra", default=None)
    ap.add_argument("--intraday", action="store_true", help="require intraday hold (5m closes) to confirm breakouts today")
    ap.add_argument("--hold-bars", type=int, default=3, help="intraday consecutive 5m closes above level (default 3 = 15m)")
    ap.add_argument("--prepost", action="store_true", help="include pre/after-market in intraday check")
    args = ap.parse_args()

    premarket = load_premarket(args.premarket)
    tickers = build_universe(args, premarket)

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

            # weekly trend + levels
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

            # strict breakout level (prior 60d high)
            lvl_series = breakout_level_prior(df, lookback=LOOKBACK)
            level = float(lvl_series.iloc[-1]) if pd.notna(lvl_series.iloc[-1]) else float(df["High"].tail(LOOKBACK).max())
            tol = tol_pct_for_ticker(t)

            # last confirmed breakout day by daily CLOSE
            bidx, blevel = find_last_breakout_close(df, lvl_series, recent=10)
            breakout_confirmed_close = bool(bidx is not None)
            breakout_volume_confirmed = bool(volume_confirmed(df, bidx)) if bidx is not None else False

            # today's "tentative" breakout by px + optional intraday hold
            breakout_confirmed_intraday = False
            if args.intraday:
                if px > level:
                    breakout_confirmed_intraday = intraday_hold_confirm(t, level, args.hold_bars, args.prepost)

            # use STRICT confirmation for daily_breakout flag
            daily_breakout = bool((breakout_confirmed_close and breakout_volume_confirmed) or breakout_confirmed_intraday)

            # retest: price near level (use px), but only meaningful if breakout confirmed
            daily_retest = bool(daily_breakout and (abs(px - level) / level * 100) <= tol)

            # box-middle check (same as before)
            lo60 = float(df["Low"].rolling(60).min().iloc[-1])
            hi60 = float(df["High"].rolling(60).max().iloc[-1])
            mid60 = (lo60 + hi60) / 2
            in_daily_box_middle = (abs(px - mid60) / mid60 * 100) < 3.0

            row = {
                "ticker": t,
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
                "daily_breakout": bool(daily_breakout),
                "daily_retest": bool(daily_retest),
                "in_daily_box_middle": bool(in_daily_box_middle),
                # strict diagnostics
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

    grade_rank = {"A": 0, "B": 1, "C": 2}
    out["grade_rank"] = out["grade"].map(grade_rank).fillna(9).astype(int)
    out = out.sort_values(["grade_rank", "score", "gap_pct"], ascending=[True, False, True])

    # rounding
    for c in ["price","gap_pct","rsi14","px_vs_sma50","px_vs_sma200","room_to_weekly_r1_pct","weekly_r1","daily_break_level","score"]:
        out[c] = out[c].astype(float).round(2)

    cols = [
        "ticker","grade","score","price","gap_pct","rsi14",
        "room_to_weekly_r1_pct","weekly_r1",
        "daily_breakout","daily_retest","daily_break_level",
        "breakout_confirmed_close","breakout_volume_confirmed","breakout_confirmed_intraday",
        "px_vs_sma50","px_vs_sma200",
    ]

    print("\n=== WATCHLIST (STRICT) ===")
    print(out[cols].head(25).to_string(index=False))

    ts = datetime.now(ZoneInfo("Asia/Seoul")).strftime("%Y%m%d_%H%M%S")
    stamped = f"report_v2_{ts}_KST.csv"
    final = out.drop(columns=["grade_rank"])
    final.to_csv(stamped, index=False)
    final.to_csv("report_v2.csv", index=False)
    print(f"\nSaved: {stamped}")
    print("Saved: report_v2.csv (latest)")
    if skipped:
        print("Saved: scan_skipped.log")


if __name__ == "__main__":
    main()
