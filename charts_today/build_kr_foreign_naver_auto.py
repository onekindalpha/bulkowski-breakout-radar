#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
build_kr_foreign_naver_auto.py

Daily auto-builder for Korea foreign net-buy overlay from Naver Finance.

Outputs:
- kr_foreign_naver_auto.txt
- kr_foreign_naver_auto.csv

What it does:
1) Downloads Naver Finance foreign net-buy ranking page
2) Tries to extract the latest visible ranking block
3) Maps names to KRX tickers from Naver item links when possible
4) Falls back to pykrx name mapping if needed
5) Writes top N names as Yahoo/KRX symbols (.KS/.KQ)

Notes:
- Intended to run DAILY after/near market close
- HTML structure on Naver can change, so a debug HTML/CSV is also written
- If the page changes, check:
    foreign_naver_raw.html
    foreign_naver_debug.csv
"""
from __future__ import annotations

import argparse
import re
from datetime import date, timedelta
from pathlib import Path

import pandas as pd
import requests
from bs4 import BeautifulSoup

try:
    from pykrx import stock
except Exception:
    stock = None

URL = "https://finance.naver.com/sise/sise_deal_rank.naver"

OUT_TXT = Path("kr_foreign_naver_auto.txt")
OUT_CSV = Path("kr_foreign_naver_auto.csv")
RAW_HTML = Path("foreign_naver_raw.html")
DBG_CSV = Path("foreign_naver_debug.csv")

HEADERS = {
    "User-Agent": "Mozilla/5.0",
    "Referer": "https://finance.naver.com/",
}


def ymd(d: date) -> str:
    return d.strftime("%Y%m%d")


def recent_dates(n: int = 40) -> list[str]:
    today = date.today()
    return [ymd(today - timedelta(days=i)) for i in range(n)]


def fetch_html() -> str:
    r = requests.get(URL, headers=HEADERS, timeout=20)
    r.raise_for_status()
    # Naver Korean pages are often EUC-KR / cp949
    if not r.encoding or r.encoding.lower() == "iso-8859-1":
        r.encoding = r.apparent_encoding or "cp949"
    return r.text


def _clean_text(x) -> str:
    s = str(x).strip()
    s = re.sub(r"\s+", " ", s)
    return s


def _try_anchor_code_map(html: str) -> dict[str, str]:
    """
    Build {company_name: 6digit_code} from item links in page HTML.
    """
    soup = BeautifulSoup(html, "html.parser")
    out = {}
    for a in soup.select("a[href*='item/main.naver?code=']"):
        href = a.get("href", "")
        m = re.search(r"code=(\d{6})", href)
        name = _clean_text(a.get_text())
        if m and name and name not in out:
            out[name] = m.group(1)
    return out


def _flatten_columns(df: pd.DataFrame) -> list[str]:
    cols = []
    for c in df.columns:
        if isinstance(c, tuple):
            parts = [str(x).strip() for x in c if str(x).strip() and "Unnamed" not in str(x)]
            cols.append(" | ".join(parts))
        else:
            cols.append(str(c).strip())
    return cols


def _pick_latest_table(html: str) -> pd.DataFrame:
    tables = pd.read_html(html)
    best = None
    best_score = -1

    for t in tables:
        df = t.copy()
        df.columns = _flatten_columns(df)
        cols = df.columns.tolist()
        joined = " || ".join(cols)

        # Score tables that look like foreign ranking blocks
        score = 0
        if "종목명" in joined:
            score += 4
        if "금액" in joined:
            score += 3
        if "수량" in joined:
            score += 1
        if any(re.search(r"\d{2}\.\d{2}\.\d{2}", c) for c in cols):
            score += 3
        if len(df) >= 5:
            score += 1

        if score > best_score:
            best_score = score
            best = df

    if best is None:
        raise RuntimeError("Could not find a usable Naver ranking table from HTML.")

    return best


def _extract_latest_block(df: pd.DataFrame) -> pd.DataFrame:
    """
    Handle both:
    - multi-date style columns like '26.03.17 | 종목명', '26.03.17 | 금액'
    - simple flat columns with 종목명 / 금액
    """
    cols = df.columns.tolist()

    date_pat = re.compile(r"(\d{2}\.\d{2}\.\d{2})")
    date_groups = {}
    for c in cols:
        m = date_pat.search(c)
        if m:
            date_groups.setdefault(m.group(1), []).append(c)

    # If date-grouped, pick the latest date-like group
    if date_groups:
        latest = sorted(date_groups.keys())[-1]
        use_cols = date_groups[latest]

        name_col = next((c for c in use_cols if "종목명" in c), None)
        amt_col = next((c for c in use_cols if "금액" in c), None)
        qty_col = next((c for c in use_cols if "수량" in c), None)

        if not name_col:
            raise RuntimeError(f"Latest grouped table detected ({latest}) but 종목명 column not found.")
        out = pd.DataFrame({
            "name": df[name_col],
            "amount": df[amt_col] if amt_col else None,
            "qty": df[qty_col] if qty_col else None,
            "source_date": latest,
        })
        return out

    # Otherwise flat table
    name_col = next((c for c in cols if "종목명" in c), None)
    amt_col = next((c for c in cols if "금액" in c), None)
    qty_col = next((c for c in cols if "수량" in c), None)

    if not name_col:
        raise RuntimeError("Flat ranking table found but 종목명 column not found.")

    out = pd.DataFrame({
        "name": df[name_col],
        "amount": df[amt_col] if amt_col else None,
        "qty": df[qty_col] if qty_col else None,
        "source_date": "",
    })
    return out


def _normalize_ranking(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    out["name"] = out["name"].map(_clean_text)
    out = out[out["name"].astype(str).str.len() > 0].copy()
    out = out[~out["name"].str.contains("순매수|영역|코스피|코스닥|종목명", na=False)].copy()

    if "amount" in out.columns:
        out["amount"] = (
            out["amount"].astype(str)
            .str.replace(",", "", regex=False)
            .str.replace(r"[^\d\.\-]", "", regex=True)
        )
        out["amount"] = pd.to_numeric(out["amount"], errors="coerce")

    if "qty" in out.columns:
        out["qty"] = (
            out["qty"].astype(str)
            .str.replace(",", "", regex=False)
            .str.replace(r"[^\d\.\-]", "", regex=True)
        )
        out["qty"] = pd.to_numeric(out["qty"], errors="coerce")

    out = out.drop_duplicates(subset=["name"]).reset_index(drop=True)
    return out


def _find_working_krx_date() -> str:
    if stock is None:
        return ""
    for ds in recent_dates(40):
        try:
            ks = stock.get_market_ticker_list(ds, market="KOSPI")
            kq = stock.get_market_ticker_list(ds, market="KOSDAQ")
            if ks or kq:
                return ds
        except Exception:
            pass
    return ""


def _pykrx_name_map(ds: str) -> dict[str, str]:
    out = {}
    if stock is None or not ds:
        return out
    for market, suffix in [("KOSPI", ".KS"), ("KOSDAQ", ".KQ")]:
        try:
            ticks = stock.get_market_ticker_list(ds, market=market)
        except Exception:
            ticks = []
        for t in ticks:
            try:
                nm = stock.get_market_ticker_name(t)
            except Exception:
                continue
            if nm and nm not in out:
                out[str(nm).strip()] = f"{str(t).zfill(6)}{suffix}"
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--top", type=int, default=20, help="top N foreign-net-buy names to export")
    args = ap.parse_args()

    html = fetch_html()
    RAW_HTML.write_text(html, encoding="utf-8")

    anchor_map = _try_anchor_code_map(html)

    table = _pick_latest_table(html)
    latest = _extract_latest_block(table)
    latest = _normalize_ranking(latest)

    ds = _find_working_krx_date()
    pykrx_map = _pykrx_name_map(ds)

    rows = []
    txt = []
    seen = set()

    for _, r in latest.iterrows():
        name = r["name"]
        sym = ""

        if name in anchor_map:
            code = anchor_map[name]
            # decide suffix using pykrx if available
            if name in pykrx_map:
                sym = pykrx_map[name]
            else:
                # if pykrx name map unavailable, guess KOSPI first
                sym = code + ".KS"

        elif name in pykrx_map:
            sym = pykrx_map[name]

        if not sym:
            rows.append({
                "name": name,
                "ticker": "",
                "amount": r.get("amount"),
                "qty": r.get("qty"),
                "source_date": r.get("source_date", ""),
                "status": "unmapped",
            })
            continue

        if sym in seen:
            continue
        seen.add(sym)
        txt.append(sym)
        rows.append({
            "name": name,
            "ticker": sym,
            "amount": r.get("amount"),
            "qty": r.get("qty"),
            "source_date": r.get("source_date", ""),
            "status": "ok",
        })
        if len(txt) >= args.top:
            break

    pd.DataFrame(rows).to_csv(OUT_CSV, index=False)
    OUT_TXT.write_text("\n".join(txt) + ("\n" if txt else ""), encoding="utf-8")

    print(f"Saved: {OUT_TXT.name} ({len(txt)} tickers)")
    print(f"Saved: {OUT_CSV.name} ({len(rows)} rows)")
    if rows:
        ok = [x for x in rows if x["status"] == "ok"]
        print("Top mapped names:")
        for x in ok[:10]:
            print(f" - {x['ticker']}  {x['name']}  amount={x['amount']}")

if __name__ == "__main__":
    main()
