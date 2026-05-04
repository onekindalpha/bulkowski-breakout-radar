#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Yahoo (yfinance) premarket/regular/afterhours updater with:
- KST timestamp header
- grouped tickers (macro_watch_yahoo.txt / tickers_core.txt / tickers_leverage2x.txt) + fallback tickers.txt
- alias mapping for "labels" (SP, 500, 10Y, DOLLAR, OIL, GASOLINE...) -> real Yahoo symbols
- fast batch fetch via yf.download (one request for many symbols)
- debug CSV with yf_symbol, bar timestamp, staleness
- persistent skip-cache for repeatedly failing symbols (optional, default ON)

Usage:
  python update_premarket_yf_auto_fast.py
  python update_premarket_yf_auto_fast.py --quiet
  python update_premarket_yf_auto_fast.py --refresh-bad   # ignore bad cache this run
  python update_premarket_yf_auto_fast.py --no-bad-cache  # don't read/write bad cache
"""
from __future__ import annotations

import re, json, sys, contextlib, io, logging
from pathlib import Path
from dataclasses import dataclass
from datetime import datetime, timedelta

try:
    from zoneinfo import ZoneInfo
except Exception:
    ZoneInfo = None

import pandas as pd
import yfinance as yf

# -----------------------
# Config
# -----------------------
GROUP_FILES = [
    "macro_watch_yahoo.txt",
    "tickers_core.txt",
    "tickers_leverage2x.txt",
]
FALLBACK_FILE = "tickers.txt"

OUT_CSV = "premarket_auto.csv"
OUT_DEBUG = "premarket_auto_debug.csv"

BAD_CACHE_PATH = Path("yf_bad_symbols.json")
BAD_TTL_DAYS = 7  # skip repeatedly failing symbols for N days

# Labels / junk tokens to drop (they are not real Yahoo symbols)
DROP_TOKENS = {
    "-", "ETF", "FUTURES", "AIRLINE", "AIRLINES", "INDEX", "INDICES",
    "SP", "NASDAQ", "RATES", "US", "YIELD", "10Y", "100", "500", "DOLLAR",
    "OIL", "GAS", "GASOLINE", "RBOB", "NATURAL", "BRENT", "WTI",
}

# Label -> real Yahoo symbol mapping (keeps your display label but fetches actual symbol)
ALIASES = {
    # Macro / commodities
    "WTI": "CL=F",
    "OIL": "CL=F",         # or "USO" if you prefer ETF
    "BRENT": "BZ=F",
    "GAS": "NG=F",
    "NATURAL": "NG=F",
    "GASOLINE": "RB=F",    # RBOB gasoline futures
    "RBOB": "RB=F",
    "VIX": "^VIX",
    # Markets
    "SP": "^GSPC",
    "500": "^GSPC",
    "NASDAQ": "^IXIC",     # or "^NDX"
    "100": "^NDX",
    # Rates / dollar
    "10Y": "^TNX",
    "YIELD": "^TNX",
    "RATES": "^TNX",
    "DOLLAR": "DX-Y.NYB",
    # "US" ambiguous -> treat as S&P500
    "US": "^GSPC",
}

# Valid-ish Yahoo symbol pattern (includes ^ and =F and . and -)
YF_SYMBOL_RE = re.compile(r"^[A-Z0-9\^][A-Z0-9\^=\.\-\/]{0,14}$")

# -----------------------
# Helpers
# -----------------------
def tz(name: str):
    if ZoneInfo is None:
        return None
    return ZoneInfo(name)

def now_kst_str() -> str:
    if ZoneInfo is None:
        return (datetime.utcnow() + timedelta(hours=9)).strftime("%Y-%m-%d %H:%M:%S KST")
    return datetime.now(tz("Asia/Seoul")).strftime("%Y-%m-%d %H:%M:%S KST")

def now_et() -> datetime:
    if ZoneInfo is None:
        # crude fallback: ET ~= UTC-5 (DST not handled without zoneinfo)
        return datetime.utcnow() - timedelta(hours=5)
    return datetime.now(tz("America/New_York"))

def session_mode(et: datetime) -> str:
    # US equities session boundaries (ET)
    t = et.hour * 60 + et.minute
    if 4*60 <= t < 9*60 + 30:
        return "premarket"
    if 9*60 + 30 <= t < 16*60:
        return "regular"
    if 16*60 <= t < 20*60:
        return "afterhours"
    return "closed"

@dataclass
class Item:
    group: str
    display: str   # what you write in CSV (label or ticker)
    yf_symbol: str # what yfinance fetches

def load_tokens_from_file(path: Path) -> list[str]:
    raw = path.read_text(encoding="utf-8", errors="ignore").splitlines()
    tokens: list[str] = []
    for line in raw:
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = re.split(r"[\s,;]+", line)
        for p in parts:
            p = p.strip()
            if p:
                tokens.append(p)
    return tokens

def normalize_token(t: str) -> str:
    t = t.strip()
    if t.startswith("$"):
        t = t[1:]
    return t.strip().upper()

def load_grouped_items() -> list[Item]:
    items: list[Item] = []
    any_group = False
    for fn in GROUP_FILES:
        p = Path(fn)
        if not p.exists():
            continue
        any_group = True
        group = p.stem
        for tok in load_tokens_from_file(p):
            disp = normalize_token(tok)
            if not disp:
                continue

            if disp in ALIASES:
                items.append(Item(group=group, display=disp, yf_symbol=ALIASES[disp]))
                continue

            if disp in DROP_TOKENS or disp.isdigit():
                continue

            if YF_SYMBOL_RE.fullmatch(disp):
                items.append(Item(group=group, display=disp, yf_symbol=disp))
                continue

            # ignore unknown junk
            continue

    if not any_group:
        p = Path(FALLBACK_FILE)
        if not p.exists():
            return []
        group = "tickers"
        for tok in load_tokens_from_file(p):
            disp = normalize_token(tok)
            if not disp:
                continue
            if disp in ALIASES:
                items.append(Item(group=group, display=disp, yf_symbol=ALIASES[disp]))
                continue
            if disp in DROP_TOKENS or disp.isdigit():
                continue
            if YF_SYMBOL_RE.fullmatch(disp):
                items.append(Item(group=group, display=disp, yf_symbol=disp))
    return items

def load_bad_cache() -> dict[str, str]:
    if not BAD_CACHE_PATH.exists():
        return {}
    try:
        return json.loads(BAD_CACHE_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}

def save_bad_cache(cache: dict[str, str]) -> None:
    try:
        BAD_CACHE_PATH.write_text(json.dumps(cache, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:
        pass

def is_bad_recent(symbol: str, cache: dict[str, str], ttl_days: int) -> bool:
    ts = cache.get(symbol)
    if not ts:
        return False
    try:
        dt = datetime.fromisoformat(ts.replace("Z",""))
        return (datetime.utcnow() - dt) < timedelta(days=ttl_days)
    except Exception:
        return False

def _silence_yfinance():
    logging.getLogger("yfinance").setLevel(logging.ERROR)
    logging.getLogger("urllib3").setLevel(logging.ERROR)

def fetch_last_prices(symbols: list[str], prepost: bool=True) -> tuple[dict[str, float|None], dict[str, dict]]:
    prices: dict[str, float|None] = {s: None for s in symbols}
    meta: dict[str, dict] = {s: {} for s in symbols}
    if not symbols:
        return prices, meta

    _silence_yfinance()

    buf_out, buf_err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(buf_out), contextlib.redirect_stderr(buf_err):
        df = yf.download(
            tickers=" ".join(symbols),
            period="5d",
            interval="1m",
            prepost=prepost,
            group_by="ticker",
            threads=True,
            auto_adjust=False,
            progress=False,
        )

    if df is None or len(df) == 0:
        for s in symbols:
            meta[s] = {"note": "yf_empty"}
        return prices, meta

    idx = df.index
    if getattr(idx, "tz", None) is None:
        df = df.tz_localize("UTC")
    else:
        df = df.tz_convert("UTC")

    et_tz = tz("America/New_York")
    now_utc = datetime.utcnow().replace(tzinfo=tz("UTC") if ZoneInfo else None)

    for s in symbols:
        try:
            if isinstance(df.columns, pd.MultiIndex):
                if s in df.columns.get_level_values(0):
                    sub = df[s]
                elif s in df.columns.get_level_values(-1):
                    sub = df.xs(s, axis=1, level=-1)
                else:
                    meta[s] = {"note": "no_columns"}
                    continue
                close = sub.get("Close")
            else:
                close = df.get("Close") if "Close" in df.columns else None

            if close is None:
                meta[s] = {"note": "no_close"}
                continue

            close = close.dropna()
            if close.empty:
                meta[s] = {"note": "no_bars"}
                continue

            last_ts = close.index[-1]
            last_px = float(close.iloc[-1])
            prices[s] = last_px

            if ZoneInfo is not None:
                bar_ts_et = last_ts.tz_convert(et_tz)
                stale_min = int((now_utc - last_ts).total_seconds() // 60)
                meta[s] = {
                    "bar_ts_utc": last_ts.isoformat(),
                    "bar_ts_et": bar_ts_et.isoformat(),
                    "stale_min": stale_min,
                    "note": "",
                }
            else:
                meta[s] = {
                    "bar_ts_utc": str(last_ts),
                    "bar_ts_et": str(last_ts),
                    "stale_min": "",
                    "note": "",
                }
        except Exception as e:
            meta[s] = {"note": f"exc:{type(e).__name__}"}
    return prices, meta

def main():
    refresh_bad = ("--refresh-bad" in sys.argv)
    no_bad_cache = ("--no-bad-cache" in sys.argv)
    quiet = ("--quiet" in sys.argv)

    items = load_grouped_items()
    if not items:
        raise SystemExit("No tickers found (group files or tickers.txt).")

    bad_cache = {} if (no_bad_cache or refresh_bad) else load_bad_cache()

    unique_symbols: list[str] = []
    for it in items:
        if not refresh_bad and not no_bad_cache and is_bad_recent(it.yf_symbol, bad_cache, BAD_TTL_DAYS):
            continue
        if it.yf_symbol not in unique_symbols:
            unique_symbols.append(it.yf_symbol)

    et = now_et()
    mode = session_mode(et)
    kst = now_kst_str()

    if not quiet:
        print(f"Mode: {mode} | saved_at_kr={kst} | symbols={len(unique_symbols)} | items={len(items)}")

    prices, meta = fetch_last_prices(unique_symbols, prepost=True)

    failed = {s for s in unique_symbols if prices.get(s) is None}
    if not no_bad_cache and failed:
        now_iso = datetime.utcnow().replace(microsecond=0).isoformat() + "Z"
        for s in failed:
            bad_cache[s] = now_iso
        save_bad_cache(bad_cache)

    grouped_order: list[str] = []
    grouped_map: dict[str, list[Item]] = {}
    for it in items:
        grouped_map.setdefault(it.group, []).append(it)
        if it.group not in grouped_order:
            grouped_order.append(it.group)

    with open(OUT_CSV, "w", newline="", encoding="utf-8") as f:
        f.write(f"# saved_at_kr,{kst}\n")
        f.write(f"# mode,{mode}\n")
        for g in grouped_order:
            f.write(f"# group: {g}\n")
            f.write("ticker,premarket\n")
            for it in grouped_map[g]:
                px = prices.get(it.yf_symbol)
                if px is None:
                    f.write(f"{it.display},\n")
                else:
                    f.write(f"{it.display},{round(px, 2)}\n")

    with open(OUT_DEBUG, "w", newline="", encoding="utf-8") as f:
        f.write(f"# saved_at_kr,{kst}\n")
        f.write(f"# mode,{mode}\n")
        f.write("group,ticker,yf_symbol,premarket,bar_ts_et,bar_ts_utc,stale_min,note\n")
        for g in grouped_order:
            for it in grouped_map[g]:
                px = prices.get(it.yf_symbol)
                m = meta.get(it.yf_symbol, {})
                bar_et = m.get("bar_ts_et", "")
                bar_utc = m.get("bar_ts_utc", "")
                stale = m.get("stale_min", "")
                note = m.get("note", "")
                if px is None:
                    f.write(f"{g},{it.display},{it.yf_symbol},,{bar_et},{bar_utc},{stale},{note or 'no_data'}\n")
                else:
                    f.write(f"{g},{it.display},{it.yf_symbol},{round(px, 4)},{bar_et},{bar_utc},{stale},{note}\n")

    out_path = Path(OUT_CSV).resolve()
    dbg_path = Path(OUT_DEBUG).resolve()
    missing_disp = sorted({it.display for it in items if prices.get(it.yf_symbol) is None})

    if not quiet:
        print(f"Saved {out_path} and {dbg_path}")
        print(f"Total items: {len(items)} | Unique symbols fetched: {len(unique_symbols)} | Missing items: {len(missing_disp)}")
        if missing_disp:
            print("Missing tickers (display labels):", ", ".join(missing_disp))
        if not no_bad_cache:
            print(f"Bad-cache: {BAD_CACHE_PATH.resolve()} (TTL {BAD_TTL_DAYS}d)")

if __name__ == "__main__":
    main()
