#!/usr/bin/env python3
"""
bulkowski_entry_exit_signals_v2_autofill.py

One command: compute entry/exit signals AND auto-fill missing custom_break_level on the fly.

- Reads positions.csv (required): ticker, entry_price, shares, [custom_break_level], [entry_date]
- If custom_break_level is missing/blank/NaN, it is computed as:
    prior_60d_high (rolling max High, shifted by 1)
  evaluated on:
    - entry_date if provided (YYYY-MM-DD) using nearest previous trading day
    - otherwise latest daily bar

- Optionally writes back filled levels:
    --write-back positions.csv        (overwrite given file)
    --write-back-out positions_filled.csv  (write a new file, default when --write-back-out is set)

Signals:
- Drawdown (uses yfinance 1m latest if available, else last daily close):
    WARN if dd <= -warn-dd (default 3)
    SELL if dd <= -stop-dd (default 5)
- Break-line failure (daily CLOSE):
    WARN if last close < break_level
    SELL if last two closes < break_level

Usage:
  python bulkowski_entry_exit_signals_v2_autofill.py
  python bulkowski_entry_exit_signals_v2_autofill.py --warn-dd 2 --stop-dd 3
  python bulkowski_entry_exit_signals_v2_autofill.py --write-back positions.csv
"""

from __future__ import annotations

import argparse
import io
from contextlib import redirect_stdout, redirect_stderr
from dataclasses import dataclass
from datetime import datetime
from zoneinfo import ZoneInfo
from pathlib import Path

import numpy as np
import pandas as pd
import yfinance as yf

KST = ZoneInfo("Asia/Seoul")
LOOKBACK = 60


def now_kst_str() -> str:
    return datetime.now(KST).strftime("%Y-%m-%d %H:%M:%S KST")


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


def download_daily(sym: str, period="2y") -> pd.DataFrame:
    df = silent_download(sym, period=period, interval="1d", auto_adjust=False, progress=False, threads=False)
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


def prior_60d_high_shifted(df: pd.DataFrame, lookback=LOOKBACK) -> pd.Series:
    return df["High"].rolling(lookback).max().shift(1)


def pick_index_for_date(idx: pd.DatetimeIndex, target: pd.Timestamp) -> int:
    if len(idx) == 0:
        return -1
    if target >= idx[-1]:
        return len(idx) - 1
    pos = idx.searchsorted(target, side="right") - 1
    return int(max(0, pos))


def compute_level(df: pd.DataFrame, target_date: pd.Timestamp | None) -> float:
    lvl = prior_60d_high_shifted(df, LOOKBACK)
    if target_date is None:
        i = len(df) - 1
    else:
        i = pick_index_for_date(df.index, target_date)
    val = lvl.iloc[i] if (i >= 0 and i < len(lvl)) else np.nan
    if pd.isna(val):
        return float(df["High"].tail(LOOKBACK).max())
    return float(val)


def failure_signals(df: pd.DataFrame, level: float) -> tuple[bool, bool]:
    close = df["Close"].astype(float)
    if len(close) < 3:
        return False, False
    warn = bool(close.iloc[-1] < level)
    sell = bool((close.iloc[-1] < level) and (close.iloc[-2] < level))
    return warn, sell


@dataclass
class Position:
    ticker: str
    entry: float
    shares: float
    custom_level: float | None
    entry_date: pd.Timestamp | None


def load_positions(path: str) -> list[Position]:
    df = pd.read_csv(path)
    df.columns = [c.strip() for c in df.columns]
    need = {"ticker","entry_price","shares"}
    miss = need - set(df.columns)
    if miss:
        raise ValueError(f"{path} missing columns: {sorted(miss)}")

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
        ed = None
        if "entry_date" in df.columns:
            v = r.get("entry_date")
            if pd.notna(v) and str(v).strip() != "":
                try:
                    ed = pd.to_datetime(str(v).strip()).tz_localize(None)
                except Exception:
                    ed = None
        out.append(Position(t, entry, sh, lvl, ed))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--positions", default="positions.csv")
    ap.add_argument("--warn-dd", type=float, default=3.0)
    ap.add_argument("--stop-dd", type=float, default=5.0)
    ap.add_argument("--write-back", default=None, help="overwrite this positions csv with filled custom_break_level (e.g. positions.csv)")
    ap.add_argument("--write-back-out", default=None, help="write filled positions to this file (no overwrite)")
    args = ap.parse_args()

    positions = load_positions(args.positions)

    rows = []
    filled_rows = []

    print(f"KST_NOW: {now_kst_str()}")
    print(f"positions: {len(positions)} tickers | warn_dd={args.warn_dd}% stop_dd={args.stop_dd}%\n")

    for p in positions:
        sym = p.ticker
        ddf = download_daily(sym, period="2y")
        if ddf.empty or len(ddf) < (LOOKBACK + 10):
            rows.append({
                "ticker": sym,
                "status": "NO_DATA",
                "entry": p.entry,
                "current_px": np.nan,
                "drawdown_pct": np.nan,
                "break_level": p.custom_level if p.custom_level is not None else np.nan,
                "break_level_src": "custom" if p.custom_level is not None else "missing",
                "warn_fail_close_below_level": False,
                "sell_fail_2_closes_below_level": False,
            })
            continue

        # fill level if missing
        if p.custom_level is None or (not np.isfinite(p.custom_level)):
            level = compute_level(ddf, p.entry_date)
            level_src = "auto_prior60d_high_shifted"
        else:
            level = float(p.custom_level)
            level_src = "custom"

        # current price (live 1m if possible)
        live_px, live_ts = download_live_1m(sym)
        last_close = float(ddf["Close"].iloc[-1])
        if live_px is not None and np.isfinite(live_px):
            cur_px = float(live_px)
            px_src = f"yf_1m@{live_ts}"
        else:
            cur_px = last_close
            px_src = "last_close"

        dd_pct = (cur_px / p.entry - 1.0) * 100.0
        warn_dd = dd_pct <= -args.warn_dd
        sell_dd = dd_pct <= -args.stop_dd

        warn_fail, sell_fail = failure_signals(ddf, level)

        status = "OK"
        reason = ""
        if sell_dd or sell_fail:
            status = "SELL_SIGNAL"
            reason = "dd<=stop" if sell_dd else "2_closes_below_break"
        elif warn_dd or warn_fail:
            status = "WARN"
            reason = "dd<=warn" if warn_dd else "close_below_break"

        rows.append({
            "ticker": sym,
            "status": status,
            "entry": round(p.entry, 4),
            "current_px": round(cur_px, 4),
            "drawdown_pct": round(dd_pct, 2),
            "break_level": round(level, 4),
            "break_level_src": level_src,
            "reason": reason,
            "px_source": px_src,
            "last_close": round(last_close, 4),
        })

        filled_rows.append({
            "ticker": sym,
            "entry_price": p.entry,
            "shares": p.shares,
            "custom_break_level": round(level, 4),
            "entry_date": p.entry_date.date().isoformat() if p.entry_date is not None else "",
        })

    out = pd.DataFrame(rows)
    order = {"SELL_SIGNAL": 0, "WARN": 1, "OK": 2, "NO_DATA": 9}
    out["rank"] = out["status"].map(order).fillna(9).astype(int)
    out = out.sort_values(["rank","ticker"]).drop(columns=["rank"]).reset_index(drop=True)

    print("=== ENTRY / EXIT SIGNALS (v2 autofill) ===")
    cols = ["ticker","status","entry","current_px","drawdown_pct","break_level","break_level_src","reason","px_source"]
    print(out[cols].to_string(index=False))

    ts = datetime.now(KST).strftime("%Y%m%d_%H%M%S")
    fn = f"signals_{ts}_KST.csv"
    out.to_csv(fn, index=False)
    print(f"\nSaved: {fn}")

    # write-back filled positions if requested
    if args.write_back or args.write_back_out:
        filled = pd.DataFrame(filled_rows)
        if args.write_back_out:
            Path(args.write_back_out).write_text(filled.to_csv(index=False), encoding="utf-8")
            print(f"Saved: {args.write_back_out}")
        if args.write_back:
            Path(args.write_back).write_text(filled.to_csv(index=False), encoding="utf-8")
            print(f"Overwrote: {args.write_back}")


if __name__ == "__main__":
    main()
