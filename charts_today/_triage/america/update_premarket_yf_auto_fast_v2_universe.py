#!/usr/bin/env python3
"""
update_premarket_yf_auto_fast_v2.py

Goals
- FAST: single batch yfinance download for all tickers (1m, 1d, prepost=True).
- All sources always considered (no long commands): macro_watch_yahoo/tickers_core/tickers_leverage2x/finviz_manual
- Missing tickers: skip quickly via persistent skiplist (.yahoo_skiplist.txt)
- Outputs:
    premarket_auto.csv
    premarket_auto_debug.csv
  Both include header lines with counts & timestamp (KST).

Flags
  --refresh-bad : ignore skiplist for this run (re-test missing tickers)

Notes
- Yahoo/yfinance "real-time" is not guaranteed; treat as a fast snapshot.
- For your workflow: you can still override with Samsung manual prices later.
"""

from __future__ import annotations
import argparse
import io
from contextlib import redirect_stdout, redirect_stderr
from pathlib import Path
from datetime import datetime
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
import yfinance as yf

from pipeline_config import load_default_groups, union_ordered, print_group_counts, now_kst_str, write_header_lines

SKIPLIST_PATH = Path(".yahoo_skiplist.txt")

def load_skiplist() -> set[str]:
    if not SKIPLIST_PATH.exists():
        return set()
    s = set()
    for line in SKIPLIST_PATH.read_text(encoding="utf-8", errors="ignore").splitlines():
        t = line.strip().upper()
        if t and not t.startswith("#"):
            s.add(t)
    return s

def save_skiplist(s: set[str]) -> None:
    SKIPLIST_PATH.write_text("\n".join(sorted(s)) + "\n", encoding="utf-8")

def safe_download(tickers: list[str]) -> pd.DataFrame:
    buf = io.StringIO()
    with redirect_stdout(buf), redirect_stderr(buf):
        df = yf.download(
            tickers=tickers,
            period="1d",
            interval="1m",
            group_by="ticker",
            auto_adjust=False,
            prepost=True,
            progress=False,
            threads=True,
        )
    return df

def last_px_for_ticker(df: pd.DataFrame, t: str) -> tuple[float | None, str | None]:
    """
    Returns (last_close, last_timestamp_iso).
    Works for both single-ticker and multi-ticker outputs.
    """
    if df is None or df.empty:
        return None, None

    try:
        if isinstance(df.columns, pd.MultiIndex):
            # expected layout: (field, ticker) OR (ticker, field) depending on yfinance
            lvl0 = df.columns.get_level_values(0)
            lvl1 = df.columns.get_level_values(1)
            sub = None
            if t in set(lvl1):
                sub = df.xs(t, level=1, axis=1)
            elif t in set(lvl0):
                sub = df[t]
            else:
                return None, None
        else:
            # single ticker
            sub = df
        if "Close" not in sub.columns:
            return None, None
        s = sub["Close"].dropna()
        if s.empty:
            return None, None
        ts = s.index[-1]
        px = float(s.iloc[-1])
        # timestamp as ISO (naive)
        ts_iso = pd.to_datetime(ts).to_pydatetime().replace(tzinfo=None).isoformat()
        return px, ts_iso
    except Exception:
        return None, None

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--universe-file", default=None, help="Optional: ticker list file to query (e.g. tickers.txt). If set, overrides group union.")
    ap.add_argument("--refresh-bad", action="store_true", help="re-test tickers in skiplist")
    args = ap.parse_args()

    
# Optional universe override (keeps your commands short)
if args.universe_file:
    uf = Path(args.universe_file)
    if not uf.exists():
        raise FileNotFoundError(f"--universe-file not found: {uf}")
    tickers_universe = []
    for line in uf.read_text(encoding="utf-8", errors="ignore").splitlines():
        s = line.strip()
        if not s or s.startswith("#"):
            continue
        tickers_universe.append(s)
    seen=set()
    tickers_universe=[t for t in tickers_universe if not (t in seen or seen.add(t))]
else:
    tickers_universe = None
groups = load_default_groups()
    print_group_counts(groups, title="INPUT TXT COUNTS (ALL GROUPS)")

    # build per-group ticker lists + union
    union = union_ordered(groups)

# Universe override: if provided, use it as the base union (before skiplist filtering)
if tickers_universe is not None:
    union = tickers_universe


    skip = load_skiplist()
    if args.refresh_bad:
        effective = union
    else:
        effective = [t for t in union if t not in skip]

    print(f"KST_NOW: {now_kst_str()}")
    print(f"SKIPLIST_SIZE: {len(skip)}  (refresh_bad={args.refresh_bad})")
    print(f"UNION_TOTAL: {len(union)}  |  WILL_QUERY: {len(effective)}\n")

    # download once
    df = safe_download(effective)

    # map ticker -> px,ts
    got = {}
    missing = []
    for t in effective:
        px, ts = last_px_for_ticker(df, t)
        if px is None or ts is None or not np.isfinite(px):
            missing.append(t)
        else:
            got[t] = (px, ts)

    # update skiplist with missing (unless refresh-bad: still record)
    if missing:
        skip.update(missing)
        save_skiplist(skip)

    # build outputs with group info
    rows_debug = []
    for g in groups:
        for t in g.tickers:
            px, ts = (got.get(t, (np.nan, "")))
            status = "ok" if t in got else ("skipped" if (not args.refresh_bad and t in load_skiplist()) else "missing")
            rows_debug.append({
                "group": g.group,
                "ticker": t,
                "yahoo_symbol": t,
                "premarket": px,
                "yahoo_ts": ts,
                "status": status,
            })

    debug_df = pd.DataFrame(rows_debug)

    # premarket_auto.csv: one row per ticker (union order), with best px
    auto_rows = []
    for t in union:
        px, ts = got.get(t, (np.nan, ""))
        auto_rows.append({"ticker": t, "premarket": px, "yahoo_ts": ts})
    auto_df = pd.DataFrame(auto_rows)

    # header lines
    header = [
        f"saved_at_kr,{now_kst_str()}",
        f"input_groups,{','.join([g.group for g in groups])}",
        f"count_union,{len(union)}",
        *[f"count_{g.group},{len(g.tickers)}" for g in groups],
        f"queried,{len(effective)}",
        f"ok,{len(got)}",
        f"missing,{len(missing)}",
        f"skiplist_size,{len(skip)}",
    ]

    # write with header comments
    auto_body = auto_df.to_csv(index=False)
    debug_body = debug_df.to_csv(index=False)

    from pipeline_config import write_header_lines
    write_header_lines("premarket_auto.csv", header, auto_body)
    write_header_lines("premarket_auto_debug.csv", header, debug_body)

    print("Saved: premarket_auto.csv")
    print("Saved: premarket_auto_debug.csv")
    if missing:
        print(f"NEW_MISSING_ADDED_TO_SKIPLIST: {len(missing)}")

if __name__ == "__main__":
    main()
