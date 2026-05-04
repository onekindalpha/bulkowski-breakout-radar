#!/usr/bin/env python3
"""
merge_premarkets_v2_ultra.py

Ultra-robust merge for premarket_auto.csv + premarket_manual.csv.

Fixes:
- pandas ParserError when premarket_manual.csv contains mixed schemas / extra commas / old headers.
- KeyError when manual contains tickers not present in auto.

Rules:
- Manual overrides auto when both exist.
- Manual-only tickers are kept.
- Blank/NA premarket values are ignored (do not overwrite a valid value).

Input files (same names as before):
- premarket_auto.csv   (optional)
- premarket_manual.csv (optional)

Output:
- premarket.csv
"""

from __future__ import annotations
from pathlib import Path
import csv
import pandas as pd

AUTO = Path("premarket_auto.csv")
MANUAL = Path("premarket_manual.csv")
OUT = Path("premarket.csv")

def _parse_csv_loose(path: Path) -> pd.DataFrame:
    """Loose CSV parser that tolerates extra columns and bad lines."""
    if not path.exists():
        return pd.DataFrame(columns=["ticker", "premarket"])
    rows = []
    with path.open("r", encoding="utf-8", errors="ignore", newline="") as f:
        reader = csv.reader(f)
        for parts in reader:
            if not parts:
                continue
            raw0 = (parts[0] or "").strip()
            if not raw0:
                continue
            if raw0.startswith("#"):
                continue
            h = raw0.lower()
            if h in ("ticker", "symbol"):
                continue
            ticker = raw0.upper().strip()
            pm = None
            if len(parts) >= 2:
                pm_raw = (parts[1] or "").strip()
                if pm_raw:
                    try:
                        pm = float(pm_raw)
                    except Exception:
                        pm = None
            if ticker:
                rows.append({"ticker": ticker, "premarket": pm})

    df = pd.DataFrame(rows)
    if df.empty:
        return pd.DataFrame(columns=["ticker", "premarket"])
    df = df.dropna(subset=["ticker"])
    df["ticker"] = df["ticker"].astype(str).str.upper().str.strip()
    df = df.drop_duplicates(subset=["ticker"], keep="last")
    return df[["ticker", "premarket"]]

def _parse_auto(path: Path) -> pd.DataFrame:
    if not path.exists():
        return pd.DataFrame(columns=["ticker", "premarket"])
    try:
        df = pd.read_csv(path)
    except Exception:
        return _parse_csv_loose(path)

    cols = {c.lower().strip(): c for c in df.columns}
    ticker_col = cols.get("ticker") or cols.get("symbol")
    if not ticker_col:
        return _parse_csv_loose(path)
    pm_col = cols.get("premarket") or cols.get("price") or cols.get("last") or cols.get("pm")

    df = df.rename(columns={ticker_col: "ticker"})
    if pm_col and pm_col != "premarket":
        df = df.rename(columns={pm_col: "premarket"})
    if "premarket" not in df.columns:
        df["premarket"] = pd.NA

    df["ticker"] = df["ticker"].astype(str).str.upper().str.strip()
    df = df.drop_duplicates(subset=["ticker"], keep="last")
    return df[["ticker", "premarket"]]

def main():
    a = _parse_auto(AUTO)
    m = _parse_csv_loose(MANUAL)

    if a.empty and m.empty:
        OUT.write_text("ticker,premarket,source\n", encoding="utf-8")
        print("Saved premarket.csv (0 tickers).")
        return

    a["source"] = "auto"
    m["source"] = "manual"

    combined = pd.concat([a, m], ignore_index=True)
    combined = combined.dropna(subset=["ticker"])
    combined["ticker"] = combined["ticker"].astype(str).str.upper().str.strip()

    # manual should be preferred if it has a numeric price
    combined["source_rank"] = combined["source"].map({"auto": 0, "manual": 1}).fillna(0).astype(int)
    combined = combined.sort_values(["ticker", "source_rank"], ascending=[True, True])

    def pick(g: pd.DataFrame) -> pd.Series:
        g2 = g.dropna(subset=["premarket"])
        if not g2.empty:
            row = g2.iloc[-1]
            return pd.Series({"ticker": row["ticker"], "premarket": row["premarket"], "source": row["source"]})
        row = g.iloc[-1]
        return pd.Series({"ticker": row["ticker"], "premarket": row["premarket"], "source": row["source"]})

    out = combined.groupby("ticker", as_index=False).apply(pick).reset_index(drop=True)
    out.to_csv(OUT, index=False)

    print(f"Saved premarket.csv ({len(out)} tickers).")
    print(f"manual tickers parsed: {len(m)} | auto tickers parsed: {len(a)}")

if __name__ == "__main__":
    main()
