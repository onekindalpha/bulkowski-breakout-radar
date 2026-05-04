#!/usr/bin/env python3
from __future__ import annotations
import argparse, subprocess
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo
import pandas as pd
from pipeline_config_korea import now_kst_str, write_header_lines

KST = ZoneInfo('Asia/Seoul')
TICKER_RE = __import__('re').compile(r'^\d{6}\.(KS|KQ)$')

def prompt_float(prompt: str):
    s = input(prompt).strip()
    if s == '':
        return None
    try:
        return float(s.replace(',', ''))
    except Exception:
        print('  (invalid price; press Enter to skip)')
        return None

def load_candidates(path: str, limit: int) -> list[str]:
    p = Path(path)
    if not p.exists():
        return []
    out = []
    for line in p.read_text(encoding='utf-8', errors='ignore').splitlines():
        s = line.strip().upper()
        if not s:
            continue
        if TICKER_RE.match(s):
            out.append(s)
        if len(out) >= limit:
            break
    return out

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--max', type=int, default=10)
    ap.add_argument('--bulkowski-cmd', type=str, default='python bulkowski_scan_from_debugcsv_korea_v2.py --top 10 --out candidates_korea.txt')
    ap.add_argument('--candidates', type=str, default='candidates_korea.txt')
    args = ap.parse_args()

    print(f'KST_NOW: {now_kst_str()}')

    # always refresh candidates first
    cp = subprocess.run(args.bulkowski_cmd, shell=True, text=True, capture_output=True, check=False)
    if cp.stdout:
        print(cp.stdout, end='' if cp.stdout.endswith('\n') else '\n')
    if cp.returncode != 0:
        if cp.stderr:
            print(cp.stderr)
        raise SystemExit(f'bulkowski scan failed with exit code {cp.returncode}')

    tickers = load_candidates(args.candidates, args.max)
    if not tickers:
        stale = Path('premarket_manual_korea.csv')
        if stale.exists():
            stale.unlink()
            print("Removed stale premarket_manual_korea.csv because no fresh candidates were found.")
        raise SystemExit('No valid Korea tickers parsed from candidates_korea.txt.')

    print(f"\nUSING candidates_korea.txt: {len(tickers)} tickers")
    print("Enter Korea current price for each ticker.")
    print("Leave price empty to SKIP that ticker.\n")

    rows = []
    for i, t in enumerate(tickers, 1):
        px = prompt_float(f'[{i}/{len(tickers)}] {t} Price: ')
        if px is None:
            continue
        rows.append({'ticker': t, 'premarket': float(px), 'entered_at_kr': datetime.now(KST).strftime('%Y-%m-%d %H:%M:%S'), 'source': 'manual'})

    out_path = Path('premarket_manual_korea.csv')
    if not rows:
        if out_path.exists():
            out_path.unlink()
            print('Removed stale premarket_manual_korea.csv because no manual rows were entered.')
        print('\nNo manual prices entered. Nothing to save.')
        return

    df = pd.DataFrame(rows, columns=['ticker','premarket','entered_at_kr','source'])
    df = df.drop_duplicates(subset=['ticker'], keep='last').sort_values('ticker')
    header = [f'saved_at_kr,{now_kst_str()}', f'count_rows,{len(df)}', 'candidates_source,candidates_korea.txt']
    write_header_lines(str(out_path), header, df.to_csv(index=False))
    print(f'\nSaved premarket_manual_korea.csv ({len(df)} rows).')

if __name__ == '__main__':
    main()
