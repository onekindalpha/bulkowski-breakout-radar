#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
build_kr_strong_stocks_auto_v3.py

A = 강한 종목 리더용 overlay
Patched:
- use yf.Ticker(sym).history() instead of yf.download()
- works better for KR tickers in many environments
- writes debug csv if nothing is fetched
"""
from __future__ import annotations
import argparse
import re
from pathlib import Path

import pandas as pd
import yfinance as yf

SEED_FILES = [
    "kr_manual_conviction.txt",
    "kr_tactical_leverage.txt",
    "finviz_manual_korea.txt",
    "tickers_core_korea.txt",
    "tickers_leverage2x_korea.txt",
    "macro_watch_yahoo_korea.txt",
]

TICKER_RE = re.compile(r"^\d{6}\.(KS|KQ)$")


def read_seed_tickers(paths: list[str]) -> list[str]:
    out, seen = [], set()
    for path in paths:
        p = Path(path)
        if not p.exists():
            continue
        for line in p.read_text(encoding="utf-8", errors="ignore").splitlines():
            s = line.strip()
            if not s or s.startswith("#"):
                continue
            if "#" in s:
                s = s.split("#", 1)[0].strip()
            for tok in re.split(r"[\s,;]+", s):
                t = tok.strip().upper()
                if not t:
                    continue
                if re.fullmatch(r"\d{6}", t):
                    t = t + ".KS"
                if TICKER_RE.match(t) and t not in seen:
                    seen.add(t)
                    out.append(t)
    return out


def rank_desc(series: pd.Series) -> pd.Series:
    s = pd.to_numeric(series, errors="coerce")
    n = max(len(s.dropna()), 1)
    return (1.0 - s.rank(method="average", ascending=False, pct=True) + (1.0 / n)).fillna(0.0)


def fetch_history(sym: str) -> pd.DataFrame:
    tk = yf.Ticker(sym)
    df = tk.history(period="3mo", interval="1d", auto_adjust=False)
    if df is None or df.empty:
        return pd.DataFrame()
    return df


def fetch_rows(tickers: list[str]) -> tuple[pd.DataFrame, pd.DataFrame]:
    rows = []
    debug = []
    total = len(tickers)

    for i, sym in enumerate(tickers, 1):
        try:
            df = fetch_history(sym)
            if df.empty or "Close" not in df.columns:
                debug.append({"ticker": sym, "status": "no_data_or_no_close", "rows": 0})
                continue

            s = pd.to_numeric(df["Close"], errors="coerce").dropna()
            if len(s) < 25:
                debug.append({"ticker": sym, "status": "too_short", "rows": int(len(s))})
                continue

            chg = float((s.iloc[-1] / s.iloc[-2] - 1.0) * 100.0) if len(s) >= 2 else None
            w = float((s.iloc[-1] / s.iloc[-6] - 1.0) * 100.0) if len(s) >= 6 else None
            m = float((s.iloc[-1] / s.iloc[-22] - 1.0) * 100.0) if len(s) >= 22 else None
            rows.append({"ticker": sym, "change": chg, "perf_week": w, "perf_month": m})
            debug.append({"ticker": sym, "status": "ok", "rows": int(len(s))})
        except Exception as e:
            debug.append({"ticker": sym, "status": f"error:{type(e).__name__}", "rows": 0})
        if i in (20, 40, total):
            print(f"... strong stocks {i}/{total}")

    return pd.DataFrame(rows), pd.DataFrame(debug)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--top", type=int, default=20)
    ap.add_argument("--wt-change", type=float, default=0.5)
    ap.add_argument("--wt-perf-week", type=float, default=0.3)
    ap.add_argument("--wt-perf-month", type=float, default=0.2)
    args = ap.parse_args()

    tickers = read_seed_tickers(SEED_FILES)
    if not tickers:
        Path("kr_strong_stocks_auto.txt").write_text("", encoding="utf-8")
        pd.DataFrame(columns=["ticker","score","change","perf_week","perf_month"]).to_csv("kr_strong_stocks_auto.csv", index=False)
        raise SystemExit("No Korea seed tickers found for strong-stocks overlay.")

    df, dbg = fetch_rows(tickers)
    dbg.to_csv("kr_strong_stocks_auto_debug.csv", index=False)

    if df.empty:
        Path("kr_strong_stocks_auto.txt").write_text("", encoding="utf-8")
        pd.DataFrame(columns=["ticker","score","change","perf_week","perf_month"]).to_csv("kr_strong_stocks_auto.csv", index=False)
        raise SystemExit("Could not fetch any yfinance momentum rows for Korea strong-stocks overlay. See kr_strong_stocks_auto_debug.csv")

    df["rank_change"] = rank_desc(df["change"])
    df["rank_perf_week"] = rank_desc(df["perf_week"])
    df["rank_perf_month"] = rank_desc(df["perf_month"])
    tot = args.wt_change + args.wt_perf_week + args.wt_perf_month
    df["score"] = (
        args.wt_change * df["rank_change"]
        + args.wt_perf_week * df["rank_perf_week"]
        + args.wt_perf_month * df["rank_perf_month"]
    ) / tot

    df = df.sort_values(["score","change","perf_week","perf_month","ticker"],
                        ascending=[False,False,False,False,True]).reset_index(drop=True)
    sel = df.head(args.top).copy()

    Path("kr_strong_stocks_auto.txt").write_text(
        "\n".join(sel["ticker"].tolist()) + ("\n" if len(sel) else ""),
        encoding="utf-8"
    )
    sel[["ticker","score","change","perf_week","perf_month"]].to_csv("kr_strong_stocks_auto.csv", index=False)

    print(f"Saved: kr_strong_stocks_auto.txt ({len(sel)} tickers)")
    print(f"Saved: kr_strong_stocks_auto.csv ({len(sel)} rows)")
    print("Top names:")
    for i, r in sel.head(10).iterrows():
        print(f"[A] #{i+1} {r['ticker']}  score={r['score']:.4f}  chg={r['change']:.2f}%  w={r['perf_week']:.2f}%  m={r['perf_month']:.2f}%")

if __name__ == "__main__":
    main()
