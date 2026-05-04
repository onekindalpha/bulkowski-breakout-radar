# bulkowski_scan_from_debugcsv.py
# pip install yfinance pandas numpy

import re
import numpy as np
import pandas as pd
import yfinance as yf
from contextlib import redirect_stdout, redirect_stderr
import io

DEBUG_CSV = "premarket_auto_debug.csv"

def load_universe(csv_path: str) -> list[str]:
    df = pd.read_csv(csv_path, comment="#")

    # --- Compatibility across debug CSV formats ---
    # Alpaca debug used: error, yahoo_symbol
    # Yahoo debug uses: note, yf_symbol (and may not have error/yahoo_symbol)
    err_col = None
    if "error" in df.columns:
        err_col = "error"
    elif "note" in df.columns:
        err_col = "note"

    # "ok" rows: no error/note AND (if present) premarket is numeric
    if err_col is not None:
        ok_err = df[err_col].isna() | (df[err_col].astype(str).str.strip() == "")
    else:
        ok_err = pd.Series([True] * len(df), index=df.index)

    if "premarket" in df.columns:
        df["_premarket_num"] = pd.to_numeric(df["premarket"], errors="coerce")
        ok_px = df["_premarket_num"].notna()
    else:
        ok_px = pd.Series([True] * len(df), index=df.index)

    # 스캔 대상: tickers_core 중에서 정상 row만
    df = df[(df["group"] == "tickers_core") & ok_err & ok_px]

    # pick a symbol column
    sym_col = None
    for c in ["yahoo_symbol", "yf_symbol", "symbol", "ticker"]:
        if c in df.columns:
            sym_col = c
            break
    if sym_col is None:
        raise KeyError(f"No symbol column found. Columns={list(df.columns)}")

    tickers = df[sym_col].dropna().astype(str).unique().tolist()

    # 레버리지 ETF는 기본 제외(원하면 주석 처리)
    exclude = {"ERX","DIG","GUSH","UYM","BOIL","UCO"}
    tickers = [t for t in tickers if t not in exclude]

    # 심볼 sanity (Yahoo symbols + futures + indices)
    tickers = [t for t in tickers if re.match(r"^[A-Z0-9\^\=\.\-\/]+$", t)]
    return tickers

def get_daily(symbol: str, period="9mo") -> pd.DataFrame:
    # yfinance가 뿜는 delisted 메시지 등 콘솔 스팸 억제
    buf = io.StringIO()
    with redirect_stdout(buf), redirect_stderr(buf):
        df = yf.download(symbol, period=period, interval="1d", auto_adjust=True, progress=False, threads=False)
    if df is None or df.empty:
        return pd.DataFrame()
    df = df.dropna()
    return df

def breakout_score(df: pd.DataFrame) -> float:
    """
    Bulkowski 스타일 '돌파'에 가까운 간단 스코어:
    - 최근 60일 고점(저항) 돌파 여부
    - 최근 20일 박스 상단 돌파 여부
    - 최근 20일 저점이 높아지는지(상승삼각의 'higher lows' 근사)
    - 돌파일 거래량(있다면) 증가 (yfinance에서 Volume 제공)
    """
    if len(df) < 80:
        return 0.0

    close = df["Close"].values
    high = df["High"].values
    low = df["Low"].values
    vol = df["Volume"].values if "Volume" in df.columns else None

    def is_higher_lows(window=20) -> bool:
        l = low[-window:]
        # 저점이 우상향인지: 선형회귀 기울기 > 0
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

    # 돌파면 거래량 증가(근사): 최근 5일 평균 vs 이전 20일 평균
    if vol is not None and len(vol) > 30:
        v5 = np.mean(vol[-5:])
        v20 = np.mean(vol[-25:-5])
        if v20 > 0 and v5 / v20 >= 1.3:
            score += 1.0

    return score

def double_bottom_score(df: pd.DataFrame) -> float:
    """
    아주 단순한 더블바텀 근사:
    - 최근 120일 안에 비슷한 저점이 2번 등장
    - 그 사이에 중간 반등(넥라인)이 존재
    - 현재가가 넥라인 근처/상단이면 가점
    """
    if len(df) < 140:
        return 0.0

    close = df["Close"].values
    low = df["Low"].values
    window = 120
    L = low[-window:]
    idx_sorted = np.argsort(L)[:10]  # 가장 낮은 10개 지점
    idx_sorted = np.sort(idx_sorted)

    # 서로 떨어진 저점 2개 찾기 (최소 15거래일 간격)
    best = 0.0
    for i in range(len(idx_sorted)):
        for j in range(i+1, len(idx_sorted)):
            a, b = idx_sorted[i], idx_sorted[j]
            if b - a < 15:
                continue
            la, lb = L[a], L[b]
            # 저점 유사도(±3%)
            if abs(la - lb) / max(la, 1e-9) <= 0.03:
                mid_peak = np.max(close[-window + a : -window + b])
                neckline = mid_peak
                last = close[-1]
                # 넥라인 대비 위치
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

if __name__ == "__main__":
    universe = load_universe(DEBUG_CSV)
    result = scan(universe)
    print(result.head(10).to_string(index=False))