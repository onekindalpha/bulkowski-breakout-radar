#!/usr/bin/env python3
"""make_premarket_manual_5_260316.py

Goal: restore *legacy* ergonomics without touching originals.

- Automatically uses the latest candidates list so you don't type tickers.
- Generates a timestamped candidates file from Bulkowski stdout if none exists.
- Keeps a stable pointer file `candidates.txt` to the latest candidates_*.txt.

Only writes/reads candidates*.txt and candidates.txt (no extra bulkowski_top10.* artifacts).
Outputs: premarket_manual.csv (same as original pipeline).

Usage (recommended legacy flow):
  python update_premarket_yf_auto_debug.py
  python bulkowski_scan_from_debugcsv.py
  python make_premarket_manual_5_260316.py

Or just run this; it will auto-generate candidates from bulkowski if needed.
"""

from __future__ import annotations

import argparse
import os
import re
import subprocess
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd

from pipeline_config import (
    load_default_groups,
    union_ordered,
    now_kst_str,
    read_tickers_from_file,
    write_header_lines,
)

KST = ZoneInfo("Asia/Seoul")

CAND_POINTER = Path("candidates.txt")
CAND_GLOB = "candidates_*.txt"
BULKOWSKI_SCRIPT = Path("bulkowski_scan_from_debugcsv.py")


def _ts() -> str:
    return datetime.now(KST).strftime("%Y%m%d_%H%M%S_KST")


def _safe_symlink(target: Path, link: Path) -> None:
    """Create/replace symlink (or fallback to copy on platforms that block symlinks)."""
    try:
        if link.exists() or link.is_symlink():
            link.unlink()
        os.symlink(target.name, link)  # relative link in same folder
    except Exception:
        # fallback: write the content (keeps behavior deterministic)
        try:
            link.write_text(target.read_text(encoding="utf-8", errors="ignore"), encoding="utf-8")
        except Exception:
            pass


def _parse_bulkowski_stdout_to_tickers(text: str, limit: int = 10) -> list[str]:
    """Parse the first token per line from bulkowski printed table."""
    out: list[str] = []
    for line in text.splitlines():
        s = line.strip()
        if not s:
            continue
        if s.lower().startswith("symbol"):
            continue
        tok = re.split(r"\s+", s)[0].strip().upper()
        if not tok:
            continue
        # basic sanity: tickers are usually short-ish; keep legacy flexible but skip obvious junk
        if tok.startswith("#"):
            continue
        if tok not in out:
            out.append(tok)
        if len(out) >= limit:
            break
    return out


def _latest_candidates_file() -> Path | None:
    cands = sorted(Path(".").glob(CAND_GLOB), key=lambda p: p.stat().st_mtime, reverse=True)
    return cands[0] if cands else None


def _load_latest_candidates() -> tuple[str, list[str]]:
    """Return (source_name, tickers)."""
    # 1) pointer file if exists and has tickers
    if CAND_POINTER.exists():
        t = read_tickers_from_file(str(CAND_POINTER))
        if t:
            return ("candidates.txt", t)

    # 2) latest timestamped candidates file
    latest = _latest_candidates_file()
    if latest:
        t = read_tickers_from_file(str(latest))
        if t:
            # refresh pointer for convenience
            _safe_symlink(latest, CAND_POINTER)
            return (latest.name, t)

    return ("", [])


def _ensure_candidates_from_bulkowski(limit: int = 10) -> tuple[str, list[str]]:
    """If no candidates available, run bulkowski and generate candidates_*.txt + candidates.txt."""
    if not BULKOWSKI_SCRIPT.exists():
        return ("", [])

    try:
        out = subprocess.check_output(["python", str(BULKOWSKI_SCRIPT)], text=True)
    except Exception:
        return ("", [])

    tickers = _parse_bulkowski_stdout_to_tickers(out, limit=limit)
    if not tickers:
        return ("", [])

    ts = _ts()
    fname = Path(f"candidates_{ts}.txt")
    lines = [f"# generated_at={ts}"] + tickers
    fname.write_text("\n".join(lines) + "\n", encoding="utf-8")
    _safe_symlink(fname, CAND_POINTER)
    return (fname.name, tickers)


def _prompt_float(prompt: str) -> float | None:
    s = input(prompt).strip()
    if s == "":
        return None
    try:
        return float(s.replace(",", ""))
    except Exception:
        return None


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--max", type=int, default=10, help="max tickers to request (default 10)")
    ap.add_argument(
        "--regen",
        action="store_true",
        help="force regenerate candidates from bulkowski stdout (writes candidates_*.txt)",
    )
    args = ap.parse_args()

    groups = load_default_groups()
    union = union_ordered(groups)
    union_set = set(union)

    print(f"KST_NOW: {now_kst_str()}")
    print(f"UNION_TOTAL(from txts): {len(union)}")

    src, cands = ("", [])
    if not args.regen:
        src, cands = _load_latest_candidates()

    if args.regen or not cands:
        src2, cands2 = _ensure_candidates_from_bulkowski(limit=args.max)
        if cands2:
            src, cands = src2, cands2

    rows: list[dict] = []

    if cands:
        cands = cands[: args.max]
        print(f"\nUSING {src}: {len(cands)} tickers")
        print("Enter Samsung '장전/프리' last price for each ticker.")
        print("Leave price empty to SKIP that ticker.\n")

        for i, t in enumerate(cands, 1):
            px = _prompt_float(f"[{i}/{len(cands)}] {t} Price: ")
            if px is None:
                continue
            rows.append(
                {
                    "ticker": t,
                    "premarket": float(px),
                    "entered_at_kr": datetime.now(KST).strftime("%Y-%m-%d %H:%M:%S"),
                    "source": "manual",
                }
            )

    else:
        print("\n[WARN] No candidates found and bulkowski auto-gen failed.")
        print("Enter up to N manual prices (ticker + price). Leave ticker empty to finish.\n")
        for i in range(1, args.max + 1):
            t = input(f"[{i}/{args.max}] Ticker: ").strip().upper()
            if not t:
                break
            if t not in union_set:
                print(f"  - '{t}' not in your txt-union (still allowed).")
            px = _prompt_float("  Price: ")
            if px is None:
                continue
            rows.append(
                {
                    "ticker": t,
                    "premarket": float(px),
                    "entered_at_kr": datetime.now(KST).strftime("%Y-%m-%d %H:%M:%S"),
                    "source": "manual",
                }
            )

    df = pd.DataFrame(rows, columns=["ticker", "premarket", "entered_at_kr", "source"])
    if not df.empty:
        df = df.drop_duplicates(subset=["ticker"], keep="last").sort_values("ticker")

    header = [
        f"saved_at_kr,{now_kst_str()}",
        f"count_rows,{len(df)}",
        f"candidates_source,{src or 'fallback_manual'}",
    ]
    body = df.to_csv(index=False)
    write_header_lines("premarket_manual.csv", header, body)
    print(f"\nSaved premarket_manual.csv ({len(df)} rows).")


if __name__ == "__main__":
    main()
