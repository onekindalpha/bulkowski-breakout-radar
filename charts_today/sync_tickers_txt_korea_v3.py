#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
sync_tickers_txt_korea_v3.py

국장용 최종 합본:
- 기존 core/manual/leverage
- A: kr_strong_stocks_auto.txt
- B: kr_sector_rotation_auto.txt
를 자동으로 포함
"""
from __future__ import annotations
from pathlib import Path
import re

TICKER_RE = re.compile(r"^[A-Z0-9\^\=\.\-\/]{1,20}$")

STANDARD = [
    "kr_strong_stocks_auto.txt",
    "kr_sector_rotation_auto.txt",
    "kr_manual_conviction.txt",
    "kr_tactical_leverage.txt",
    "macro_watch_yahoo_korea.txt",
    "tickers_core_korea.txt",
    "tickers_leverage2x_korea.txt",
    "finviz_manual_korea.txt",
]

INCLUDE_GLOBS = [
    "kr_*.txt",
    "tickers_*_korea.txt",
    "*_watch_*_korea.txt",
    "finviz_*_korea.txt",
]

EXCLUDE_NAMES = {
    "tickers_korea.txt",
    "candidates_korea.txt","candidates_2x_korea.txt",
    "premarket_korea.csv","premarket_auto_korea.csv","premarket_auto_debug_korea.csv","premarket_manual_korea.csv",
    "report_v2.csv","report_v2_korea.csv",
}

def read_tickers(path: Path) -> list[str]:
    out = []
    if not path.exists():
        return out
    for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        s = line.strip()
        if not s or s.startswith("#"):
            continue
        if "#" in s:
            s = s.split("#", 1)[0].strip()
        for tok in re.split(r"[\s,;]+", s):
            t = tok.strip().upper()
            if t and TICKER_RE.match(t):
                out.append(t)
    return out

def main():
    here = Path(".")
    files = []
    for name in STANDARD:
        p = here / name
        if p.exists():
            files.append(p)

    for pat in INCLUDE_GLOBS:
        for p in here.glob(pat):
            if p.name in EXCLUDE_NAMES:
                continue
            if p.suffix.lower() != ".txt":
                continue
            if p not in files:
                files.append(p)

    seen = set()
    union = []
    for p in sorted(files, key=lambda x: x.name):
        for t in read_tickers(p):
            if t not in seen:
                seen.add(t)
                union.append(t)

    union_sorted = sorted(union)
    (here / "tickers_korea.txt").write_text("\n".join(union_sorted) + ("\n" if union_sorted else ""), encoding="utf-8")

    print(f"Saved: tickers_korea.txt  (count={len(union_sorted)})")
    print("Sources:")
    for p in sorted(files, key=lambda x: x.name):
        print(" -", p.name)

if __name__ == "__main__":
    main()
