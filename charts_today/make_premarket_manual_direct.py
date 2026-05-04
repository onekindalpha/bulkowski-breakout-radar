#!/usr/bin/env python3
from __future__ import annotations

import argparse
import re
import subprocess
from datetime import datetime
from zoneinfo import ZoneInfo

import pandas as pd

from pipeline_config import now_kst_str, write_header_lines

KST = ZoneInfo("Asia/Seoul")


def parse_bulkowski_stdout_to_tickers(text: str, limit: int = 10) -> list[str]:
    out: list[str] = []
    for line in text.splitlines():
        s = line.strip()
        if not s:
            continue
        if s.lower().startswith("symbol"):
            continue
        tok = re.split(r"\s+", s)[0].strip().upper()
        if not tok or tok.startswith("#"):
            continue
        if tok not in out:
            out.append(tok)
        if len(out) >= limit:
            break
    return out


def prompt_float(prompt: str) -> float | None:
    s = input(prompt).strip()
    if s == "":
        return None
    try:
        return float(s.replace(",", ""))
    except Exception:
        print("  (invalid price; press Enter to skip)")
        return None


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--max", type=int, default=10, help="number of bulkowski tickers to use")
    ap.add_argument("--bulkowski-cmd", type=str, default="python bulkowski_scan_from_debugcsv.py",
                    help="command to run bulkowski scan")
    args = ap.parse_args()

    print(f"KST_NOW: {now_kst_str()}")

    cp = subprocess.run(
        args.bulkowski_cmd,
        shell=True,
        text=True,
        capture_output=True,
        check=False,
    )

    if cp.returncode != 0:
        print(cp.stdout)
        print(cp.stderr)
        raise SystemExit(f"bulkowski scan failed with exit code {cp.returncode}")

    tickers = parse_bulkowski_stdout_to_tickers(cp.stdout, limit=args.max)

    if not tickers:
        raise SystemExit("No tickers parsed from bulkowski stdout.")

    print(f"\nUSING bulkowski stdout: {len(tickers)} tickers")
    print("Enter Samsung '장전/프리' last price for each ticker.")
    print("Leave price empty to SKIP that ticker.\n")

    rows: list[dict] = []
    for i, t in enumerate(tickers, 1):
        px = prompt_float(f"[{i}/{len(tickers)}] {t} Price: ")
        if px is None:
            continue
        rows.append({
            "ticker": t,
            "premarket": float(px),
            "entered_at_kr": datetime.now(KST).strftime("%Y-%m-%d %H:%M:%S"),
            "source": "manual",
        })

    if not rows:
        print("\nNo manual prices entered. Nothing to save.")
        return

    df = pd.DataFrame(rows, columns=["ticker", "premarket", "entered_at_kr", "source"])
    df = df.drop_duplicates(subset=["ticker"], keep="last").sort_values("ticker")

    header = [
        f"saved_at_kr,{now_kst_str()}",
        f"count_rows,{len(df)}",
        "candidates_source,bulkowski_stdout",
    ]
    body = df.to_csv(index=False)
    write_header_lines("premarket_manual.csv", header, body)

    print(f"\nSaved premarket_manual.csv ({len(df)} rows).")


if __name__ == "__main__":
    main()