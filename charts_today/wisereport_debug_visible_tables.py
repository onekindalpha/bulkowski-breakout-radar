#!/usr/bin/env python3
# -*- coding: utf-8 -*-
from __future__ import annotations

import argparse
from pathlib import Path

try:
    from playwright.sync_api import sync_playwright
except Exception:
    sync_playwright = None

URL = "https://comp.wisereport.co.kr/company/c1050001.aspx?cmp_cd={code}"


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


def get_visible_tables(page):
    js = r"""
    () => {
      const isVisible = (el) => {
        const st = window.getComputedStyle(el);
        const r = el.getBoundingClientRect();
        return st.display !== 'none' && st.visibility !== 'hidden' && r.width > 0 && r.height > 0;
      };
      const tables = Array.from(document.querySelectorAll('table')).filter(isVisible);
      return tables.slice(0, 20).map((t, idx) => ({
        index: idx,
        rows: Array.from(t.querySelectorAll('tr')).map(tr =>
          Array.from(tr.querySelectorAll('th,td')).map(td => (td.innerText || '').replace(/\s+/g, ' ').trim())
        )
      }));
    }
    """
    return page.evaluate(js)


def dump_tables(tables, out_path: Path, section: str):
    with out_path.open("a", encoding="utf-8") as f:
        f.write(f"\n===== {section} =====\n")
        for t in tables:
            f.write(f"\n--- TABLE {t['index']} ---\n")
            for row in t["rows"][:25]:
                f.write("\t".join(row) + "\n")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--code", required=True)
    ap.add_argument("--out", default="wisereport_debug_tables.txt")
    ap.add_argument("--headless", action="store_true")
    args = ap.parse_args()

    if sync_playwright is None:
        raise SystemExit("pip install playwright pandas && python -m playwright install chromium")

    out_path = Path(args.out)
    out_path.write_text("", encoding="utf-8")

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=args.headless)
        context = browser.new_context(locale="ko-KR")
        page = context.new_page()
        page.goto(URL.format(code=args.code), wait_until="networkidle", timeout=30000)
        page.wait_for_timeout(1800)

        click_text(page, "투자지표")
        dump_tables(get_visible_tables(page), out_path, "투자지표-초기")

        click_text(page, "수익성")
        dump_tables(get_visible_tables(page), out_path, "투자지표-수익성")

        click_text(page, "성장성")
        dump_tables(get_visible_tables(page), out_path, "투자지표-성장성")

        click_text(page, "안정성")
        dump_tables(get_visible_tables(page), out_path, "투자지표-안정성")

        page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        page.wait_for_timeout(800)
        dump_tables(get_visible_tables(page), out_path, "투자지표-하단공통표")

        click_text(page, "컨센서스")
        dump_tables(get_visible_tables(page), out_path, "컨센서스")

        browser.close()

    print(f"saved: {out_path}")


if __name__ == "__main__":
    main()
