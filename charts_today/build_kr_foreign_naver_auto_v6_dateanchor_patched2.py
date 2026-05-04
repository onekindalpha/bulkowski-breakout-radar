#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
build_kr_foreign_naver_auto_v6_dateanchor.py

Naver foreign net-buy overlay builder for Korea.

Key fix vs older versions:
- NEVER assume left/right/latest by position.
- Try to read date labels near each candidate table/block.
- Pick only the blocks tied to the latest parsed date.
- If date cannot be parsed at all, fallback to score/order heuristics.
- Emit richer debug CSV including parsed dates and block signatures.
"""
from __future__ import annotations

import argparse
import hashlib
import re
from datetime import date, datetime, timedelta
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

DATE_PATTERNS = [
    re.compile(r"(20\d{2})[./-](\d{2})[./-](\d{2})"),
    re.compile(r"(?<!\d)(\d{2})[./-](\d{2})[./-](\d{2})(?!\d)"),  # 26.03.18
]


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


def working_krx_date() -> str:
    today = date.today()
    wd = today.weekday()  # Mon=0

    if wd == 0:
        d = today - timedelta(days=3)
    elif wd == 6:
        d = today - timedelta(days=2)
    elif wd == 5:
        d = today - timedelta(days=1)
    else:
        d = today - timedelta(days=1)

    return ymd(d)


def code_suffix_map(ds: str) -> dict[str, str]:
    # pykrx market ticker fetch is unreliable in this environment.
    # Fall back to yfinance-based suffix probing only.
    return {}


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


def parse_date_tokens(text: str) -> list[date]:
    out = []
    today_year = date.today().year
    for pat in DATE_PATTERNS:
        for m in pat.finditer(text):
            try:
                if len(m.group(1)) == 4:
                    y = int(m.group(1))
                    mm = int(m.group(2))
                    dd = int(m.group(3))
                else:
                    yy = int(m.group(1))
                    y = 2000 + yy if yy <= 79 else 1900 + yy
                    mm = int(m.group(2))
                    dd = int(m.group(3))
                if 2000 <= y <= today_year + 1:
                    out.append(date(y, mm, dd))
            except Exception:
                pass
    # uniq preserve order
    uniq = []
    seen = set()
    for d in out:
        if d not in seen:
            uniq.append(d)
            seen.add(d)
    return uniq


def nearest_context_text(node, max_prev_siblings: int = 6) -> str:
    texts = []
    # own table text start (trimmed heavily)
    own = clean_text(node.get_text(" ", strip=True))
    if own:
        texts.append(own[:600])
    # previous siblings of the table itself
    cur = node
    steps = 0
    while steps < max_prev_siblings:
        sib = cur.previous_sibling
        if sib is None:
            break
        cur = sib
        t = clean_text(getattr(sib, "get_text", lambda *a, **k: str(sib))(" ", strip=True) if hasattr(sib, "get_text") else str(sib))
        if t:
            texts.append(t[:300])
            steps += 1
    # parent / grandparent neighborhood
    p = node.parent
    depth = 0
    while p is not None and depth < 3:
        t = clean_text(p.get_text(" ", strip=True))
        if t:
            texts.append(t[:1200])
        # previous siblings of parent containers often contain the date header
        curp = p
        steps = 0
        while steps < max_prev_siblings:
            sib = curp.previous_sibling
            if sib is None:
                break
            curp = sib
            t2 = clean_text(getattr(sib, "get_text", lambda *a, **k: str(sib))(" ", strip=True) if hasattr(sib, "get_text") else str(sib))
            if t2:
                texts.append(t2[:300])
                steps += 1
        p = p.parent
        depth += 1
    return " || ".join(texts)


def block_signature(rows: list[dict]) -> str:
    key = "|".join(f"{r['code']}:{r['amount']}" for r in rows[:8])
    return hashlib.md5(key.encode("utf-8")).hexdigest()[:12]


def collect_candidate_blocks(html: str, page_url: str):
    soup = BeautifulSoup(html, "html.parser")
    blocks = []
    debug = []

    for i, table in enumerate(soup.find_all("table")):
        text = clean_text(table.get_text(" ", strip=True))
        anchors = table.find_all("a", href=re.compile(r"item/main\.naver\?code=\d{6}"))
        rows = extract_rows_from_table(table, page_url)
        score = table_score(text, len(anchors))
        ctx = nearest_context_text(table)
        parsed_dates = parse_date_tokens(ctx)
        latest_date = max(parsed_dates) if parsed_dates else None
        sig = block_signature(rows) if rows else ""
        debug.append({
            "page_url": page_url,
            "block_type": "table",
            "block_idx": i,
            "score": score,
            "anchors": len(anchors),
            "rows": len(rows),
            "dates_found": ",".join(d.isoformat() for d in parsed_dates),
            "latest_date": latest_date.isoformat() if latest_date else "",
            "signature": sig,
            "snippet": text[:220],
            "context_snippet": ctx[:500],
        })
        if rows:
            blocks.append({
                "score": score,
                "rows": rows,
                "snippet": text[:180],
                "page_url": page_url,
                "block_idx": i,
                "dates": parsed_dates,
                "latest_date": latest_date,
                "signature": sig,
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

    pd.DataFrame(all_debug).to_csv(DBG_CSV, index=False)
    return all_blocks


def dedupe_blocks(blocks: list[dict]) -> list[dict]:
    out = []
    seen = set()
    for b in blocks:
        sig = (b.get("page_url"), b.get("signature"))
        if sig in seen:
            continue
        seen.add(sig)
        out.append(b)
    return out


def pick_best_rows(blocks):
    if not blocks:
        return [], {}

    blocks = dedupe_blocks(blocks)

    # 1) Prefer latest parsed date across blocks.
    dated_blocks = [b for b in blocks if b.get("latest_date") is not None]
    if dated_blocks:
        max_dt = max(b["latest_date"] for b in dated_blocks)
        candidate = [b for b in dated_blocks if b["latest_date"] == max_dt]
        # Among latest-date blocks, prefer stronger score and more rows; stable by later block index.
        candidate = sorted(candidate, key=lambda b: (b["score"], len(b["rows"]), b.get("block_idx", -1)), reverse=True)
        chosen = candidate
        reason = {"mode": "date_anchor", "selected_date": max_dt.isoformat(), "selected_block_count": len(chosen)}
    else:
        blocks_sorted = sorted(blocks, key=lambda b: (b["score"], len(b["rows"]), b.get("block_idx", -1)), reverse=True)
        best = blocks_sorted[0]
        chosen = []
        for blk in blocks_sorted[:5]:
            if blk["score"] < max(best["score"] - 3, 1):
                continue
            chosen.append(blk)
        reason = {"mode": "fallback_no_date", "selected_date": "", "selected_block_count": len(chosen)}

    rows = []
    seen = set()
    # if multiple same-date blocks exist (duplicate render), merge unique rows preserving chosen block order.
    for blk in chosen:
        for r in blk["rows"]:
            key = (r["code"], r["name"])
            if key not in seen:
                seen.add(key)
                rows.append(r)
    return rows, reason


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--top", type=int, default=20)
    args = ap.parse_args()

    ds = working_krx_date()
    suffix_map = code_suffix_map(ds)
    probe_cache = {}

    blocks = collect_all_blocks()
    rows, reason = pick_best_rows(blocks)

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
            "selection_mode": reason.get("mode", ""),
            "selected_date": reason.get("selected_date", ""),
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
    print(f"Selection mode: {reason.get('mode','')}")
    if reason.get("selected_date"):
        print(f"Selected date: {reason['selected_date']}")

    ok = [x for x in out_rows if x["status"] == "ok"]
    if ok:
        print("Top mapped names:")
        for x in ok[:10]:
            print(f" - {x['ticker']}  {x['name']}  amount={x['amount']}")
    else:
        print("No mapped names. Check foreign_naver_raw.html and foreign_naver_debug.csv")


if __name__ == "__main__":
    main()
