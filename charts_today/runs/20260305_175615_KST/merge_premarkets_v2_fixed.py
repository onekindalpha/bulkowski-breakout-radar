#!/usr/bin/env python3
"""
merge_premarkets_v2_fixed.py

Fixes KeyError when manual contains tickers not present in auto.

Inputs:
- premarket_auto.csv (from update_premarket_yf_auto_fast_*.py)  [optional]
- premarket_manual.csv (from make_premarket_manual_5.py)        [optional]

Output:
- premarket.csv (manual overrides auto; manual-only tickers kept)

This script is drop-in compatible with merge_premarkets_v2.py name.
"""
from pathlib import Path
import pandas as pd

AUTO = Path("premarket_auto.csv")
MANUAL = Path("premarket_manual.csv")
OUT = Path("premarket.csv")

def _load(path: Path) -> pd.DataFrame:
    if not path.exists():
        return pd.DataFrame()
    df = pd.read_csv(path)
    # normalize columns
    cols = {c.lower().strip(): c for c in df.columns}
    # find ticker column
    ticker_col = None
    for k in ["ticker","symbol"]:
        if k in cols:
            ticker_col = cols[k]
            break
    if ticker_col is None:
        raise ValueError(f"{path} missing ticker/symbol column. cols={list(df.columns)}")
    df = df.rename(columns={ticker_col:"ticker"})
    df["ticker"] = df["ticker"].astype(str).str.upper().str.strip()
    # premarket price column
    pm_col = None
    for k in ["premarket","price","last","pm"]:
        if k in cols:
            pm_col = cols[k]
            break
    if pm_col is not None and pm_col != "premarket":
        df = df.rename(columns={pm_col:"premarket"})
    if "premarket" not in df.columns:
        # create placeholder if missing
        df["premarket"] = pd.NA
    return df

def main():
    a = _load(AUTO)
    m = _load(MANUAL)

    if a.empty and m.empty:
        OUT.write_text("ticker,premarket\n", encoding="utf-8")
        print("Saved premarket.csv (0 tickers).")
        return

    # Keep essential columns first, but preserve anything else if present.
    # We'll combine and keep the last occurrence per ticker (manual should come last).
    a["source"] = "auto"
    m["source"] = "manual"

    combined = pd.concat([a, m], ignore_index=True, sort=False)
    combined = combined.dropna(subset=["ticker"])
    combined["ticker"] = combined["ticker"].astype(str).str.upper().str.strip()

    # Drop duplicate tickers, keeping last (manual override)
    combined = combined.sort_values(by=["source"]).drop_duplicates(subset=["ticker"], keep="last")

    # Output with ticker first
    cols = ["ticker","premarket","source"] + [c for c in combined.columns if c not in ("ticker","premarket","source")]
    combined[cols].to_csv(OUT, index=False)

    print(f"Saved premarket.csv ({len(combined)} tickers).")
    if not m.empty:
        print(f"manual rows: {len(m)} (overrides applied)")
    if not a.empty:
        print(f"auto rows: {len(a)}")

if __name__ == "__main__":
    main()
