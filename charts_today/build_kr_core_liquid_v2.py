#!/usr/bin/env python3
"""Build kr_core_liquid.txt from KOSPI200 + KOSDAQ150 using explicit business dates."""
from __future__ import annotations
from datetime import date, timedelta
from pathlib import Path
from pykrx import stock


def ymd(d: date) -> str:
    return d.strftime('%Y%m%d')

def recent_dates(n: int = 20):
    today = date.today()
    return [ymd(today - timedelta(days=i)) for i in range(n)]

def get_index_codes_by_name(ds: str, market: str):
    out = {}
    try:
        codes = stock.get_index_ticker_list(ds, market=market)
    except Exception:
        return out
    for c in codes:
        try:
            out[str(stock.get_index_ticker_name(c))] = str(c)
        except Exception:
            pass
    return out

def get_pdf(code: str):
    for ds in recent_dates(10):
        try:
            return list(stock.get_index_portfolio_deposit_file(code, ds))
        except TypeError:
            break
        except Exception:
            continue
    try:
        return list(stock.get_index_portfolio_deposit_file(code))
    except Exception:
        return []

def suffix_map(ds: str):
    out = {}
    for market, suffix in [('KOSPI', '.KS'), ('KOSDAQ', '.KQ')]:
        try:
            ticks = stock.get_market_ticker_list(ds, market=market)
        except Exception:
            ticks = []
        for t in ticks:
            out[str(t)] = suffix
    return out

def main():
    ref_ds = None
    smap = None
    for ds in recent_dates(20):
        sm = suffix_map(ds)
        if sm:
            ref_ds = ds; smap = sm; break
    if not ref_ds or not smap:
        raise SystemExit('Could not fetch recent KRX business day via pykrx.')
    kospi = get_index_codes_by_name(ref_ds, 'KOSPI')
    kosdaq = get_index_codes_by_name(ref_ds, 'KOSDAQ')
    code_200 = kospi.get('코스피 200') or kospi.get('코스피200') or '1028'
    code_150 = kosdaq.get('코스닥 150') or kosdaq.get('코스닥150')
    if not code_150:
        for name, code in kosdaq.items():
            if '150' in name:
                code_150 = code; break
    if not code_150:
        raise SystemExit('Could not identify KOSDAQ150 index code via pykrx.')
    ticks = []
    seen = set()
    for code in (code_200, code_150):
        for t in get_pdf(code):
            suffix = smap.get(str(t))
            if not suffix:
                continue
            sym = str(t) + suffix
            if sym not in seen:
                seen.add(sym)
                ticks.append(sym)
    Path('kr_core_liquid.txt').write_text('\n'.join(ticks) + ('\n' if ticks else ''), encoding='utf-8')
    print(f'Saved: kr_core_liquid.txt ({len(ticks)} tickers)')

if __name__ == '__main__':
    main()
