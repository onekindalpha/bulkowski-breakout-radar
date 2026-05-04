#!/usr/bin/env python3
"""
monitor_positions_onecmd.py

One-command positions monitor with:
- AUTO-FILL custom_break_level (breakout line proxy) so you don't have to remember it
- ALERTS (macOS Notification Center) with sane "arming" to avoid false SELLs
- Default risk: WARN at -2%, SELL at -3% (because you said you want 2~3% max loss)
- Uses yfinance live 1m prices (prepost=True) in batch
- Uses daily bars (cached) for break-level failure checks

Auto-fill strategy (in order):
1) If a recent report exists (report_v2.csv or newest report_v2_*_KST.csv) and contains:
     ticker, daily_break_level
   then use that as custom_break_level for that ticker.
   (This approximates "the breakout line you were watching" at scan time.)
2) Else compute a reproducible proxy from daily bars:
     prior 60-trading-day HIGH (rolling max High shifted by 1)
   Optionally on entry_date if provided.

It can also write back filled levels into positions.csv to keep it stable.

Files:
- input: positions.csv (required)
- optional: report_v2.csv or report_v2_*_KST.csv
- output: position_watch_latest.csv (every loop)
- optional: positions_filled.csv / overwrite positions.csv

Usage:
  # One-shot
  python monitor_positions_onecmd.py

  # Watch (recommended)
  python monitor_positions_onecmd.py --watch --interval 60

  # Write-back filled custom_break_level into positions.csv (so it won't change)
  python monitor_positions_onecmd.py --write-back positions.csv --watch

  # If you want different thresholds
  python monitor_positions_onecmd.py --warn-dd 2 --stop-dd 3 --watch

Position file format (positions.csv):
  ticker,entry_price,shares,custom_break_level(optional),entry_date(optional YYYY-MM-DD)

Notes:
- "support fail" SELL uses daily CLOSE and is ARMED only after we've observed a daily close >= break_level at least once.
- Primary stop is drawdown (current price vs entry). This reacts intraday.
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

KST = ZoneInfo("Asia/Seoul")
STATE_FILE = Path(".notify_state.json")

LOOKBACK = 60
VOL_AVG = 20


def now_kst_str() -> str:
    return datetime.now(KST).strftime("%Y-%m-%d %H:%M:%S KST")


def utc_ts() -> int:
    return int(datetime.now(timezone.utc).timestamp())


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
    print(f"\n>>> ALERT: {title} | {message}\n")


def load_state() -> dict:
    if not STATE_FILE.exists():
        return {}
    try:
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def save_state(state: dict) -> None:
    STATE_FILE.write_text(json.dumps(state, indent=2, sort_keys=True), encoding="utf-8")


def should_notify(prev_state: dict, ticker: str, status: str, repeat_min: int) -> bool:
    rec = prev_state.get(ticker, {})
    prev_status = rec.get("status", "OK")
    last_ts = int(rec.get("last_ts", 0))
    if status != prev_status:
        return True
    if status in ("WARN", "SELL_SIGNAL") and (utc_ts() - last_ts) >= repeat_min * 60:
        return True
    return False


def update_state(state: dict, ticker: str, status: str, armed: bool) -> None:
    state[ticker] = {"status": status, "last_ts": utc_ts(), "armed": bool(armed)}


@dataclass
class Position:
    ticker: str
    entry: float
    shares: float
    custom_level: float | None
    entry_date: pd.Timestamp | None


def load_positions(path="positions.csv") -> pd.DataFrame:
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError("positions.csv not found")
    df = pd.read_csv(p)
    df.columns = [c.strip() for c in df.columns]
    for c in ["ticker", "entry_price", "shares", "custom_break_level", "entry_date"]:
        if c not in df.columns:
            if c == "custom_break_level" or c == "entry_date":
                continue
            raise ValueError(f"positions.csv must include: ticker, entry_price, shares (missing {c})")
    df["ticker"] = df["ticker"].astype(str).str.strip().str.upper()
    df["entry_price"] = pd.to_numeric(df["entry_price"], errors="coerce")
    df["shares"] = pd.to_numeric(df["shares"], errors="coerce")
    if "custom_break_level" in df.columns:
        df["custom_break_level"] = pd.to_numeric(df["custom_break_level"], errors="coerce")
    else:
        df["custom_break_level"] = np.nan
    if "entry_date" in df.columns:
        df["entry_date"] = df["entry_date"].astype(str).str.strip()
    else:
        df["entry_date"] = ""
    df = df.dropna(subset=["ticker", "entry_price", "shares"])
    # ensure no duplicate tickers
    if df["ticker"].duplicated().any():
        dups = df[df["ticker"].duplicated(keep=False)]["ticker"].tolist()
        raise ValueError(f"Duplicate tickers in positions.csv: {sorted(set(dups))}")
    return df


def newest_report_path() -> Path | None:
    # prefer newest stamped snapshot, otherwise report_v2.csv
    stamped = sorted(Path(".").glob("report_v2_*_KST.csv"), key=lambda p: p.stat().st_mtime, reverse=True)
    if stamped:
        return stamped[0]
    p = Path("report_v2.csv")
    return p if p.exists() else None


def load_break_levels_from_report() -> dict[str, float]:
    rp = newest_report_path()
    if rp is None:
        return {}
    try:
        df = pd.read_csv(rp, comment="#")
    except Exception:
        return {}
    need = {"ticker", "daily_break_level"}
    if not need.issubset(set(df.columns)):
        return {}
    out = {}
    for _, r in df.dropna(subset=["ticker", "daily_break_level"]).iterrows():
        t = str(r["ticker"]).strip().upper()
        try:
            out[t] = float(r["daily_break_level"])
        except Exception:
            pass
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


def prior_60d_high_shifted(df: pd.DataFrame, lookback=LOOKBACK) -> pd.Series:
    return df["High"].rolling(lookback).max().shift(1)


def pick_index_for_date(idx: pd.DatetimeIndex, target: pd.Timestamp) -> int:
    if len(idx) == 0:
        return -1
    if target >= idx[-1]:
        return len(idx) - 1
    pos = idx.searchsorted(target, side="right") - 1
    return int(max(0, pos))


def compute_level_from_daily(df: pd.DataFrame, entry_date: str) -> float:
    lvl = prior_60d_high_shifted(df, LOOKBACK)
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


def batch_live_prices(tickers: list[str]) -> dict[str, tuple[float | None, str]]:
    if not tickers:
        return {}
    df = silent_download(
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
        sub = normalize(df, t)
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


def close_below_for_n_days(close: pd.Series, level: float, n: int, buffer_pct: float) -> bool:
    if n <= 0:
        return False
    if len(close) < n + 1:
        return False
    thr = level * (1.0 - buffer_pct / 100.0)
    tail = close.iloc[-n:].astype(float)
    return bool((tail < thr).all())


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--positions", default="positions.csv")
    ap.add_argument("--watch", action="store_true")
    ap.add_argument("--interval", type=int, default=60)
    ap.add_argument("--warn-dd", type=float, default=2.0, help="default 2 (you said 2~3%)")
    ap.add_argument("--stop-dd", type=float, default=3.0, help="default 3 (you said 2~3%)")
    ap.add_argument("--repeat-min", type=int, default=15)
    ap.add_argument("--sell-close-days", type=int, default=2)
    ap.add_argument("--sell-buffer", type=float, default=0.3)
    ap.add_argument("--daily-refresh-min", type=int, default=60)
    ap.add_argument("--write-back", default=None, help="overwrite positions file with filled custom_break_level")
    ap.add_argument("--write-back-out", default=None, help="write filled positions to a new file")
    args = ap.parse_args()

    pos_df = load_positions(args.positions)
    break_from_report = load_break_levels_from_report()

    # daily cache
    daily_cache: dict[str, pd.DataFrame] = {}
    daily_cache_ts: dict[str, int] = {}

    state = load_state()

    def fill_levels() -> pd.DataFrame:
        filled = pos_df.copy()
        levels = []
        srcs = []
        for _, r in filled.iterrows():
            t = r["ticker"]
            cur = r.get("custom_break_level", np.nan)
            if np.isfinite(cur):
                levels.append(float(cur)); srcs.append("custom"); continue
            if t in break_from_report and np.isfinite(break_from_report[t]):
                levels.append(float(break_from_report[t])); srcs.append("report_daily_break_level"); continue
            # fallback compute from daily
            ddf = download_daily(t, period="2y")
            if ddf.empty:
                levels.append(np.nan); srcs.append("missing_daily"); continue
            levels.append(round(compute_level_from_daily(ddf, str(r.get("entry_date","")).strip()), 4))
            srcs.append("auto_prior60d_high_shifted")
        filled["custom_break_level_filled"] = levels
        filled["break_level_src"] = srcs
        return filled

    filled_pos = fill_levels()

    # optional write-back
    if args.write_back_out:
        filled_pos_out = filled_pos.drop(columns=["custom_break_level"]).rename(columns={"custom_break_level_filled":"custom_break_level"})
        filled_pos_out.to_csv(args.write_back_out, index=False)
        print(f"Saved: {args.write_back_out}")

    if args.write_back:
        filled_pos_out = filled_pos.drop(columns=["custom_break_level"]).rename(columns={"custom_break_level_filled":"custom_break_level"})
        filled_pos_out.to_csv(args.write_back, index=False)
        print(f"Overwrote: {args.write_back}")

    tickers = filled_pos["ticker"].tolist()

    print(f"KST_NOW: {now_kst_str()}")
    print(f"positions: {len(tickers)} tickers | warn_dd={args.warn_dd}% stop_dd={args.stop_dd}%")
    print(f"support SELL: (ARMED) close<{args.sell_close_days}d below level*(1-{args.sell_buffer}%)")
    print(f"daily_refresh_min={args.daily_refresh_min} | price source: yfinance 1m prepost=True")
    rp = newest_report_path()
    print(f"break_level source priority: positions.csv > {rp.name if rp else 'no_report'} > auto_prior60d_high_shifted\n")

    def run_once():
        # refresh daily cache if needed
        for t in tickers:
            if t not in daily_cache_ts or (utc_ts() - daily_cache_ts[t]) >= args.daily_refresh_min * 60:
                ddf = download_daily(t, period="2y")
                daily_cache[t] = ddf
                daily_cache_ts[t] = utc_ts()

        live = batch_live_prices(tickers)

        rows = []
        for _, r in filled_pos.iterrows():
            t = r["ticker"]
            entry = float(r["entry_price"])
            level = float(r["custom_break_level_filled"]) if np.isfinite(r["custom_break_level_filled"]) else np.nan

            px, ts_iso = live.get(t, (None, ""))
            if px is None or (not np.isfinite(px)):
                cur_px = np.nan
                px_src = "no_live"
            else:
                cur_px = float(px)
                px_src = f"yf_1m@{ts_iso}"

            ddf = daily_cache.get(t, pd.DataFrame())
            if ddf is None or ddf.empty or (not np.isfinite(level)):
                rows.append({
                    "ticker": t, "status":"NO_DATA", "entry":entry, "current_px":cur_px,
                    "drawdown_pct": np.nan, "break_level": level, "armed": bool(state.get(t, {}).get("armed", False)),
                    "reason": "no_daily_or_level", "px_source": px_src,
                })
                continue

            close = ddf["Close"].astype(float)
            last_close = float(close.iloc[-1])

            armed_prev = bool(state.get(t, {}).get("armed", False))
            armed_now = armed_prev or bool(last_close >= level)

            # drawdown-based signals (primary for 2~3% style)
            dd_pct = np.nan
            warn_dd = False
            sell_dd = False
            if np.isfinite(cur_px):
                dd_pct = (cur_px / entry - 1.0) * 100.0
                warn_dd = dd_pct <= -args.warn_dd
                sell_dd = dd_pct <= -args.stop_dd

            # support-based signals (secondary; requires arming)
            sell_support = bool(armed_now and close_below_for_n_days(close, level, args.sell_close_days, args.sell_buffer))

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

            rows.append({
                "ticker": t,
                "status": status,
                "entry": round(entry, 4),
                "current_px": round(cur_px, 4) if np.isfinite(cur_px) else np.nan,
                "drawdown_pct": round(dd_pct, 2) if np.isfinite(dd_pct) else np.nan,
                "break_level": round(level, 4),
                "armed": bool(armed_now),
                "reason": reason,
                "px_source": px_src,
                "break_level_src": r["break_level_src"],
            })

            update_state(state, t, status, armed_now)

        out = pd.DataFrame(rows)
        order = {"SELL_SIGNAL":0,"WARN":1,"OK":2,"NO_DATA":9}
        out["rank"] = out["status"].map(order).fillna(9).astype(int)
        out = out.sort_values(["rank","ticker"]).drop(columns=["rank"]).reset_index(drop=True)

        print(f"\n=== POSITION WATCH ({now_kst_str()}) ===")
        cols = ["ticker","status","entry","current_px","drawdown_pct","break_level","armed","reason","break_level_src","px_source"]
        print(out[cols].to_string(index=False))

        # notify
        for _, rr in out.iterrows():
            t = rr["ticker"]
            status = rr["status"]
            if status in ("WARN", "SELL_SIGNAL") and should_notify(state, t, status, args.repeat_min):
                msg = f"{t} {status} | px={rr['current_px']} | dd={rr['drawdown_pct']}% | level={rr['break_level']} | {rr['reason']}"
                notify("Bulkowski Watch", msg)

        save_state(state)
        out.to_csv("position_watch_latest.csv", index=False)

    run_once()
    if args.watch:
        while True:
            time.sleep(max(5, args.interval))
            run_once()


if __name__ == "__main__":
    main()
