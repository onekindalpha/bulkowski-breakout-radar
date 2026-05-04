import pandas as pd
import numpy as np
import yfinance as yf
from pathlib import Path

# ---------- indicators ----------
def rsi(series, period=14):
    delta = series.diff()
    gain = delta.clip(lower=0).rolling(period).mean()
    loss = (-delta.clip(upper=0)).rolling(period).mean()
    rs = gain / loss.replace(0, np.nan)
    return 100 - (100 / (1 + rs))

def atr(df, period=14):
    h, l, c = df["High"], df["Low"], df["Close"]
    tr = pd.concat([(h-l), (h-c.shift()).abs(), (l-c.shift()).abs()], axis=1).max(axis=1)
    return tr.rolling(period).mean()

def swing_levels(df, lookback=180, pivot=3):
    """
    간단 스윙: 최근 lookback에서 pivot 기준 로컬 스윙 하이/로우 마지막 값을 R1/S1로 사용.
    (완벽하진 않지만 자동화에 충분히 유용)
    """
    d = df.tail(lookback).copy()
    highs = d["High"].values
    lows  = d["Low"].values
    idxs  = d.index.to_list()

    sh = []
    sl = []
    for i in range(pivot, len(d)-pivot):
        if highs[i] == max(highs[i-pivot:i+pivot+1]):
            sh.append((idxs[i], highs[i]))
        if lows[i] == min(lows[i-pivot:i+pivot+1]):
            sl.append((idxs[i], lows[i]))

    r1 = float(sh[-1][1]) if sh else float(d["High"].max())
    s1 = float(sl[-1][1]) if sl else float(d["Low"].min())
    return r1, s1

def breakout_and_retest(df, price, lookback=60, tol=0.6):
    """
    breakout: price > 최근 lookback 고점
    retest: price가 '최근 고점(돌파선)' 근처(±tol%)에 위치
    """
    hi = float(df["High"].rolling(lookback).max().iloc[-1])
    breakout = price > hi
    # 돌파선 근처: 가격이 hi 근방에 있으면 retest 후보
    retest = (abs(price - hi) / hi * 100) <= tol
    return hi, breakout, retest

def grade(row):
    """
    A/B/C 등급(오늘 갭/뉴스 장에서 후보 압축용)
    - A: open air(weekly R1까지 공간 충분) + 일봉 돌파/리테스트 구조 + 갭이 저항에 바로 박히지 않음
    - B: 움직임은 있는데 weekly R1 너무 가깝거나(추격 위험) 구조가 애매
    - C: 레인지 한가운데/레벨 촘촘(오늘 효율 낮음)
    """
    room = row["room_to_weekly_r1_pct"]
    gap  = abs(row["gap_pct"])
    in_middle = row["in_daily_box_middle"]

    if in_middle and gap < 2:
        return "C"

    # A 조건(보수적)
    if (room >= 2.0) and (row["daily_breakout"] or row["daily_retest"]) and (room - max(gap, 0) >= 0.8):
        return "A"

    # B: 나머지 중 관찰가치는 있는 경우
    if (room >= 0.8) and (gap >= 1.0 or row["daily_breakout"] or row["daily_retest"]):
        return "B"

    return "C"

def score(row):
    s = 0.0
    # 추세
    s += 2.0 if row["weekly_up"] else 0.0
    s += 1.0 if row["px_vs_sma200"] > 0 else 0.0
    s += 1.0 if row["px_vs_sma50"] > 0 else 0.0

    # RSI 과열 회피
    if 50 <= row["rsi14"] <= 65: s += 3.0
    elif 65 < row["rsi14"] <= 70: s += 1.0
    elif row["rsi14"] > 70: s -= 3.0

    # 오늘 갭(너무 크면 추격 리스크)
    if abs(row["gap_pct"]) >= 4: s -= 2.0
    elif abs(row["gap_pct"]) >= 2: s -= 1.0

    # 구조(돌파/리테스트)
    if row["daily_breakout"]: s += 2.0
    if row["daily_retest"]: s += 1.0

    # open air(weekly R1까지 공간)
    if row["room_to_weekly_r1_pct"] >= 3: s += 2.0
    elif row["room_to_weekly_r1_pct"] >= 1.5: s += 1.0
    elif row["room_to_weekly_r1_pct"] < 0.7: s -= 2.0

    return s

# ---------- inputs ----------
tickers = [t.strip().upper() for t in Path("tickers.txt").read_text().splitlines() if t.strip()]

premarket = {}
pm_path = Path("premarket.csv")
if pm_path.exists():
    pm = pd.read_csv(pm_path)
    if "ticker" in pm.columns and "premarket" in pm.columns:
        pm = pm.dropna()
        premarket = dict(zip(pm["ticker"].str.upper(), pm["premarket"]))

rows = []
for t in tickers:
    df = yf.download(t, period="5y", interval="1d", auto_adjust=False, progress=False)
    if df is None or df.empty or len(df) < 260:
        continue
    df = df.dropna()

    close = df["Close"]
    sma20 = close.rolling(20).mean()
    sma50 = close.rolling(50).mean()
    sma200 = close.rolling(200).mean()

    rsi14 = rsi(close, 14)
    atr14 = atr(df, 14)

    last_close = float(close.iloc[-1])
    px = float(premarket.get(t, last_close))
    gap_pct = (px / last_close - 1.0) * 100.0 if t in premarket else 0.0

    # Weekly series
    w = df.resample("W-FRI").agg({"Open":"first","High":"max","Low":"min","Close":"last","Volume":"sum"}).dropna()
    wclose = w["Close"]
    w_sma20 = wclose.rolling(20).mean()
    w_sma50 = wclose.rolling(50).mean()
    weekly_up = bool((wclose.iloc[-1] > w_sma20.iloc[-1]) and (w_sma20.iloc[-1] > w_sma50.iloc[-1]))

    # Weekly levels
    w_r1, w_s1 = swing_levels(w.rename(columns={"High":"High","Low":"Low"}), lookback=min(180, len(w)), pivot=2)
    room_to_weekly_r1_pct = ((w_r1 / px) - 1) * 100 if px > 0 else np.nan

    # Daily breakout / retest
    daily_break_level, daily_breakout, daily_retest = breakout_and_retest(df, px, lookback=60, tol=0.6)

    # "박스 한가운데" 감지(대충): 최근 60일 범위의 중앙값 근처면 레인지 중앙으로 간주
    lo60 = float(df["Low"].rolling(60).min().iloc[-1])
    hi60 = float(df["High"].rolling(60).max().iloc[-1])
    mid60 = (lo60 + hi60) / 2
    in_daily_box_middle = (abs(px - mid60) / mid60 * 100) < 3.0  # 중앙 ±3% 이내면

    row = {
        "ticker": t,
        "price": px,
        "last_close": last_close,
        "gap_pct": gap_pct,

        "rsi14": float(rsi14.iloc[-1]),
        "atr14": float(atr14.iloc[-1]),

        "sma50": float(sma50.iloc[-1]),
        "sma200": float(sma200.iloc[-1]),
        "px_vs_sma50": (px / float(sma50.iloc[-1]) - 1) * 100,
        "px_vs_sma200": (px / float(sma200.iloc[-1]) - 1) * 100,

        "weekly_up": weekly_up,
        "weekly_r1": w_r1,
        "weekly_s1": w_s1,
        "room_to_weekly_r1_pct": float(room_to_weekly_r1_pct),

        "daily_break_level": float(daily_break_level),
        "daily_breakout": bool(daily_breakout),
        "daily_retest": bool(daily_retest),
        "in_daily_box_middle": bool(in_daily_box_middle),
        "hi60": hi60,
        "lo60": lo60,
    }

    row["grade"] = grade(row)
    row["score"] = score(row)
    rows.append(row)

out = pd.DataFrame(rows)
if out.empty:
    print("No data.")
    raise SystemExit(0)

# A 먼저, score 높은 순
grade_order = {"A": 0, "B": 1, "C": 2}
out["grade_rank"] = out["grade"].map(grade_order)

out = out.sort_values(["grade_rank","score","gap_pct"], ascending=[True, False, True])

# 보기 좋게 반올림
for c in ["price","gap_pct","rsi14","px_vs_sma50","px_vs_sma200","room_to_weekly_r1_pct","weekly_r1","weekly_s1","daily_break_level","score"]:
    out[c] = out[c].astype(float).round(2)

cols = ["ticker","grade","score","price","gap_pct","rsi14",
        "room_to_weekly_r1_pct","weekly_r1",
        "daily_breakout","daily_retest","daily_break_level",
        "px_vs_sma50","px_vs_sma200"]

print("\n=== WATCHLIST (A->B->C) ===")
print(out[cols].head(15).to_string(index=False))

out.drop(columns=["grade_rank"]).to_csv("report_v2.csv", index=False)
print("\nSaved: report_v2.csv")