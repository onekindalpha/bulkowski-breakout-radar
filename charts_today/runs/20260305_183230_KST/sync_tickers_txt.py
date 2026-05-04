#!/usr/bin/env python3
"""
sync_tickers_txt.py

Auto-generate tickers.txt as a UNION of your watchlist txt files.

It supports:
- The "standard" files (if present):
    macro_watch_yahoo.txt
    tickers_core.txt
    tickers_leverage2x.txt
    finviz_manual.txt (and common typo finviz_manul.txt)
- PLUS any additional txt files you create that match these safe patterns:
    tickers_*.txt
    *_watch_*.txt
    finviz_*.txt
  (excluding obvious output files like premarket/report/positions/candidates)

Output:
  tickers.txt  (one ticker per line, sorted)

Usage:
  python sync_tickers_txt.py
"""

from __future__ import annotations
from pathlib import Path
import re

TICKER_RE = re.compile(r"^[A-Z0-9\^\=\.\-\/]{1,20}$")

STANDARD = [
    "macro_watch_yahoo.txt",
    "tickers_core.txt",
    "tickers_leverage2x.txt",
    "finviz_manual.txt",
    "finviz_manul.txt",
]

INCLUDE_GLOBS = [
    "tickers_*.txt",
    "*_watch_*.txt",
    "finviz_*.txt",
]

EXCLUDE_NAMES = {
    "tickers.txt",
    "candidates.txt",
    "candidates_2x.txt",
    "positions.csv",
    "positions_filled.csv",
    "positions_rebuilt.csv",
    "premarket.csv",
    "premarket_auto.csv",
    "premarket_auto_debug.csv",
    "premarket_manual.csv",
    "report_v2.csv",
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
            # Do NOT mix Korea universes into US tickers.txt
            if p.name.endswith("_korea.txt") or "_korea" in p.stem or p.name == "tickers_korea.txt":
                continue
            if p.name in EXCLUDE_NAMES:
                continue
            # avoid picking up generated reports
            if p.name.startswith("report_v2_") or p.name.startswith("buy_report_") or p.name.startswith("signals_"):
                continue
            # keep only .txt
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
    (here / "tickers.txt").write_text("\n".join(union_sorted) + ("\n" if union_sorted else ""), encoding="utf-8")
    print(f"Saved: tickers.txt  (count={len(union_sorted)})")
    if files:
        print("Sources:")
        for p in sorted(files, key=lambda x: x.name):
            print(" -", p.name)

if __name__ == "__main__":
    main()
