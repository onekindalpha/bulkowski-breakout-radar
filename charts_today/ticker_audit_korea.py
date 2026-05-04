#!/usr/bin/env python3
"""
ticker_audit_korea.py

Quick "where did my tickers go?" tool.
Prints counts + missing sets across your pipeline artifacts:

  - Input txt union (macro/core/2x/finviz_manual)
  - premarket_auto_debug.csv
  - candidates.txt
  - premarket_manual.csv
  - premarket.csv
  - report_v2.csv

Usage:
  python ticker_audit_korea.py
"""

from __future__ import annotations
import pandas as pd
from pathlib import Path

from pipeline_config_korea import load_default_groups, union_ordered, print_group_counts

def load_csv_tickers(path: str, col_candidates: list[str]) -> set[str]:
    p = Path(path)
    if not p.exists():
        return set()
    try:
        df = pd.read_csv(p, comment="#")
    except Exception:
        return set()
    col = None
    for c in col_candidates:
        if c in df.columns:
            col = c
            break
    if col is None:
        return set()
    return set(df[col].dropna().astype(str).str.strip().str.upper().tolist())

def load_txt_tickers(path: str) -> set[str]:
    p = Path(path)
    if not p.exists():
        return set()
    out = set()
    for line in p.read_text(encoding="utf-8", errors="ignore").splitlines():
        s = line.strip()
        if not s or s.startswith("#"):
            continue
        out.add(s.split("#",1)[0].strip().upper())
    return out

def show_missing(name_a, a: set[str], name_b, b: set[str], limit=40):
    miss = sorted(list(a - b))
    print(f"{name_a} -> {name_b} missing: {len(miss)}")
    if miss:
        print("  " + ", ".join(miss[:limit]) + ("" if len(miss)<=limit else f" ...(+{len(miss)-limit})"))
    print()

def main():
    groups = load_default_groups()
    print_group_counts(groups, title="INPUT TXT COUNTS (ALL GROUPS)")
    union = set(union_ordered(groups))

    auto_dbg = load_csv_tickers("premarket_auto_debug_korea.csv", ["ticker","yahoo_symbol"])
    cands = load_txt_tickers("candidates_korea.txt")
    pm_manual = load_csv_tickers("premarket_manual_korea.csv", ["ticker"])
    pm = load_csv_tickers("premarket_korea.csv", ["ticker"])
    report = load_csv_tickers("report_v2_korea.csv", ["ticker"])

    print("=== STAGE COUNTS ===")
    print(f"union_txts           {len(union)}")
    print(f"premarket_auto_debug {len(auto_dbg)}")
    print(f"candidates_txt       {len(cands)}")
    print(f"premarket_manual     {len(pm_manual)}")
    print(f"premarket_merged     {len(pm)}")
    print(f"report_v2            {len(report)}")
    print()

    show_missing("union_txts", union, "premarket_auto_debug", auto_dbg)
    show_missing("premarket_auto_debug", auto_dbg, "candidates_txt", cands)
    show_missing("candidates_txt", cands, "premarket_merged", pm)
    show_missing("premarket_merged", pm, "report_v2", report)

    # also: candidates not in report (your "why didn't it show?")
    show_missing("candidates_txt", cands, "report_v2", report)

if __name__ == "__main__":
    main()
