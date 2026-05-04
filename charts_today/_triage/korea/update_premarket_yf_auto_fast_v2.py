#!/usr/bin/env python3
from __future__ import annotations
import argparse
import io
from contextlib import redirect_stdout, redirect_stderr
from pathlib import Path
import numpy as np
import pandas as pd
import yfinance as yf
from pipeline_config import load_default_groups, union_ordered, print_group_counts, now_kst_str, write_header_lines

SKIPLIST_PATH = Path('.yahoo_skiplist.txt')

def load_skiplist() -> set[str]:
    if not SKIPLIST_PATH.exists():
        return set()
    s = set()
    for line in SKIPLIST_PATH.read_text(encoding='utf-8', errors='ignore').splitlines():
        t = line.strip().upper()
        if t and not t.startswith('#'):
            s.add(t)
    return s

def save_skiplist(s: set[str]) -> None:
    SKIPLIST_PATH.write_text('\n'.join(sorted(s)) + '\n', encoding='utf-8')

def safe_download(tickers: list[str]) -> pd.DataFrame:
    if not tickers:
        return pd.DataFrame()
    buf = io.StringIO()
    with redirect_stdout(buf), redirect_stderr(buf):
        df = yf.download(
            tickers=tickers,
            period='1d',
            interval='1m',
            group_by='ticker',
            auto_adjust=False,
            prepost=True,
            progress=False,
            threads=True,
        )
    return df

def last_px_for_ticker(df: pd.DataFrame, t: str) -> tuple[float | None, str | None]:
    if df is None or df.empty:
        return None, None
    try:
        if isinstance(df.columns, pd.MultiIndex):
            lvl0 = df.columns.get_level_values(0)
            lvl1 = df.columns.get_level_values(1)
            if t in set(lvl1):
                sub = df.xs(t, level=1, axis=1)
            elif t in set(lvl0):
                sub = df[t]
            else:
                return None, None
        else:
            sub = df
        if 'Close' not in sub.columns:
            return None, None
        s = sub['Close'].dropna()
        if s.empty:
            return None, None
        ts = pd.to_datetime(s.index[-1]).to_pydatetime().replace(tzinfo=None).isoformat()
        return float(s.iloc[-1]), ts
    except Exception:
        return None, None

def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument('--universe-file', default=None, help='Optional: ticker list file. If set, scan ONLY these tickers.')
    ap.add_argument('--refresh-bad', action='store_true', help='re-test tickers in skiplist')
    args = ap.parse_args()

    groups = load_default_groups()
    print_group_counts(groups, title='INPUT TXT COUNTS (ALL GROUPS)')
    union = union_ordered(groups)

    if args.universe_file:
        uf = Path(args.universe_file)
        if not uf.exists():
            raise FileNotFoundError(f'--universe-file not found: {uf}')
        tickers = []
        for line in uf.read_text(encoding='utf-8', errors='ignore').splitlines():
            s = line.strip().upper()
            if not s or s.startswith('#'):
                continue
            tickers.append(s)
        seen = set()
        union = [t for t in tickers if not (t in seen or seen.add(t))]

    skip = load_skiplist()
    effective = union if args.refresh_bad else [t for t in union if t not in skip]

    print(f'KST_NOW: {now_kst_str()}')
    print(f'SKIPLIST_SIZE: {len(skip)}  (refresh_bad={args.refresh_bad})')
    print(f'UNION_TOTAL: {len(union)}  |  WILL_QUERY: {len(effective)}\n')

    df = safe_download(effective)
    got: dict[str, tuple[float, str]] = {}
    missing: list[str] = []
    for t in effective:
        px, ts = last_px_for_ticker(df, t)
        if px is None or ts is None or not np.isfinite(px):
            missing.append(t)
        else:
            got[t] = (px, ts)

    if missing:
        skip.update(missing)
        save_skiplist(skip)

    rows_debug = []
    skip_now = load_skiplist()
    for g in groups:
        for t in g.tickers:
            px, ts = got.get(t, (np.nan, ''))
            status = 'ok' if t in got else ('skipped' if (not args.refresh_bad and t in skip_now) else 'missing')
            rows_debug.append({
                'group': g.group,
                'ticker': t,
                'yahoo_symbol': t,
                'premarket': px,
                'yahoo_ts': ts,
                'status': status,
            })
    debug_df = pd.DataFrame(rows_debug)

    auto_df = pd.DataFrame([
        {'ticker': t, 'premarket': got.get(t, (np.nan, ''))[0], 'yahoo_ts': got.get(t, (np.nan, ''))[1]}
        for t in union
    ])

    header = [
        f'saved_at_kr,{now_kst_str()}',
        f'input_groups,{",".join([g.group for g in groups])}',
        f'count_union,{len(union)}',
        *[f'count_{g.group},{len(g.tickers)}' for g in groups],
        f'queried,{len(effective)}',
        f'ok,{len(got)}',
        f'missing,{len(missing)}',
        f'skiplist_size,{len(skip)}',
    ]

    write_header_lines('premarket_auto.csv', header, auto_df.to_csv(index=False))
    write_header_lines('premarket_auto_debug.csv', header, debug_df.to_csv(index=False))
    print('Saved: premarket_auto.csv')
    print('Saved: premarket_auto_debug.csv')
    if missing:
        print(f'NEW_MISSING_ADDED_TO_SKIPLIST: {len(missing)}')

if __name__ == '__main__':
    main()
