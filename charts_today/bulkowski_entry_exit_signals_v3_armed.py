#!/usr/bin/env python3
"""
bulkowski_entry_exit_signals_v3_armed.py

Fixes the confusion you saw:
- No more immediate SELL_SIGNAL just because the *previous two daily closes* were below break_level
  BEFORE you entered.
- Break-level failure checks are ARMED only after entry_date, and only after we observe at least
  one daily CLOSE >= break_level after entry_date.

Signals (default):
- WARN: drawdown <= -warn-dd
- SELL: drawdown <= -stop-dd
- Optional support fail (secondary):
    WARN: (ARMED) last close < break_level
    SELL: (ARMED) last N closes < break_level*(1-sell_buffer%)

Inputs:
- positions.csv columns:
    ticker, entry_price, shares
  optional:
    custom_break_level, entry_date (YYYY-MM-DD)
- break_level autofill priority:
    positions.custom_break_level > report_v2 daily_break_level > auto prior 60d high (shifted)

Outputs:
- signals_<stamp>_KST.csv

Usage:
  python bulkowski_entry_exit_signals_v3_armed.py --positions positions.csv --warn-dd 2 --stop-dd 3
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


def now_kst_str():
    return datetime.now(KST).strftime("%Y-%m-%d %H:%M:%S KST")


def stamp():
    return datetime.now(KST).strftime("%Y%m%d_%H%M%S_KST")


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


def load_report_levels() -> dict[str, float]:
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


def download_daily(sym: str) -> pd.DataFrame:
    df = silent_download(sym, period="2y", interval="1d", auto_adjust=False, progress=False, threads=False)
    df = normalize(df, sym)
    need = {"Open","High","Low","Close","Volume"}
    if df.empty or not need.issubset(set(df.columns)):
        return pd.DataFrame()
    df = df[list(need)].dropna().sort_index()
    if not isinstance(df.index, pd.DatetimeIndex):
        df.index = pd.to_datetime(df.index, errors="coerce")
        df = df.dropna()
    if isinstance(df.index, pd.DatetimeIndex) and df.index.tz is not None:
        df.index = df.index.tz_localize(None)
    return df


def download_live_1m(sym: str) -> tuple[float | None, str]:
    df = silent_download(sym, period="1d", interval="1m", auto_adjust=False, progress=False, threads=False, prepost=True)
    df = normalize(df, sym)
    if df.empty or "Close" not in df.columns:
        return None, ""
    s = df["Close"].dropna()
    if s.empty:
        return None, ""
    ts = s.index[-1]
    px = float(s.iloc[-1])
    ts_iso = pd.to_datetime(ts).to_pydatetime().replace(tzinfo=None).isoformat()
    return px, ts_iso


def prior_60d_high_shifted(df: pd.DataFrame) -> pd.Series:
    return df["High"].rolling(LOOKBACK).max().shift(1)


def pick_index_for_date(idx: pd.DatetimeIndex, target: pd.Timestamp) -> int:
    if len(idx) == 0:
        return -1
    pos = idx.searchsorted(target, side="right") - 1
    return int(max(0, min(len(idx)-1, pos)))


def compute_level(df: pd.DataFrame, entry_date: str) -> float:
    lvl = prior_60d_high_shifted(df)
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


def armed_after_entry(df: pd.DataFrame, level: float, entry_date: str) -> bool:
    if not entry_date:
        return False
    try:
        d = pd.to_datetime(entry_date).tz_localize(None)
    except Exception:
        return False
    sub = df[df.index >= d]
    if sub.empty:
        return False
    return bool((sub["Close"].astype(float) >= level).any())


def close_below_n_after_entry(df: pd.DataFrame, level: float, n: int, buffer_pct: float, entry_date: str) -> bool:
    if not entry_date:
        return False
    try:
        d = pd.to_datetime(entry_date).tz_localize(None)
    except Exception:
        return False
    sub = df[df.index >= d]
    if len(sub) < n:
        return False
    thr = level * (1.0 - buffer_pct/100.0)
    tail = sub["Close"].astype(float).iloc[-n:]
    return bool((tail < thr).all())


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--positions", default="positions.csv")
    ap.add_argument("--warn-dd", type=float, default=2.0)
    ap.add_argument("--stop-dd", type=float, default=3.0)
    ap.add_argument("--sell-close-days", type=int, default=2)
    ap.add_argument("--sell-buffer", type=float, default=0.3)
    args = ap.parse_args()

    pos = pd.read_csv(args.positions)
    pos.columns = [c.strip() for c in pos.columns]
    for req in ["ticker","entry_price","shares"]:
        if req not in pos.columns:
            raise SystemExit(f"{args.positions} missing column: {req}")

    pos["ticker"] = pos["ticker"].astype(str).str.strip().str.upper()
    pos["entry_price"] = pd.to_numeric(pos["entry_price"], errors="coerce")
    pos["shares"] = pd.to_numeric(pos["shares"], errors="coerce")
    if "custom_break_level" not in pos.columns:
        pos["custom_break_level"] = np.nan
    else:
        pos["custom_break_level"] = pd.to_numeric(pos["custom_break_level"], errors="coerce")
    if "entry_date" not in pos.columns:
        pos["entry_date"] = ""
    pos["entry_date"] = pos["entry_date"].astype(str).str.strip()

    report_levels = load_report_levels()

    rows = []
    for _, r in pos.dropna(subset=["ticker","entry_price","shares"]).iterrows():
        t = r["ticker"]
        entry = float(r["entry_price"])
        entry_date = str(r.get("entry_date","")).strip()

        ddf = download_daily(t)
        if ddf.empty or len(ddf) < 120:
            rows.append({"ticker":t, "status":"NO_DATA", "reason":"no_daily"})
            continue

        # fill level
        src = "custom"
        level = r["custom_break_level"]
        if not np.isfinite(level):
            if t in report_levels and np.isfinite(report_levels[t]):
                level = report_levels[t]
                src = "report_daily_break_level"
            else:
                level = compute_level(ddf, entry_date)
                src = "auto_prior60d_high_shifted"
        level = float(level)

        # current price
        px, ts = download_live_1m(t)
        last_close = float(ddf["Close"].iloc[-1])
        prev_close = float(ddf["Close"].iloc[-2]) if len(ddf) >= 2 else np.nan
        cur_px = float(px) if (px is not None and np.isfinite(px)) else last_close
        px_src = f"yf_1m@{ts}" if (px is not None and np.isfinite(px)) else "last_close"

        dd_pct = (cur_px/entry - 1.0)*100.0
        warn_dd = dd_pct <= -args.warn_dd
        sell_dd = dd_pct <= -args.stop_dd

        armed = armed_after_entry(ddf, level, entry_date)
        warn_support = armed and (last_close < level)
        sell_support = armed and close_below_n_after_entry(ddf, level, args.sell_close_days, args.sell_buffer, entry_date)

        status = "OK"
        reason = ""
        if sell_dd:
            status = "SELL_SIGNAL"
            reason = "dd<=stop"
        elif sell_support:
            status = "SELL_SIGNAL"
            reason = f"armed_close_below_break_{args.sell_close_days}d"
        elif warn_dd:
            status = "WARN"
            reason = "dd<=warn"
        elif warn_support:
            status = "WARN"
            reason = "armed_close_below_break_1d"

        rows.append({
            "ticker": t,
            "status": status,
            "entry": round(entry, 4),
            "current_px": round(cur_px, 4),
            "drawdown_pct": round(dd_pct, 2),
            "break_level": round(level, 4),
            "break_level_src": src,
            "entry_date": entry_date,
            "armed": bool(armed),
            "last_close": round(last_close, 4),
            "prev_close": round(prev_close, 4) if np.isfinite(prev_close) else np.nan,
            "reason": reason,
            "px_source": px_src,
        })

    out = pd.DataFrame(rows)
    order = {"SELL_SIGNAL":0,"WARN":1,"OK":2,"NO_DATA":9}
    out["rank"] = out["status"].map(order).fillna(9).astype(int)
    out = out.sort_values(["rank","ticker"]).drop(columns=["rank"]).reset_index(drop=True)

    print(f"KST_NOW: {now_kst_str()}")
    print(f"positions: {len(out)} tickers | warn_dd={args.warn_dd}% stop_dd={args.stop_dd}%")
    print("\n=== ENTRY / EXIT SIGNALS (v3 armed) ===")
    cols = ["ticker","status","entry","current_px","drawdown_pct","break_level","break_level_src","entry_date","armed","last_close","prev_close","reason"]
    print(out[cols].to_string(index=False))

    fn = f"signals_{stamp()}.csv"
    out.to_csv(fn, index=False)
    print(f"\nSaved: {fn}")


if __name__ == "__main__":
    main()
