#!/usr/bin/env python3
"""
watch_positions_notify_v2.py

Goal: No manual price input needed.
- Pulls "current" price automatically from Yahoo via yfinance intraday (1m, prepost=True).
- Uses your positions.csv for entry + optional custom_break_level (breakout line).
- Sends macOS Notification Center alerts when:
    (A) Breakout line support fails (daily CLOSE back under break_level)
        - WARN: last close < level
        - SELL: last two closes < level
    (B) Drawdown from entry hits thresholds using current price
        - WARN: <= -warn-dd (default 3%)
        - SELL: <= -stop-dd (default 5%)

Notes:
- Yahoo/yfinance is often delayed and not guaranteed real-time. Treat it as an automated snapshot.
- The "support fail" rule is close-based, so it usually changes only after the daily bar updates.
- In watch mode we refresh daily bars on a slower cadence to keep it fast.

Usage:
  python watch_positions_notify_v2.py
  python watch_positions_notify_v2.py --watch --interval 60
  python watch_positions_notify_v2.py --watch --interval 60 --warn-dd 2 --stop-dd 5
"""

from __future__ import annotations

import argparse
import io
import json
import subprocess
import sys
import time
from contextlib import redirect_stdout, redirect_stderr
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
import yfinance as yf


STATE_FILE = Path(".notify_state.json")
KST = ZoneInfo("Asia/Seoul")


def now_kst_str() -> str:
    return datetime.now(KST).strftime("%Y-%m-%d %H:%M:%S KST")


def utc_ts() -> int:
    return int(datetime.now(timezone.utc).timestamp())


def load_state() -> dict:
    if not STATE_FILE.exists():
        return {}
    try:
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def save_state(state: dict) -> None:
    STATE_FILE.write_text(json.dumps(state, indent=2, sort_keys=True), encoding="utf-8")


def mac_notify(title: str, message: str) -> None:
    try:
        t = title.replace('"', '\\"')
        m = message.replace('"', '\\"')
        subprocess.run(
            ["osascript", "-e", f'display notification "{m}" with title "{t}"'],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except Exception:
        pass


def notify(title: str, message: str) -> None:
    if sys.platform == "darwin":
        mac_notify(title, message)
    # always print to terminal too
    print(f"\n>>> ALERT: {title} | {message}\n")


def safe_yf_download(*args, **kwargs) -> pd.DataFrame:
    buf = io.StringIO()
    with redirect_stdout(buf), redirect_stderr(buf):
        return yf.download(*args, **kwargs)


def normalize_multi(df: pd.DataFrame, ticker: str) -> pd.DataFrame:
    if df is None or df.empty:
        return pd.DataFrame()
    out = df.copy()
    if isinstance(out.columns, pd.MultiIndex):
        lvl0 = out.columns.get_level_values(0)
        lvl1 = out.columns.get_level_values(1)
        if ticker in set(lvl1):
            out = out.xs(ticker, level=1, axis=1).copy()
        elif ticker in set(lvl0):
            out = out[ticker].copy()
    out.columns = [str(c).strip() for c in out.columns]
    return out


def load_premarket_csv(path="premarket.csv") -> dict[str, float]:
    """
    Optional fallback. If you don't update premarket.csv often, ignore this.
    """
    p = Path(path)
    if not p.exists():
        return {}
    try:
        df = pd.read_csv(p, comment="#")
    except Exception:
        return {}
    if "ticker" not in df.columns or "premarket" not in df.columns:
        return {}
    out: dict[str, float] = {}
    for _, r in df.dropna().iterrows():
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
    break_level: float | None


def load_positions(path="positions.csv") -> list[Position]:
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError("positions.csv not found")
    df = pd.read_csv(p)
    need = {"ticker", "entry_price", "shares"}
    miss = need - set(df.columns)
    if miss:
        raise ValueError(f"positions.csv missing columns: {sorted(miss)}")
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


# --- price fetching (intraday batch) ---
def batch_live_prices(tickers: list[str]) -> dict[str, tuple[float | None, str]]:
    """
    Fetch latest 1m close for all tickers in one request.
    Returns: ticker -> (price or None, timestamp_iso or "")
    """
    if not tickers:
        return {}
    df = safe_yf_download(
        tickers=tickers,
        period="1d",
        interval="1m",
        auto_adjust=False,
        group_by="ticker",
        prepost=True,
        progress=False,
        threads=True,
    )
    out: dict[str, tuple[float | None, str]] = {}
    for t in tickers:
        sub = normalize_multi(df, t)
        if sub.empty or "Close" not in sub.columns:
            out[t] = (None, "")
            continue
        s = sub["Close"].dropna()
        if s.empty:
            out[t] = (None, "")
            continue
        ts = s.index[-1]
        px = float(s.iloc[-1])
        ts_iso = pd.to_datetime(ts).to_pydatetime().replace(tzinfo=None).isoformat()
        out[t] = (px, ts_iso)
    return out


# --- daily bars (for support-fail rule + computed level fallback) ---
def download_daily(ticker: str, period="9mo") -> pd.DataFrame:
    df = safe_yf_download(
        ticker,
        period=period,
        interval="1d",
        auto_adjust=False,
        progress=False,
        threads=False,
    )
    df = normalize_multi(df, ticker)
    need = {"Open", "High", "Low", "Close", "Volume"}
    if df.empty or not need.issubset(set(df.columns)):
        return pd.DataFrame()
    df = df[list(need)].dropna().sort_index()
    if not isinstance(df.index, pd.DatetimeIndex):
        df.index = pd.to_datetime(df.index, errors="coerce")
        df = df.dropna()
    if isinstance(df.index, pd.DatetimeIndex) and df.index.tz is not None:
        df.index = df.index.tz_localize(None)
    return df


def prior_60d_high_shifted(df: pd.DataFrame, lookback=60) -> float:
    s = df["High"].rolling(lookback).max().shift(1)
    val = s.iloc[-1]
    if pd.isna(val):
        return float(df["High"].tail(lookback).max())
    return float(val)


def failure_signals(df: pd.DataFrame, level: float) -> tuple[bool, bool]:
    close = df["Close"].astype(float)
    if len(close) < 3:
        return False, False
    warn = bool(close.iloc[-1] < level)
    sell = bool((close.iloc[-1] < level) and (close.iloc[-2] < level))
    return warn, sell


def should_notify(prev_state: dict, ticker: str, status: str, repeat_min: int) -> bool:
    rec = prev_state.get(ticker, {})
    prev_status = rec.get("status", "OK")
    last_ts = int(rec.get("last_ts", 0))
    if status != prev_status:
        return True
    if status in ("WARN", "SELL_SIGNAL") and (utc_ts() - last_ts) >= repeat_min * 60:
        return True
    return False


def update_state(state: dict, ticker: str, status: str) -> None:
    state[ticker] = {"status": status, "last_ts": utc_ts()}


def one_pass(
    positions: list[Position],
    pm_fallback: dict[str, float],
    warn_dd: float,
    stop_dd: float,
    daily_cache: dict[str, pd.DataFrame],
    daily_cache_ts: dict[str, int],
    daily_refresh_min: int,
) -> pd.DataFrame:
    tickers = [p.ticker for p in positions]
    live = batch_live_prices(tickers)

    rows = []
    for pos in positions:
        t = pos.ticker

        # current price: prefer live, else fallback pm csv, else NaN
        live_px, live_ts = live.get(t, (None, ""))
        if live_px is not None and np.isfinite(live_px):
            cur_px = float(live_px)
            px_src = f"yf_1m@{live_ts}"
        elif t in pm_fallback and np.isfinite(pm_fallback[t]):
            cur_px = float(pm_fallback[t])
            px_src = "premarket.csv"
        else:
            cur_px = np.nan
            px_src = "none"

        # daily cache refresh
        need_refresh = True
        if t in daily_cache and t in daily_cache_ts:
            if (utc_ts() - daily_cache_ts[t]) < daily_refresh_min * 60:
                need_refresh = False

        if need_refresh:
            ddf = download_daily(t, period="9mo")
            daily_cache[t] = ddf
            daily_cache_ts[t] = utc_ts()
        else:
            ddf = daily_cache.get(t, pd.DataFrame())

        if ddf is None or ddf.empty:
            rows.append({
                "ticker": t,
                "status": "NO_DATA",
                "entry": pos.entry,
                "current_px": cur_px,
                "drawdown_pct": np.nan,
                "break_level": pos.break_level if pos.break_level is not None else np.nan,
                "warn_reason": "no_daily_ohlcv",
                "px_source": px_src,
            })
            continue

        last_close = float(ddf["Close"].iloc[-1])

        level = float(pos.break_level) if pos.break_level is not None else prior_60d_high_shifted(ddf, 60)

        warn_fail, sell_fail = failure_signals(ddf, level)

        dd_pct = np.nan
        warn_dd_hit = False
        sell_dd_hit = False
        if np.isfinite(cur_px):
            dd_pct = (cur_px / pos.entry - 1.0) * 100.0
            warn_dd_hit = dd_pct <= -warn_dd
            sell_dd_hit = dd_pct <= -stop_dd

        status = "OK"
        reason = ""
        if sell_dd_hit or sell_fail:
            status = "SELL_SIGNAL"
            reason = "dd<=stop" if sell_dd_hit else "2_closes_below_break"
        elif warn_dd_hit or warn_fail:
            status = "WARN"
            reason = "dd<=warn" if warn_dd_hit else "close_below_break"

        rows.append({
            "ticker": t,
            "status": status,
            "entry": round(pos.entry, 4),
            "current_px": round(float(cur_px), 4) if np.isfinite(cur_px) else np.nan,
            "drawdown_pct": round(float(dd_pct), 2) if np.isfinite(dd_pct) else np.nan,
            "break_level": round(level, 4),
            "warn_reason": reason,
            "px_source": px_src,
            "last_close": round(last_close, 4),
        })

    out = pd.DataFrame(rows)
    order = {"SELL_SIGNAL": 0, "WARN": 1, "OK": 2, "NO_DATA": 9}
    out["rank"] = out["status"].map(order).fillna(9).astype(int)
    out = out.sort_values(["rank", "ticker"]).drop(columns=["rank"]).reset_index(drop=True)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--positions", default="positions.csv")
    ap.add_argument("--premarket", default="premarket.csv", help="optional fallback only")
    ap.add_argument("--watch", action="store_true")
    ap.add_argument("--interval", type=int, default=60)
    ap.add_argument("--warn-dd", type=float, default=3.0)
    ap.add_argument("--stop-dd", type=float, default=5.0)
    ap.add_argument("--repeat-min", type=int, default=15)
    ap.add_argument("--daily-refresh-min", type=int, default=60, help="refresh daily bars every N minutes (default 60)")
    args = ap.parse_args()

    positions = load_positions(args.positions)
    state = load_state()

    daily_cache: dict[str, pd.DataFrame] = {}
    daily_cache_ts: dict[str, int] = {}

    print(f"KST_NOW: {now_kst_str()}")
    print(f"positions.csv: {len(positions)} tickers")
    print(f"warn_dd={args.warn_dd}%  stop_dd={args.stop_dd}%  interval={args.interval}s  repeat={args.repeat_min}min")
    print(f"daily_refresh_min={args.daily_refresh_min}")
    print("Price source: yfinance 1m prepost=True (fallback: premarket.csv)\n")

    def run_once():
        pm_fallback = load_premarket_csv(args.premarket)
        out = one_pass(
            positions,
            pm_fallback,
            args.warn_dd,
            args.stop_dd,
            daily_cache,
            daily_cache_ts,
            args.daily_refresh_min,
        )

        print(f"\n=== POSITION WATCH ({now_kst_str()}) ===")
        cols = ["ticker","status","entry","current_px","drawdown_pct","break_level","warn_reason","px_source"]
        print(out[cols].to_string(index=False))

        # notifications
        for _, r in out.iterrows():
            t = r["ticker"]
            status = r["status"]
            if status in ("WARN", "SELL_SIGNAL"):
                if should_notify(state, t, status, args.repeat_min):
                    msg = (
                        f"{t} {status} | px={r['current_px']} | dd={r['drawdown_pct']}% | "
                        f"level={r['break_level']} | {r['warn_reason']}"
                    )
                    notify("Bulkowski Watch", msg)
                    update_state(state, t, status)
            else:
                update_state(state, t, "OK")

        save_state(state)
        out.to_csv("position_watch_latest.csv", index=False)

    run_once()
    if args.watch:
        while True:
            time.sleep(max(5, args.interval))
            run_once()


if __name__ == "__main__":
    main()
