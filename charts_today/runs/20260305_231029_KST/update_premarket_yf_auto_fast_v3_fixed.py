#!/usr/bin/env python3
"""
update_premarket_yf_auto_fast_v3_fixed.py

Fixes universe-file override bug:
- If --universe-file is provided, ONLY those tickers are considered for querying/skipping.
- WILL_QUERY is computed from BASE (universe-file or union) minus skiplist (unless --refresh-bad).
- Avoids the confusing case where BASE=38 but WILL_QUERY=89.

Outputs:
- premarket_auto.csv
- premarket_auto_debug.csv
"""
import argparse
from pathlib import Path
from datetime import datetime
from zoneinfo import ZoneInfo
import contextlib, io
import pandas as pd

from pipeline_config import load_default_groups, print_group_counts, union_ordered, now_kst_str

SKIPLIST_PATH = Path(".yahoo_skiplist.txt")
KST = ZoneInfo("Asia/Seoul")

def read_tickers_file(path: str) -> list[str]:
    p = Path(path)
    if not p.exists():
        return []
    out = []
    for line in p.read_text(encoding="utf-8", errors="ignore").splitlines():
        s = line.strip().upper()
        if not s or s.startswith("#"):
            continue
        if "#" in s:
            s = s.split("#", 1)[0].strip()
        for tok in s.replace(",", " ").split():
            t = tok.strip().upper()
            if t:
                out.append(t)
    # dedupe keep order
    seen=set()
    uniq=[]
    for t in out:
        if t not in seen:
            seen.add(t)
            uniq.append(t)
    return uniq

def load_skiplist() -> set[str]:
    if not SKIPLIST_PATH.exists():
        return set()
    s = set()
    for line in SKIPLIST_PATH.read_text(encoding="utf-8", errors="ignore").splitlines():
        t = line.strip().upper()
        if t:
            s.add(t)
    return s

def save_skiplist(s: set[str]):
    SKIPLIST_PATH.write_text("\n".join(sorted(s)) + ("\n" if s else ""), encoding="utf-8")

def yf_quote(yf, ticker: str):
    # 1m prepost can be flaky; keep it simple (2d, 1m) with prepost
    try:
        df = yf.download(ticker, period="2d", interval="1m", prepost=True, progress=False)
    except Exception as e:
        return None, str(e)
    if df is None or df.empty:
        return None, "no_data"
    # Try last close from last row
    try:
        px = float(df["Close"].dropna().iloc[-1])
        ts = df.index[-1]
        return (px, ts), ""
    except Exception as e:
        return None, f"bad_df:{e}"

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--refresh-bad", action="store_true", help="re-test tickers in skiplist")
    ap.add_argument("--universe-file", default=None, help="Optional: file with tickers to query (one per line). If set, queries only those tickers.")
    args = ap.parse_args()

    import yfinance as yf

    groups = load_default_groups()
    print_group_counts(groups)

    union = union_ordered(groups)

    base = union
    if args.universe_file:
        uni = read_tickers_file(args.universe_file)
        if uni:
            base = uni
        print(f"USING universe-file: {args.universe_file} (tickers={len(uni)})")

    skip = load_skiplist()
    if args.refresh_bad:
        effective = base
    else:
        effective = [t for t in base if t not in skip]

    print(f"KST_NOW: {now_kst_str()}")
    print(f"SKIPLIST_SIZE: {len(skip)}  (refresh_bad={args.refresh_bad})")
    print(f"UNION_TOTAL: {len(union)}  |  BASE_TOTAL: {len(base)}  |  WILL_QUERY: {len(effective)}")

    rows=[]
    dbg=[]
    new_missing=set()

    # silence yfinance spam
    with contextlib.redirect_stderr(io.StringIO()):
        for t in effective:
            (qt, err) = (None, "")
            res, err = yf_quote(yf, t)
            if res is None:
                new_missing.add(t)
                dbg.append({"ticker": t, "status":"missing", "error": err})
                continue
            px, ts = res
            rows.append({"ticker": t, "premarket": px, "ts": str(ts)})
            dbg.append({"ticker": t, "status":"ok", "error": ""})

    pd.DataFrame(rows).to_csv("premarket_auto.csv", index=False)
    pd.DataFrame(dbg).to_csv("premarket_auto_debug.csv", index=False)
    print("Saved: premarket_auto.csv")
    print("Saved: premarket_auto_debug.csv")

    if new_missing:
        # update skiplist (only for tickers we actually attempted)
        if not args.refresh_bad:
            before = len(skip)
            skip |= new_missing
            save_skiplist(skip)
            print(f"NEW_MISSING_ADDED_TO_SKIPLIST: {len(skip)-before}")
        else:
            print(f"Missing (refresh_bad run): {len(new_missing)}")

if __name__ == "__main__":
    main()
