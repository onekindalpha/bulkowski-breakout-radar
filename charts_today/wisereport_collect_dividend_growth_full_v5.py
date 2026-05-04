#!/usr/bin/env python3
# -*- coding: utf-8 -*-
from __future__ import annotations

import argparse
import re
from typing import Dict, List, Optional

import pandas as pd

try:
    from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError
except Exception:
    sync_playwright = None
    PlaywrightTimeoutError = Exception

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)
DEFAULT_URL_TEMPLATE = "https://comp.wisereport.co.kr/company/c1050001.aspx?cmp_cd={code}"

RAW_COLUMNS = [
    "종목코드","종목명","현재가",
    "매출액증가율_2021","매출액증가율_2022","매출액증가율_2023","매출액증가율_2024","매출액증가율_2025",
    "영업이익증가율_2021","영업이익증가율_2022","영업이익증가율_2023","영업이익증가율_2024","영업이익증가율_2025",
    "순이익증가율_2021","순이익증가율_2022","순이익증가율_2023","순이익증가율_2024","순이익증가율_2025",
    "자기자본증가율_2021","자기자본증가율_2022","자기자본증가율_2023","자기자본증가율_2024","자기자본증가율_2025",
    "영업이익률_2021","영업이익률_2022","영업이익률_2023","영업이익률_2024","영업이익률_2025",
    "순이익률_2021","순이익률_2022","순이익률_2023","순이익률_2024","순이익률_2025",
    "ROE_2021","ROE_2022","ROE_2023","ROE_2024","ROE_2025",
    "부채비율_2021","부채비율_2022","부채비율_2023","부채비율_2024","부채비율_2025",
    "유동비율_2021","유동비율_2022","유동비율_2023","유동비율_2024","유동비율_2025",
    "EPS_2021","EPS_2022","EPS_2023","EPS_2024","EPS_2025","EPS_2026",
    "PER_2021","PER_2022","PER_2023","PER_2024","PER_2025","PER_2026",
    "DPS_2021","DPS_2022","DPS_2023","DPS_2024","DPS_2025","DPS_2026",
    "현금배당수익률_2021","현금배당수익률_2022","현금배당수익률_2023","현금배당수익률_2024","현금배당수익률_2025","현금배당수익률_2026",
    "현금배당성향_2021","현금배당성향_2022","현금배당성향_2023","현금배당성향_2024","현금배당성향_2025","현금배당성향_2026",
    "매출액_2022","매출액_2023","매출액_2024","매출액_2025","매출액_2026","매출액_2027","매출액_2028",
    "영업이익_2022","영업이익_2023","영업이익_2024","영업이익_2025","영업이익_2026","영업이익_2027","영업이익_2028",
    "당기순이익_2022","당기순이익_2023","당기순이익_2024","당기순이익_2025","당기순이익_2026","당기순이익_2027","당기순이익_2028",
    "_notes","_page_url",
]

BAD_NAME_TOKENS = {"메인메뉴","투자지표","컨센서스","재무분석","지분현황","기업개요","기업실적분석"}


def parse_number(x) -> Optional[float]:
    if x is None:
        return None
    s = str(x).strip()
    if s in {"", "-", "N/A", "nan", "None"}:
        return None
    s = s.replace(",", "").replace("%", "").replace("배", "").replace("원", "")
    s = s.replace("억원", "").replace("백만원", "").replace("천원", "")
    s = s.replace("\u2212", "-").replace("\xa0", " ")
    if s.startswith("(") and s.endswith(")"):
        s = "-" + s[1:-1]
    s = s.replace(" ", "")
    try:
        return float(s)
    except Exception:
        return None


def normalize_code(s: str) -> str:
    m = re.search(r"(\d{6})", str(s))
    return m.group(1) if m else str(s).strip()


def parse_code_list(text: str) -> List[str]:
    out = []
    for x in re.split(r"[\s,]+", text.strip()):
        c = normalize_code(x)
        if re.fullmatch(r"\d{6}", c):
            out.append(c)
    return list(dict.fromkeys(out))


def good_name(x: str) -> bool:
    x = re.sub(r"\s+", " ", str(x or "")).strip()
    return bool(x and len(x) < 80 and x not in BAD_NAME_TOKENS and not re.fullmatch(r"\d{6}", x))


def extract_name(page, code: str) -> str:
    texts = []
    for sel in ["#compBody", "body"]:
        try:
            texts.append(page.locator(sel).inner_text(timeout=1000))
        except Exception:
            pass
    try:
        texts.insert(0, page.title())
    except Exception:
        pass
    for txt in texts:
        for pat in [rf"([가-힣A-Za-z0-9&.\- ]+)\s*{code}", r"([가-힣A-Za-z0-9&.\- ]+)\s*-\s*컨센서스"]:
            m = re.search(pat, txt)
            if m:
                nm = re.sub(r"\s+", " ", m.group(1)).strip()
                nm = re.sub(r"\s+\d{6}$", "", nm).strip()
                if good_name(nm):
                    return nm
    return code


def extract_current_price(page) -> Optional[float]:
    for sel in [".num_price strong",".snapshot .num",".price .num","span#cTB11",".box_type_l .num","em#current_price"]:
        try:
            loc = page.locator(sel)
            for i in range(min(loc.count(), 5)):
                n = parse_number(loc.nth(i).inner_text().strip())
                if n is not None and n > 0:
                    return n
        except Exception:
            pass
    return None


def click_text(page, txt: str) -> bool:
    for expr in [lambda: page.get_by_text(txt, exact=False), lambda: page.locator(f"text={txt}")]:
        try:
            loc = expr()
            if loc.count() > 0:
                loc.first.click(timeout=5000)
                page.wait_for_timeout(1200)
                return True
        except Exception:
            pass
    return False


def click_investment_subtab(page, txt: str, wait_keyword: str) -> bool:
    # 1) 투자지표 섹션 내부의 실제 탭 요소를 최대한 좁혀서 클릭
    candidates = [
        'text=/^수익성$|^성장성$|^안정성$|^활동성$/',
        'a',
        'button',
        'li',
        'span',
    ]
    for base in candidates:
        try:
            loc = page.locator(base).filter(has_text=txt)
            if loc.count() > 0:
                for i in range(min(loc.count(), 8)):
                    try:
                        el = loc.nth(i)
                        el.scroll_into_view_if_needed(timeout=1000)
                        el.click(timeout=3000, force=True)
                        page.wait_for_timeout(1200)
                        if wait_table_keyword(page, wait_keyword, timeout=4000):
                            return True
                    except Exception:
                        continue
        except Exception:
            pass

    # 2) 텍스트 직접 클릭 fallback
    if click_text(page, txt):
        if wait_table_keyword(page, wait_keyword, timeout=4000):
            return True
    return False


def wait_table_keyword(page, keyword: str, timeout: int = 4000) -> bool:
    js = f"""
    () => {{
      const isVisible = (el) => {{
        const st = window.getComputedStyle(el);
        const r = el.getBoundingClientRect();
        return st.display !== 'none' && st.visibility !== 'hidden' && r.width > 0 && r.height > 0;
      }};
      const tables = Array.from(document.querySelectorAll('table')).filter(isVisible);
      const txt = tables.map(t => t.innerText).join("\\n");
      return txt.includes({keyword!r});
    }}
    """
    try:
        page.wait_for_function(js, timeout=timeout)
        return True
    except Exception:
        return False


def get_visible_table_matrix(page):
    js = r"""
    () => {
      const isVisible = (el) => {
        const st = window.getComputedStyle(el);
        const r = el.getBoundingClientRect();
        return st.display !== 'none' && st.visibility !== 'hidden' && r.width > 0 && r.height > 0;
      };
      const tables = Array.from(document.querySelectorAll('table')).filter(isVisible);
      return tables.slice(0, 30).map((t, idx) => ({
        index: idx,
        rows: Array.from(t.querySelectorAll('tr')).map(tr =>
          Array.from(tr.querySelectorAll('th,td')).map(td => (td.innerText || '').replace(/\s+/g, ' ').trim())
        )
      }));
    }
    """
    try:
        return page.evaluate(js)
    except Exception:
        return []


def choose_matrix(mats, keywords: List[str]):
    best = None
    best_score = -1
    for mat in mats:
        flat = " ".join(" ".join(r) for r in mat["rows"][:40])
        score = sum(1 for kw in keywords if kw in flat)
        if score > best_score:
            best_score = score
            best = mat["rows"]
    return best


def row_values_from_matrix(mat, row_keywords: List[str], years: List[int]) -> Dict[int, Optional[float]]:
    out = {y: None for y in years}
    if not mat:
        return out
    header = mat[0]
    year_pos = {}
    for i, h in enumerate(header):
        m = re.search(r"(20\d{2})", h)
        if m:
            y = int(m.group(1))
            if y in out and y not in year_pos:
                year_pos[y] = i
    target_row = None
    for row in mat[1:]:
        first = row[0] if row else ""
        for kw in row_keywords:
            if kw in first:
                target_row = row
                break
        if target_row:
            break
    if target_row is None:
        return out

    if year_pos:
        for y, pos in year_pos.items():
            if pos < len(target_row):
                out[y] = parse_number(target_row[pos])

    if all(v is None for v in out.values()):
        nums = [parse_number(v) for v in target_row[1:]]
        nums = [v for v in nums if v is not None]
        for i, y in enumerate(years):
            if i < len(nums):
                out[y] = nums[i]
    return out


def fill_from_matrix(mat, row, mapping, years):
    hits = 0
    for target, kws in mapping.items():
        vals = row_values_from_matrix(mat, kws, years)
        if any(v is not None for v in vals.values()):
            for y, v in vals.items():
                row[f"{target}_{y}"] = v
            hits += 1
    return hits


def parse_investment(page, row):
    hits = 0

    if click_investment_subtab(page, "수익성", "영업이익률"):
        mat = choose_matrix(get_visible_table_matrix(page), ["영업이익률","순이익률","ROE"])
        hits += fill_from_matrix(mat, row, {
            "영업이익률": ["영업이익률"],
            "순이익률": ["순이익률"],
            "ROE": ["ROE"],
        }, [2021,2022,2023,2024,2025])
    else:
        row["_notes"] = ((row.get("_notes") or "") + "; 수익성탭실패").strip("; ")

    if click_investment_subtab(page, "성장성", "매출액증가율"):
        mat = choose_matrix(get_visible_table_matrix(page), ["매출액증가율","영업이익증가율","순이익증가율","자기자본증가율"])
        hits += fill_from_matrix(mat, row, {
            "매출액증가율": ["매출액증가율"],
            "영업이익증가율": ["영업이익증가율"],
            "순이익증가율": ["순이익증가율"],
            "자기자본증가율": ["자기자본증가율"],
        }, [2021,2022,2023,2024,2025])
    else:
        row["_notes"] = ((row.get("_notes") or "") + "; 성장성탭실패").strip("; ")

    if click_investment_subtab(page, "안정성", "부채비율"):
        mat = choose_matrix(get_visible_table_matrix(page), ["부채비율","유동비율"])
        hits += fill_from_matrix(mat, row, {
            "부채비율": ["부채비율"],
            "유동비율": ["유동비율"],
        }, [2021,2022,2023,2024,2025])
    else:
        row["_notes"] = ((row.get("_notes") or "") + "; 안정성탭실패").strip("; ")

    try:
        page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        page.wait_for_timeout(700)
    except Exception:
        pass

    mat = choose_matrix(get_visible_table_matrix(page), ["EPS","PER","DPS","현금배당수익률","현금배당성향"])
    hits += fill_from_matrix(mat, row, {
        "EPS": ["EPS"],
        "PER": ["PER"],
        "DPS": ["DPS"],
        "현금배당수익률": ["현금배당수익률"],
        "현금배당성향": ["현금배당성향"],
    }, [2021,2022,2023,2024,2025,2026])

    try:
        page.evaluate("window.scrollTo(0, 0)")
        page.wait_for_timeout(400)
    except Exception:
        pass

    return hits


def parse_consensus(page, row):
    mat = choose_matrix(get_visible_table_matrix(page), ["재무년월","매출액","영업이익","당기순이익","EPS","PER","ROE"])
    return fill_from_matrix(mat, row, {
        "매출액": ["매출액"],
        "영업이익": ["영업이익"],
        "당기순이익": ["당기순이익","순이익"],
    }, [2022,2023,2024,2025,2026,2027,2028])


def collect_one(page, code: str, url_template: str, wait_ms: int):
    row = {c: None for c in RAW_COLUMNS}
    row["종목코드"] = code
    row["_page_url"] = url_template.format(code=code)
    row["_notes"] = ""
    page.goto(row["_page_url"], wait_until="networkidle", timeout=30000)
    page.wait_for_timeout(wait_ms)

    row["종목명"] = extract_name(page, code)
    row["현재가"] = extract_current_price(page)

    if click_text(page, "컨센서스"):
        parse_consensus(page, row)
    else:
        row["_notes"] = ((row.get("_notes") or "") + "; 컨센서스탭실패").strip("; ")

    if click_text(page, "투자지표"):
        parse_investment(page, row)
    else:
        row["_notes"] = ((row.get("_notes") or "") + "; 투자지표탭실패").strip("; ")

    return row


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--codes", required=True)
    ap.add_argument("--out", default="wisereport_raw_full_v5.tsv")
    ap.add_argument("--url-template", default=DEFAULT_URL_TEMPLATE)
    ap.add_argument("--wait-ms", type=int, default=1800)
    ap.add_argument("--headless", action="store_true")
    args = ap.parse_args()

    if sync_playwright is None:
        raise SystemExit("Playwright not installed. pip install playwright pandas ; python -m playwright install chromium")

    codes = parse_code_list(args.codes)
    rows = []
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=args.headless)
        context = browser.new_context(user_agent=USER_AGENT, locale="ko-KR")
        page = context.new_page()
        for i, code in enumerate(codes, 1):
            print(f"[{i}/{len(codes)}] {code}", flush=True)
            try:
                rows.append(collect_one(page, code, args.url_template, args.wait_ms))
            except Exception as e:
                row = {c: None for c in RAW_COLUMNS}
                row["종목코드"] = code
                row["종목명"] = code
                row["_page_url"] = args.url_template.format(code=code)
                row["_notes"] = f"error: {e}"
                rows.append(row)
        browser.close()

    df = pd.DataFrame(rows)
    for c in RAW_COLUMNS:
        if c not in df.columns:
            df[c] = None
    df = df[RAW_COLUMNS]
    df.to_csv(args.out, sep="\t", index=False, encoding="utf-8-sig")
    print(f"saved: {args.out} ({len(df)} rows)", flush=True)


if __name__ == "__main__":
    main()
