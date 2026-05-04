# update_premarket_yf_auto.py
import re
import time
from pathlib import Path
from datetime import datetime, time as dtime
from zoneinfo import ZoneInfo

import pandas as pd
import yfinance as yf

TZ_ET = ZoneInfo("America/New_York")
PRE_START = dtime(4, 0)   # 04:00 ET
PRE_END   = dtime(9, 30)  # 09:30 ET
REGULAR_START = dtime(9, 30)  # 정규장 시작 09:30 ET

def load_tickers(path="tickers.txt"):
    text = Path(path).read_text(encoding="utf-8", errors="ignore")
    tokens = re.split(r"[\s,;]+", text.strip())
    out, seen = [], set()
    for tok in tokens:
        tok = tok.strip().upper()
        if not tok or tok.startswith("#"):
            continue
        tok = re.sub(r"[^A-Z0-9\.\-]", "", tok)  # MP% 같은 오타 방어
        if tok and tok not in seen:
            seen.add(tok)
            out.append(tok)
    return out

def _ensure_et_index(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return df
    if df.index.tz is None:
        df = df.tz_localize("UTC").tz_convert(TZ_ET)
    else:
        df = df.tz_convert(TZ_ET)
    return df

def fetch_last_price(ticker: str, retry=2, pause=0.6):
    """프리마켓(04:00~09:30 ET) 또는 정규장 시작 후 마지막가 반환."""
    for _ in range(retry + 1):
        try:
            tk = yf.Ticker(ticker)
            intr = tk.history(period="2d", interval="1m", prepost=True, auto_adjust=False)
            if intr is None or intr.empty:
                time.sleep(pause)
                continue

            intr = _ensure_et_index(intr)
            now_et = datetime.now(TZ_ET)
            today = now_et.date()

            today_data = intr[intr.index.date == today]
            if today_data.empty:
                time.sleep(pause)
                continue

            # 정규장 시작 전: 프리마켓 구간(04:00~09:30) 마지막가
            # 정규장 시작 후: 당일 전체(프리+정규+애프터) 마지막가
            if now_et.time() < REGULAR_START:
                filtered = today_data[
                    (today_data.index.time >= PRE_START) & (today_data.index.time < PRE_END)
                ]
            else:
                filtered = today_data

            if filtered.empty:
                time.sleep(pause)
                continue

            px = float(filtered["Close"].dropna().iloc[-1])
            if px > 0:
                return px
        except Exception:
            time.sleep(pause)
    return None

def main():
    tickers = load_tickers("tickers.txt")
    if not tickers:
        print("No tickers found in tickers.txt")
        return

    now_et = datetime.now(TZ_ET)
    mode = "premarket (04:00~09:30 ET)" if now_et.time() < REGULAR_START else "last price (regular/extended)"
    print(f"Mode: {mode}")

    rows = []
    for i, t in enumerate(tickers, 1):
        px = fetch_last_price(t)
        if px is not None:
            rows.append({"ticker": t, "premarket": round(px, 2)})
        time.sleep(0.15)  # 레이트리밋 완화

        if i % 10 == 0:
            print(f"... {i}/{len(tickers)} processed")

    out = pd.DataFrame(rows).sort_values("ticker")
    out.to_csv("premarket_auto.csv", index=False)
    print(f"Saved premarket_auto.csv ({len(out)} tickers)")

if __name__ == "__main__":
    main()