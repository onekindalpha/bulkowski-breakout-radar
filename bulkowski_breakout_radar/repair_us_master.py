#!/usr/bin/env python3
from pathlib import Path
import argparse
import pandas as pd

BAD_TOKENS = {
    "GLOBAL", "CLEAN", "ENERGY", "GRANITESHARES", "LONG",
    "CRUDE", "BRENT", "GASOLINE", "RBOB", "NASDAQ", "VIX", "SP", "S&P",
    "DIREXION", "DAILY", "SEMICONDUCTOR", "SEMICONDUCTORS",
    "PROSHARES", "ULTRA", "BENCHMARK", "MICROSECTORS",
    "ROBOTICS", "BIG", "DATA", "HEALTHCARE", "INACTIVE", "WILL",
    "AUTO-SKIP", "NO", "BIOTECH", "PHARMACEUTICAL", "MEDICAL",
    "CONSUMER", "DISCRETIONARY", "INDUSTRIALS", "AEROSPACE",
    "DEFENSE", "TRANSPORTATION", "ELECTRIC", "AUTONOMOUS", "VEHICLES",
    "SOURCE", "VIASAT", "1X", "2X", "3X", "-3X",
}

OVERRIDES = {
    "CL=F": ("WTI Crude Oil Futures", "FUTURE", "Macro", "Crude Oil"),
    "BZ=F": ("Brent Crude Oil Futures", "FUTURE", "Macro", "Crude Oil"),
    "RB=F": ("RBOB Gasoline Futures", "FUTURE", "Macro", "Gasoline"),
    "NG=F": ("Natural Gas Futures", "FUTURE", "Macro", "Natural Gas"),
    "ES=F": ("S&P 500 Futures", "FUTURE", "Macro", "US Equity Index Futures"),
    "NQ=F": ("Nasdaq 100 Futures", "FUTURE", "Macro", "US Equity Index Futures"),
    "^VIX": ("CBOE Volatility Index", "INDEX", "Macro", "Volatility"),
    "^TNX": ("US 10Y Treasury Yield Index", "INDEX", "Macro", "Rates"),
    "GC=F": ("Gold Futures", "FUTURE", "Macro", "Gold"),

    "FLEX": ("Flex Ltd.", "STOCK", "Technology", "Electronic Components"),
    "ABB": ("ABB Ltd.", "STOCK", "Industrials", "Electrical Equipment"),
    "SQ": ("Block, Inc. / legacy SQ", "STOCK", "Financial Technology", "Payments"),
    "PLL": ("Piedmont Lithium Inc.", "STOCK", "Basic Materials", "Lithium"),
    "LAAC": ("Lithium Americas (Argentina) Corp.", "STOCK", "Basic Materials", "Lithium"),
    "MRO": ("Marathon Oil Corporation / legacy", "STOCK", "Energy", "Oil & Gas E&P"),
    "SICK": ("Direxion Daily Healthcare Bear ETF", "ETF", "ETF", "Inverse Healthcare ETF"),

    "LTHM": ("Livent Corporation / legacy lithium ticker", "STOCK", "Basic Materials", "Lithium / legacy"),
    "IXF": ("iShares Global Financials ETF", "ETF", "ETF", "Global Financials ETF"),
    "IYD": ("iShares U.S. Consumer Discretionary ETF", "ETF", "ETF", "US Consumer Discretionary ETF"),
    "IYV": ("iShares U.S. Technology ETF / legacy mapping", "ETF", "ETF", "US Technology ETF"),
    "FBF": ("FBF / legacy or unresolved ticker", "STOCK", "분류 미확인", "업종 미확인"),
    "AUX": ("AUX / unresolved ticker", "STOCK", "분류 미확인", "업종 미확인"),
    "CWX": ("CWX / unresolved ticker", "STOCK", "분류 미확인", "업종 미확인"),
    "FPP": ("FPP / unresolved ticker", "STOCK", "분류 미확인", "업종 미확인"),
    "GAX": ("GAX / unresolved ticker", "STOCK", "분류 미확인", "업종 미확인"),
    "GIP": ("GIP / unresolved ticker", "STOCK", "분류 미확인", "업종 미확인"),
    "HCX": ("HCX / unresolved ticker", "STOCK", "분류 미확인", "업종 미확인"),
    "IUX": ("IUX / unresolved ticker", "STOCK", "분류 미확인", "업종 미확인"),
}

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--path", default="bulkowski_breakout_radar/data/us/ticker_master_us.csv")
    args = ap.parse_args()

    p = Path(args.path)
    if not p.exists():
        raise SystemExit(f"missing: {p}")

    df = pd.read_csv(p)
    if "ticker" not in df.columns:
        raise SystemExit("ticker column missing")

    df["ticker"] = df["ticker"].astype(str).str.strip().str.upper()
    before = len(df)

    # Drop obvious non-ticker words
    df = df[~df["ticker"].isin(BAD_TOKENS)].copy()

    for col in ["name", "asset_type", "sector", "industry", "source"]:
        if col not in df.columns:
            df[col] = ""

    for ticker, (name, asset_type, sector, industry) in OVERRIDES.items():
        mask = df["ticker"].eq(ticker)
        if mask.any():
            df.loc[mask, "name"] = name
            df.loc[mask, "asset_type"] = asset_type
            df.loc[mask, "sector"] = sector
            df.loc[mask, "industry"] = industry
            df.loc[mask, "source"] = "override"

    df = df.drop_duplicates(subset=["ticker"], keep="first").reset_index(drop=True)
    df.to_csv(p, index=False)

    unresolved = df["name"].astype(str).eq("종목명 조회 필요").sum()
    print(f"repaired {p}")
    print(f"rows: {before} -> {len(df)}")
    print(f"unresolved names: {unresolved}")

if __name__ == "__main__":
    main()
