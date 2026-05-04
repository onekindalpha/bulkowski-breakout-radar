#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
build_macro_watch_yahoo_korea.py

Auto-build macro_watch_yahoo_korea.txt

Purpose:
- create a Korea macro/benchmark/sector-proxy watchlist automatically
- validate symbols with yfinance
- keep broad market proxies
- rank sector/theme proxies by 1d/1w/1m mixed momentum
- append a few bellwether single names for context

Outputs:
- macro_watch_yahoo_korea.txt
- macro_watch_yahoo_korea.csv

Notes:
- This is not a data-scrape of Yahoo screener pages.
- It uses a curated Korea proxy universe, then validates and ranks using Yahoo Finance price history.
"""

from __future__ import annotations
import argparse
from pathlib import Path

import pandas as pd
import yfinance as yf


# Broad market / beta proxies (kept if valid)
BROAD_PROXIES = [
    ("069500.KS", "KODEX 200", "broad"),
    ("102110.KS", "TIGER 200", "broad"),
    ("122630.KS", "KODEX Leverage", "broad"),
    ("252670.KS", "KODEX 200 Inverse 2X", "broad"),
    ("229200.KQ", "KODEX KOSDAQ150", "broad"),
    ("233740.KS", "KODEX KOSDAQ150 Leverage", "broad"),
    ("251340.KS", "KODEX KOSDAQ150 Futures Inverse", "broad"),
]

# Sector/theme ETF proxies (ranked by recent momentum)
SECTOR_PROXIES = [
    ("091160.KS", "KODEX Semiconductor", "semiconductor"),
    ("091230.KS", "TIGER Semiconductor", "semiconductor"),
    ("396500.KS", "TIGER Semiconductor TOP10", "semiconductor"),
    ("488080.KS", "TIGER Semiconductor TOP10 Leverage", "semiconductor"),
    ("494310.KS", "KODEX Semiconductor Leverage", "semiconductor"),
    ("469150.KS", "ACE AI Semiconductor TOP3+", "semiconductor"),
    ("395270.KS", "HANARO K-Semiconductor", "semiconductor"),
    ("395160.KS", "KODEX AI Semiconductor", "semiconductor"),
    ("471990.KS", "KODEX AI Semiconductor Core Equipment", "semiconductor"),
    ("381180.KS", "TIGER US Philadelphia Semiconductor Nasdaq", "global_semiconductor"),

    ("266370.KS", "KODEX IT", "it"),
    ("091170.KS", "KODEX Banks", "banks"),
    ("471460.KS", "KODEX K-Defense Industry", "defense"),
    ("139230.KS", "TIGER 200 Heavy Industries", "shipbuilding_industrials"),
    ("139220.KS", "TIGER 200 Construction", "construction"),
    ("305720.KS", "KODEX 2차전지산업", "battery"),
    ("364980.KS", "TIGER 2차전지TOP10", "battery"),
    ("364970.KS", "TIGER BioTOP10", "bio"),
    ("266420.KS", "KODEX Healthcare", "healthcare"),
    ("117680.KS", "KODEX EnergyChemicals", "energy_materials"),
]

# Bellwether single names for context (kept if valid; not ranked as ETF sectors)
BELLWETHERS = [
    ("005930.KS", "Samsung Electronics", "bellwether"),
    ("000660.KS", "SK hynix", "bellwether"),
    ("012450.KS", "Hanwha Aerospace", "bellwether"),
    ("042660.KS", "Hanwha Ocean", "bellwether"),
    ("034020.KS", "Doosan Enerbility", "bellwether"),
    ("329180.KS", "HD Hyundai Heavy Industries", "bellwether"),
    ("035420.KS", "NAVER", "bellwether"),
    ("015760.KS", "KEPCO", "bellwether"),
]

ALL = BROAD_PROXIES + SECTOR_PROXIES + BELLWETHERS


def rank_desc(series: pd.Series) -> pd.Series:
    s = pd.to_numeric(series, errors="coerce")
    n = max(len(s.dropna()), 1)
    return (1.0 - s.rank(method="average", ascending=False, pct=True) + (1.0 / n)).fillna(0.0)


def fetch_metrics(sym: str) -> tuple[bool, dict]:
    try:
        tk = yf.Ticker(sym)
        df = tk.history(period="3mo", interval="1d", auto_adjust=False)
        if df is None or df.empty or "Close" not in df.columns:
            return False, {"ticker": sym, "status": "no_data"}
        s = pd.to_numeric(df["Close"], errors="coerce").dropna()
        if len(s) < 25:
            return False, {"ticker": sym, "status": f"too_short:{len(s)}"}
        chg = float((s.iloc[-1] / s.iloc[-2] - 1.0) * 100.0) if len(s) >= 2 else None
        w = float((s.iloc[-1] / s.iloc[-6] - 1.0) * 100.0) if len(s) >= 6 else None
        m = float((s.iloc[-1] / s.iloc[-22] - 1.0) * 100.0) if len(s) >= 22 else None
        return True, {"ticker": sym, "change": chg, "perf_week": w, "perf_month": m, "status": "ok"}
    except Exception as e:
        return False, {"ticker": sym, "status": f"error:{type(e).__name__}"}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--top-sectors", type=int, default=8, help="top N sector/theme proxies to keep")
    ap.add_argument("--keep-broad", action="store_true", default=True, help="always keep broad proxies")
    ap.add_argument("--keep-bellwethers", action="store_true", default=True, help="always keep bellwether single names")
    ap.add_argument("--wt-change", type=float, default=0.5)
    ap.add_argument("--wt-perf-week", type=float, default=0.3)
    ap.add_argument("--wt-perf-month", type=float, default=0.2)
    args = ap.parse_args()

    rows = []
    total = len(ALL)
    for i, (ticker, name, bucket) in enumerate(ALL, 1):
        ok, metrics = fetch_metrics(ticker)
        metrics["name"] = name
        metrics["bucket"] = bucket
        rows.append(metrics)
        if i in (10, 20, total):
            print(f"... macro watch validation {i}/{total}")

    df = pd.DataFrame(rows)
    df.to_csv("macro_watch_yahoo_korea_debug.csv", index=False)

    valid = df[df["status"] == "ok"].copy()
    if valid.empty:
        Path("macro_watch_yahoo_korea.txt").write_text("", encoding="utf-8")
        pd.DataFrame(columns=["ticker","name","bucket","score","change","perf_week","perf_month"]).to_csv("macro_watch_yahoo_korea.csv", index=False)
        raise SystemExit("No valid Yahoo Korea macro proxies found. See macro_watch_yahoo_korea_debug.csv")

    # Rank sector/theme proxies only
    sect = valid[valid["bucket"].isin([b for _,_,b in SECTOR_PROXIES])].copy()
    if not sect.empty:
        sect["rank_change"] = rank_desc(sect["change"])
        sect["rank_perf_week"] = rank_desc(sect["perf_week"])
        sect["rank_perf_month"] = rank_desc(sect["perf_month"])
        tot = args.wt_change + args.wt_perf_week + args.wt_perf_month
        sect["score"] = (
            args.wt_change * sect["rank_change"]
            + args.wt_perf_week * sect["rank_perf_week"]
            + args.wt_perf_month * sect["rank_perf_month"]
        ) / tot
        sect = sect.sort_values(["score","change","perf_week","perf_month","ticker"],
                                ascending=[False,False,False,False,True]).reset_index(drop=True)
        top_sect = sect.head(args.top_sectors).copy()
    else:
        top_sect = pd.DataFrame(columns=list(valid.columns) + ["score"])

    broad = valid[valid["bucket"] == "broad"].copy() if args.keep_broad else pd.DataFrame(columns=valid.columns)
    bells = valid[valid["bucket"] == "bellwether"].copy() if args.keep_bellwethers else pd.DataFrame(columns=valid.columns)

    final = pd.concat([broad, top_sect, bells], ignore_index=True, sort=False)
    final = final.drop_duplicates(subset=["ticker"]).reset_index(drop=True)

    txt_lines = final["ticker"].tolist()
    Path("macro_watch_yahoo_korea.txt").write_text(
        "\n".join(txt_lines) + ("\n" if txt_lines else ""),
        encoding="utf-8"
    )

    out = final[["ticker","name","bucket"]].copy()
    if "score" in final.columns:
        out["score"] = final["score"]
    else:
        out["score"] = pd.NA
    out["change"] = final["change"]
    out["perf_week"] = final["perf_week"]
    out["perf_month"] = final["perf_month"]
    out.to_csv("macro_watch_yahoo_korea.csv", index=False)

    print(f"Saved: macro_watch_yahoo_korea.txt ({len(txt_lines)} tickers)")
    print(f"Saved: macro_watch_yahoo_korea.csv ({len(out)} rows)")
    print("Selected sector/theme proxies:")
    if not top_sect.empty:
        for i, r in top_sect.iterrows():
            print(f" - {r['ticker']} {r['name']} [{r['bucket']}] score={r['score']:.4f} chg={r['change']:.2f}% w={r['perf_week']:.2f}% m={r['perf_month']:.2f}%")
    else:
        print(" - (none)")

if __name__ == "__main__":
    main()
