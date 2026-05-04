import re
from pathlib import Path

import numpy as np
import pandas as pd
import yfinance as yf


# ---------- indicators ----------
def rsi(series: pd.Series, period=14) -> pd.Series:
    delta = series.diff()
    gain = delta.clip(lower=0).rolling(period).mean()
    loss = (-delta.clip(upper=0)).rolling(period).mean()
    rs = gain / loss.replace(0, np.nan)
    return 100 - (100 / (1 + rs))


def swing_levels(df: pd.DataFrame, lookback=180, pivot=2):
    d = df.tail(min(lookback, len(df))).copy()
    highs = d["High"].to_numpy()
    lows = d["Low"].to_numpy()

    sh, sl = [], []
    for i in range(pivot, len(d) - pivot):
        if highs[i] == np.max(highs[i - pivot : i + pivot + 1]):
            sh.append(highs[i])
        if lows[i] == np.min(lows[i - pivot : i + pivot + 1]):
            sl.append(lows[i])

    r1 = float(sh[-1]) if sh else float(d["High"].max())
    s1 = float(sl[-1]) if sl else float(d["Low"].min())
    return r1, s1


def atr14(df: pd.DataFrame, period: int = 14) -> pd.Series:
    # Average True Range (simple moving average)
    high = df["High"].astype(float)
    low = df["Low"].astype(float)
    close = df["Close"].astype(float)
    tr = pd.concat([
        (high - low),
        (high - close.shift(1)).abs(),
        (low - close.shift(1)).abs(),
    ], axis=1).max(axis=1)
    return tr.rolling(period).mean()


def breakout_throwback_addon(
    df: pd.DataFrame,
    price: float,
    lookback: int = 60,
    tol_pct: float = 2.75,
    max_breakout_age: int = 30,
    retest_window: int = 15,
    use_atr_tol: bool = True,
    atr_k: float = 0.8,
):
    """
    Bulkowski-style approximation:
      - breakout level = rolling lookback high EXCLUDING current bar (shifted by 1)
      - breakout (today) = price > breakout level
      - throwback/pullback (retest) requires:
          * a breakout happened within last max_breakout_age bars
          * current price is near the breakout price (within tol)
          * current price is not decisively below breakout price (hold)
      - add-on signal (추매 신호, pragmatic):
          * a retest/hold happened within retest_window bars after breakout
          * price breaks above short-term high (5-bar high)
    Returns:
      break_level_today, breakout_today, retest_today, add_on_today, breakout_price, breakout_age_bars, tol_eff_pct
    """
    if df is None or df.empty or len(df) < lookback + 5:
        return np.nan, False, False, False, np.nan, np.nan, np.nan

    # breakout level excluding the current bar
    prior_high = df["High"].astype(float).rolling(lookback).max().shift(1)
    break_level_today = float(prior_high.iloc[-1])
    breakout_today = bool(price > break_level_today)

    # find last breakout within max_breakout_age bars: Close > prior_high (on that day)
    close = df["Close"].astype(float)
    brk = close > prior_high
    breakout_price = np.nan
    breakout_age_bars = np.nan

    if max_breakout_age > 0 and len(df) >= max_breakout_age:
        recent = brk.iloc[-max_breakout_age:]
        if bool(recent.any()):
            last_brk_idx = recent[recent].index[-1]
            breakout_price = float(prior_high.loc[last_brk_idx])
            # bars since breakout (approx)
            breakout_age_bars = int(df.index.get_indexer([df.index[-1]])[0] - df.index.get_indexer([last_brk_idx])[0])

    # effective tolerance: max(user tol, ATR-based tol)
    tol_eff_pct = float(tol_pct)
    if use_atr_tol and not np.isnan(breakout_price):
        a = float(atr14(df, 14).iloc[-1])
        if np.isfinite(a) and a > 0:
            tol_from_atr = (atr_k * a / breakout_price) * 100.0
            tol_eff_pct = float(max(tol_eff_pct, tol_from_atr))

    retest_today = False
    add_on_today = False

    if not np.isnan(breakout_price) and np.isfinite(breakout_price) and breakout_price > 0:
        # today retest: near breakout price + hold above it
        near = (abs(price - breakout_price) / breakout_price * 100.0) <= tol_eff_pct
        held = price >= breakout_price * (1.0 - tol_eff_pct / 100.0)
        retest_today = bool(near and held)

        # retest happened recently (using daily OHLC, not intraday)
        low = df["Low"].astype(float)
        touched = low <= breakout_price * (1.0 + tol_eff_pct / 100.0)
        held_close = close >= breakout_price * (1.0 - tol_eff_pct / 100.0)
        retest_recent = bool((touched & held_close).iloc[-retest_window:].any()) if retest_window > 0 else retest_today

        # add-on: break above short-term high after a recent retest
        high5 = float(df["High"].astype(float).tail(5).max())
        add_on_today = bool(retest_recent and (price > high5))

    return break_level_today, breakout_today, retest_today, add_on_today, breakout_price, breakout_age_bars, tol_eff_pct


# Bulkowski-style retest tolerance by ticker type (변동성 반영)
ETF_1X = {"XLE", "XOP", "OIH", "XLB", "IYE", "IYM"}
ETF_2X = {"GUSH", "ERX", "UCO", "BOIL", "DIG", "UYM"}


def tol_pct_for_ticker(ticker: str) -> float:
    t = ticker.upper()
    if t in ETF_2X:
        return 4.5  # 4.0~5.0
    if t in ETF_1X:
        return 1.75  # 1.5~2.0
    return 2.75  # 개별주 2.5~3.0


def grade(row):
    room = row["room_to_weekly_r1_pct"]
    gap = abs(row["gap_pct"])
    in_middle = row["in_daily_box_middle"]

    if in_middle and gap < 2:
        return "C"

    if (room >= 2.0) and (row.get("daily_breakout") or row.get("daily_retest") or row.get("daily_add_on")) and (room - max(gap, 0) >= 0.8):
        return "A"

    if (room >= 0.8) and (gap >= 1.0 or row.get("daily_breakout") or row.get("daily_retest") or row.get("daily_add_on")):
        return "B"

    return "C"


def score(row):
    s = 0.0
    s += 2.0 if row["weekly_up"] else 0.0
    s += 1.0 if row["px_vs_sma200"] > 0 else 0.0
    s += 1.0 if row["px_vs_sma50"] > 0 else 0.0

    if 50 <= row["rsi14"] <= 65:
        s += 3.0
    elif 65 < row["rsi14"] <= 70:
        s += 1.0
    elif row["rsi14"] > 70:
        s -= 3.0

    if abs(row["gap_pct"]) >= 4:
        s -= 2.0
    elif abs(row["gap_pct"]) >= 2:
        s -= 1.0

    if row["daily_breakout"]:
        s += 2.0
    if row.get("daily_retest", False):
        s += 1.0
    if row.get("daily_add_on", False):
        s += 2.0

    if row["room_to_weekly_r1_pct"] >= 3:
        s += 2.0
    elif row["room_to_weekly_r1_pct"] >= 1.5:
        s += 1.0
    elif row["room_to_weekly_r1_pct"] < 0.7:
        s -= 2.0

    return s


# ---------- IO ----------
def load_tickers(path="tickers.txt"):
    text = Path(path).read_text(encoding="utf-8", errors="ignore").strip()
    tokens = re.split(r"[\s,;]+", text)
    tickers, seen = [], set()
    for tok in tokens:
        tok = tok.strip().upper()
        if not tok or tok.startswith("#"):
            continue
        if tok not in seen:
            seen.add(tok)
            tickers.append(tok)
    return tickers


def load_premarket(path="premarket.csv"):
    p = Path(path)
    if not p.exists():
        return {}
    try:
        pm = pd.read_csv(p)
    except pd.errors.EmptyDataError:
        return {}
    if "ticker" not in pm.columns or "premarket" not in pm.columns:
        return {}
    pm = pm.dropna()
    out = {}
    for _, r in pm.iterrows():
        t = str(r["ticker"]).strip().upper()
        try:
            out[t] = float(r["premarket"])
        except Exception:
            continue
    return out


def normalize_ohlcv(raw: pd.DataFrame, ticker: str) -> pd.DataFrame:
    if raw is None or raw.empty:
        return pd.DataFrame()

    df = raw.copy()

    # ✅ yfinance가 주는 MultiIndex 처리(네 AAPL처럼: level0=Close/High..., level1=AAPL)
    if isinstance(df.columns, pd.MultiIndex):
        lvl0 = df.columns.get_level_values(0)
        lvl1 = df.columns.get_level_values(1)

        if ticker in set(lvl1):
            # (field, ticker) 구조
            df = df.xs(ticker, level=1, axis=1).copy()
        elif ticker in set(lvl0):
            # (ticker, field) 구조
            df = df[ticker].copy()
        else:
            # 마지막 레벨만 남겨보는 fallback
            df.columns = [str(c[-1]) for c in df.columns]

    # 컬럼 정리
    df.columns = [str(c).strip() for c in df.columns]

    # yfinance가 간혹 'Adj Close'만 주거나 컬럼명이 다른 경우 대비
    rename = {}
    for c in df.columns:
        if c.lower() == "adj close":
            rename[c] = "Adj Close"
    if rename:
        df = df.rename(columns=rename)

    required = {"Open", "High", "Low", "Close", "Volume"}
    if not required.issubset(set(df.columns)):
        return pd.DataFrame()

    df = df[["Open", "High", "Low", "Close", "Volume"]].dropna()

    # resample 위해 DateTimeIndex 강제
    df = df.sort_index()
    if not isinstance(df.index, pd.DatetimeIndex):
        df.index = pd.to_datetime(df.index, errors="coerce")
        df = df.dropna()
    if isinstance(df.index, pd.DatetimeIndex) and df.index.tz is not None:
        df.index = df.index.tz_localize(None)

    return df


def main():
    tickers = load_tickers("tickers.txt")
    premarket = load_premarket("premarket.csv")

    rows = []
    for t in tickers:
        try:
            raw = yf.download(
                t, period="5y", interval="1d",
                auto_adjust=False, progress=False,
                threads=False
            )
            df = normalize_ohlcv(raw, t)
            if df.empty or len(df) < 260:
                continue

            close = df["Close"].astype(float)
            sma50 = close.rolling(50).mean()
            sma200 = close.rolling(200).mean()
            rsi14 = rsi(close, 14)

            last_close = float(close.iloc[-1])
            px = float(premarket.get(t, last_close))
            gap_pct = (px / last_close - 1.0) * 100.0 if t in premarket else 0.0

            w = df.resample("W-FRI").agg(
                {"Open": "first", "High": "max", "Low": "min", "Close": "last", "Volume": "sum"}
            ).dropna()
            if len(w) < 60:
                continue

            wclose = w["Close"].astype(float)
            w_sma20 = wclose.rolling(20).mean()
            w_sma50 = wclose.rolling(50).mean()
            weekly_up = bool((wclose.iloc[-1] > w_sma20.iloc[-1]) and (w_sma20.iloc[-1] > w_sma50.iloc[-1]))

            w_r1, w_s1 = swing_levels(w, lookback=min(180, len(w)), pivot=2)
            room_to_weekly_r1_pct = ((w_r1 / px) - 1) * 100 if px > 0 else np.nan

            tol = tol_pct_for_ticker(t)
            daily_break_level, daily_breakout, daily_retest, daily_add_on, daily_breakout_price, breakout_age_bars, tol_eff = breakout_throwback_addon(df, px, lookback=60, tol_pct=tol)

            lo60 = float(df["Low"].rolling(60).min().iloc[-1])
            hi60 = float(df["High"].rolling(60).max().iloc[-1])
            mid60 = (lo60 + hi60) / 2
            in_daily_box_middle = (abs(px - mid60) / mid60 * 100) < 3.0

            row = {
                "ticker": t,
                "price": px,
                "gap_pct": gap_pct,
                "rsi14": float(rsi14.iloc[-1]),
                "px_vs_sma50": (px / float(sma50.iloc[-1]) - 1) * 100,
                "px_vs_sma200": (px / float(sma200.iloc[-1]) - 1) * 100,
                "weekly_up": weekly_up,
                "weekly_r1": w_r1,
                "weekly_s1": w_s1,
                "room_to_weekly_r1_pct": float(room_to_weekly_r1_pct),
                "daily_break_level": float(daily_break_level),
                "daily_breakout": bool(daily_breakout),
                "daily_retest": bool(daily_retest),
                "daily_add_on": bool(daily_add_on),
                "daily_breakout_price": float(daily_breakout_price) if np.isfinite(daily_breakout_price) else np.nan,
                "breakout_age_bars": float(breakout_age_bars) if np.isfinite(breakout_age_bars) else np.nan,
                "tol_eff_pct": float(tol_eff) if np.isfinite(tol_eff) else np.nan,
                "in_daily_box_middle": bool(in_daily_box_middle),
            }
            row["grade"] = grade(row)
            row["score"] = score(row)
            rows.append(row)

        except Exception as e:
            # 여기서는 조용히 스킵(원하면 print로 바꿀 수 있음)
            continue

    out = pd.DataFrame(rows)
    if out.empty:
        print("No usable results. (Most likely: columns format unexpected or tickers invalid.)")
        return

    grade_rank = {"A": 0, "B": 1, "C": 2}
    out["grade_rank"] = out["grade"].map(grade_rank)
    out = out.sort_values(["grade_rank", "score", "gap_pct"], ascending=[True, False, True])

    for c in ["price", "gap_pct", "rsi14", "px_vs_sma50", "px_vs_sma200",
              "room_to_weekly_r1_pct", "weekly_r1", "weekly_s1", "daily_break_level", "score"]:
        out[c] = out[c].astype(float).round(2)

    cols = [
        "ticker", "grade", "score", "price", "gap_pct", "rsi14",
        "room_to_weekly_r1_pct", "weekly_r1",
        "daily_breakout", "daily_retest", "daily_break_level",
        "px_vs_sma50", "px_vs_sma200",
    ]
    print("\n=== WATCHLIST (A -> B -> C) ===")
    print(out[cols].head(20).to_string(index=False))

    out.drop(columns=["grade_rank"]).to_csv("report_v2.csv", index=False)
    print("\nSaved: report_v2.csv")


if __name__ == "__main__":
    main()