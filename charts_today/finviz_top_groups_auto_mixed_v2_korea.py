#!/usr/bin/env python3
"""
Practical Korea sector-overlay builder.
Uses pykrx KRX sector indices with explicit recent business dates to avoid the
implicit nearest-business-day lookup that failed in your environment.

Output:
- kr_top_groups_auto_mixed.txt
- kr_top_groups_mixed_groups.csv
- kr_top_groups_mixed_members.csv
"""
from __future__ import annotations
import argparse
from datetime import date, timedelta
from pathlib import Path
import pandas as pd

# import lazily so script still explains errors cleanly
from pykrx import stock


def ymd(d: date) -> str:
    return d.strftime('%Y%m%d')


def recent_dates(n: int = 20) -> list[str]:
    today = date.today()
    ds = []
    for i in range(n):
        d = today - timedelta(days=i)
        ds.append(ymd(d))
    return ds


def get_working_market_tickers(market: str) -> tuple[str, list[str]]:
    for ds in recent_dates(20):
        try:
            ticks = stock.get_market_ticker_list(ds, market=market)
            if ticks:
                return ds, list(ticks)
        except Exception:
            pass
    return '', []


def suffix_map_for_date(ds: str) -> dict[str, str]:
    out = {}
    for market, suffix in [('KOSPI', '.KS'), ('KOSDAQ', '.KQ')]:
        try:
            ticks = stock.get_market_ticker_list(ds, market=market)
        except Exception:
            ticks = []
        for t in ticks:
            out[str(t)] = suffix
    return out


def get_index_codes_with_names(ds: str, market: str) -> list[tuple[str, str]]:
    try:
        codes = stock.get_index_ticker_list(ds, market=market)
    except Exception:
        return []
    out = []
    for c in codes:
        try:
            name = stock.get_index_ticker_name(c)
        except Exception:
            continue
        out.append((str(c), str(name)))
    return out


def is_sector_like(name: str) -> bool:
    bad = ['코스피', '코스닥', '200', '150', '100', '50', '대형주', '중형주', '소형주', '스타', '테마', 'KRX 300', '선물', '인버스', '레버리지']
    return not any(b in name for b in bad)


def get_index_close(ds: str, code: str) -> float | None:
    # try 1m, 1w, 1m windows via ohlcv ranges later; this gets single-day close
    try:
        df = stock.get_index_ohlcv(ds, ds, code)
        if df is None or df.empty:
            return None
        col = '종가' if '종가' in df.columns else df.columns[-1]
        return float(df[col].iloc[-1])
    except Exception:
        return None


def period_return(code: str, end_ds: str, days_back: int) -> float | None:
    # choose a start date far enough back, then use first/last available close in window
    end_d = pd.to_datetime(end_ds).date()
    start_ds = ymd(end_d - timedelta(days=days_back*2))
    try:
        df = stock.get_index_ohlcv(start_ds, end_ds, code)
        if df is None or df.empty:
            return None
        col = '종가' if '종가' in df.columns else df.columns[-1]
        s = pd.to_numeric(df[col], errors='coerce').dropna()
        if len(s) < 2:
            return None
        return float((s.iloc[-1] / s.iloc[0] - 1.0) * 100.0)
    except Exception:
        return None


def get_pdf(code: str):
    # try optional date arg first, then plain call
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


def rank_desc(series: pd.Series) -> pd.Series:
    s = pd.to_numeric(series, errors='coerce')
    return (1.0 - s.rank(method='average', ascending=False, pct=True) + (1.0 / max(len(s.dropna()), 1))).fillna(0.0)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--top-sectors', type=int, default=5)
    ap.add_argument('--max-per-group', type=int, default=15)
    ap.add_argument('--wt-change', type=float, default=0.5)
    ap.add_argument('--wt-perf-week', type=float, default=0.3)
    ap.add_argument('--wt-perf-month', type=float, default=0.2)
    args = ap.parse_args()

    ds, kospi_t = get_working_market_tickers('KOSPI')
    ds2, kosdaq_t = get_working_market_tickers('KOSDAQ')
    ref_ds = ds or ds2
    if not ref_ds:
        raise SystemExit('Could not fetch recent KRX business day via pykrx. Try again later.')
    smap = suffix_map_for_date(ref_ds)

    idx = []
    for market in ('KOSPI', 'KOSDAQ'):
        idx.extend(get_index_codes_with_names(ref_ds, market))
    idx = [(c, n) for c, n in idx if is_sector_like(n)]
    rows = []
    for code, name in idx:
        chg = period_return(code, ref_ds, 1)
        pw = period_return(code, ref_ds, 7)
        pm = period_return(code, ref_ds, 30)
        if chg is None and pw is None and pm is None:
            continue
        rows.append({'index_code': code, 'group_name': name, 'change': chg, 'perf_week': pw, 'perf_month': pm})
    if not rows:
        raise SystemExit('No Korea sector index rows built.')
    df = pd.DataFrame(rows)
    df['rank_change'] = rank_desc(df['change'])
    df['rank_perf_week'] = rank_desc(df['perf_week'])
    df['rank_perf_month'] = rank_desc(df['perf_month'])
    tot = args.wt_change + args.wt_perf_week + args.wt_perf_month
    df['score'] = (args.wt_change*df['rank_change'] + args.wt_perf_week*df['rank_perf_week'] + args.wt_perf_month*df['rank_perf_month']) / tot
    df = df.sort_values(['score', 'change', 'perf_week', 'perf_month', 'group_name'], ascending=[False, False, False, False, True]).reset_index(drop=True)
    selected = df.head(args.top_sectors).copy()

    members_rows = []
    final_tickers = []
    seen = set()
    for i, r in selected.iterrows():
        code, name = str(r['index_code']), str(r['group_name'])
        print(f"[group] #{i+1} {name} score={r['score']:.4f} chg={r['change'] if pd.notna(r['change']) else 'NA'} w={r['perf_week'] if pd.notna(r['perf_week']) else 'NA'} m={r['perf_month'] if pd.notna(r['perf_month']) else 'NA'}")
        pdf = get_pdf(code)
        count = 0
        for t in pdf:
            t = str(t)
            suffix = smap.get(t)
            if not suffix:
                continue
            sym = t + suffix
            members_rows.append({'group_name': name, 'index_code': code, 'ticker': sym})
            if sym not in seen:
                seen.add(sym)
                final_tickers.append(sym)
                count += 1
            if count >= args.max_per_group:
                break

    Path('kr_top_groups_auto_mixed.txt').write_text('\n'.join(final_tickers) + ('\n' if final_tickers else ''), encoding='utf-8')
    selected.to_csv('kr_top_groups_mixed_groups.csv', index=False)
    pd.DataFrame(members_rows).to_csv('kr_top_groups_mixed_members.csv', index=False)
    print(f"Saved: kr_top_groups_auto_mixed.txt ({len(final_tickers)} tickers)")
    print(f"Saved: kr_top_groups_mixed_groups.csv ({len(selected)} groups)")
    print(f"Saved: kr_top_groups_mixed_members.csv ({len(members_rows)} rows before final dedupe)")

if __name__ == '__main__':
    main()
