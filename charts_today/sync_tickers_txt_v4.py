#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
sync_tickers_txt_v4.py

Creates tickers.txt as a UNION (unique, ordered) of source TXT files.

Coin behavior (final-output filter, source files untouched):
- --coin none : remove ALL crypto/bitcoin/ethereum-related tickers from final output
- --coin eth  : remove ALL crypto tickers except an ETH allowlist
- --coin etc  : alias of eth

This fixes the problem where old source files still contain crypto tickers.
"""

from __future__ import annotations
from pathlib import Path
from datetime import datetime, timezone, timedelta
import argparse
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

CRYPTO_REMOVE = {
    "IBIT", "FBTC", "ARKB", "BITB", "HODL", "BRRR", "EZBC", "GBTC",
    "ETHA", "FETH", "ETH", "EZET", "ETHV", "ETHW", "ETHE", "ETHU", "ESK", "AETH", "TETH",
    "COIN", "HOOD", "CME", "MSTR", "CRCL",
    "MARA", "RIOT", "CLSK", "IREN", "CIFR", "HUT", "BTDR", "CORZ",
    "SQ", "PYPL",
}

ETH_ALLOW = {
    "ETHA", "FETH", "ETH", "EZET", "ETHV", "ETHW", "ETHE", "ETHU",
    "COIN", "CRCL",
}

def now_kst() -> str:
    return datetime.now(KST).strftime("%Y-%m-%d %H:%M:%S KST")

def _strip_inline_comment(s: str) -> str:
    if "#" in s:
        s = s.split("#", 1)[0]
    return s.strip()

def normalize_ticker(s: str) -> str:
    return s.strip().upper()

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
        for tok in re.split(r"[\s,;]+", s):
            t = normalize_ticker(tok)
            if t:
                out.append(t)
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

def apply_coin_filter(tickers: list[str], coin_mode: str) -> list[str]:
    if coin_mode == "none":
        return [t for t in tickers if t not in CRYPTO_REMOVE]

    if coin_mode in {"eth", "etc"}:
        out = []
        for t in tickers:
            if t in CRYPTO_REMOVE and t not in ETH_ALLOW:
                continue
            out.append(t)
        return out

    return tickers

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--coin", choices=["none", "eth", "etc"], default="none",
                    help="none=remove all crypto tickers, eth/etc=keep only ETH allowlist")
    args = ap.parse_args()

    sources = resolve_sources()
    merged: list[str] = []
    counts = []
    for s in sources:
        t = read_tickers(s)
        counts.append((s, len(t)))
        merged = merge_unique_ordered(merged, t)

    before = len(merged)
    merged = apply_coin_filter(merged, args.coin)
    after = len(merged)

    out = Path("tickers.txt")
    out.write_text("\n".join(merged) + ("\n" if merged else ""), encoding="utf-8")

    print(f"Saved: {out}  (count={len(merged)})")
    print("KST_NOW:", now_kst())
    print("coin_mode:", args.coin)
    print("removed_by_coin_filter:", before - after)
    print("Sources:")
    for s, n in counts:
        print(f" - {s}  ({n})")

if __name__ == "__main__":
    main()
