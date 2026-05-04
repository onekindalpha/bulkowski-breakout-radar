import os
import re
import math
import csv
import requests
from pathlib import Path

BASE = "https://data.alpaca.markets"
HEADERS = {
    "APCA-API-KEY-ID": os.getenv("APCA_API_KEY_ID", ""),
    "APCA-API-SECRET-KEY": os.getenv("APCA_API_SECRET_KEY", ""),
    "Accept": "application/json",
}

def load_tickers(path="tickers.txt"):
    text = Path(path).read_text(encoding="utf-8", errors="ignore").strip()
    tokens = re.split(r"[\s,;]+", text)
    out, seen = [], set()
    for t in tokens:
        t = t.strip().upper()
        if not t or t.startswith("#"):
            continue
        t = re.sub(r"[^A-Z0-9\.\-]", "", t)  # MP% 같은 오타 방지
        if t and t not in seen:
            seen.add(t)
            out.append(t)
    return out

def chunk(lst, n):
    for i in range(0, len(lst), n):
        yield lst[i:i+n]

def get_latest_trades(symbols):
    # https://data.alpaca.markets/v2/stocks/trades/latest?symbols=AAPL,TSLA&feed=iex
    url = f"{BASE}/v2/stocks/trades/latest"
    params = {"symbols": ",".join(symbols), "feed": "iex"}
    r = requests.get(url, headers=HEADERS, params=params, timeout=15)
    r.raise_for_status()
    data = r.json()
    return data.get("trades", {})  # dict: {SYM: {p: price, ...}}

def get_latest_quotes(symbols):
    # https://data.alpaca.markets/v2/stocks/quotes/latest?symbols=AAPL,TSLA&feed=iex
    url = f"{BASE}/v2/stocks/quotes/latest"
    params = {"symbols": ",".join(symbols), "feed": "iex"}
    r = requests.get(url, headers=HEADERS, params=params, timeout=15)
    r.raise_for_status()
    data = r.json()
    return data.get("quotes", {})  # dict: {SYM: {bp:..., ap:...}}

def midpoint(q):
    bp = q.get("bp")
    ap = q.get("ap")
    if bp is not None and ap is not None and bp > 0 and ap > 0:
        return (bp + ap) / 2.0
    if bp is not None and bp > 0:
        return float(bp)
    if ap is not None and ap > 0:
        return float(ap)
    return None

def main():
    if not HEADERS["APCA-API-KEY-ID"] or not HEADERS["APCA-API-SECRET-KEY"]:
        raise SystemExit("Set APCA_API_KEY_ID and APCA_API_SECRET_KEY env vars first.")

    tickers = load_tickers("tickers.txt")
    if not tickers:
        raise SystemExit("No tickers found in tickers.txt")

    prices = {}
    missing = set(tickers)

    # Alpaca는 한번에 많은 심볼도 되지만 URL 길이/안정성 위해 배치
    for syms in chunk(tickers, 100):
        trades = get_latest_trades(syms)
        for sym, tr in trades.items():
            p = tr.get("p")
            if p is not None and p > 0:
                prices[sym] = float(p)
                missing.discard(sym)

    # 체결이 없으면 quote로 보완
    if missing:
        for syms in chunk(sorted(missing), 100):
            quotes = get_latest_quotes(syms)
            for sym, q in quotes.items():
                p = midpoint(q)
                if p is not None and p > 0:
                    prices[sym] = float(p)
                    missing.discard(sym)

    # CSV 저장
    out_path = Path("premarket.csv")
    with out_path.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["ticker", "premarket"])
        for sym in sorted(prices.keys()):
            w.writerow([sym, round(prices[sym], 2)])

    print(f"Saved premarket.csv ({len(prices)} tickers). Missing: {len(missing)}")
    if missing:
        print("Missing symbols (no trade/quote from IEX right now):", ", ".join(sorted(missing)))

if __name__ == "__main__":
    main()