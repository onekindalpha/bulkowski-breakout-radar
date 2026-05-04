import math
from dataclasses import dataclass
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
import yfinance as yf

# ====== 네 유니버스 (필요하면 여기만 편집) ======
TICKERS = [
    # Energy (refining/services/upstream)
    "PSX","MPC","VLO","SLB","HAL","BKR","CVE","EQNR","E",
    # Midstream
    "EPD","ENB","TRP","PBA","WES","VNOM","WMB","TRGP",
    # Copper/mining
    "FCX","SCCO","TECK","RIO","VALE","MP",
    # Uranium
    "CCJ",
    # Gold/Silver
    "AEM","NEM","FNV","GFI","AU","AGI","HL","SBSW",
]

# 그룹 중복 컷 (네 룰 반영)
GROUP_CAPS = {
    "REFINING": (["PSX","MPC","VLO"], 2),
    "COPPER":   (["FCX","SCCO","TECK"], 2),  # 필요 시 1로 바꿔도 됨
    "GOLD":     (["AEM","NEM"], 2),
}

# ====== 유틸 ======
def atr_pct(df: pd.DataFrame, n=14):
    h, l, c = df["High"], df["Low"], df["Close"]
    prev_c = c.shift(1)
    tr = pd.concat([(h-l).abs(), (h-prev_c).abs(), (l-prev_c).abs()], axis=1).max(axis=1)
    atr = tr.rolling(n).mean()
    return (atr / c) * 100

def swing_levels(df: pd.DataFrame, lookback=60, pivot=5):
    """
    최근 lookback 구간에서 pivot 기준 로컬 스윙 하이/로우 탐색.
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

    r1 = sh[-1][1] if sh else float(d["High"].max())
    s1 = sl[-1][1] if sl else float(d["Low"].min())
    return r1, s1

@dataclass
class Row:
    ticker: str
    last: float
    gap_pct: float
    pre_vol: float
    adv_dollar_m: float
    atr14_pct: float
    room_to_r1_pct: float
    trend_score: int
    score: float
    r1: float
    s1: float

def fetch_daily(ticker: str):
    # 2년치: MA200/레벨 계산용
    d = yf.download(ticker, period="2y", interval="1d", auto_adjust=False, progress=False)
    if d.empty:
        return None
    d = d.dropna()
    return d

def fetch_intraday_5m_prepost(ticker: str):
    # 최근 며칠 5m + 프리/애프터 포함 (Yahoo가 가끔 누락/지연 가능)
    i = yf.download(ticker, period="5d", interval="5m", prepost=True, auto_adjust=False, progress=False)
    if i.empty:
        return None
    i = i.dropna()
    return i

def compute_row(ticker: str) -> Row | None:
    d = fetch_daily(ticker)
    if d is None or len(d) < 220:
        return None

    # liquidity: 20D 평균 달러 거래대금(백만$)
    adv = (d["Close"] * d["Volume"]).rolling(20).mean().iloc[-1]
    adv_m = float(adv / 1e6) if pd.notna(adv) else 0.0

    # trend score (간단/안전한 버전)
    c = d["Close"]
    sma50  = c.rolling(50).mean()
    sma200 = c.rolling(200).mean()
    last = float(c.iloc[-1])

    trend_score = 0
    if last > float(sma200.iloc[-1]): trend_score += 1
    if float(sma50.iloc[-1]) > float(sma200.iloc[-1]): trend_score += 1
    # 20일 MA 기울기(상승이면 +1)
    sma20 = c.rolling(20).mean()
    if len(sma20.dropna()) > 10 and (sma20.iloc[-1] - sma20.iloc[-10]) > 0: trend_score += 1

    # levels (일봉 기반)
    r1, s1 = swing_levels(d, lookback=90, pivot=5)
    room_to_r1_pct = ((r1 / last) - 1) * 100 if last > 0 else 0.0

    # volatility
    a14 = atr_pct(d, 14).iloc[-1]
    a14 = float(a14) if pd.notna(a14) else 0.0

    # premarket gap/volume (가능하면)
    gap_pct = 0.0
    pre_vol = 0.0

    i = fetch_intraday_5m_prepost(ticker)
    if i is not None and not i.empty:
        # 미국 동부 기준 프리 04:00~09:30 / 정규 09:30~16:00
        tz_et = ZoneInfo("America/New_York")
        ii = i.copy()
        if ii.index.tz is None:
            # yfinance 인덱스가 tz 없는 경우가 있어서 ET로 가정
            ii.index = ii.index.tz_localize(tz_et)
        else:
            ii = ii.tz_convert(tz_et)

        # 최근 "정규장 마지막 종가"를 찾기: 16:00 근처 마지막 바
        # 간단히: 각 일자별 정규장(09:30~16:00) 마지막 close를 구해서 가장 최근 것을 prev_close로 사용
        def is_regular(ts):
            t = ts.timetz()
            return (t >= datetime(2000,1,1,9,30,tzinfo=tz_et).timetz()) and (t <= datetime(2000,1,1,16,0,tzinfo=tz_et).timetz())

        regular = ii[ii.index.map(is_regular)]
        if not regular.empty:
            prev_close = float(regular["Close"].groupby(regular.index.date).last().iloc[-1])

            # "오늘 프리" : 04:00~09:30
            now_et = datetime.now(tz_et)
            today = now_et.date()
            pre = ii[(ii.index.date == today)]
            if not pre.empty:
                pre = pre[(pre.index.time >= datetime(2000,1,1,4,0,tzinfo=tz_et).timetz()) &
                          (pre.index.time <  datetime(2000,1,1,9,30,tzinfo=tz_et).timetz())]
                if not pre.empty:
                    last_pre = float(pre["Close"].iloc[-1])
                    gap_pct = ((last_pre / prev_close) - 1) * 100
                    pre_vol = float(pre["Volume"].sum())

    # score: 네 목적(갭/민감도/유동성) 중심으로 가중치
    # - gap(절대값) 크게
    # - trend는 보조
    # - 유동성(거래대금) 필수
    # - R1까지 공간이 너무 없으면 감점(오늘 추격 방지)
    score = 0.0
    score += min(abs(gap_pct), 8.0) * 3.0          # 갭 민감도
    score += min(trend_score, 3) * 1.5             # 추세
    score += min(adv_m / 200.0, 5.0) * 2.0         # 유동성(거래대금)
    score += min(a14 / 3.0, 5.0) * 1.0             # 움직임(ATR%)
    # 공간 페널티: R1이 너무 가까우면(예: 0~0.7%) 오늘은 눌림 위험
    if room_to_r1_pct < 0.7:
        score -= 4.0
    elif room_to_r1_pct < 1.5:
        score -= 1.5

    return Row(
        ticker=ticker, last=last, gap_pct=float(gap_pct), pre_vol=float(pre_vol),
        adv_dollar_m=float(adv_m), atr14_pct=float(a14), room_to_r1_pct=float(room_to_r1_pct),
        trend_score=int(trend_score), score=float(score), r1=float(r1), s1=float(s1)
    )

def apply_group_caps(rows: list[Row]) -> list[Row]:
    # score 순 정렬 후, 그룹 cap을 넘으면 제거
    rows = sorted(rows, key=lambda r: r.score, reverse=True)
    keep = []
    counts = {k: 0 for k in GROUP_CAPS.keys()}

    group_map = {}
    for g,(names,cap) in GROUP_CAPS.items():
        for t in names:
            group_map[t] = g

    for r in rows:
        g = group_map.get(r.ticker)
        if g is None:
            keep.append(r)
            continue
        cap = GROUP_CAPS[g][1]
        if counts[g] < cap:
            keep.append(r)
            counts[g] += 1
    return keep

def main():
    out = []
    for t in TICKERS:
        try:
            r = compute_row(t)
            if r is None:
                continue
            # 유동성 너무 낮으면 컷(원하면 기준 조정)
            if r.adv_dollar_m < 50:  # $50M/day 미만은 오늘같은 날 비효율적일 가능성
                continue
            out.append(r)
        except Exception as e:
            # 실패해도 전체는 계속
            continue

    out = apply_group_caps(out)
    out = sorted(out, key=lambda r: r.score, reverse=True)[:10]

    df = pd.DataFrame([r.__dict__ for r in out])
    if df.empty:
        print("No results (data unavailable).")
        return

    # 보기 좋게
    df["gap_pct"] = df["gap_pct"].map(lambda x: round(x, 2))
    df["adv_dollar_m"] = df["adv_dollar_m"].map(lambda x: round(x, 1))
    df["atr14_pct"] = df["atr14_pct"].map(lambda x: round(x, 2))
    df["room_to_r1_pct"] = df["room_to_r1_pct"].map(lambda x: round(x, 2))
    df["score"] = df["score"].map(lambda x: round(x, 2))
    df["r1"] = df["r1"].map(lambda x: round(x, 2))
    df["s1"] = df["s1"].map(lambda x: round(x, 2))
    df["last"] = df["last"].map(lambda x: round(x, 2))

    cols = ["ticker","score","gap_pct","pre_vol","adv_dollar_m","atr14_pct","room_to_r1_pct","trend_score","r1","s1","last"]
    print(df[cols].to_string(index=False))

if __name__ == "__main__":
    main()