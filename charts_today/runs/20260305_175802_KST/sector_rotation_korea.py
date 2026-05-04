#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
sector_rotation_korea.py (v2)

- KRX: compare latest vs previous buy_report_korea_*_KST.csv and summarize sector mix.
- US : optionally load a US buy_report_*.csv and infer sector/industry using yfinance with on-disk cache
       so "UNKNOWN" mostly disappears.

Key idea:
- We keep your KRX sector tags (SEMI/CHEM/BEAUTY/FOOD/PHARMA).
- For US, yfinance usually returns sector="Technology" and industry="Semiconductors" for semis.
  (We DO NOT auto-merge/rename semis into TECH unless you opt in later.)

Outputs:
- One block for KRX snapshot + delta
- Optional US snapshot
- Optional "industry" glimpse for US (top tickers)
- Optional alignment in a common schema (--align-gics)

Usage:
  python sector_rotation_korea.py
  python sector_rotation_korea.py --us-report buy_report_20260304_211844_KST.csv
  python sector_rotation_korea.py --us-report ... --align-gics
"""

from __future__ import annotations

import argparse
import glob
import json
import re
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import pandas as pd

# -----------------------
# KRX mapping (edit)
# -----------------------
SECTOR_OVERRIDES_KRX: Dict[str, str] = {
    "488080.KS": "SEMI",
    "471990.KS": "SEMI",
    "471760.KS": "SEMI",
    "469150.KS": "SEMI",
    "390390.KS": "SEMI",
    "051910.KS": "CHEM",
    "161890.KS": "BEAUTY",
    "097950.KS": "FOOD",
    "004370.KS": "FOOD",
    "000100.KS": "PHARMA",
    "000250.KQ": "PHARMA",
}

TXT_SOURCES_KRX = [
    "tickers_core_korea.txt",
    "tickers_leverage2x_korea.txt",
    "finviz_manual_korea.txt",
    "macro_watch_yahoo_korea.txt",
    "finviz_manul_korea.txt",  # typo-safe
]

# -----------------------
# US mapping / cache
# -----------------------
# Some ETFs / special tickers you already understand:
SECTOR_OVERRIDES_US: Dict[str, str] = {
    "XLU": "UTILITIES",
    "XLE": "ENERGY",
    "XOP": "ENERGY",
    "SMH": "SEMI",
    "SOXX": "SEMI",
    "XLK": "TECH",
    "XLV": "HEALTH",
    "XLP": "STAPLES",
    "XLY": "DISCRETIONARY",
    "XLF": "FINANCIALS",
    "XLI": "INDUSTRIALS",
    "XLB": "MATERIALS",
    "XLC": "COMM",
    "SPY": "SPY",
    # a few common "non-sector" macro tickers
    "UUP": "FX_USD",
    "IYR": "REAL_ESTATE",
}

# yfinance sector normalization (GICS-ish labels)
YF_SECTOR_MAP = {
    "Technology": "TECH",
    "Healthcare": "HEALTH",
    "Consumer Defensive": "STAPLES",
    "Consumer Cyclical": "DISCRETIONARY",
    "Energy": "ENERGY",
    "Financial Services": "FINANCIALS",
    "Industrials": "INDUSTRIALS",
    "Basic Materials": "MATERIALS",
    "Utilities": "UTILITIES",
    "Communication Services": "COMM",
    "Real Estate": "REAL_ESTATE",
}

US_CACHE_PATH = Path(".us_yf_cache.json")  # stores sector+industry


_TICKER_RE = re.compile(r"^[A-Z0-9\.\-]+$")


def _read_sector_tags_from_txt(txt_paths: List[str]) -> Dict[str, str]:
    """
    Optional tag format in txt comments:
      TICKER  # TAG | Name...
    """
    out: Dict[str, str] = {}
    for fn in txt_paths:
        p = Path(fn)
        if not p.exists():
            continue
        for line in p.read_text(encoding="utf-8", errors="ignore").splitlines():
            s = line.strip()
            if not s or s.startswith("#"):
                continue
            tag = ""
            if "#" in s:
                left, right = s.split("#", 1)
                right = right.strip()
                if "|" in right:
                    tag = right.split("|", 1)[0].strip()
                s = left.strip()
            if not s:
                continue
            t = re.split(r"[\s,;]+", s)[0].strip().upper()
            if t.startswith("A") and len(t) > 1 and t[1:].isdigit():
                t = t[1:]
            if t and tag and _TICKER_RE.match(t) and t not in out:
                out[t] = tag.upper()
    return out


def _latest_two_reports(pattern: str) -> List[Path]:
    files = sorted([Path(p) for p in glob.glob(pattern)], key=lambda p: p.stat().st_mtime, reverse=True)
    return files[:2]


def _load_report(path: Path) -> pd.DataFrame:
    df = pd.read_csv(path)
    if "ticker" not in df.columns:
        if "ticker_display" in df.columns:
            df["ticker"] = df["ticker_display"].astype(str).str.split(" ").str[0]
        elif "TickerDisplay" in df.columns:
            df["ticker"] = df["TickerDisplay"].astype(str).str.split(" ").str[0]
        else:
            raise ValueError(f"No ticker column found in {path}")
    df["ticker"] = df["ticker"].astype(str).str.strip()
    return df


def _sector_stats(df: pd.DataFrame, sector_col: str = "sector") -> pd.DataFrame:
    score_col = next((c for c in ["PRIORITY_SCORE", "priority_score", "score"] if c in df.columns), None)
    if score_col is None:
        df["_score_"] = 0.0
        score_col = "_score_"
    nb_col = next((c for c in ["near_buy", "NEAR_BUY"] if c in df.columns), None)

    g = df.groupby(sector_col, dropna=False)
    out = pd.DataFrame({"count": g.size(), "score_sum": g[score_col].sum()}).reset_index()

    if nb_col:
        nb = df[df[nb_col].astype(bool)]
        out_nb = nb.groupby(sector_col).size().reset_index(name="near_buy_count")
        out = out.merge(out_nb, on=sector_col, how="left")
    else:
        out["near_buy_count"] = 0

    out["near_buy_count"] = out["near_buy_count"].fillna(0).astype(int)
    return out.sort_values(["score_sum", "count"], ascending=False)


def _delta_table(cur: pd.DataFrame, prev: pd.DataFrame, sector_col: str = "sector") -> pd.DataFrame:
    cur2 = cur.set_index(sector_col)
    prev2 = prev.set_index(sector_col)
    all_idx = sorted(set(cur2.index) | set(prev2.index))
    rows = []
    for s in all_idx:
        c = cur2.loc[s] if s in cur2.index else pd.Series({"count": 0, "score_sum": 0.0, "near_buy_count": 0})
        p = prev2.loc[s] if s in prev2.index else pd.Series({"count": 0, "score_sum": 0.0, "near_buy_count": 0})
        rows.append({
            "sector": s,
            "count": int(c.get("count", 0)),
            "count_prev": int(p.get("count", 0)),
            "d_count": int(c.get("count", 0)) - int(p.get("count", 0)),
            "score_sum": float(c.get("score_sum", 0.0)),
            "score_prev": float(p.get("score_sum", 0.0)),
            "d_score": float(c.get("score_sum", 0.0)) - float(p.get("score_sum", 0.0)),
            "near_buy": int(c.get("near_buy_count", 0)),
            "near_buy_prev": int(p.get("near_buy_count", 0)),
            "d_near_buy": int(c.get("near_buy_count", 0)) - int(p.get("near_buy_count", 0)),
        })
    return pd.DataFrame(rows).sort_values(["d_score", "d_count"], ascending=False)


def print_block(title: str, df: pd.DataFrame, max_rows: int = 30) -> None:
    print("\n" + "=" * 72)
    print(title)
    print("=" * 72)
    if df.empty:
        print("(none)")
        return
    with pd.option_context("display.max_rows", max_rows, "display.max_columns", 60, "display.width", 200):
        print(df.to_string(index=False))


def _load_us_cache() -> Dict[str, Dict[str, str]]:
    try:
        return json.loads(US_CACHE_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save_us_cache(cache: Dict[str, Dict[str, str]]) -> None:
    try:
        US_CACHE_PATH.write_text(json.dumps(cache, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:
        pass


def infer_us_sector_industry_yf(ticker: str, cache: Dict[str, Dict[str, str]]) -> Tuple[str, str, str]:
    """
    Returns (label, yf_sector, yf_industry)
      label: normalized label used in tables (TECH/ENERGY/... or SEMI if overridden)
    """
    t = str(ticker).strip().upper()
    if not t:
        return "UNKNOWN", "", ""
    if t in SECTOR_OVERRIDES_US:
        # override label; still keep raw info from cache if exists
        raw = cache.get(t, {})
        return SECTOR_OVERRIDES_US[t], raw.get("sector", ""), raw.get("industry", "")

    if t in cache:
        ys = cache[t].get("sector", "") or ""
        yi = cache[t].get("industry", "") or ""
        label = YF_SECTOR_MAP.get(ys, ys.upper() or "UNKNOWN")
        return label, ys, yi

    # fetch
    try:
        import yfinance as yf
        info = yf.Ticker(t).info or {}
        ys = (info.get("sector") or "").strip()
        yi = (info.get("industry") or "").strip()
        cache[t] = {"sector": ys, "industry": yi}
        _save_us_cache(cache)
        label = YF_SECTOR_MAP.get(ys, ys.upper() or "UNKNOWN")
        return label, ys, yi
    except Exception:
        return "UNKNOWN", "", ""


# Optional alignment schema (KRX -> GICS-ish)
ALIGN_GICS_MAP_KRX = {
    "SEMI": "TECH",      # if you want semis merged into TECH for alignment
    "CHEM": "MATERIALS",
    "FOOD": "STAPLES",
    "BEAUTY": "STAPLES", # or DISCRETIONARY (choose your preference)
    "PHARMA": "HEALTH",
    "UNKNOWN": "UNKNOWN",
}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--topn", type=int, default=10)
    ap.add_argument("--pattern", default="buy_report_korea_*_KST.csv")
    ap.add_argument("--us-report", default=None)
    ap.add_argument("--align-gics", action="store_true", help="Map KRX sectors into a common GICS-ish schema for alignment.")
    ap.add_argument("--show-us-industry", action="store_true", help="Print US ticker -> (sector, industry) for the topn.")
    args = ap.parse_args()

    kr_files = _latest_two_reports(args.pattern)
    if not kr_files:
        raise FileNotFoundError(f"No Korea buy report found matching {args.pattern}")

    tags_kr = _read_sector_tags_from_txt(TXT_SOURCES_KRX)

    cur_path = kr_files[0]
    cur_df = _load_report(cur_path).head(args.topn)
    cur_df["sector"] = cur_df["ticker"].apply(lambda t: SECTOR_OVERRIDES_KRX.get(t, tags_kr.get(t, "UNKNOWN")))
    cur_stats = _sector_stats(cur_df, "sector")

    if len(kr_files) >= 2:
        prev_path = kr_files[1]
        prev_df = _load_report(prev_path).head(args.topn)
        prev_df["sector"] = prev_df["ticker"].apply(lambda t: SECTOR_OVERRIDES_KRX.get(t, tags_kr.get(t, "UNKNOWN")))
        prev_stats = _sector_stats(prev_df, "sector")
        delta = _delta_table(cur_stats, prev_stats, "sector")
    else:
        prev_path = None
        delta = pd.DataFrame()

    print_block(f"[KRX] Sector snapshot (TOP {args.topn}) — {cur_path.name}", cur_stats)
    if prev_path:
        print_block(f"[KRX] Sector delta vs previous — prev={prev_path.name}", delta)

    if args.us_report:
        us_cache = _load_us_cache()
        us_path = Path(args.us_report)
        us_df = _load_report(us_path).head(args.topn)
        us_df["ticker"] = us_df["ticker"].astype(str).str.upper()

        labels = []
        raw_sector = []
        raw_industry = []
        for t in us_df["ticker"].tolist():
            lbl, ys, yi = infer_us_sector_industry_yf(t, us_cache)
            labels.append(lbl)
            raw_sector.append(ys)
            raw_industry.append(yi)
        us_df["sector"] = labels
        us_df["yf_sector"] = raw_sector
        us_df["yf_industry"] = raw_industry

        us_stats = _sector_stats(us_df, "sector")
        print_block(f"[US] Sector snapshot from buy report (TOP {args.topn}) — {us_path.name}", us_stats)

        if args.show_us_industry:
            show = us_df[["ticker", "sector", "yf_sector", "yf_industry"]].copy()
            print_block("[US] Top tickers raw yfinance sector/industry (debug)", show, max_rows=args.topn)

        # Alignment
        if args.align_gics:
            kr_common = cur_df.copy()
            kr_common["common_sector"] = kr_common["sector"].map(ALIGN_GICS_MAP_KRX).fillna("UNKNOWN")
            us_common = us_df.copy()
            us_common["common_sector"] = us_common["sector"]  # already GICS-ish labels
            common = sorted(set(kr_common["common_sector"]) & set(us_common["common_sector"]))
            print("\n[ALIGN-GICS] Common sectors:", (", ".join(common) if common else "(none)"))
        else:
            common = sorted(set(cur_stats["sector"]) & set(us_stats["sector"]))
            print("\n[ALIGN] Common sectors:", (", ".join(common) if common else "(none)"))

if __name__ == "__main__":
    main()
