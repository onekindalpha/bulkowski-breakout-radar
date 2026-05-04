#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
build_kr_foreign_netbuy_auto_v2.py

Build:
- kr_foreign_netbuy_auto.txt
- kr_foreign_netbuy_auto.csv

Default behavior:
- use FOREIGNER cumulative net-buy over the last 3 business-day window
- fall back across recent end-dates until a working snapshot is found

Why:
- Korea single-day net-buy is noisy
- 3-day default is more stable
- 5-day can be used as a slower confirmation view

Examples:
    python build_kr_foreign_netbuy_auto_v2.py
    python build_kr_foreign_netbuy_auto_v2.py --days 5 --top 30
"""
from __future__ import annotations

import argparse
from datetime import date, timedelta
from pathlib import Path
import pandas as pd

try:
    from pykrx import stock
except Exception:
    stock = None


OUT_TXT = Path("kr_foreign_netbuy_auto.txt")
OUT_CSV = Path("kr_foreign_netbuy_auto.csv")


def ymd(d: date) -> str:
    return d.strftime("%Y%m%d")


def recent_dates(n: int = 40) -> list[str]:
    today = date.today()
    return [ymd(today - timedelta(days=i)) for i in range(n)]


def _col(df: pd.DataFrame, candidates: list[str]) -> str | None:
    for c in candidates:
        if c in df.columns:
            return c
    return None


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


def _get_market_business_dates(ds_end: str, days: int) -> list[str]:
    """
    Build a recent business-date list ending at ds_end by probing backwards.
    This avoids relying on pykrx's internal nearest-day helper that can fail.
    """
    end_d = pd.to_datetime(ds_end).date()
    out = []
    for i in range(days * 4 + 10):
        d = end_d - timedelta(days=i)
        out.append(ymd(d))
    return sorted(set(out))


def _fetch_market_foreign_window(ds_end: str, market: str, days: int) -> pd.DataFrame:
    """
    Foreign cumulative net-buy over a recent date window.
    Uses a broader calendar range because pykrx itself handles business days within it.
    """
    if stock is None:
        return pd.DataFrame()

    end_d = pd.to_datetime(ds_end).date()
    start_d = end_d - timedelta(days=max(days * 4, 7))
    start_ds = ymd(start_d)

    try:
        df = stock.get_market_net_purchases_of_equities(start_ds, ds_end, market, "외국인")
    except Exception:
        return pd.DataFrame()

    if df is None or df.empty:
        return pd.DataFrame()

    df = df.copy()
    df.index = df.index.astype(str)
    df = df.reset_index().rename(columns={"index": "ticker"})
    df["market"] = market
    df["window_end"] = ds_end
    df["window_days"] = days
    return df


def _find_first_working_window(days: int) -> tuple[str, pd.DataFrame]:
    """
    Try recent end-dates until a non-empty combined KOSPI+KOSDAQ foreign window is found.
    """
    for ds in recent_dates(40):
        kospi = _fetch_market_foreign_window(ds, "KOSPI", days)
        kosdaq = _fetch_market_foreign_window(ds, "KOSDAQ", days)
        if (kospi is not None and not kospi.empty) or (kosdaq is not None and not kosdaq.empty):
            merged = pd.concat([kospi, kosdaq], ignore_index=True)
            if not merged.empty:
                return ds, merged
    return "", pd.DataFrame()


def _preserve_or_blank_on_fail(message: str, keep_last_on_fail: bool):
    if keep_last_on_fail and OUT_TXT.exists() and OUT_CSV.exists():
        raise SystemExit(message + " Existing successful files were kept unchanged.")
    OUT_TXT.write_text("", encoding="utf-8")
    pd.DataFrame(columns=["date","days","market","ticker","name","net_buy_amount"]).to_csv(OUT_CSV, index=False)
    raise SystemExit(message)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--top", type=int, default=20, help="top N foreign net-buy names to export")
    ap.add_argument("--days", type=int, default=3, help="foreign cumulative net-buy window in business-ish days (default: 3)")
    ap.add_argument("--keep-last-on-fail", action="store_true", default=True,
                    help="keep previous successful output files instead of blanking them on failure (default: on)")
    ap.add_argument("--no-keep-last-on-fail", dest="keep_last_on_fail", action="store_false")
    args = ap.parse_args()

    if stock is None:
        raise SystemExit("pykrx is not installed. Run: pip install pykrx")

    ds, df = _find_first_working_window(args.days)

    if not ds or df.empty:
        _preserve_or_blank_on_fail(
            f"Could not fetch foreign net-buy ranking from pykrx for a {args.days}-day window.",
            args.keep_last_on_fail
        )

    name_col = _col(df, ["종목명", "name"])
    net_col = _col(df, ["순매수거래대금", "순매수금액", "순매수대금", "net_buy_amount"])

    if net_col is None:
        # keep raw csv for debugging
        raw_debug = Path("kr_foreign_netbuy_auto_raw_debug.csv")
        df.to_csv(raw_debug, index=False)
        _preserve_or_blank_on_fail(
            f"Foreign data fetched for end={ds}, but expected net-buy column not found. Raw debug saved to {raw_debug}.",
            args.keep_last_on_fail
        )

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
            "days": args.days,
            "market": r.get("market", ""),
            "ticker": sym,
            "name": name,
            "net_buy_amount": netv,
        })
        txt_lines.append(sym)

        if len(txt_lines) >= args.top:
            break

    if not txt_lines:
        _preserve_or_blank_on_fail(
            f"Foreign data was fetched for end={ds} but no suffix-mapped tickers were produced.",
            args.keep_last_on_fail
        )

    OUT_TXT.write_text("\n".join(txt_lines) + ("\n" if txt_lines else ""), encoding="utf-8")
    pd.DataFrame(rows).to_csv(OUT_CSV, index=False)

    print(f"Saved: {OUT_TXT.name} ({len(txt_lines)} tickers) [end={ds}, days={args.days}]")
    print(f"Saved: {OUT_CSV.name} ({len(rows)} rows)")
    print("Top names:")
    for r in rows[:10]:
        try:
            netv = int(float(r["net_buy_amount"]))
        except Exception:
            netv = r["net_buy_amount"]
        print(f" - {r['ticker']}  {r['name']}  net_buy={netv}")


if __name__ == "__main__":
    main()
