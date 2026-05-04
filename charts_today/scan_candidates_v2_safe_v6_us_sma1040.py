import re
import argparse
from pathlib import Path

import numpy as np
import pandas as pd
import yfinance as yf
from datetime import datetime
from zoneinfo import ZoneInfo
import io
from contextlib import redirect_stdout, redirect_stderr


def safe_download(symbol: str) -> pd.DataFrame:
    """Download OHLCV silently (suppresses yfinance console spam)."""
    buf = io.StringIO()
    with redirect_stdout(buf), redirect_stderr(buf):
        return yf.download(
            symbol,
            period="5y",
            interval="1d",
            auto_adjust=False,
            progress=False,
            threads=False,
        )



# ---------- indicators ----------
def rsi(series: pd.Series, period=14) -> pd.Series:
    delta = series.diff()
    gain = delta.clip(lower=0).rolling(period).mean()
    loss = (-delta.clip(upper=0)).rolling(period).mean()
    rs = gain / loss.replace(0, np.nan)
    return 100 - (100 / (1 + rs))




def ma_slope_label(ma: pd.Series, lookback: int = 3) -> str:
    """Simple MA slope label for downstream review scripts."""
    m = ma.dropna()
    if len(m) < lookback + 1:
        return ""
    now = float(m.iloc[-1])
    prev = float(m.iloc[-1 - lookback])
    if now > prev:
        return "UP"
    if now < prev:
        return "DN"
    return "FLAT"


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


def breakout_and_retest(df: pd.DataFrame, price: float, lookback=60, tol_pct=0.6):
    hi = float(df["High"].rolling(lookback).max().iloc[-1])
    breakout = price > hi
    retest = (abs(price - hi) / hi * 100) <= tol_pct
    return hi, breakout, retest


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

    if (room >= 2.0) and (row["daily_breakout"] or row["daily_retest"]) and (room - max(gap, 0) >= 0.8):
        return "A"

    if (room >= 0.8) and (gap >= 1.0 or row["daily_breakout"] or row["daily_retest"]):
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
    if row["daily_retest"]:
        s += 1.0

    if row["room_to_weekly_r1_pct"] >= 3:
        s += 2.0
    elif row["room_to_weekly_r1_pct"] >= 1.5:
        s += 1.0
    elif row["room_to_weekly_r1_pct"] < 0.7:
        s -= 2.0

    return s


# ---------- IO ----------
def load_tickers_file(path: str) -> list[str]:
    """Load tickers from a text file.

    Fixes:
      - Ignores full-line comments starting with '#'
      - Strips inline comments (everything after '#')
      - Filters out non-ticker tokens (CORE_STOCKS, (테마), etc.)
    """
    p = Path(path)
    if not p.exists():
        return []

    # Allowed Yahoo-like symbols: AAPL, BRK.B, BRK-B, ^VIX, CL=F, DX-Y.NYB
    ticker_re = re.compile(r"^[A-Z0-9\^=\.\-]{1,20}$")

    tickers: list[str] = []
    seen = set()

    for line in p.read_text(encoding="utf-8", errors="ignore").splitlines():
        raw = line.strip()
        if not raw:
            continue
        if raw.startswith("#"):
            continue

        # remove inline comment
        if "#" in raw:
            raw = raw.split("#", 1)[0].strip()
        if not raw:
            continue

        for tok in re.split(r"[\s,;]+", raw):
            tok = tok.strip().upper()
            if not tok:
                continue
            if not ticker_re.match(tok):
                continue
            if tok not in seen:
                seen.add(tok)
                tickers.append(tok)

    return tickers

def load_premarket(path="premarket.csv") -> dict[str, float]:
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
    out: dict[str, float] = {}
    for _, r in pm.iterrows():
        t = str(r["ticker"]).strip().upper()
        try:
            out[t] = float(r["premarket"])
        except Exception:
            continue
    return out


def build_universe(args, premarket: dict[str, float]) -> list[str]:
    """
    너가 말한 'txt 3' 기준 default universe:
      - tickers_core.txt + tickers_leverage2x.txt
    추가 옵션:
      - --extra tickers_extra.txt (원하면)
      - 그리고 premarket.csv에 있는 티커는 무조건 합침 (manual-only도 포함)
    """
    base = []
    # explicit override: --universe 하나만 쓰고 싶으면
    if args.universe:
        base = load_tickers_file(args.universe)
    else:
        # default: core + leverage2x (둘 다 없으면 tickers.txt fallback)
        core = load_tickers_file(args.core)
        lev2 = load_tickers_file(args.lev2)
        base = core + lev2
        if not base:
            base = load_tickers_file("tickers.txt")

    if args.extra:
        base += load_tickers_file(args.extra)

    # union + keep order
    seen = set()
    out = []
    for t in base:
        if t not in seen:
            seen.add(t)
            out.append(t)
    for t in premarket.keys():
        if t not in seen:
            seen.add(t)
            out.append(t)
    return out


def normalize_ohlcv(raw: pd.DataFrame, ticker: str) -> pd.DataFrame:
    if raw is None or raw.empty:
        return pd.DataFrame()

    df = raw.copy()

    # yfinance MultiIndex 처리
    if isinstance(df.columns, pd.MultiIndex):
        lvl0 = df.columns.get_level_values(0)
        lvl1 = df.columns.get_level_values(1)

        if ticker in set(lvl1):
            df = df.xs(ticker, level=1, axis=1).copy()
        elif ticker in set(lvl0):
            df = df[ticker].copy()
        else:
            df.columns = [str(c[-1]) for c in df.columns]

    df.columns = [str(c).strip() for c in df.columns]

    required = {"Open", "High", "Low", "Close", "Volume"}
    if not required.issubset(set(df.columns)):
        return pd.DataFrame()

    df = df[["Open", "High", "Low", "Close", "Volume"]].dropna()
    df = df.sort_index()

    if not isinstance(df.index, pd.DatetimeIndex):
        df.index = pd.to_datetime(df.index, errors="coerce")
        df = df.dropna()
    if isinstance(df.index, pd.DatetimeIndex) and df.index.tz is not None:
        df.index = df.index.tz_localize(None)

    return df


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--premarket", default="premarket.csv")
    ap.add_argument("--universe", default=None, help="single tickers file override (optional)")
    ap.add_argument("--core", default="tickers_core.txt")
    ap.add_argument("--lev2", default="tickers_leverage2x.txt")
    ap.add_argument("--extra", default=None, help="optional extra tickers file")
    args = ap.parse_args()

    premarket = load_premarket(args.premarket)
    tickers = build_universe(args, premarket)

    skipped = []  # (ticker, reason)
    rows = []

    for t in tickers:
        try:
            raw = safe_download(t)
            df = normalize_ohlcv(raw, t)
            if df.empty:
                skipped.append((t, "no_ohlcv_or_columns"))
                continue
            if len(df) < 260:
                skipped.append((t, f"too_short(len={len(df)})"))
                continue

            close = df["Close"].astype(float)
            sma10 = close.rolling(10).mean()
            sma40 = close.rolling(40).mean()
            sma50 = close.rolling(50).mean()
            sma200 = close.rolling(200).mean()
            rsi14 = rsi(close, 14)

            last_close = float(close.iloc[-1])
            px = float(premarket.get(t, last_close))
            gap_pct = (px / last_close - 1.0) * 100.0 if t in premarket else 0.0

            w = df.resample("W-FRI").agg({"Open": "first", "High": "max", "Low": "min", "Close": "last", "Volume": "sum"}).dropna()
            if len(w) < 60:
                skipped.append((t, f"weekly_too_short(len={len(w)})"))
                continue

            wclose = w["Close"].astype(float)
            w_sma20 = wclose.rolling(20).mean()
            w_sma50 = wclose.rolling(50).mean()
            weekly_up = bool((wclose.iloc[-1] > w_sma20.iloc[-1]) and (w_sma20.iloc[-1] > w_sma50.iloc[-1]))

            w_r1, w_s1 = swing_levels(w, lookback=min(180, len(w)), pivot=2)
            room_to_weekly_r1_pct = ((w_r1 / px) - 1) * 100 if px > 0 else np.nan

            tol = tol_pct_for_ticker(t)
            daily_break_level, daily_breakout, daily_retest = breakout_and_retest(df, px, lookback=60, tol_pct=tol)

            lo60 = float(df["Low"].rolling(60).min().iloc[-1])
            hi60 = float(df["High"].rolling(60).max().iloc[-1])
            mid60 = (lo60 + hi60) / 2
            in_daily_box_middle = (abs(px - mid60) / mid60 * 100) < 3.0

            sma10_last = float(sma10.iloc[-1]) if pd.notna(sma10.iloc[-1]) else np.nan
            sma40_last = float(sma40.iloc[-1]) if pd.notna(sma40.iloc[-1]) else np.nan
            sma50_last = float(sma50.iloc[-1]) if pd.notna(sma50.iloc[-1]) else np.nan
            sma200_last = float(sma200.iloc[-1]) if pd.notna(sma200.iloc[-1]) else np.nan

            row = {
                "ticker": t,
                "price": px,
                "gap_pct": gap_pct,
                "rsi14": float(rsi14.iloc[-1]),
                "sma10": sma10_last,
                "sma40": sma40_last,
                "sma50": sma50_last,
                "sma200": sma200_last,
                "px_vs_sma10": ((px / sma10_last) - 1) * 100 if pd.notna(sma10_last) and sma10_last != 0 else np.nan,
                "px_vs_sma40": ((px / sma40_last) - 1) * 100 if pd.notna(sma40_last) and sma40_last != 0 else np.nan,
                "px_vs_sma50": ((px / sma50_last) - 1) * 100 if pd.notna(sma50_last) and sma50_last != 0 else np.nan,
                "px_vs_sma200": ((px / sma200_last) - 1) * 100 if pd.notna(sma200_last) and sma200_last != 0 else np.nan,
                "sma10_slope": ma_slope_label(sma10, lookback=3),
                "sma40_slope": ma_slope_label(sma40, lookback=3),
                "weekly_up": weekly_up,
                "weekly_r1": w_r1,
                "weekly_s1": w_s1,
                "room_to_weekly_r1_pct": float(room_to_weekly_r1_pct),
                "daily_break_level": float(daily_break_level),
                "daily_breakout": bool(daily_breakout),
                "daily_retest": bool(daily_retest),
                "in_daily_box_middle": bool(in_daily_box_middle),
            }
            row["grade"] = grade(row)
            row["score"] = score(row)
            rows.append(row)

        except Exception as e:
            skipped.append((t, f"exception:{type(e).__name__}"))
            continue

    if skipped:
        Path("scan_skipped.log").write_text("\n".join([f"{t}\t{r}" for t, r in skipped]), encoding="utf-8")

    out = pd.DataFrame(rows)
    if out.empty:
        print("No usable results. (Most likely: columns format unexpected or tickers invalid.)")
        if skipped:
            print("See scan_skipped.log for skip reasons.")
        return

    grade_rank = {"A": 0, "B": 1, "C": 2}
    out["grade_rank"] = out["grade"].map(grade_rank)
    out = out.sort_values(["grade_rank", "score", "gap_pct"], ascending=[True, False, True])

    for c in ["price", "gap_pct", "rsi14",
              "sma10", "sma40", "sma50", "sma200",
              "px_vs_sma10", "px_vs_sma40", "px_vs_sma50", "px_vs_sma200",
              "room_to_weekly_r1_pct", "weekly_r1", "weekly_s1", "daily_break_level", "score"]:
        out[c] = out[c].astype(float).round(2)

    cols = [
        "ticker", "grade", "score", "price", "gap_pct", "rsi14",
        "room_to_weekly_r1_pct", "weekly_r1",
        "daily_breakout", "daily_retest", "daily_break_level",
        "px_vs_sma10", "sma10_slope",
        "px_vs_sma40", "sma40_slope",
        "px_vs_sma50", "px_vs_sma200",
    ]
    print("\n=== WATCHLIST (A -> B -> C) ===")
    print(out[cols].head(25).to_string(index=False))

    # ---- output files (timestamped, KST) ----
    ts = datetime.now(ZoneInfo("Asia/Seoul")).strftime("%Y%m%d_%H%M%S")
    stamped = f"report_v2_{ts}_KST.csv"

    final = out.drop(columns=["grade_rank"])
    final.to_csv(stamped, index=False)          # immutable snapshot
    final.to_csv("report_v2.csv", index=False)  # latest (compat)

    print(f"\nSaved: {stamped}")
    print("Saved: report_v2.csv (latest)")
    if skipped:
        print("Saved: scan_skipped.log (skipped tickers + reasons)")


if __name__ == "__main__":
    main()