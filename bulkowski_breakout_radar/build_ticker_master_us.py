#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Build ticker_master_us.csv for Bulkowski Breakout Radar.

Best-effort metadata builder:
1. Existing output file, if present, is reused first.
2. finviz_top_groups_members.csv supplies Company/Sector/Industry when available.
3. Built-in ETF / mega-cap seed map fills common names quickly.
4. Optional yfinance lookup fills unresolved rows.

This script intentionally degrades gracefully: if yfinance/rate limits fail, it still
writes a valid master with ticker fallback names so the dashboard can run.
"""
from __future__ import annotations
import argparse
import time
from pathlib import Path
import pandas as pd

try:
    import yfinance as yf
except Exception:
    yf = None

ETF_TICKERS = {
    "SPY","QQQ","DIA","IWM","MDY","TQQQ","SQQQ","QLD","QID","UPRO","SPXU","SPXL","SPXS","SSO","SDS",
    "SOXL","SOXS","SOXX","SMH","XSD","TECL","TECS","FNGU","FNGD","BULZ","BERZ","USD",
    "XLE","XOP","OIH","XLB","XLK","XLU","XLI","XLV","XLP","XLY","IYE","IYM","IYG","IYW","IYR","IYH","IYZ","IYV",
    "ERX","ERY","GUSH","DRIP","DIG","UCO","SCO","BOIL","KOLD","USO","BNO","UNG",
    "GLD","IAU","GLDM","SLV","SIVR","GDX","GDXJ","NUGT","DUST","UGL","GLL","AGQ","SIL","SILJ",
    "TLT","IEF","VIXY","HYG","LQD","UUP","JETS","PAVE","GRID","IBB","AIQ","BOTZ","LIT","BATT","COPX",
    "IBIT","FBTC","ARKB","BITB","HODL","BRRR","EZBC","GBTC","ETHA","FETH","ETH","EZET","ETHV","ETHW","ETHE","QETH",
    "LABU","LABD","CURE","DRN","DRV","FAS","FAZ","TMF","TMV","UBOT","AIBU","AIBD","NVDL","NVDU","NVDD",
}

BUILTIN = {
    "NVDA": ("NVIDIA Corporation", "Technology", "Semiconductors"),
    "AVGO": ("Broadcom Inc.", "Technology", "Semiconductors"),
    "AMD": ("Advanced Micro Devices", "Technology", "Semiconductors"),
    "TSM": ("Taiwan Semiconductor Manufacturing", "Technology", "Semiconductors"),
    "ASML": ("ASML Holding", "Technology", "Semiconductor Equipment"),
    "MU": ("Micron Technology", "Technology", "Memory Semiconductors"),
    "ANET": ("Arista Networks", "Technology", "Networking"),
    "VRT": ("Vertiv Holdings", "Industrials", "Power / Data Center Infrastructure"),
    "SMCI": ("Super Micro Computer", "Technology", "AI Servers"),
    "MSFT": ("Microsoft Corporation", "Technology", "Software / Cloud"),
    "AAPL": ("Apple Inc.", "Technology", "Consumer Electronics"),
    "GOOGL": ("Alphabet Inc. Class A", "Communication Services", "Internet / Search"),
    "GOOG": ("Alphabet Inc. Class C", "Communication Services", "Internet / Search"),
    "META": ("Meta Platforms", "Communication Services", "Social / AI"),
    "AMZN": ("Amazon.com", "Consumer Discretionary", "E-commerce / Cloud"),
    "TSLA": ("Tesla Inc.", "Consumer Discretionary", "EV / Energy"),
    "BRK-B": ("Berkshire Hathaway", "Financials", "Financial Conglomerate"),
    "JPM": ("JPMorgan Chase", "Financials", "Banking"),
    "V": ("Visa Inc.", "Financials", "Payments"),
    "MA": ("Mastercard", "Financials", "Payments"),
    "COST": ("Costco Wholesale", "Consumer Staples", "Retail / Wholesale"),
    "WMT": ("Walmart", "Consumer Staples", "Retail"),
    "LLY": ("Eli Lilly", "Health Care", "Pharmaceuticals"),
    "ABBV": ("AbbVie", "Health Care", "Pharmaceuticals"),
    "VST": ("Vistra Corp.", "Utilities", "Power Generation"),
    "CEG": ("Constellation Energy", "Utilities", "Nuclear / Power"),
    "GEV": ("GE Vernova", "Industrials", "Power Equipment"),
    "ETN": ("Eaton", "Industrials", "Electrical Equipment"),
    "PWR": ("Quanta Services", "Industrials", "Grid Infrastructure"),
    "SPY": ("SPDR S&P 500 ETF", "ETF", "S&P 500 ETF"),
    "QQQ": ("Invesco QQQ Trust", "ETF", "Nasdaq 100 ETF"),
    "TQQQ": ("ProShares UltraPro QQQ", "ETF", "Nasdaq 100 leveraged ETF"),
    "SQQQ": ("ProShares UltraPro Short QQQ", "ETF", "Nasdaq 100 inverse leveraged ETF"),
    "SOXL": ("Direxion Daily Semiconductor Bull 3X", "ETF", "Semiconductor leveraged ETF"),
    "SOXS": ("Direxion Daily Semiconductor Bear 3X", "ETF", "Semiconductor inverse leveraged ETF"),
    "SMH": ("VanEck Semiconductor ETF", "ETF", "Semiconductor ETF"),
    "SOXX": ("iShares Semiconductor ETF", "ETF", "Semiconductor ETF"),
    "NVDL": ("GraniteShares 2x Long NVDA Daily ETF", "ETF", "Single-stock leveraged NVDA ETF"),
    "NVDU": ("Direxion Daily NVDA Bull 2X", "ETF", "Single-stock leveraged NVDA ETF"),
    "IBIT": ("iShares Bitcoin Trust", "ETF", "Spot Bitcoin ETF"),
    "ETHA": ("iShares Ethereum Trust ETF", "ETF", "Spot Ethereum ETF"),
    "QETH": ("Ether ETF proxy", "ETF", "Ethereum ETF / proxy"),
}


def read_csv(path: Path) -> pd.DataFrame:
    if path is None or not path.exists():
        return pd.DataFrame()
    try:
        return pd.read_csv(path, comment="#")
    except Exception:
        return pd.read_csv(path, comment="#", engine="python")


def load_tickers(paths: list[Path]) -> list[str]:
    out, seen = [], set()
    for p in paths:
        if not p or not p.exists():
            continue
        if p.suffix.lower() == ".csv":
            df = read_csv(p)
            col = None
            for c in ["ticker", "Ticker", "symbol", "Symbol"]:
                if c in df.columns:
                    col = c
                    break
            if col:
                vals = df[col].dropna().astype(str).tolist()
            else:
                vals = []
        else:
            vals = []
            for line in p.read_text(encoding="utf-8", errors="ignore").splitlines():
                line = line.split("#", 1)[0].strip()
                if not line:
                    continue
                vals += re_split(line)
        for v in vals:
            t = str(v).strip().upper()
            if not t or t in {"TICKER", "SOURCE", "S&P"}:
                continue
            if t not in seen:
                seen.add(t)
                out.append(t)
    return out


def re_split(s: str) -> list[str]:
    import re
    return [x for x in re.split(r"[\s,;]+", s) if x]


def asset_type_for(ticker: str, name: str = "", sector: str = "") -> str:
    n = str(name or "").upper()
    sec = str(sector or "").upper()
    if ticker.upper() in ETF_TICKERS or "ETF" in n or "TRUST" in n and ticker.upper() in ETF_TICKERS or sec == "ETF":
        return "ETF"
    if "=F" in ticker or ticker.startswith("^"):
        return "INDEX/FUTURE"
    return "STOCK"


def finviz_map(path: Path) -> dict[str, dict]:
    df = read_csv(path)
    out = {}
    if df.empty or "Ticker" not in df.columns:
        return out
    for _, r in df.iterrows():
        t = str(r.get("Ticker", "")).strip().upper()
        if not t:
            continue
        out[t] = {
            "ticker": t,
            "name": str(r.get("Company", "") or "").strip(),
            "sector": str(r.get("Sector", "") or "").strip(),
            "industry": str(r.get("Industry", "") or "").strip(),
            "source": "finviz_members",
        }
    return out


def yf_lookup(ticker: str) -> dict | None:
    if yf is None or "=F" in ticker or ticker.startswith("^"):
        return None
    try:
        info = yf.Ticker(ticker).get_info()
        if not isinstance(info, dict) or not info:
            return None
        name = info.get("shortName") or info.get("longName") or ""
        sector = info.get("sector") or ""
        industry = info.get("industry") or ""
        quote_type = str(info.get("quoteType") or "").upper()
        if quote_type in {"ETF", "MUTUALFUND"}:
            sector = sector or "ETF"
            industry = industry or "ETF / Fund"
        if not name:
            return None
        return {"name": name, "sector": sector, "industry": industry, "source": "yfinance"}
    except Exception:
        return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--report", default="data/us/report_v2.csv")
    ap.add_argument("--tickers", default="data/us/tickers.txt")
    ap.add_argument("--premarket", default="data/us/premarket_auto.csv")
    ap.add_argument("--finviz-members", default="")
    ap.add_argument("--out", default="data/us/ticker_master_us.csv")
    ap.add_argument("--max-yf", type=int, default=250)
    ap.add_argument("--force-refresh", action="store_true")
    args = ap.parse_args()

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    existing = {}
    if out_path.exists() and not args.force_refresh:
        old = read_csv(out_path)
        if not old.empty and "ticker" in old.columns:
            for _, r in old.iterrows():
                existing[str(r["ticker"]).strip().upper()] = r.to_dict()

    tickers = load_tickers([Path(args.report), Path(args.tickers), Path(args.premarket)])
    fmap = finviz_map(Path(args.finviz_members)) if args.finviz_members else {}

    rows = []
    yf_count = 0
    unresolved = []
    for i, t in enumerate(tickers, 1):
        row = None
        if t in existing and str(existing[t].get("name", "")).strip() and str(existing[t].get("name", "")).strip().upper() != t:
            row = existing[t]
            row["source"] = row.get("source", "existing")
        elif t in fmap:
            row = fmap[t]
        elif t in BUILTIN:
            n, s, ind = BUILTIN[t]
            row = {"ticker": t, "name": n, "sector": s, "industry": ind, "source": "builtin"}
        elif yf_count < args.max_yf:
            got = yf_lookup(t)
            yf_count += 1
            time.sleep(0.03)
            if got:
                row = {"ticker": t, **got}
        if row is None:
            row = {"ticker": t, "name": t, "sector": "Unmapped", "industry": "Unmapped", "source": "fallback"}
            unresolved.append(t)
        name = str(row.get("name", "") or t).strip()
        sector = str(row.get("sector", "") or "Unmapped").strip()
        industry = str(row.get("industry", "") or "Unmapped").strip()
        asset = str(row.get("asset_type", "") or asset_type_for(t, name, sector)).strip()
        rows.append({"ticker": t, "name": name, "asset_type": asset, "sector": sector, "industry": industry, "source": row.get("source", "")})
        if i == 1 or i % 50 == 0 or i == len(tickers):
            print(f"... us metadata {i}/{len(tickers)} {t} -> {name}", flush=True)

    df = pd.DataFrame(rows).drop_duplicates("ticker", keep="last")
    df.to_csv(out_path, index=False)
    print(f"Saved: {out_path} ({len(df)} rows)")
    print(f"yfinance lookups: {yf_count}")
    print(f"unresolved names: {len(unresolved)}")
    if unresolved[:30]:
        print("unresolved sample:", ", ".join(unresolved[:30]))

if __name__ == "__main__":
    main()
