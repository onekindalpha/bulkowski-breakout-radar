#!/usr/bin/env python3
from pathlib import Path
import argparse
import pandas as pd

OVERRIDES = {
    "00680K.KS": {
        "name": "미래에셋증권2우B",
        "asset_type": "PREFERRED",
        "sector": "Financial Services",
        "industry": "Securities / Preferred",
        "source": "override",
    },
}

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--path", default="bulkowski_breakout_radar/data/kr/ticker_master_korea.csv")
    args = ap.parse_args()

    p = Path(args.path)
    if not p.exists():
        raise SystemExit(f"missing: {p}")

    df = pd.read_csv(p)

    if "ticker" not in df.columns:
        raise SystemExit("ticker column missing")

    for col in ["name", "asset_type", "sector", "industry", "source"]:
        if col not in df.columns:
            df[col] = ""

    df["ticker"] = df["ticker"].astype(str).str.strip()

    for ticker, vals in OVERRIDES.items():
        mask = df["ticker"].eq(ticker)
        if mask.any():
            for k, v in vals.items():
                df.loc[mask, k] = v
        else:
            row = {"ticker": ticker, **vals}
            df = pd.concat([df, pd.DataFrame([row])], ignore_index=True)

    df.to_csv(p, index=False)
    print(f"repaired: {p}")
    print("applied overrides:", ", ".join(OVERRIDES.keys()))

if __name__ == "__main__":
    main()
