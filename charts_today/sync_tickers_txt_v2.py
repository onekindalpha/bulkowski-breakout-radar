#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""sync_tickers_txt_v2.py

Creates tickers.txt as a UNION (unique, ordered) of your source TXT files.

Patch:
- Automatically includes generated group-overlay txt files such as:
    finviz_top_groups_auto.txt
    finviz_top_groups_auto_today.txt
    finviz_top_groups_auto_mixed.txt
    finviz_top_groups_auto_active.txt
- Keeps your standard source files too
"""
from __future__ import annotations
from pathlib import Path
from datetime import datetime, timezone, timedelta
import re

KST = timezone(timedelta(hours=9))

DEFAULT_SOURCES = [
    "finviz_manual.txt",
    "macro_watch_yahoo.txt",
    "tickers_core.txt",
    "tickers_leverage2x.txt",
    "tickers_leverage_global.txt",
    "sp69_tickers_only.txt",
]

INCLUDE_GLOBS = [
    "finviz_top_groups_auto*.txt",
]

EXCLUDE_NAMES = {
    "tickers.txt",
    "candidates.txt",
    "premarket.csv",
    "premarket_auto.csv",
    "premarket_auto_debug.csv",
    "premarket_manual.csv",
    "report_v2.csv",
}

def now_kst() -> str:
    return datetime.now(KST).strftime("%Y-%m-%d %H:%M:%S KST")

def _strip_inline_comment(s: str) -> str:
    if "#" in s:
        s = s.split("#", 1)[0]
    return s.strip()

def read_tickers(path: str) -> list[str]:
    p = Path(path)
    if not p.exists():
        return []
    txt = p.read_text(encoding="utf-8", errors="ignore")
    out: list[str] = []
    for line in txt.splitlines():
        s = _strip_inline_comment(line)
        if not s:
            continue
        out.append(s)
    return out

def merge_unique_ordered(a: list[str], b: list[str]) -> list[str]:
    seen = set(a)
    out = list(a)
    for x in b:
        if x not in seen:
            seen.add(x)
            out.append(x)
    return out

def resolve_sources() -> list[str]:
    here = Path(".")
    sources = [s for s in DEFAULT_SOURCES if (here / s).exists()]

    extra = []
    for pat in INCLUDE_GLOBS:
        for p in sorted(here.glob(pat), key=lambda x: x.name):
            if p.name in EXCLUDE_NAMES:
                continue
            if p.suffix.lower() != ".txt":
                continue
            if p.name not in sources and p.name not in extra:
                extra.append(p.name)

    return sources + extra

def main():
    sources = resolve_sources()
    merged: list[str] = []
    counts = []
    for s in sources:
        t = read_tickers(s)
        counts.append((s, len(t)))
        merged = merge_unique_ordered(merged, t)

    out = Path("tickers.txt")
    out.write_text("\n".join(merged) + ("\n" if merged else ""), encoding="utf-8")

    print(f"Saved: {out}  (count={len(merged)})")
    print("KST_NOW:", now_kst())
    print("Sources:")
    for s, n in counts:
        print(f" - {s}  ({n})")

if __name__ == "__main__":
    main()
