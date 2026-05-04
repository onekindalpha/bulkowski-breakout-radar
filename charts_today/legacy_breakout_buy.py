#!/usr/bin/env python3
"""
legacy_breakout_buy.py

Standalone "legacy breakout buy" recommender.

✅ Does NOT modify any of your existing scripts.
✅ Just reads your existing pipeline outputs (premarket.csv, report_v2.csv)
   and fetches OHLCV via yfinance to compute Option-A breakout stats.

Goal (your legacy style)
- Among "confirmed breakouts" (price > break_level), prioritize:
  1) 확실히 넘은 애들 (price > break_level)
  2) 덜 멀어진 애들 (extension small)
  3) 신선한 돌파 (breakout_age small)
- Print BUY_CANDIDATE / WATCH / LATE blocks with:
  price > break_level (+Δ, +%)
  breakout_date, hold_days, vol_confirmed, hold_confirmed, rsi14

Option A (default): "전일까지 60일 고점 돌파" anchor
- break_level = 60D rolling High max, shifted by 1 day (prev-60D-high)
- breakout_date = last False->True transition on Close > break_level_anchor
- vol_confirmed = breakout day volume >= VOL_MULT * avg20 prior volume
- hold_confirmed = consecutive closes above anchor >= HOLD_MIN_DAYS

Option B (optional informational block):
- If report_v2.csv has daily_breakout/daily_break_level, print those too.

Usage
  python legacy_breakout_buy.py
  python legacy_breakout_buy.py --universe-file candidates.txt
  python legacy_breakout_buy.py --top 20
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd
import yfinance as yf


# ---------------- config (tune here, not required) ----------------
LOOKBACK_HIGH = 60
VOL_AVG = 20
VOL_MULT = 1.20
HOLD_MIN_DAYS = 2           # closes above anchor (incl. breakout day) to call hold_confirmed
MAX_BREAKOUT_AGE = 5        # trading days since breakout day (excluding breakout day)
RSI_BUY_MAX = 72.0
RSI_LATE_MIN = 75.0
EXT_1X_BUY_MAX = 1.5        # %
EXT_2X_BUY_MAX = 2.5        # %
EXT_LATE_MIN = 4.5          # % (late/extended)


# ---------------- utils ----------------
def read_tokens(path: Path) -> List[str]:
    if not path.exists():
        return []
    txt = path.read_text(encoding="utf-8", errors="ignore")
    # split on whitespace / comma / semicolon
    toks = [t.strip().upper() for t in pd.unique(pd.Series(txt.replace(",", " ").replace(";", " ").split()))]
    toks = [t for t in toks if t and not t.startswith("#")]
    return list(toks)


def load_premarket_csv(path: Path) -> Dict[str, float]:
    if not path.exists():
        return {}
    try:
        df = pd.read_csv(path)
    except Exception:
        return {}

    # try common column names
    col_ticker = None
    for c in ["ticker", "symbol", "Ticker", "SYMBOL"]:
        if c in df.columns:
            col_ticker = c
            break
    if col_ticker is None:
        # maybe ticker is the index column
        df = df.reset_index()
        col_ticker = "index"

    col_px = None
    for c in ["premarket", "price", "last", "pm", "premarket_price"]:
        if c in df.columns:
            col_px = c
            break
    if col_px is None:
        return {}

    out: Dict[str, float] = {}
    for _, r in df.iterrows():
        t = str(r[col_ticker]).strip().upper()
        if not t or t == "NAN":
            continue
        try:
            out[t] = float(r[col_px])
        except Exception:
            continue
    return out


def rsi(series: pd.Series, period: int = 14) -> pd.Series:
    delta = series.diff()
    gain = delta.clip(lower=0).rolling(period).mean()
    loss = (-delta.clip(upper=0)).rolling(period).mean()
    rs = gain / loss.replace(0, np.nan)
    return 100 - (100 / (1 + rs))


def normalize_ohlcv(raw: pd.DataFrame, ticker: str) -> pd.DataFrame:
    if raw is None or raw.empty:
        return pd.DataFrame()
    df = raw.copy()

    # yfinance sometimes returns MultiIndex columns
    if isinstance(df.columns, pd.MultiIndex):
        lvl0 = df.columns.get_level_values(0)
        lvl1 = df.columns.get_level_values(1)
        if ticker in set(lvl1):
            df = df.xs(ticker, level=1, axis=1).copy()
        elif ticker in set(lvl0):
            df = df[ticker].copy()
        else:
            df.columns = [str(c[-1]) for c in df.columns]

    df.columns = [str(c).strip() for c in df.columns]
    req = {"Open", "High", "Low", "Close", "Volume"}
    if not req.issubset(set(df.columns)):
        return pd.DataFrame()

    df = df[["Open", "High", "Low", "Close", "Volume"]].dropna()
    df = df.sort_index()
    if not isinstance(df.index, pd.DatetimeIndex):
        df.index = pd.to_datetime(df.index, errors="coerce")
        df = df.dropna()
    if isinstance(df.index, pd.DatetimeIndex) and df.index.tz is not None:
        df.index = df.index.tz_localize(None)
    return df


def infer_2x_set(paths: List[Path]) -> set:
    # Start with your mentioned ones
    base = {
        "ERX", "GUSH", "UCO", "BOIL", "DIG", "UYM",
        "SCO", "KOLD", "BULZ", "FNGU", "SOXL", "TECL", "TQQQ", "SQQQ"
    }
    for p in paths:
        for t in read_tokens(p):
            # keep only "ticker-looking" tokens
            if t.isalnum() and len(t) <= 6 and not t.startswith("$"):
                base.add(t)
    return base


@dataclass
class LegacyRow:
    ticker: str
    price: float
    anchor: float
    diff: float
    diff_pct: float
    breakout_date: Optional[pd.Timestamp]
    breakout_age: Optional[int]     # trading days since breakout day (excluding breakout day)
    hold_days: Optional[int]        # consecutive closes above anchor (including breakout day)
    vol_confirmed: Optional[bool]
    hold_confirmed: Optional[bool]
    rsi14: Optional[float]
    reason: str


def compute_option_a(ticker: str, px: float) -> Optional[LegacyRow]:
    try:
        raw = yf.download(
            ticker, period="2y", interval="1d",
            auto_adjust=False, progress=False,
            threads=False,
        )
        df = normalize_ohlcv(raw, ticker)
        if df.empty or len(df) < (LOOKBACK_HIGH + 5):
            return None

        close = df["Close"].astype(float)
        high = df["High"].astype(float)
        vol = df["Volume"].astype(float)

        # Anchor = prev-60D-high (shift by 1 to exclude current day in the series)
        prev_hi = high.rolling(LOOKBACK_HIGH).max().shift(1)
        anchor_today = float(prev_hi.iloc[-1]) if not np.isnan(prev_hi.iloc[-1]) else np.nan
        if not np.isfinite(anchor_today) or anchor_today <= 0:
            return None

        # Close-based breakout history
        mask = (close > prev_hi)
        trans = mask & (~mask.shift(1).fillna(False))
        if not trans.any():
            # no breakout yet; still show as WATCH if near? -> return None (legacy: recommend only "넘은 애들")
            return None

        breakout_ts = trans[trans].index[-1]
        anchor_break = float(prev_hi.loc[breakout_ts])

        # breakout_age: number of trading sessions after breakout day
        pos_break = df.index.get_loc(breakout_ts)
        age = (len(df) - 1) - pos_break

        # hold_days: consecutive closes above anchor_break from breakout day
        after = close.iloc[pos_break:]
        hold_days = int((after > anchor_break).cumprod().sum())

        hold_confirmed = bool(hold_days >= HOLD_MIN_DAYS)

        # volume confirm on breakout day
        vol_break = float(vol.loc[breakout_ts])
        avg20 = vol.rolling(VOL_AVG).mean().shift(1).loc[breakout_ts]
        vol_confirmed = bool(np.isfinite(avg20) and avg20 > 0 and vol_break >= float(avg20) * VOL_MULT)

        # RSI
        rsi14 = float(rsi(close, 14).iloc[-1])

        diff = float(px - anchor_break)
        diff_pct = (diff / anchor_break) * 100.0

        reason = "A:60D(prev) close breakout"
        return LegacyRow(
            ticker=ticker,
            price=float(px),
            anchor=float(anchor_break),
            diff=diff,
            diff_pct=diff_pct,
            breakout_date=pd.Timestamp(breakout_ts),
            breakout_age=int(age),
            hold_days=int(hold_days),
            vol_confirmed=bool(vol_confirmed),
            hold_confirmed=bool(hold_confirmed),
            rsi14=float(rsi14),
            reason=reason,
        )
    except Exception:
        return None


def classify(row: LegacyRow, is_2x: bool) -> str:
    # Must be above anchor to be considered
    if row.price <= row.anchor:
        return "IGNORE"

    ext_buy_max = EXT_2X_BUY_MAX if is_2x else EXT_1X_BUY_MAX

    # LATE
    if (row.diff_pct >= EXT_LATE_MIN) or (row.rsi14 is not None and row.rsi14 >= RSI_LATE_MIN):
        return "LATE"

    # BUY_CANDIDATE strict
    if (
        (row.vol_confirmed is True)
        and (row.hold_confirmed is True)
        and (row.breakout_age is not None and row.breakout_age <= MAX_BREAKOUT_AGE)
        and (row.diff_pct <= ext_buy_max)
        and (row.rsi14 is not None and row.rsi14 <= RSI_BUY_MAX)
    ):
        return "BUY_CANDIDATE"

    # WATCH (still above anchor but stretched or lacking confirmations)
    return "WATCH"


def fmt_row(row: LegacyRow) -> str:
    bd = row.breakout_date.date().isoformat() if row.breakout_date is not None else "-"
    hold = f"{row.hold_days}d" if row.hold_days is not None else "-"
    age = f"{row.breakout_age}d" if row.breakout_age is not None else "-"
    vc = "T" if row.vol_confirmed else "F"
    hc = "T" if row.hold_confirmed else "F"
    rsi14 = f"{row.rsi14:.2f}" if row.rsi14 is not None and np.isfinite(row.rsi14) else "-"
    return (
        f"{row.ticker:<6} {row.price:>8.2f} > {row.anchor:>8.2f}  "
        f"({row.diff:+.2f}, {row.diff_pct:+.2f}%)  "
        f"breakout_date={bd}  hold={hold}  age={age}  vol={vc}  hold_ok={hc}  rsi={rsi14}"
    )


def print_block(title: str, rows: List[LegacyRow], is_2x_map: Dict[str, bool]) -> None:
    print(f"\n=== {title} ===")
    if not rows:
        print("(none)")
        return
    for r in rows:
        t = r.ticker
        tag = "2x" if is_2x_map.get(t, False) else "1x"
        print(f"{fmt_row(r)}  [{tag}]")


def print_option_b_from_report(report_path: Path) -> None:
    if not report_path.exists():
        return
    try:
        df = pd.read_csv(report_path)
    except Exception:
        return
    needed = {"ticker", "price", "daily_breakout", "daily_break_level"}
    if not needed.issubset(set(df.columns)):
        return
    b = df[df["daily_breakout"] == True].copy()
    if b.empty:
        return
    b["ticker"] = b["ticker"].astype(str).str.upper().str.strip()
    b = b.dropna(subset=["price", "daily_break_level"])
    b["diff"] = b["price"].astype(float) - b["daily_break_level"].astype(float)
    b["diff_pct"] = (b["diff"] / b["daily_break_level"].astype(float)) * 100.0
    b = b.sort_values(["diff_pct"], ascending=[True])

    print("\n=== OPTION B (pattern level from report_v2.csv) ===")
    for _, r in b.iterrows():
        t = r["ticker"]
        px = float(r["price"])
        lvl = float(r["daily_break_level"])
        diff = float(r["diff"])
        dp = float(r["diff_pct"])
        retest = bool(r["daily_retest"]) if "daily_retest" in b.columns else False
        rt = "retest=T" if retest else "retest=F"
        print(f"{t:<6} {px:>8.2f} > {lvl:>8.2f}  ({diff:+.2f}, {dp:+.2f}%)  {rt}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--universe-file", default="", help="default: candidates.txt if exists else tickers.txt")
    ap.add_argument("--premarket", default="premarket.csv", help="merged premarket file (ticker,premarket)")
    ap.add_argument("--report", default="report_v2.csv", help="optional: for Option-B info block")
    ap.add_argument("--top", type=int, default=0, help="limit tickers processed (0 = all)")
    args = ap.parse_args()

    cwd = Path(".")
    # Universe pick
    if args.universe_file:
        uni_path = Path(args.universe_file)
    else:
        uni_path = cwd / "candidates.txt" if (cwd / "candidates.txt").exists() else (cwd / "tickers.txt")

    tickers = read_tokens(uni_path)
    if not tickers:
        print(f"[legacy_breakout_buy] universe is empty: {uni_path}")
        return
    if args.top and args.top > 0:
        tickers = tickers[: args.top]

    premarket = load_premarket_csv(Path(args.premarket))

    # Build 2x set
    two_x = infer_2x_set([
        cwd / "tickers_leverage2x.txt",
        cwd / "tickers_leverage_global.txt",
        cwd / "tickers_leverage_global_clean.txt",
    ])
    is_2x_map = {t: (t in two_x) for t in tickers}

    # Option B informational (pattern) block
    print_option_b_from_report(Path(args.report))

    # Option A legacy breakout
    rows: List[LegacyRow] = []
    for t in tickers:
        # price = merged premarket if available else last_close inside compute
        px = premarket.get(t, np.nan)
        if not np.isfinite(px):
            # will fall back to last_close by passing NaN? compute expects float; so we'll fill later
            # easiest: compute once with last_close as px by reading data first in compute_option_a; but compute_option_a
            # needs px. We'll just skip if no premarket and let px be last close after fetch:
            # -> Fetch df quickly to get last_close.
            try:
                raw = yf.download(t, period="3mo", interval="1d", auto_adjust=False, progress=False, threads=False)
                df = normalize_ohlcv(raw, t)
                if df.empty:
                    continue
                px = float(df["Close"].iloc[-1])
            except Exception:
                continue

        r = compute_option_a(t, float(px))
        if r is not None:
            rows.append(r)

    # Keep only "넘은 애들"
    rows = [r for r in rows if r.price > r.anchor]

    # classify and sort (legacy priority: smaller extension first, then fresher)
    buckets = {"BUY_CANDIDATE": [], "WATCH": [], "LATE": []}
    for r in rows:
        k = classify(r, is_2x_map.get(r.ticker, False))
        if k in buckets:
            buckets[k].append(r)

    for k in buckets:
        buckets[k].sort(key=lambda x: (x.diff_pct, x.breakout_age if x.breakout_age is not None else 999))

    print("\n=== OPTION A (60D prev-high breakout) ===")
    print(f"universe={uni_path}  tickers={len(tickers)}  processed_breakouts={len(rows)}")
    print(f"rules: hold>={HOLD_MIN_DAYS}d, vol>={VOL_MULT}x avg{VOL_AVG}, age<={MAX_BREAKOUT_AGE}d, "
          f"ext<=({EXT_1X_BUY_MAX}% 1x / {EXT_2X_BUY_MAX}% 2x), rsi<={RSI_BUY_MAX}")

    print_block("BUY_CANDIDATE", buckets["BUY_CANDIDATE"], is_2x_map)
    print_block("WATCH", buckets["WATCH"], is_2x_map)
    print_block("LATE", buckets["LATE"], is_2x_map)

    print("\nTip: if you want ONLY the 10 Bulkowski candidates, run:")
    print("  python legacy_breakout_buy.py --universe-file candidates.txt")

if __name__ == "__main__":
    main()
