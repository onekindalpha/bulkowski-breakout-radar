#!/usr/bin/env python3
"""
update_premarket_yf_auto_debug_korea_v2.py

Korea auto price updater.

Patch:
- Automatically includes these korea sources if present:
    kr_manual_conviction.txt
    kr_tactical_leverage.txt
    kr_top_groups_auto_mixed.txt
    kr_foreign_netbuy_auto.txt
- Still reads legacy korea files too
- Outputs:
    premarket_auto_korea.csv
    premarket_auto_debug_korea.csv
"""
from __future__ import annotations
import re
import time
from pathlib import Path
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import contextlib
import io
import logging
import warnings

import yfinance as yf

TZ_KST = ZoneInfo("Asia/Seoul")
TZ_ET = ZoneInfo("America/New_York")

warnings.filterwarnings("ignore")
for name in ("yfinance", "urllib3", "requests"):
    logging.getLogger(name).setLevel(logging.CRITICAL)

GROUP_FILES = [
    "kr_manual_conviction.txt",
    "kr_tactical_leverage.txt",
    "kr_top_groups_auto_mixed.txt",
    "kr_foreign_netbuy_auto.txt",
    "macro_watch_yahoo_korea.txt",
    "tickers_core_korea.txt",
    "tickers_leverage2x_korea.txt",
    "finviz_manual_korea.txt",
]

TICKER_RE = re.compile(r"^(?:\d{6}\.(?:KS|KQ)|[A-Z0-9\.\-\=\^]+)$")

@contextlib.contextmanager
def _suppress_all_output():
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
        yield

def load_tickers(path: str) -> list[str]:
    p = Path(path)
    if not p.exists():
        return []
    out = []
    seen = set()
    for line in p.read_text(encoding="utf-8", errors="ignore").splitlines():
        s = line.strip()
        if not s or s.startswith("#"):
            continue
        if "#" in s:
            s = s.split("#", 1)[0].strip()
        for tok in re.split(r"[\s,;]+", s):
            t = tok.strip().upper()
            if not t:
                continue
            if re.fullmatch(r"\d{6}", t):
                t = t + ".KS"
            if TICKER_RE.match(t) and t not in seen:
                seen.add(t)
                out.append(t)
    return out

def resolve_grouped() -> dict[str, list[str]]:
    out = {}
    for fname in GROUP_FILES:
        if Path(fname).exists():
            ticks = load_tickers(fname)
            if ticks:
                out[Path(fname).stem] = ticks
    return out

def fetch_last_price(sym: str):
    try:
        with _suppress_all_output():
            tk = yf.Ticker(sym)
            df = tk.history(period="5d", interval="5m", auto_adjust=False, prepost=True)
    except Exception as e:
        return None, "", None, f"{type(e).__name__}: {e}"
    if df is None or df.empty or "Close" not in df.columns:
        return None, "", None, "no_data"
    close = df["Close"].dropna()
    if close.empty:
        return None, "", None, "no_data"
    ts = df.index[-1]
    try:
        ts = ts.tz_convert(TZ_KST)
    except Exception:
        pass
    px = float(close.iloc[-1])
    if not (px > 0):
        return None, "", None, "no_data"
    return px, "yf_close(5m)", ts, ""

def main():
    grouped = resolve_grouped()
    if not grouped:
        raise SystemExit("No korea group files found.")

    now_kst = datetime.now(TZ_KST).strftime("%Y-%m-%d %H:%M:%S KST")
    print(f"Mode: korea_last_price | saved_at_kr={now_kst}")

    pairs = []
    seen = set()
    for g, ticks in grouped.items():
        for t in ticks:
            if (g, t) not in seen:
                seen.add((g, t))
                pairs.append((g, t))

    prices = {}
    meta = {}
    total = len(pairs)
    for i, (g, sym) in enumerate(pairs, 1):
        px, src, ts, err = fetch_last_price(sym)
        if px is not None:
            prices[sym] = px
        meta[(g, sym)] = {
            "src": src,
            "ts_kst": ts.isoformat() if ts is not None else "",
            "error": err
        }
        time.sleep(0.03)
        if i in (1, total) or (i % 25 == 0):
            print(f"... {i}/{total} processed")

    with open("premarket_auto_korea.csv", "w", encoding="utf-8") as f:
        f.write(f"# saved_at_kr,{now_kst}\n")
        f.write("# provider,yahoo(yfinance)\n")
        for g, ticks in grouped.items():
            f.write(f"# group: {g}\n")
            f.write("ticker,premarket\n")
            for t in ticks:
                val = prices.get(t)
                f.write(f"{t},{'' if val is None else round(val, 2)}\n")

    with open("premarket_auto_debug_korea.csv", "w", encoding="utf-8") as f:
        f.write(f"# saved_at_kr,{now_kst}\n")
        f.write("# provider,yahoo(yfinance)\n")
        f.write("group,ticker,premarket,src,ts_kst,error\n")
        for g, ticks in grouped.items():
            for t in ticks:
                val = prices.get(t)
                m = meta.get((g, t), {})
                f.write(f"{g},{t},{'' if val is None else round(val,2)},{m.get('src','')},{m.get('ts_kst','')},{m.get('error','')}\n")

    print(f"Saved {Path('premarket_auto_korea.csv').resolve()} and {Path('premarket_auto_debug_korea.csv').resolve()}")

if __name__ == "__main__":
    main()
