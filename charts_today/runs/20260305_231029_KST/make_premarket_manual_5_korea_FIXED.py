import json
#!/usr/bin/env python3
"""
make_premarket_manual_5_korea.py (replacement)

Goal
- If candidates.txt exists: DO NOT ask for tickers.
  Ask ONLY prices for each candidate ticker in order.
- If candidates.txt does not exist: fallback to manual ticker+price input (up to N).

No more tickers.txt warnings.
Ticker universe is taken from pipeline_config (macro/core/2x/finviz_manual),
but we allow any ticker you type in fallback mode.

Outputs
- premarket_manual.csv  (OVERWRITES each run)
  columns: ticker,premarket,entered_at_kr,source

Usage
  python make_premarket_manual_5_korea.py
  python make_premarket_manual_5_korea.py --max 10
"""

from __future__ import annotations

import argparse
from datetime import datetime
from zoneinfo import ZoneInfo
from pathlib import Path

import pandas as pd

from pipeline_config_korea import load_default_groups, union_ordered, now_kst_str, read_tickers_from_file, write_header_lines

KST = ZoneInfo("Asia/Seoul")



# --- Name lookup helpers (best-effort; cached) ---
NAME_CACHE_PATH = Path(".name_cache_korea.json")

def _load_name_cache() -> dict:
    try:
        return json.loads(NAME_CACHE_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}

def _save_name_cache(cache: dict) -> None:
    try:
        NAME_CACHE_PATH.write_text(json.dumps(cache, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:
        pass

def get_display_name(ticker: str, cache: dict) -> str:
    """
    Returns a human-readable name for ticker (company/ETF), if available.
    Uses local cache first, then yfinance as a fallback.
    """
    t = str(ticker).strip()
    if not t:
        return ""
    if t in cache:
        return str(cache[t] or "").strip()

    # last resort: yfinance lookup (can be slow)
    try:
        import yfinance as yf
        info = yf.Ticker(t).info or {}
        name = info.get("shortName") or info.get("longName") or info.get("name") or ""
        name = str(name).strip()
        if name:
            cache[t] = name
            _save_name_cache(cache)
            return name
    except Exception:
        return ""
    return ""
# --- end name helpers ---

def prompt_float(prompt: str) -> float | None:
    s = input(prompt).strip()
    if s == "":
        return None
    try:
        return float(s.replace(",", ""))
    except Exception:
        return None


def main():
    name_cache = _load_name_cache()
    ap = argparse.ArgumentParser()
    ap.add_argument("--max", type=int, default=10, help="max tickers in fallback mode (default 10)")
    args = ap.parse_args()

    # union from all txt groups (for reference / sanity only)
    groups = load_default_groups()
    union = union_ordered(groups)
    union_set = set(union)

    candidates_path = Path("candidates_korea.txt")
    rows = []

    print(f"KST_NOW: {now_kst_str()}")
    print(f"UNION_TOTAL(from txts): {len(union)}")

    if candidates_path.exists():
        cands = read_tickers_from_file(str(candidates_path))
        print(f"USING candidates.txt: {len(cands)} tickers")
        print("Enter Samsung '장전/프리' last price for each ticker.")
        print("Leave price empty to SKIP that ticker.\n")

        for i, t in enumerate(cands, 1):
            px = None
            while px is None:
                name = get_display_name(t, name_cache)
                prompt = f"[{i}/{len(cands)}] {t} ({name}) Price: " if name else f"[{i}/{len(cands)}] {t} Price: "
                px = prompt_float(prompt)
                # allow skip
                if px is None:
                    # user entered blank => skip
                    break
            if px is None:
                continue
            rows.append({
                "ticker": t,
                "premarket": float(px),
                "entered_at_kr": datetime.now(KST).strftime("%Y-%m-%d %H:%M:%S"),
                "source": "manual",
            })

    else:
        print("\nEnter up to N manual prices (ticker + price).")
        print("Tip: use Samsung Securities '장전/프리' current price (Last).")
        print("Leave ticker empty to finish.\n")

        for i in range(1, args.max + 1):
            t = input(f"[{i}/{args.max}] Ticker: ").strip().upper()
            if not t:
                break
            if t not in union_set:
                print(f"  - '{t}' not in your txt-union (still allowed).")
            px = None
            while px is None:
                px = prompt_float("  Price: ")
                if px is None:
                    print("  (invalid price; try again or press Enter to skip)")
                    # if user pressed Enter, px None but we want skip; treat as skip
                    break
            if px is None:
                continue
            rows.append({
                "ticker": t,
                "premarket": float(px),
                "entered_at_kr": datetime.now(KST).strftime("%Y-%m-%d %H:%M:%S"),
                "source": "manual",
            })

    df = pd.DataFrame(rows)
    # overwrite each run; keep deterministic order
    df = df.drop_duplicates(subset=["ticker"], keep="last").sort_values("ticker")

    header = [
        f"saved_at_kr,{now_kst_str()}",
        f"count_rows,{len(df)}",
        "note,overwrites each run; candidates.txt mode asks price only",
    ]
    body = df.to_csv(index=False)
    write_header_lines("premarket_manual_korea.csv", header, body)

    print(f"\nSaved premarket_manual_korea.csv ({len(df)} rows).")


if __name__ == "__main__":
    main()