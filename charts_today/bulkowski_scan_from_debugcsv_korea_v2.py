#!/usr/bin/env python3
from __future__ import annotations
import argparse, re, io
from contextlib import redirect_stdout, redirect_stderr
from pathlib import Path
import numpy as np
import pandas as pd
import yfinance as yf

DEBUG_CSV = 'premarket_auto_debug_korea.csv'

# FIX:
# - include actual korea group names produced by update_premarket_yf_auto_debug_korea.py
INCLUDE_GROUPS = {
    'kr_core_liquid',
    'kr_top_groups_auto_mixed',
    'kr_manual_conviction',
    'kr_tactical_leverage',
    'tickers_core_korea',
    'tickers_leverage2x_korea',
    'finviz_manual_korea',
    'macro_watch_yahoo_korea',
}
EXCLUDE = set()

def load_universe(csv_path: str) -> list[str]:
    df = pd.read_csv(csv_path, comment='#')
    if 'group' not in df.columns or 'ticker' not in df.columns:
        raise SystemExit(f'{csv_path} missing required columns')
    if 'error' in df.columns:
        df = df[df['error'].isna() | (df['error'].astype(str).str.strip() == '')]
    df = df[df['group'].astype(str).isin(INCLUDE_GROUPS)]
    tickers = df['ticker'].dropna().astype(str).str.upper().unique().tolist()
    tickers = [t for t in tickers if t not in EXCLUDE and re.match(r'^\d{6}\.(KS|KQ)$', t)]
    return tickers

def get_daily(symbol: str, period='9mo') -> pd.DataFrame:
    buf = io.StringIO()
    with redirect_stdout(buf), redirect_stderr(buf):
        df = yf.download(symbol, period=period, interval='1d', auto_adjust=True, progress=False, threads=False)
    if df is None or df.empty:
        return pd.DataFrame()
    return df.dropna()

def breakout_score(df: pd.DataFrame) -> float:
    if len(df) < 80:
        return 0.0
    close = df['Close'].values
    high = df['High'].values
    low = df['Low'].values
    vol = df['Volume'].values if 'Volume' in df.columns else None

    def is_higher_lows(window=20):
        l = low[-window:]
        x = np.arange(len(l))
        slope = np.polyfit(x, l, 1)[0]
        return slope > 0

    last = close[-1]
    r60 = np.max(high[-60:-1])
    r20 = np.max(high[-20:-1])
    score = 0.0
    if last > r60:
        score += 3.0
    if last > r20:
        score += 2.0
    if is_higher_lows(25):
        score += 1.5
    if vol is not None and len(vol) > 30:
        v5 = np.mean(vol[-5:])
        v20 = np.mean(vol[-25:-5])
        if v20 > 0 and v5 / v20 >= 1.3:
            score += 1.0
    return score

def double_bottom_score(df: pd.DataFrame) -> float:
    if len(df) < 140:
        return 0.0
    close = df['Close'].values
    low = df['Low'].values
    window = 120
    L = low[-window:]
    idx_sorted = np.sort(np.argsort(L)[:10])
    best = 0.0
    for i in range(len(idx_sorted)):
        for j in range(i + 1, len(idx_sorted)):
            a, b = idx_sorted[i], idx_sorted[j]
            if b - a < 15:
                continue
            la, lb = L[a], L[b]
            if abs(la - lb) / max(la, 1e-9) <= 0.03:
                mid_peak = np.max(close[-window + a : -window + b])
                neckline = mid_peak
                last = close[-1]
                tmp = 1.5
                if last >= neckline:
                    tmp += 1.5
                elif last >= neckline * 0.98:
                    tmp += 0.8
                best = max(best, tmp)
    return best

def scan(universe: list[str]) -> pd.DataFrame:
    rows = []
    for sym in universe:
        df = get_daily(sym)
        if df.empty:
            continue
        s1 = breakout_score(df)
        s2 = double_bottom_score(df)
        total = s1 + s2
        rows.append((sym, total, s1, s2, float(df['Close'].iloc[-1])))
    out = pd.DataFrame(rows, columns=['symbol', 'score_total', 'score_breakout', 'score_double_bottom', 'last_close'])
    if out.empty:
        return out
    return out.sort_values(['score_total', 'score_breakout', 'score_double_bottom'], ascending=False).reset_index(drop=True)

def write_candidates(df: pd.DataFrame, top_n: int, out_txt: str = 'candidates_korea.txt'):
    top = df.head(top_n)
    Path(out_txt).write_text(
        "\n".join(top['symbol'].astype(str).tolist()) + ("\n" if len(top) else ""),
        encoding='utf-8'
    )
    print(f"Saved: {out_txt} ({len(top)} tickers)")

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--debug-csv', default=DEBUG_CSV)
    ap.add_argument('--top', type=int, default=10)
    ap.add_argument('--out', default='candidates_korea.txt')
    args = ap.parse_args()

    universe = load_universe(args.debug_csv)
    result = scan(universe)
    if result.empty:
        # prevent stale candidates confusion
        Path(args.out).write_text("", encoding='utf-8')
        print('No results.')
        print(f"Saved: {args.out} (0 tickers)")
        return
    print(result.head(args.top).to_string(index=False))
    write_candidates(result, args.top, args.out)

if __name__ == '__main__':
    main()
