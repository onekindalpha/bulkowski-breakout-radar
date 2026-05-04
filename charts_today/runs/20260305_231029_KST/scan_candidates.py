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
    high = df["High"]
    low = df["Low"]
    close = df["Close"]
    tr = pd.concat([
        (high - low),
        (high - close.shift()).abs(),
        (low - close.shift()).abs()
    ], axis=1).max(axis=1)
    return tr.rolling(period).mean()

def last_swing_levels(df, lookback=20):
    # simple: 최근 N일 최고/최저
    return df["High"].rolling(lookback).max().iloc[-1], df["Low"].rolling(lookback).min().iloc[-1]

def score_row(row):
    # 불코우스키식 “추격 회피 + 레벨/추세 선호”에 맞춘 간단 점수
    s = 0
    # 추세(장기)
    if row["px_vs_sma200"] > 0: s += 2
    if row["px_vs_sma50"] > 0: s += 2

    # 과열 회피
    if 50 <= row["rsi14"] <= 65: s += 3
    elif 65 < row["rsi14"] <= 70: s += 1
    elif row["rsi14"] > 70: s -= 3
    elif row["rsi14"] < 40: s -= 1

    # 갭/변동: 오늘같은 이벤트 데이는 갭 너무 크면 추격 금지 쪽
    if abs(row["gap_pct"]) >= 4: s -= 2
    elif abs(row["gap_pct"]) >= 2: s -= 1

    # 돌파/리테스트 근접(최근 20일 고점 부근이면 “관찰 가치”)
    if row["near_20d_high"]:
        s += 2
    if row["breakout_20d"]:
        s += 2

    # 리스크-보상: ATR 대비 지지선 거리(너무 멀면 불리)
    if row["risk_atr"] <= 2.5: s += 1
    elif row["risk_atr"] > 4: s -= 1

    return s

# ---------- inputs ----------
tickers = [t.strip().upper() for t in Path("tickers.txt").read_text().splitlines() if t.strip()]
premarket = {}
if Path("premarket.csv").exists():
    pm = pd.read_csv("premarket.csv")
    premarket = dict(zip(pm["ticker"].str.upper(), pm["premarket"]))

# ---------- fetch & compute ----------
rows = []
for t in tickers:
    try:
        df = yf.download(t, period="2y", interval="1d", auto_adjust=False, progress=False)
        if df is None or df.empty or len(df) < 250:
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

        hi20, lo20 = last_swing_levels(df, 20)
        near_20d_high = (hi20 - px) / hi20 <= 0.01  # 1% 이내면 근접
        breakout_20d = px > hi20

        # 주봉 추세(간단): 주봉으로 리샘플
        w = df.resample("W-FRI").agg({"Open":"first","High":"max","Low":"min","Close":"last","Volume":"sum"}).dropna()
        wclose = w["Close"]
        w_sma20 = wclose.rolling(20).mean()
        w_sma50 = wclose.rolling(50).mean()
        weekly_up = (wclose.iloc[-1] > w_sma20.iloc[-1]) and (w_sma20.iloc[-1] > w_sma50.iloc[-1])

        # 리스크(가장 단순): “지지”를 max(SMA50, 최근 20일 저점)으로 잡고 거리/ATR로 환산
        support = max(float(sma50.iloc[-1]), float(lo20))
        risk = max(px - support, 0.0)
        risk_atr = risk / float(atr14.iloc[-1]) if float(atr14.iloc[-1]) > 0 else np.nan

        row = {
            "ticker": t,
            "price": px,
            "last_close": last_close,
            "gap_pct": gap_pct,
            "rsi14": float(rsi14.iloc[-1]),
            "sma20": float(sma20.iloc[-1]),
            "sma50": float(sma50.iloc[-1]),
            "sma200": float(sma200.iloc[-1]),
            "px_vs_sma50": (px / float(sma50.iloc[-1]) - 1) * 100,
            "px_vs_sma200": (px / float(sma200.iloc[-1]) - 1) * 100,
            "hi20": float(hi20),
            "lo20": float(lo20),
            "near_20d_high": bool(near_20d_high),
            "breakout_20d": bool(breakout_20d),
            "weekly_up": bool(weekly_up),
            "atr14": float(atr14.iloc[-1]),
            "support": support,
            "risk_atr": float(risk_atr) if np.isfinite(risk_atr) else np.nan,
        }
        row["score"] = score_row(row)
        rows.append(row)

    except Exception as e:
        print(f"[skip] {t}: {e}")

out = pd.DataFrame(rows)
if out.empty:
    print("No data.")
    raise SystemExit(0)

out = out.sort_values(["score","weekly_up","gap_pct"], ascending=[False, False, True])
out.to_csv("report.csv", index=False)

print("\n=== TOP CANDIDATES (higher score = better 'watch/entry framing') ===")
print(out[["ticker","score","price","gap_pct","rsi14","px_vs_sma50","px_vs_sma200","weekly_up","near_20d_high","breakout_20d","risk_atr"]].head(12).to_string(index=False))
print("\nSaved: report.csv")