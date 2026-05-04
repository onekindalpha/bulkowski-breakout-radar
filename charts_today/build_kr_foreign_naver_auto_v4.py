#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
build_kr_foreign_naver_auto_v4.py

Fixes for v3:
- Detects date labels (yy.mm.dd / yyyy.mm.dd) per candidate block
- Prefers the latest-dated foreign net-buy block, not the first same-scored block
- Avoids mixing rows across different dates
- Still falls back to score/row-count if date detection fails
"""
from __future__ import annotations

import argparse
import re
from datetime import date, timedelta, datetime
from pathlib import Path
from urllib.parse import urljoin

import pandas as pd
import requests
from bs4 import BeautifulSoup
import yfinance as yf

try:
    from pykrx import stock
except Exception:
    stock = None

BASE_URL = "https://finance.naver.com"
URL = "https://finance.naver.com/sise/sise_deal_rank.naver"

OUT_TXT = Path("kr_foreign_naver_auto.txt")
OUT_CSV = Path("kr_foreign_naver_auto.csv")
RAW_HTML = Path("foreign_naver_raw.html")
DBG_CSV = Path("foreign_naver_debug.csv")

HEADERS = {
    "User-Agent": "Mozilla/5.0",
    "Referer": "https://finance.naver.com/",
}

DATE_RE = re.compile(r'(\d{2,4}\.\d{2}\.\d{2})')

def ymd(d: date) -> str:
    return d.strftime("%Y%m%d")

def recent_dates(n: int = 40) -> list[str]:
    today = date.today()
    return [ymd(today - timedelta(days=i)) for i in range(n)]

def fetch_text(url: str) -> str:
    r = requests.get(url, headers=HEADERS, timeout=20)
    r.raise_for_status()
    if not r.encoding or r.encoding.lower() == "iso-8859-1":
        r.encoding = r.apparent_encoding or "cp949"
    return r.text

def clean_text(x) -> str:
    s = str(x).strip()
    s = re.sub(r"\s+", " ", s)
    return s

def parse_num(x):
    s = clean_text(x)
    s = s.replace(",", "")
    s = re.sub(r"[^\d\.\-]", "", s)
    if not s:
        return None
    try:
        return float(s)
    except Exception:
        return None

def parse_date_label(text: str):
    """Return YYYY-MM-DD string if a date label like 26.03.18 or 2026.03.18 exists."""
    m = DATE_RE.search(text or "")
    if not m:
        return None
    token = m.group(1)
    try:
        parts = token.split(".")
        y = int(parts[0])
        if y < 100:
            y += 2000
        dt = datetime(y, int(parts[1]), int(parts[2]))
        return dt.strftime("%Y-%m-%d")
    except Exception:
        return None

def working_krx_date() -> str:
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

def code_suffix_map(ds: str) -> dict[str, str]:
    out = {}
    if stock is None or not ds:
        return out
    for market, suffix in [("KOSPI", ".KS"), ("KOSDAQ", ".KQ")]:
        try:
            ticks = stock.get_market_ticker_list(ds, market=market)
        except Exception:
            ticks = []
        for t in ticks:
            out[str(t).zfill(6)] = suffix
    return out

def yfinance_probe_suffix(code: str, cache: dict[str, str]) -> str:
    if code in cache:
        return cache[code]
    for suffix in [".KS", ".KQ"]:
        sym = code + suffix
        try:
            df = yf.Ticker(sym).history(period="1mo", interval="1d", auto_adjust=False)
            if df is not None and not df.empty:
                cache[code] = suffix
                return suffix
        except Exception:
            pass
    cache[code] = ""
    return ""

def table_score(text: str, anchor_count: int) -> int:
    score = 0
    if "외국인" in text:
        score += 8
    if "순매수" in text:
        score += 6
    if "거래상위" in text:
        score += 2
    if "기관" in text:
        score -= 1
    if "개인" in text:
        score -= 1
    score += min(anchor_count, 20)
    return score

def extract_rows_from_table(table, page_url: str):
    rows = []
    for tr in table.find_all("tr"):
        a = tr.find("a", href=re.compile(r"item/main\.naver\?code=\d{6}"))
        if not a:
            continue
        href = a.get("href", "")
        m = re.search(r"code=(\d{6})", href)
        if not m:
            continue
        code = m.group(1)
        name = clean_text(a.get_text())
        tds = [clean_text(td.get_text(" ", strip=True)) for td in tr.find_all("td")]
        nums = [parse_num(x) for x in tds if parse_num(x) is not None]
        amount = None
        qty = None
        if nums:
            amount = nums[0]
            qty = nums[1] if len(nums) >= 2 else None
        rows.append({
            "name": name,
            "code": code,
            "amount": amount,
            "qty": qty,
            "page_url": page_url,
            "row_text": " | ".join(tds),
        })
    return rows

def collect_candidate_blocks(html: str, page_url: str):
    soup = BeautifulSoup(html, "html.parser")
    blocks = []
    debug = []

    for i, table in enumerate(soup.find_all("table")):
        text = clean_text(table.get_text(" ", strip=True))
        anchors = table.find_all("a", href=re.compile(r"item/main\.naver\?code=\d{6}"))
        rows = extract_rows_from_table(table, page_url)
        score = table_score(text, len(anchors))
        date_label = parse_date_label(text)

        debug.append({
            "page_url": page_url,
            "block_type": "table",
            "block_idx": i,
            "score": score,
            "anchors": len(anchors),
            "rows": len(rows),
            "date_label": date_label,
            "snippet": text[:180],
        })
        if rows:
            blocks.append({
                "score": score,
                "rows": rows,
                "snippet": text[:180],
                "page_url": page_url,
                "date_label": date_label,
            })

    iframe_urls = []
    for iframe in soup.find_all("iframe"):
        src = iframe.get("src", "")
        if not src:
            continue
        full = urljoin(BASE_URL, src)
        if full not in iframe_urls and "finance.naver.com" in full:
            iframe_urls.append(full)

    return blocks, debug, iframe_urls

def collect_all_blocks():
    html = fetch_text(URL)
    RAW_HTML.write_text(html, encoding="utf-8")

    seen_pages = set()
    to_visit = [URL]
    all_blocks = []
    all_debug = []

    while to_visit:
        page = to_visit.pop(0)
        if page in seen_pages:
            continue
        seen_pages.add(page)

        txt = html if page == URL else fetch_text(page)
        blocks, debug, iframe_urls = collect_candidate_blocks(txt, page)
        all_blocks.extend(blocks)
        all_debug.extend(debug)
        for u in iframe_urls:
            if u not in seen_pages and u not in to_visit:
                to_visit.append(u)

    dbg_df = pd.DataFrame(all_debug)
    dbg_df.to_csv(DBG_CSV, index=False)
    return all_blocks

def pick_best_rows(blocks):
    if not blocks:
        return []

    # Restrict to foreign net-buy looking blocks first.
    buyish = [b for b in blocks if "외국인" in b["snippet"] and "순매수" in b["snippet"]]
    candidates = buyish if buyish else blocks

    # Prefer latest date if any block exposes a date.
    dated = [b for b in candidates if b.get("date_label")]
    if dated:
        latest_date = max(b["date_label"] for b in dated)
        candidates = [b for b in candidates if b.get("date_label") == latest_date]

    # Then prefer highest score / most rows.
    candidates = sorted(candidates, key=lambda b: (b["score"], len(b["rows"])), reverse=True)
    best = candidates[0]

    # Merge only blocks that match the selected date (if present) and are close in score.
    rows = []
    seen = set()
    for blk in candidates[:5]:
        if blk["score"] < max(best["score"] - 3, 1):
            continue
        if best.get("date_label") and blk.get("date_label") != best.get("date_label"):
            continue
        for r in blk["rows"]:
            key = (r["code"], r["name"])
            if key not in seen:
                seen.add(key)
                rows.append(r)

    return rows if rows else best["rows"]

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--top", type=int, default=20)
    args = ap.parse_args()

    ds = working_krx_date()
    suffix_map = code_suffix_map(ds)
    probe_cache = {}

    blocks = collect_all_blocks()
    rows = pick_best_rows(blocks)

    if not rows:
        OUT_TXT.write_text("", encoding="utf-8")
        pd.DataFrame(columns=["name","ticker","amount","qty","status"]).to_csv(OUT_CSV, index=False)
        raise SystemExit("Could not find any usable foreign ranking rows from Naver. Check foreign_naver_raw.html and foreign_naver_debug.csv")

    out_rows = []
    txt = []
    seen = set()

    for r in rows:
        code = r["code"]
        suffix = suffix_map.get(code, "")
        if not suffix:
            suffix = yfinance_probe_suffix(code, probe_cache)
        sym = f"{code}{suffix}" if suffix else ""

        status = "ok" if sym else "unmapped"
        out_rows.append({
            "name": r["name"],
            "ticker": sym,
            "amount": r["amount"],
            "qty": r["qty"],
            "status": status,
            "code": code,
            "page_url": r["page_url"],
        })

        if sym and sym not in seen:
            seen.add(sym)
            txt.append(sym)
            if len(txt) >= args.top:
                break

    OUT_TXT.write_text("\n".join(txt) + ("\n" if txt else ""), encoding="utf-8")
    pd.DataFrame(out_rows).to_csv(OUT_CSV, index=False)

    print(f"Saved: {OUT_TXT.name} ({len(txt)} tickers)")
    print(f"Saved: {OUT_CSV.name} ({len(out_rows)} rows)")
    print(f"Saved: {DBG_CSV.name}")

    ok = [x for x in out_rows if x["status"] == "ok"]
    if ok:
        print("Top mapped names:")
        for x in ok[:10]:
            print(f" - {x['ticker']}  {x['name']}  amount={x['amount']}")
    else:
        print("No mapped names. Check foreign_naver_raw.html and foreign_naver_debug.csv")

if __name__ == "__main__":
    main()
