# bulkowski_scan_from_debugcsv_260316.py
# Same logic as bulkowski_scan_from_debugcsv.py, but ALSO writes:
# - bulkowski_top10.csv
# - candidates.txt (top10 tickers, one per line)
#
# This restores the "top10 -> manual price prompt (ticker names shown)" legacy flow.

import re
import numpy as np
import pandas as pd
import yfinance as yf
from contextlib import redirect_stdout, redirect_stderr
import io
from pathlib import Path
from datetime import datetime
from zoneinfo import ZoneInfo

DEBUG_CSV = "premarket_auto_debug.csv"
KST = ZoneInfo("Asia/Seoul")

OUT_CSV = Path("bulkowski_top10.csv")
OUT_CANDS = Path("candidates.txt")

def load_universe(csv_path: str) -> list[str]:
    df = pd.read_csv(csv_path, comment="#")
    # legacy: tickers_core only, no error
    df = df[(df["group"] == "tickers_core") & (df["error"].isna())]
    tickers = df["yahoo_symbol"].dropna().astype(str).unique().tolist()

    # leverage ETFs excluded by default
    exclude = {"ERX","DIG","GUSH","UYM","BOIL","UCO"}
    tickers = [t for t in tickers if t not in exclude]

    # sanity
    tickers = [t for t in tickers if re.match(r"^[A-Z0-9\^\=\.\-]+$", t)]
    return tickers

def get_daily(symbol: str, period="9mo") -> pd.DataFrame:
    buf = io.StringIO()
    with redirect_stdout(buf), redirect_stderr(buf):
        df = yf.download(symbol, period=period, interval="1d", auto_adjust=True, progress=False, threads=False)
    if df is None or df.empty:
        return pd.DataFrame()
    return df.dropna()

def breakout_score(df: pd.DataFrame) -> float:
    if len(df) < 80:
        return 0.0

    close = df["Close"].values
    high = df["High"].values
    low = df["Low"].values
    vol = df["Volume"].values if "Volume" in df.columns else None

    def is_higher_lows(window=20) -> bool:
        l = low[-window:]
        x = np.arange(len(l))
        slope = np.polyfit(x, l, 1)[0]
        return slope > 0

    last = close[-1]
    r60 = np.max(high[-60:-1])
    r20 = np.max(high[-20:-1])

    score = 0.0
    if last > r60:
        score += 3.0
    if last > r20:
        score += 2.0
    if is_higher_lows(25):
        score += 1.5

    if vol is not None and len(vol) > 30:
        v5 = np.mean(vol[-5:])
        v20 = np.mean(vol[-25:-5])
        if v20 > 0 and v5 / v20 >= 1.3:
            score += 1.0

    return score

def double_bottom_score(df: pd.DataFrame) -> float:
    if len(df) < 140:
        return 0.0

    close = df["Close"].values
    low = df["Low"].values
    window = 120
    L = low[-window:]
    idx_sorted = np.argsort(L)[:10]
    idx_sorted = np.sort(idx_sorted)

    best = 0.0
    for i in range(len(idx_sorted)):
        for j in range(i+1, len(idx_sorted)):
            a, b = idx_sorted[i], idx_sorted[j]
            if b - a < 15:
                continue
            la, lb = L[a], L[b]
            if abs(la - lb) / max(la, 1e-9) <= 0.03:
                mid_peak = np.max(close[-window + a : -window + b])
                neckline = mid_peak
                last = close[-1]
                tmp = 1.5
                if last >= neckline:
                    tmp += 1.5
                elif last >= neckline * 0.98:
                    tmp += 0.8
                best = max(best, tmp)
    return best

def scan(universe: list[str]) -> pd.DataFrame:
    rows = []
    for sym in universe:
        df = get_daily(sym)
        if df.empty:
            continue
        s1 = breakout_score(df)
        s2 = double_bottom_score(df)
        total = s1 + s2
        rows.append((sym, total, s1, s2, float(df["Close"].iloc[-1])))
    out = pd.DataFrame(rows, columns=["symbol", "score_total", "score_breakout", "score_double_bottom", "last_close"])
    out = out.sort_values("score_total", ascending=False).reset_index(drop=True)
    return out

def write_outputs(top10: pd.DataFrame) -> None:
    # write CSV
    top10.to_csv(OUT_CSV, index=False)
    # write candidates
    cands = top10["symbol"].astype(str).str.upper().tolist()
    OUT_CANDS.write_text("\n".join(cands) + "\n", encoding="utf-8")
    # also add a small header comment in csv? keep plain for simplicity

if __name__ == "__main__":
    universe = load_universe(DEBUG_CSV)
    result = scan(universe)
    top10 = result.head(10).copy()
    write_outputs(top10)
    print(top10.to_string(index=False))
    print(f"\nSaved: {OUT_CSV}  (rows={len(top10)})")
    print(f"Saved: {OUT_CANDS}  (count={len(top10)})")
