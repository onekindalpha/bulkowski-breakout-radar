#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
make_premarket_manual_clean.py

Create a clean manual ticker list from premarket_auto.csv
for manual10_legacy_review_v3_dmaall_color.py.
"""

import re
from pathlib import Path

SRC = Path("premarket_auto.csv")
OUT = Path("premarket_manual_clean.txt")

PAT = re.compile(r"^[A-Z0-9\^\=\.\-]{1,20}$")

def main():
    if not SRC.exists():
        raise SystemExit(f"missing file: {SRC}")

    seen = set()
    rows = []

    for line in SRC.read_text(encoding="utf-8", errors="ignore").splitlines():
        s = line.strip()
        if not s or s.startswith("#") or s.lower().startswith("ticker,"):
            continue

        ticker = s.split(",", 1)[0].strip().upper()
        if not PAT.match(ticker):
            continue

        if ticker not in seen:
            seen.add(ticker)
            rows.append(ticker)

    OUT.write_text("\n".join(rows) + ("\n" if rows else ""), encoding="utf-8")
    print(f"Saved: {OUT} ({len(rows)} tickers)")

if __name__ == "__main__":
    main()
