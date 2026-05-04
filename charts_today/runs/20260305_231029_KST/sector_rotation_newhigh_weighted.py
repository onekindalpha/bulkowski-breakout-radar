#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
sector_rotation_newhigh_weighted.py

Purpose
- You said: "우리가 돌린 건 new high(=breakout 후보)고, 그 'new high 후보'의 업종별 비중을
  거래대금(유동성) 기준 %로 보고 싶다. (한국은 오전, 미국은 전일 오후)"

This script does exactly that from your pipeline outputs:
- KRX: latest (and previous) buy_report_korea_*_KST.csv
- US : a specified buy_report_*_KST.csv (or any csv with a 'ticker' column)

For each market, it computes ADV20 (20-day average traded value):
  ADV20 = mean(Close * Volume) over last 20 daily bars (yfinance)

Then outputs sector mix:
  - count
  - adv20_sum
  - adv20_pct (sector share by ADV20)
Optionally:
  - split SEMI out of TECH for US using yfinance industry ("Semiconductor" substring)

Notes
- This is NOT "전체 시장 업종 비중".
  It's "우리 파이프라인이 뽑은 (new high/breakout 후보) TOP N"의 비중.
- KRX/US는 세션이 다르므로 '동시' 비교가 아니라 '선행/후행' 관점으로 보세요.

Usage
  # Korea only (TOP10)
  python sector_rotation_newhigh_weighted.py

  # Korea TOP20 + US TOP10 + split semis
  python sector_rotation_newhigh_weighted.py --kr-topn 20 --us-report buy_report_20260304_211844_KST.csv --us-topn 10 --split-semi

  # Align KRX fine tags to GICS-ish labels for easier comparison
  python sector_rotation_newhigh_weighted.py --us-report buy_report_20260304_211844_KST.csv --align-gics --split-semi
"""

from __future__ import annotations

import argparse
import glob
import json
import re
from pathlib import Path
from typing import Dict, List, Tuple, Optional

import pandas as pd


# -----------------------
# 1) KRX sector mapping (edit freely)
# -----------------------
# Primary override mapping. If ticker not present, we can read optional tag from *_korea.txt comments.
SECTOR_OVERRIDES_KRX: Dict[str, str] = {
    # Semis / AI semis ETFs (examples; add more as you want)
    "488080.KS": "SEMI",
    "471990.KS": "SEMI",
    "471760.KS": "SEMI",
    "469150.KS": "SEMI",
    "390390.KS": "SEMI",
    # Chemicals / materials
    "051910.KS": "CHEM",
    # Beauty / cosmetics
    "161890.KS": "BEAUTY",
    # Food / staples
    "097950.KS": "FOOD",
    "004370.KS": "FOOD",
    # Pharma / healthcare
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

# Optional alignment into GICS-ish labels (so KRX/US can share a schema)
ALIGN_GICS_MAP_KRX = {
    "SEMI": "TECH",      # or keep as "SEMI" if you prefer
    "CHEM": "MATERIALS",
    "FOOD": "STAPLES",
    "BEAUTY": "STAPLES",  # could also be DISCRETIONARY
    "PHARMA": "HEALTH",
    "UNKNOWN": "UNKNOWN",
}

# -----------------------
# 2) US mapping + yfinance normalization
# -----------------------
SECTOR_OVERRIDES_US: Dict[str, str] = {
    # ETFs / macro
    "XLU": "UTILITIES",
    "XLE": "ENERGY",
    "XOP": "ENERGY",
    "SPY": "SPY",
    "UUP": "FX_USD",
    "IYR": "REAL_ESTATE",
    # Semis ETFs (if you want them separated regardless of yfinance)
    "SMH": "SEMI",
    "SOXX": "SEMI",
}

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

_TICKER_RE = re.compile(r"^[A-Z0-9\.\-]+$")


# -----------------------
# 3) Caches (ADV + US sector/industry)
# -----------------------
ADV_CACHE_KRX = Path(".adv_cache_korea.json")
ADV_CACHE_US = Path(".adv_cache_us.json")
US_YF_CACHE = Path(".us_yf_cache.json")  # sector+industry cache


def _load_json(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save_json(path: Path, obj: dict) -> None:
    try:
        path.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:
        pass


def _read_sector_tags_from_txt(txt_paths: List[str]) -> Dict[str, str]:
    """
    Optional tag format:
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
    # Normalize ticker column
    if "ticker" not in df.columns:
        if "ticker_display" in df.columns:
            df["ticker"] = df["ticker_display"].astype(str).str.split(" ").str[0]
        elif "TickerDisplay" in df.columns:
            df["ticker"] = df["TickerDisplay"].astype(str).str.split(" ").str[0]
        else:
            raise ValueError(f"No ticker column found in {path}")
    df["ticker"] = df["ticker"].astype(str).str.strip()
    return df


def _infer_krx_sector(t: str, overrides: Dict[str, str], tags: Dict[str, str]) -> str:
    t = str(t).strip().upper()
    if t in overrides:
        return overrides[t]
    if t in tags:
        return tags[t]
    return "UNKNOWN"


def _infer_us_sector_industry(t: str, us_cache: dict, split_semi: bool) -> Tuple[str, str, str]:
    """
    Returns (label, yf_sector, yf_industry).
    label is normalized.
    """
    t = str(t).strip().upper()
    if not t:
        return "UNKNOWN", "", ""
    if t in SECTOR_OVERRIDES_US:
        raw = us_cache.get(t, {})
        return SECTOR_OVERRIDES_US[t], raw.get("sector", ""), raw.get("industry", "")

    if t in us_cache:
        ys = us_cache[t].get("sector", "") or ""
        yi = us_cache[t].get("industry", "") or ""
    else:
        try:
            import yfinance as yf
            info = yf.Ticker(t).info or {}
            ys = (info.get("sector") or "").strip()
            yi = (info.get("industry") or "").strip()
        except Exception:
            ys, yi = "", ""
        us_cache[t] = {"sector": ys, "industry": yi}
        _save_json(US_YF_CACHE, us_cache)

    # normalize label
    if split_semi and ys == "Technology" and "Semiconductor" in yi:
        return "SEMI", ys, yi
    label = YF_SECTOR_MAP.get(ys, ys.upper() or "UNKNOWN")
    return label, ys, yi


def _adv20(ticker: str, cache: dict, cache_path: Path, lookback_days: str = "90d") -> Tuple[float, str]:
    """
    Returns (adv20, last_date_string).
    Caches per ticker.
    """
    t = str(ticker).strip().upper()
    if not t:
        return 0.0, ""
    if t in cache and "adv20" in cache[t] and "last_date" in cache[t]:
        return float(cache[t]["adv20"]), str(cache[t]["last_date"])

    try:
        import yfinance as yf
        df = yf.download(t, period=lookback_days, interval="1d", auto_adjust=False, progress=False)
        if df is None or df.empty:
            cache[t] = {"adv20": 0.0, "last_date": ""}
            _save_json(cache_path, cache)
            return 0.0, ""
        # ensure columns
        if "Close" not in df.columns or "Volume" not in df.columns:
            cache[t] = {"adv20": 0.0, "last_date": str(df.index[-1].date())}
            _save_json(cache_path, cache)
            return 0.0, str(df.index[-1].date())

        dv = (df["Close"].astype(float) * df["Volume"].astype(float)).dropna()
        if dv.empty:
            cache[t] = {"adv20": 0.0, "last_date": str(df.index[-1].date())}
            _save_json(cache_path, cache)
            return 0.0, str(df.index[-1].date())
        adv = float(dv.tail(20).mean()) if len(dv) >= 1 else 0.0
        last_date = str(dv.index[-1].date())
        cache[t] = {"adv20": adv, "last_date": last_date}
        _save_json(cache_path, cache)
        return adv, last_date
    except Exception:
        cache[t] = {"adv20": 0.0, "last_date": ""}
        _save_json(cache_path, cache)
        return 0.0, ""


def _weighted_sector_table(df: pd.DataFrame, sector_col: str, adv_col: str = "adv20") -> pd.DataFrame:
    g = df.groupby(sector_col, dropna=False)
    out = pd.DataFrame({
        "count": g.size(),
        "adv20_sum": g[adv_col].sum(),
    }).reset_index().rename(columns={sector_col: "sector"})
    total = float(out["adv20_sum"].sum()) or 1.0
    out["adv20_pct"] = out["adv20_sum"] / total * 100.0
    out = out.sort_values(["adv20_pct", "adv20_sum"], ascending=False)
    return out


def _delta_pct(cur: pd.DataFrame, prev: pd.DataFrame) -> pd.DataFrame:
    cur2 = cur.set_index("sector")
    prev2 = prev.set_index("sector")
    all_idx = sorted(set(cur2.index) | set(prev2.index))
    rows = []
    for s in all_idx:
        c = cur2.loc[s] if s in cur2.index else pd.Series({"count": 0, "adv20_sum": 0.0, "adv20_pct": 0.0})
        p = prev2.loc[s] if s in prev2.index else pd.Series({"count": 0, "adv20_sum": 0.0, "adv20_pct": 0.0})
        rows.append({
            "sector": s,
            "adv20_pct": float(c.get("adv20_pct", 0.0)),
            "adv20_pct_prev": float(p.get("adv20_pct", 0.0)),
            "d_adv20_pct": float(c.get("adv20_pct", 0.0)) - float(p.get("adv20_pct", 0.0)),
            "count": int(c.get("count", 0)),
            "count_prev": int(p.get("count", 0)),
            "d_count": int(c.get("count", 0)) - int(p.get("count", 0)),
        })
    out = pd.DataFrame(rows).sort_values(["d_adv20_pct", "d_count"], ascending=False)
    return out


def print_block(title: str, df: pd.DataFrame, max_rows: int = 30) -> None:
    print("\n" + "=" * 88)
    print(title)
    print("=" * 88)
    if df.empty:
        print("(none)")
        return
    with pd.option_context("display.max_rows", max_rows, "display.max_columns", 60, "display.width", 220):
        print(df.to_string(index=False))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--kr-pattern", default="buy_report_korea_*_KST.csv")
    ap.add_argument("--kr-topn", type=int, default=10)
    ap.add_argument("--us-report", default=None)
    ap.add_argument("--us-topn", type=int, default=10)
    ap.add_argument("--split-semi", action="store_true", help="US: if yfinance sector=Technology and industry contains 'Semiconductor', label as SEMI.")
    ap.add_argument("--align-gics", action="store_true", help="Map KRX fine sectors to GICS-ish labels before printing alignment.")
    ap.add_argument("--show-us-industry", action="store_true", help="Print US top tickers with raw (sector, industry, adv20).")
    args = ap.parse_args()

    # ---- KRX ----
    kr_files = _latest_two_reports(args.kr_pattern)
    if not kr_files:
        raise FileNotFoundError(f"No KRX buy report found matching {args.kr_pattern}")

    tags_kr = _read_sector_tags_from_txt(TXT_SOURCES_KRX)
    adv_cache_kr = _load_json(ADV_CACHE_KRX)

    cur_kr = _load_report(kr_files[0]).head(args.kr_topn).copy()
    cur_kr["sector_raw"] = cur_kr["ticker"].apply(lambda t: _infer_krx_sector(t, SECTOR_OVERRIDES_KRX, tags_kr))
    cur_kr["sector"] = cur_kr["sector_raw"].map(ALIGN_GICS_MAP_KRX).fillna(cur_kr["sector_raw"]) if args.align_gics else cur_kr["sector_raw"]

    advs = []
    last_dates = []
    for t in cur_kr["ticker"].tolist():
        adv, ld = _adv20(t, adv_cache_kr, ADV_CACHE_KRX)
        advs.append(adv)
        last_dates.append(ld)
    cur_kr["adv20"] = advs
    cur_kr["adv_last_date"] = last_dates

    kr_table = _weighted_sector_table(cur_kr, "sector", "adv20")
    print_block(f"[KRX] NewHigh/Breakout candidates (TOP {args.kr_topn}) — {kr_files[0].name}", kr_table)

    # delta vs previous KRX report (if exists)
    if len(kr_files) >= 2:
        prev_kr = _load_report(kr_files[1]).head(args.kr_topn).copy()
        prev_kr["sector_raw"] = prev_kr["ticker"].apply(lambda t: _infer_krx_sector(t, SECTOR_OVERRIDES_KRX, tags_kr))
        prev_kr["sector"] = prev_kr["sector_raw"].map(ALIGN_GICS_MAP_KRX).fillna(prev_kr["sector_raw"]) if args.align_gics else prev_kr["sector_raw"]

        adv_cache_kr = _load_json(ADV_CACHE_KRX)  # refresh from disk
        advs2 = []
        for t in prev_kr["ticker"].tolist():
            adv, _ = _adv20(t, adv_cache_kr, ADV_CACHE_KRX)
            advs2.append(adv)
        prev_kr["adv20"] = advs2
        prev_table = _weighted_sector_table(prev_kr, "sector", "adv20")

        delta = _delta_pct(kr_table, prev_table)
        print_block(f"[KRX] ADV20-weighted sector delta vs previous (TOP {args.kr_topn}) — prev={kr_files[1].name}", delta)

    # ---- US ----
    if args.us_report:
        us_path = Path(args.us_report)
        if not us_path.exists():
            raise FileNotFoundError(f"US report not found: {us_path}")

        us_cache = _load_json(US_YF_CACHE)
        adv_cache_us = _load_json(ADV_CACHE_US)

        us_df = _load_report(us_path).head(args.us_topn).copy()
        us_df["ticker"] = us_df["ticker"].astype(str).str.upper()

        labels, ys_list, yi_list, advs_u, last_u = [], [], [], [], []
        for t in us_df["ticker"].tolist():
            lbl, ys, yi = _infer_us_sector_industry(t, us_cache, args.split_semi)
            labels.append(lbl)
            ys_list.append(ys)
            yi_list.append(yi)
            adv, ld = _adv20(t, adv_cache_us, ADV_CACHE_US)
            advs_u.append(adv)
            last_u.append(ld)

        us_df["sector_raw"] = labels
        us_df["sector"] = us_df["sector_raw"]  # already GICS-ish
        us_df["yf_sector"] = ys_list
        us_df["yf_industry"] = yi_list
        us_df["adv20"] = advs_u
        us_df["adv_last_date"] = last_u

        us_table = _weighted_sector_table(us_df, "sector", "adv20")
        print_block(f"[US] NewHigh/Breakout candidates (TOP {args.us_topn}) — {us_path.name}", us_table)

        if args.show_us_industry:
            show_cols = ["ticker", "sector", "yf_sector", "yf_industry", "adv20", "adv_last_date"]
            print_block("[US] Top tickers raw yfinance sector/industry + ADV20 (debug)", us_df[show_cols].copy(), max_rows=args.us_topn)

        # simple alignment list
        common = sorted(set(kr_table["sector"]) & set(us_table["sector"]))
        print("\n[ALIGN] Common sector labels:", (", ".join(common) if common else "(none)"))

if __name__ == "__main__":
    main()
