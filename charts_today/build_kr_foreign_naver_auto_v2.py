#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
build_kr_foreign_naver_auto_v2.py

More robust daily builder for Korea foreign net-buy overlay from Naver Finance.

Outputs:
- kr_foreign_naver_auto.txt
- kr_foreign_naver_auto.csv
- foreign_naver_raw.html
- foreign_naver_debug.csv
"""
from __future__ import annotations

import argparse
import io
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
    if not r.encoding or r.encoding.lower() == "iso-8859-1":
        r.encoding = r.apparent_encoding or "cp949"
    return r.text

def _clean_text(x) -> str:
    s = str(x).strip()
    s = re.sub(r"\s+", " ", s)
    return s

def _flatten_columns(df: pd.DataFrame) -> list[str]:
    cols = []
    for c in df.columns:
        if isinstance(c, tuple):
            parts = [str(x).strip() for x in c if str(x).strip() and "Unnamed" not in str(x)]
            cols.append(" | ".join(parts) if parts else "")
        else:
            cols.append(str(c).strip())
    return cols

def _try_anchor_code_map(html: str) -> dict[str, str]:
    soup = BeautifulSoup(html, "html.parser")
    out = {}
    for a in soup.select("a[href*='item/main.naver?code=']"):
        href = a.get("href", "")
        m = re.search(r"code=(\d{6})", href)
        name = _clean_text(a.get_text())
        if m and name and name not in out:
            out[name] = m.group(1)
    return out

def _read_all_tables(html: str) -> list[pd.DataFrame]:
    try:
        tables = pd.read_html(io.StringIO(html))
    except Exception:
        return []
    out = []
    for t in tables:
        df = t.copy()
        df.columns = _flatten_columns(df)
        out.append(df)
    return out

def _repair_header_from_rows(df: pd.DataFrame) -> pd.DataFrame:
    """
    Some Naver tables come in with numeric columns and header labels inside the first/second row.
    Try to promote a header row if it contains 종목명/수량/금액.
    """
    work = df.copy()
    cols = [str(c) for c in work.columns]
    if any("종목명" in c or "금액" in c or "수량" in c for c in cols):
        return work

    max_scan = min(len(work), 4)
    for i in range(max_scan):
        row = [_clean_text(x) for x in work.iloc[i].tolist()]
        joined = " | ".join(row)
        hits = sum(k in joined for k in ["종목명", "금액", "수량"])
        if hits >= 2:
            new_cols = [x if x else f"col{j}" for j, x in enumerate(row)]
            repaired = work.iloc[i+1:].copy().reset_index(drop=True)
            repaired.columns = new_cols
            return repaired
    return work

def _extract_latest_block_from_table(df: pd.DataFrame) -> pd.DataFrame | None:
    df = _repair_header_from_rows(df)
    cols = df.columns.tolist()

    date_pat = re.compile(r"(\d{2}\.\d{2}\.\d{2})")
    date_groups = {}
    for c in cols:
        m = date_pat.search(str(c))
        if m:
            date_groups.setdefault(m.group(1), []).append(c)

    if date_groups:
        latest = sorted(date_groups.keys())[-1]
        use_cols = date_groups[latest]
        name_col = next((c for c in use_cols if "종목명" in str(c)), None)
        amt_col = next((c for c in use_cols if "금액" in str(c)), None)
        qty_col = next((c for c in use_cols if "수량" in str(c)), None)

        if name_col:
            return pd.DataFrame({
                "name": df[name_col],
                "amount": df[amt_col] if amt_col else None,
                "qty": df[qty_col] if qty_col else None,
                "source_date": latest,
            })

    # flat layout
    name_col = next((c for c in cols if "종목명" in str(c)), None)
    amt_col = next((c for c in cols if "금액" in str(c)), None)
    qty_col = next((c for c in cols if "수량" in str(c)), None)
    if name_col:
        return pd.DataFrame({
            "name": df[name_col],
            "amount": df[amt_col] if amt_col else None,
            "qty": df[qty_col] if qty_col else None,
            "source_date": "",
        })

    # last-resort heuristic:
    # if first column looks like company names and at least one later column is numeric-ish, use it
    if len(cols) >= 2:
        c0 = cols[0]
        series0 = df[c0].astype(str).map(_clean_text)
        name_like = series0.str.contains(r"[가-힣A-Za-z]{2,}").sum()
        if name_like >= 5:
            num_candidates = []
            for c in cols[1:]:
                s = (
                    df[c].astype(str)
                    .str.replace(",", "", regex=False)
                    .str.replace(r"[^\d\.\-]", "", regex=True)
                )
                num = pd.to_numeric(s, errors="coerce")
                score = int(num.notna().sum())
                if score >= 5:
                    num_candidates.append((c, score))
            if num_candidates:
                num_candidates = sorted(num_candidates, key=lambda x: x[1], reverse=True)
                amt_col = num_candidates[0][0]
                qty_col = num_candidates[1][0] if len(num_candidates) >= 2 else None
                return pd.DataFrame({
                    "name": df[c0],
                    "amount": df[amt_col] if amt_col else None,
                    "qty": df[qty_col] if qty_col else None,
                    "source_date": "",
                })

    return None

def _normalize_ranking(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    out["name"] = out["name"].map(_clean_text)
    out = out[out["name"].astype(str).str.len() > 0].copy()
    out = out[~out["name"].str.contains("순매수|영역|코스피|코스닥|종목명|더보기|수량|금액|당일거래량", na=False)].copy()

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
    # rows with amount are best
    out = out.sort_values(["amount", "name"], ascending=[False, True], na_position="last").reset_index(drop=True)
    return out

def _find_best_extracted_table(tables: list[pd.DataFrame]) -> tuple[pd.DataFrame, pd.DataFrame]:
    debug_rows = []
    best_norm = None
    best_score = -1

    for i, t in enumerate(tables):
        try:
            extracted = _extract_latest_block_from_table(t)
            cols = [str(c) for c in t.columns.tolist()]
            if extracted is None:
                debug_rows.append({"table_idx": i, "rows": len(t), "cols": " || ".join(cols), "parsed_rows": 0, "status": "skip"})
                continue
            norm = _normalize_ranking(extracted)
            parsed_rows = len(norm)
            # Prefer tables with more mapped-looking names and available amounts
            score = parsed_rows + int(norm["amount"].notna().sum()) if "amount" in norm.columns else parsed_rows
            debug_rows.append({"table_idx": i, "rows": len(t), "cols": " || ".join(cols), "parsed_rows": parsed_rows, "status": "ok"})
            if score > best_score:
                best_score = score
                best_norm = norm
        except Exception as e:
            debug_rows.append({"table_idx": i, "rows": len(t), "cols": " || ".join([str(c) for c in t.columns.tolist()]), "parsed_rows": 0, "status": f"error:{type(e).__name__}"})

    dbg = pd.DataFrame(debug_rows)
    if best_norm is None or best_norm.empty:
        raise RuntimeError("Could not extract a usable ranking block from any parsed Naver table.")
    return best_norm, dbg

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
    ap.add_argument("--top", type=int, default=20)
    args = ap.parse_args()

    html = fetch_html()
    RAW_HTML.write_text(html, encoding="utf-8")

    anchor_map = _try_anchor_code_map(html)
    tables = _read_all_tables(html)
    if not tables:
        raise SystemExit("No HTML tables parsed from Naver page. Check foreign_naver_raw.html")

    latest, dbg = _find_best_extracted_table(tables)
    dbg.to_csv(DBG_CSV, index=False)

    ds = _find_working_krx_date()
    pykrx_map = _pykrx_name_map(ds)

    rows = []
    txt = []
    seen = set()

    for _, r in latest.iterrows():
        name = r["name"]
        sym = ""

        if name in pykrx_map:
            sym = pykrx_map[name]
        elif name in anchor_map:
            code = anchor_map[name]
            # if pykrx unavailable, default guess to KOSPI
            sym = f"{code}.KS"

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
    print(f"Saved: {DBG_CSV.name}")
    ok = [x for x in rows if x["status"] == "ok"]
    if ok:
        print("Top mapped names:")
        for x in ok[:10]:
            print(f" - {x['ticker']}  {x['name']}  amount={x['amount']}")
    else:
        print("No mapped names. Check foreign_naver_raw.html and foreign_naver_debug.csv")

if __name__ == "__main__":
    main()
