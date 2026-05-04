#!/usr/bin/env python3
from __future__ import annotations

import argparse
from pathlib import Path
from typing import Iterable

try:
    import pandas as pd
    from pykrx import stock
except Exception as e:  # pragma: no cover
    raise SystemExit(
        "This script requires 'pykrx' and 'pandas'. Install with: pip install pykrx pandas\n"
        f"Original import error: {e}"
    )


def _market_suffix_lookup() -> dict[str, str]:
    out: dict[str, str] = {}
    for t in stock.get_market_ticker_list(market="KOSPI"):
        out[str(t).zfill(6)] = ".KS"
    for t in stock.get_market_ticker_list(market="KOSDAQ"):
        out[str(t).zfill(6)] = ".KQ"
    return out


def _find_index_ticker(keyword: str, market: str) -> str:
    pairs = []
    for idx in stock.get_index_ticker_list(market=market):
        name = stock.get_index_ticker_name(idx)
        pairs.append((idx, name))
        if keyword in name:
            return idx
    raise RuntimeError(f"Could not find index containing '{keyword}' in market={market}. Seen: {pairs[:10]}")


def _format_members(index_name: str, codes: Iterable[str], suffix_map: dict[str, str]) -> list[str]:
    lines: list[str] = []
    lines.append(f"# {index_name}")
    for code in sorted({str(c).zfill(6) for c in codes}):
        suffix = suffix_map.get(code)
        if not suffix:
            continue
        name = stock.get_market_ticker_name(code)
        lines.append(f"{code}{suffix}  # {name}")
    lines.append("")
    return lines


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="kr_core_liquid.txt")
    ap.add_argument("--csv", default="kr_core_liquid.csv")
    args = ap.parse_args()

    suffix_map = _market_suffix_lookup()
    kospi200 = _find_index_ticker("코스피 200", "KOSPI")
    kosdaq150 = _find_index_ticker("코스닥 150", "KOSDAQ")

    kospi200_members = stock.get_index_portfolio_deposit_file(kospi200)
    kosdaq150_members = stock.get_index_portfolio_deposit_file(kosdaq150)

    rows = []
    for code in kospi200_members:
        code = str(code).zfill(6)
        if code in suffix_map:
            rows.append({
                "ticker": code + suffix_map[code],
                "name": stock.get_market_ticker_name(code),
                "source_index": stock.get_index_ticker_name(kospi200),
            })
    for code in kosdaq150_members:
        code = str(code).zfill(6)
        if code in suffix_map:
            rows.append({
                "ticker": code + suffix_map[code],
                "name": stock.get_market_ticker_name(code),
                "source_index": stock.get_index_ticker_name(kosdaq150),
            })

    df = pd.DataFrame(rows).drop_duplicates(subset=["ticker"]).sort_values("ticker").reset_index(drop=True)
    df.to_csv(args.csv, index=False, encoding="utf-8-sig")

    out_lines = [
        "# kr_core_liquid.txt",
        "# Auto-generated from current KOSPI200 + KOSDAQ150 constituents via pykrx.",
        "# Edit this file only if you want to intentionally override the generated core universe.",
        "",
    ]
    out_lines.extend(_format_members(stock.get_index_ticker_name(kospi200), kospi200_members, suffix_map))
    out_lines.extend(_format_members(stock.get_index_ticker_name(kosdaq150), kosdaq150_members, suffix_map))

    Path(args.out).write_text("\n".join(out_lines).rstrip() + "\n", encoding="utf-8")

    print(f"Saved: {args.out} ({len(df)} tickers)")
    print(f"Saved: {args.csv}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
