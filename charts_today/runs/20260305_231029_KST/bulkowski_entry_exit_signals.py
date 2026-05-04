#!/usr/bin/env python3
"""
bulkowski_entry_exit_signals.py

Goal:
- Confirm "breakout confirmed + holding" BEFORE entry (Bulkowski-ish confirmation).
- Emit early sell/warning signals so you can exit BEFORE a -5% loss.
- Uses yfinance daily data. Optionally uses premarket.csv (merged) as "current price".

Typical usage (run in the same folder where premarket.csv exists):
  python bulkowski_entry_exit_signals.py --positions positions.csv --premarket premarket.csv

positions.csv format (header required):
  ticker,entry_price,shares,custom_break_level
  XLE,58.04,10,57.88
  XOP,162.50,10,160.99

If custom_break_level is blank, the script computes it as "previous 60-trading-day highest high"
(= 60D rolling max high shifted by 1) based on daily bars.

Outputs:
  - signals_<YYYYMMDD_HHMMSS>_KST.csv
  - prints a compact table to terminal

Notes:
- "Breakout confirmed + holding" (entry-ready) definition in this script:
    A) A breakout day exists in the last N sessions where:
         close > prior_60d_high
    B) After breakout, within HOLD_WINDOW sessions, either:
         (1) a retest day exists: low <= level*(1+tol) AND close >= level
         OR
         (2) two consecutive closes >= level (no retest but strong hold)
  You can tweak constants below.
"""

import argparse
import io
from contextlib import redirect_stdout, redirect_stderr
from dataclasses import dataclass
from datetime import datetime
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
import yfinance as yf


# ---------- config ----------
LOOKBACK_DAYS = 60          # resistance level window
RECENT_BREAKOUT_WINDOW = 7  # breakout must have happened within last N sessions to be "fresh"
HOLD_WINDOW = 5             # how many sessions after breakout we look for a hold/retest confirmation
CONSEC_CLOSES_FOR_HOLD = 2  # if no retest, require this many consecutive closes above level

WARN_DRAWDOWN_PCT = 3.0     # warn earlier than stop
STOP_DRAWDOWN_PCT = 5.0     # stop threshold

# Tolerance for "retest near breakout level" by ticker type (volatility-aware)
ETF_1X = {"XLE", "XOP", "OIH", "XLB", "IYE", "IYM"}
ETF_2X = {"GUSH", "ERX", "UCO", "BOIL", "DIG", "UYM"}

def tol_pct_for_ticker(ticker: str) -> float:
    t = ticker.upper()
    if t in ETF_2X:
        return 4.5
    if t in ETF_1X:
        return 1.75
    return 2.75


def safe_download(symbol: str, period="2y") -> pd.DataFrame:
    """Download daily OHLCV silently (suppresses yfinance console spam)."""
    buf = io.StringIO()
    with redirect_stdout(buf), redirect_stderr(buf):
        return yf.download(
            symbol,
            period=period,
            interval="1d",
            auto_adjust=False,
            progress=False,
            threads=False,
        )


def load_premarket(path: str | None) -> dict[str, float]:
    if not path:
        return {}
    try:
        pm = pd.read_csv(path)
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


@dataclass
class Position:
    ticker: str
    entry: float
    shares: float
    custom_level: float | None


def load_positions(path: str) -> list[Position]:
    df = pd.read_csv(path)
    need = {"ticker", "entry_price", "shares"}
    missing = need - set(df.columns)
    if missing:
        raise ValueError(f"positions.csv missing columns: {sorted(missing)}. Need at least {sorted(need)}")

    out: list[Position] = []
    for _, r in df.iterrows():
        t = str(r["ticker"]).strip().upper()
        if not t:
            continue
        entry = float(r["entry_price"])
        sh = float(r["shares"])
        lvl = None
        if "custom_break_level" in df.columns:
            v = r.get("custom_break_level")
            if pd.notna(v) and str(v).strip() != "":
                lvl = float(v)
        out.append(Position(t, entry, sh, lvl))
    return out


def compute_breakout_level(df: pd.DataFrame, lookback=LOOKBACK_DAYS) -> pd.Series:
    """
    prior_lookback_high: rolling max of High shifted by 1 so it does NOT include the same day's high.
    """
    prior_high = df["High"].rolling(lookback).max().shift(1)
    return prior_high


def find_recent_breakout(df: pd.DataFrame, prior_high: pd.Series) -> tuple[int | None, float | None]:
    """
    Return:
      - breakout index (integer position in df) for the MOST RECENT breakout in last RECENT_BREAKOUT_WINDOW days
      - breakout level (prior_high value on that breakout day)
    Breakout day definition: Close > prior_high.
    """
    close = df["Close"]
    cond = (prior_high.notna()) & (close > prior_high)
    if cond.sum() == 0:
        return None, None
    # search from the end within recent window
    start = max(0, len(df) - RECENT_BREAKOUT_WINDOW)
    recent = cond.iloc[start:]
    if not recent.any():
        return None, None
    # last True
    last_idx = int(np.where(recent.values)[0][-1] + start)
    lvl = float(prior_high.iloc[last_idx])
    return last_idx, lvl


def is_hold_confirmed(df: pd.DataFrame, breakout_i: int, level: float, tol_pct: float) -> bool:
    """
    Hold confirmation:
      - within HOLD_WINDOW sessions AFTER breakout day, either:
         (1) retest day: Low <= level*(1+tol) AND Close >= level
         OR
         (2) CONSEC_CLOSES_FOR_HOLD consecutive closes >= level
    """
    if breakout_i is None:
        return False
    start = breakout_i + 1
    end = min(len(df), breakout_i + 1 + HOLD_WINDOW)
    if start >= end:
        return False
    window = df.iloc[start:end].copy()

    tol = level * (tol_pct / 100.0)
    retest = (window["Low"] <= (level + tol)) & (window["Close"] >= level)
    if retest.any():
        return True

    # consecutive closes above level
    above = (window["Close"] >= level).astype(int).to_numpy()
    if len(above) >= CONSEC_CLOSES_FOR_HOLD:
        # sliding sum
        for i in range(0, len(above) - CONSEC_CLOSES_FOR_HOLD + 1):
            if above[i:i+CONSEC_CLOSES_FOR_HOLD].sum() == CONSEC_CLOSES_FOR_HOLD:
                return True
    return False


def failure_signals(df: pd.DataFrame, level: float) -> tuple[bool, bool]:
    """
    Bulkowski-ish failure signals (daily close based):
      - warn_fail: latest CLOSE < level (first close back under the breakout line)
      - sell_fail: two consecutive closes < level
    """
    close = df["Close"].astype(float)
    if len(close) < 3:
        return False, False
    warn = bool(close.iloc[-1] < level)
    sell = bool((close.iloc[-1] < level) and (close.iloc[-2] < level))
    return warn, sell


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--positions", required=True, help="positions.csv (ticker,entry_price,shares[,custom_break_level])")
    ap.add_argument("--premarket", default="premarket.csv", help="merged premarket csv (optional). default: premarket.csv")
    ap.add_argument("--period", default="2y", help="yfinance history period for daily bars (default: 2y)")
    args = ap.parse_args()

    pm = load_premarket(args.premarket)
    positions = load_positions(args.positions)

    rows = []
    for pos in positions:
        t = pos.ticker
        df_raw = safe_download(t, period=args.period)
        if df_raw is None or df_raw.empty:
            rows.append({
                "ticker": t,
                "status": "NO_DATA",
                "entry": pos.entry,
                "current_px": pm.get(t, np.nan),
                "break_level": pos.custom_level if pos.custom_level else np.nan,
                "breakout_date": "",
                "entry_ready": False,
                "warn": "no_ohlcv",
            })
            continue

        df = df_raw.dropna().copy()
        # normalize columns if MultiIndex
        if isinstance(df.columns, pd.MultiIndex):
            lvl0 = df.columns.get_level_values(0)
            lvl1 = df.columns.get_level_values(1)
            if t in set(lvl1):
                df = df.xs(t, level=1, axis=1).copy()
            elif t in set(lvl0):
                df = df[t].copy()

        need = {"Open","High","Low","Close"}
        if not need.issubset(set(df.columns)):
            rows.append({
                "ticker": t,
                "status": "BAD_COLUMNS",
                "entry": pos.entry,
                "current_px": pm.get(t, np.nan),
                "break_level": pos.custom_level if pos.custom_level else np.nan,
                "breakout_date": "",
                "entry_ready": False,
                "warn": "missing_ohlc",
            })
            continue

        df = df.sort_index()
        # current px: prefer merged premarket, otherwise last close
        last_close = float(df["Close"].iloc[-1])
        cur_px = float(pm.get(t, last_close))

        prior_high = compute_breakout_level(df, lookback=LOOKBACK_DAYS)

        # breakout level: use custom if provided, else latest prior_high (yesterday's computed level)
        level = float(pos.custom_level) if pos.custom_level is not None else float(prior_high.iloc[-1])

        breakout_i, breakout_lvl = find_recent_breakout(df, prior_high)
        # if user supplied custom level, breakout detection should be based on that level too
        if pos.custom_level is not None:
            close = df["Close"].astype(float)
            # find most recent day close > custom level within RECENT window
            start = max(0, len(df) - RECENT_BREAKOUT_WINDOW)
            cond = close.iloc[start:] > level
            if cond.any():
                breakout_i = int(np.where(cond.values)[0][-1] + start)
                breakout_lvl = level
            else:
                breakout_i = None
                breakout_lvl = level

        tol_pct = tol_pct_for_ticker(t)
        hold_ok = is_hold_confirmed(df, breakout_i, float(breakout_lvl) if breakout_lvl is not None else level, tol_pct)
        warn_fail, sell_fail = failure_signals(df, float(breakout_lvl) if breakout_lvl is not None else level)

        # drawdown based signals (uses current_px so it can fire premarket)
        dd_pct = (cur_px / pos.entry - 1.0) * 100.0
        warn_dd = dd_pct <= -WARN_DRAWDOWN_PCT
        sell_dd = dd_pct <= -STOP_DRAWDOWN_PCT

        # status / entry readiness
        entry_ready = bool((breakout_i is not None) and hold_ok and (cur_px >= (float(breakout_lvl) if breakout_lvl else level)))
        if entry_ready:
            status = "ENTRY_READY(confirmed_hold)"
        elif breakout_i is not None:
            status = "BREAKOUT_SEEN(wait_hold)"
        else:
            status = "NO_BREAKOUT_YET"

        # selling preference: exit BEFORE -5% OR on 2 closes back under level
        if sell_dd or sell_fail:
            status = "SELL_SIGNAL"
        elif warn_dd or warn_fail:
            status = "WARN"

        breakout_date = ""
        if breakout_i is not None:
            breakout_date = str(df.index[breakout_i].date())

        rows.append({
            "ticker": t,
            "status": status,
            "entry": round(pos.entry, 4),
            "current_px": round(cur_px, 4),
            "drawdown_pct": round(dd_pct, 2),
            "break_level": round(float(breakout_lvl) if breakout_lvl is not None else level, 4),
            "tol_pct": tol_pct,
            "breakout_date": breakout_date,
            "hold_confirmed": bool(hold_ok),
            "warn_fail_close_below_level": bool(warn_fail),
            "sell_fail_2_closes_below_level": bool(sell_fail),
            "warn_dd_3pct": bool(warn_dd),
            "sell_dd_5pct": bool(sell_dd),
            "entry_ready": bool(entry_ready),
        })

    out = pd.DataFrame(rows)
    # stable ordering: SELL_SIGNAL -> WARN -> others
    order = {"SELL_SIGNAL": 0, "WARN": 1}
    out["rank"] = out["status"].map(order).fillna(9).astype(int)
    out = out.sort_values(["rank", "ticker"]).drop(columns=["rank"])

    ts = datetime.now(ZoneInfo("Asia/Seoul")).strftime("%Y%m%d_%H%M%S")
    fn = f"signals_{ts}_KST.csv"
    out.to_csv(fn, index=False)

    print("\n=== BREAKOUT ENTRY / EXIT SIGNALS ===")
    cols = ["ticker","status","entry","current_px","drawdown_pct","break_level","breakout_date","hold_confirmed"]
    print(out[cols].to_string(index=False))
    print(f"\nSaved: {fn}")

    # quick human-readable stop levels
    print("\n--- Quick Stops (entry * (1-5%)) ---")
    for pos in positions:
        stop = pos.entry * (1.0 - STOP_DRAWDOWN_PCT/100.0)
        print(f"{pos.ticker}: stop@{stop:.2f}  (entry {pos.entry})")


if __name__ == "__main__":
    main()
