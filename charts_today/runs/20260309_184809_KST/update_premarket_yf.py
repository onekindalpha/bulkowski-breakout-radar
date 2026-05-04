# update_premarket_yf.py
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


def load_tickers(path="tickers.txt"):
    text = Path(path).read_text(encoding="utf-8", errors="ignore")
    tokens = re.split(r"[\s,;]+", text.strip())
    out, seen = [], set()
    for tok in tokens:
        tok = tok.strip().upper()
        if not tok or tok.startswith("#"):
            continue
        # "MP%" 같은 오타 방어
        tok = re.sub(r"[^A-Z0-9\.\-]", "", tok)
        if tok and tok not in seen:
            seen.add(tok)
            out.append(tok)
    return out


def _ensure_et_index(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return df
    idx = df.index
    # yfinance가 tz 없는 인덱스를 줄 때가 있음
    if idx.tz is None:
        # 보통 UTC로 오는 경우가 많아서 UTC로 가정 후 ET 변환
        df = df.tz_localize("UTC").tz_convert(TZ_ET)
    else:
        df = df.tz_convert(TZ_ET)
    return df


def fetch_premarket_last(ticker: str, retry=2, pause=0.6):
    """
    오늘 프리마켓(04:00~09:30 ET) 구간에서 마지막 Close를 반환.
    못 구하면 None.
    """
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

            pre = intr[intr.index.date == today]
            if pre.empty:
                time.sleep(pause)
                continue

            pre = pre[(pre.index.time >= PRE_START) & (pre.index.time < PRE_END)]
            if pre.empty:
                time.sleep(pause)
                continue

            px = float(pre["Close"].dropna().iloc[-1])
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

    rows = []
    for i, t in enumerate(tickers, 1):
        px = fetch_premarket_last(t)
        if px is not None:
            rows.append({"ticker": t, "premarket": round(px, 2)})
        # Yahoo 레이트리밋 방지용(너무 빠르면 종종 막힘)
        time.sleep(0.15)

        if i % 10 == 0:
            print(f"... {i}/{len(tickers)} processed")

    if not rows:
        print("No premarket prices found (maybe too early / data delay).")
        # 그래도 빈 파일로 만들고 싶으면 아래 주석 해제
        # pd.DataFrame(columns=["ticker","premarket"]).to_csv("premarket.csv", index=False)
        return

    out = pd.DataFrame(rows).sort_values("ticker")
    out.to_csv("premarket.csv", index=False)
    print(f"Saved premarket.csv ({len(out)} tickers)")


if __name__ == "__main__":
    main()