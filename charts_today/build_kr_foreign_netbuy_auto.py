#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
build_kr_foreign_netbuy_auto.py

Builds:
- kr_foreign_netbuy_auto.txt
- kr_foreign_netbuy_auto.csv

using pykrx investor net purchases (foreigners), with explicit date probing.

Notes:
- This is NOT tick-by-tick realtime. It is a delayed/public-data overlay.
- It tries recent business dates and uses the first date where data exists.
"""
from __future__ import annotations
from datetime import date, timedelta
from pathlib import Path
import argparse

import pandas as pd

try:
    from pykrx import stock
except Exception:
    stock = None


def ymd(d: date) -> str:
    return d.strftime("%Y%m%d")


def recent_dates(n: int = 40) -> list[str]:
    today = date.today()
    return [ymd(today - timedelta(days=i)) for i in range(n)]


def _ticker_suffix_map(ds: str) -> dict[str, str]:
    out = {}
    if stock is None:
        return out
    for market, suffix in [("KOSPI", ".KS"), ("KOSDAQ", ".KQ")]:
        try:
            ticks = stock.get_market_ticker_list(ds, market=market)
        except Exception:
            ticks = []
        for t in ticks:
            out[str(t)] = suffix
    return out


def _fetch_market_foreign(ds: str, market: str) -> pd.DataFrame:
    """
    Returns pykrx foreign net buy ranking for a single market and single date.
    """
    if stock is None:
        return pd.DataFrame()

    try:
        df = stock.get_market_net_purchases_of_equities(ds, ds, market, "외국인")
    except Exception:
        return pd.DataFrame()

    if df is None or df.empty:
        return pd.DataFrame()

    df = df.copy()
    df.index = df.index.astype(str)
    df = df.reset_index().rename(columns={"index": "ticker"})
    df["market"] = market
    return df


def _find_first_working_foreign_snapshot() -> tuple[str, pd.DataFrame]:
    for ds in recent_dates(40):
        kospi = _fetch_market_foreign(ds, "KOSPI")
        kosdaq = _fetch_market_foreign(ds, "KOSDAQ")
        if (kospi is not None and not kospi.empty) or (kosdaq is not None and not kosdaq.empty):
            merged = pd.concat([kospi, kosdaq], ignore_index=True)
            if not merged.empty:
                return ds, merged
    return "", pd.DataFrame()


def _col(df: pd.DataFrame, candidates: list[str]) -> str | None:
    for c in candidates:
        if c in df.columns:
            return c
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--top", type=int, default=20, help="top N foreign net-buy names to export")
    args = ap.parse_args()

    if stock is None:
        raise SystemExit("pykrx is not installed. Run: pip install pykrx")

    ds, df = _find_first_working_foreign_snapshot()
    out_txt = Path("kr_foreign_netbuy_auto.txt")
    out_csv = Path("kr_foreign_netbuy_auto.csv")

    if not ds or df.empty:
        out_txt.write_text("", encoding="utf-8")
        pd.DataFrame(columns=["date","market","ticker","name","net_buy_amount"]).to_csv(out_csv, index=False)
        raise SystemExit("Could not fetch foreign net-buy ranking from pykrx.")

    name_col = _col(df, ["종목명", "name"])
    net_col = _col(df, ["순매수거래대금", "순매수금액", "net_buy_amount"])
    if net_col is None:
        out_txt.write_text("", encoding="utf-8")
        df.to_csv(out_csv, index=False)
        raise SystemExit(f"Foreign net-buy data fetched for {ds}, but expected net-buy column not found.")

    df[net_col] = pd.to_numeric(df[net_col], errors="coerce")
    df = df.dropna(subset=[net_col]).copy()
    df = df.sort_values(net_col, ascending=False).reset_index(drop=True)

    suffix_map = _ticker_suffix_map(ds)
    rows = []
    txt_lines = []
    seen = set()

    for _, r in df.iterrows():
        ticker = str(r["ticker"]).zfill(6)
        suffix = suffix_map.get(ticker)
        if not suffix:
            continue
        sym = ticker + suffix
        if sym in seen:
            continue
        seen.add(sym)

        name = str(r[name_col]) if name_col else ""
        netv = float(r[net_col])
        rows.append({
            "date": ds,
            "market": r.get("market", ""),
            "ticker": sym,
            "name": name,
            "net_buy_amount": netv,
        })
        txt_lines.append(sym)
        if len(txt_lines) >= args.top:
            break

    out_txt.write_text("\n".join(txt_lines) + ("\n" if txt_lines else ""), encoding="utf-8")
    pd.DataFrame(rows).to_csv(out_csv, index=False)

    print(f"Saved: kr_foreign_netbuy_auto.txt ({len(txt_lines)} tickers) [date={ds}]")
    print(f"Saved: kr_foreign_netbuy_auto.csv ({len(rows)} rows)")
    if rows:
        print("Top names:")
        for r in rows[:10]:
            print(f" - {r['ticker']}  {r['name']}  net_buy={int(r['net_buy_amount'])}")


if __name__ == "__main__":
    main()
