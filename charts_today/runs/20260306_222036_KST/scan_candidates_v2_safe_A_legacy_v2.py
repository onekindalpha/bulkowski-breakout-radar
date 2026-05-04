import re
from pathlib import Path

import numpy as np
import pandas as pd
import yfinance as yf
import argparse
import contextlib
import io




# ---------- legacy controls ----------
def _read_lines(path: str) -> list[str]:
    p = Path(path)
    if not p.exists():
        return []
    out: list[str] = []
    for line in p.read_text(encoding="utf-8", errors="ignore").splitlines():
        s = line.strip()
        if not s or s.startswith("#"):
            continue
        if "#" in s:
            s = s.split("#", 1)[0].strip()
        if not s:
            continue
        out.append(s)
    return out

def _append_skip(skipfile: str, ticker: str, reason: str = ""):
    try:
        with open(skipfile, "a", encoding="utf-8") as f:
            f.write(f"{ticker}\t{reason}\n")
    except Exception:
        pass

def _yf_download_one(ticker: str, period: str, interval: str, quiet: bool) -> pd.DataFrame:
    """Download OHLCV from yfinance, but never crash, and optionally suppress spam."""
    try:
        buf_out = io.StringIO() if quiet else None
        buf_err = io.StringIO() if quiet else None
        cm_out = contextlib.redirect_stdout(buf_out) if quiet else contextlib.nullcontext()
        cm_err = contextlib.redirect_stderr(buf_err) if quiet else contextlib.nullcontext()
        with cm_out, cm_err:
            df = yf.download(
                ticker,
                period=period,
                interval=interval,
                auto_adjust=False,
                progress=False,
                threads=False,
            )
        if df is None:
            return pd.DataFrame()
        return df
    except Exception:
        return pd.DataFrame()


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
def load_tickers(path: str) -> list[str]:
    """Read tickers from a text file (one per line or separated by spaces/commas)."""
    if not Path(path).exists():
        return []
    raw_lines = Path(path).read_text(encoding="utf-8", errors="ignore").splitlines()
    tokens: list[str] = []
    for line in raw_lines:
        s = line.strip()
        if not s or s.startswith("#"):
            continue
        if "#" in s:
            s = s.split("#", 1)[0].strip()
        if not s:
            continue
        tokens += re.split(r"[\s,;]+", s)
    tickers: list[str] = []
    seen = set()
    for tok in tokens:
        t = tok.strip().upper()
        if not t:
            continue
        if t not in seen:
            seen.add(t)
            tickers.append(t)
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


def _load_ticker_set_from_csv(path: str, ticker_col: str = "ticker") -> set[str]:
    p = Path(path)
    if not p.exists():
        return set()
    try:
        df = pd.read_csv(p)
    except Exception:
        return set()
    if ticker_col not in df.columns:
        # try common alternatives
        for c in df.columns:
            if str(c).strip().lower() in ("symbol", "code", "종목", "티커"):
                ticker_col = c
                break
        else:
            return set()
    return {str(x).strip().upper() for x in df[ticker_col].dropna().tolist() if str(x).strip()}



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
    ap = argparse.ArgumentParser()
    ap.add_argument("--universe-file", default=None, help="Ticker list file to scan (default: universe_filtered.txt if exists, else tickers.txt)")
    ap.add_argument("--premarket", default="premarket.csv", help="Premarket merged CSV (default: premarket.csv)")
    ap.add_argument("--manual-csv", default="premarket_manual.csv", help="Manual prices CSV to tag px_source (default: premarket_manual.csv)")
    ap.add_argument("--auto-csv", default="premarket_auto.csv", help="Auto prices CSV to tag px_source (default: premarket_auto.csv)")

    ap.add_argument("--period", default="5y", help="yfinance period (default: 5y)")
    ap.add_argument("--interval", default="1d", help="yfinance interval (default: 1d)")
    ap.add_argument("--skipfile", default="yf_missing_legacy.txt", help="Local skiplist for missing Yahoo symbols")
    ap.add_argument("--refresh-skip", action="store_true", help="Ignore existing skipfile for this run")
    ap.add_argument("--no-quiet", action="store_false", dest="quiet", default=True, help="Show yfinance spam (default: quiet)")
    ap.add_argument("--quiet", action="store_true", help="(kept for compatibility; quiet is ON by default)")
    args = ap.parse_args()

    universe_path = args.universe_file or ("universe_filtered.txt" if Path("universe_filtered.txt").exists() else "tickers.txt")
    base_tickers = load_tickers(universe_path)

    if (not args.refresh_skip) and Path(args.skipfile).exists():
        skipped = {line.split("\t", 1)[0].strip().upper() for line in _read_lines(args.skipfile)}
    else:
        skipped = set()

    tickers = [t for t in base_tickers if t not in skipped]
    premarket = load_premarket(args.premarket)

    manual_set = _load_ticker_set_from_csv(args.manual_csv)
    auto_set = _load_ticker_set_from_csv(args.auto_csv)
    # classify sources only within our current universe
    manual_in_uni = sum(1 for t in tickers if t in manual_set)
    auto_in_uni = sum(1 for t in tickers if (t in premarket and t not in manual_set))
    close_in_uni = len(tickers) - sum(1 for t in tickers if t in premarket)



    try:
        now_kst = pd.Timestamp.utcnow().tz_convert("Asia/Seoul").strftime("%Y-%m-%d %H:%M:%S KST")
    except Exception:
        now_kst = ""
    print(f"KST_NOW: {now_kst}")
    print(f"UNIVERSE: {universe_path}  tickers={len(base_tickers)}  using={len(tickers)}  skipped={len(skipped)}")
    print(f"PRICE_SOURCES in universe: manual={manual_in_uni} | auto={auto_in_uni} | close_only={close_in_uni}")
    if manual_in_uni > 0:
        # show manual tickers list (short)
        man_list = [t for t in tickers if t in manual_set]
        print("MANUAL_TICKERS:", ", ".join(man_list[:30]) + (" ..." if len(man_list)>30 else ""))

    if len(skipped) > 0:
        print(f"skipfile: {args.skipfile}  (use --refresh-skip to ignore)")
    print()


    rows = []
    for t in tickers:
        try:
            raw = _yf_download_one(t, period=args.period, interval=args.interval, quiet=args.quiet)
            if raw is None or (hasattr(raw, 'empty') and raw.empty):
                _append_skip(args.skipfile, t, 'no_data')
                continue
            try:
                df = normalize_ohlcv(raw, t)
            except Exception as e:
                _append_skip(args.skipfile, t, f"normalize:{type(e).__name__}")
                continue
            if df.empty or len(df) < 260:
                continue

            close = df["Close"].astype(float)
            sma50 = close.rolling(50).mean()
            sma200 = close.rolling(200).mean()
            rsi14 = rsi(close, 14)

            last_close = float(close.iloc[-1])
            px = float(premarket.get(t, last_close))
            px_source = (
                'manual' if t in manual_set else ('auto' if t in premarket else 'close')
            )
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
            daily_break_level, daily_breakout, daily_retest = breakout_and_retest(df, px, lookback=60, tol_pct=tol)

            lo60 = float(df["Low"].rolling(60).min().iloc[-1])
            hi60 = float(df["High"].rolling(60).max().iloc[-1])
            mid60 = (lo60 + hi60) / 2
            in_daily_box_middle = (abs(px - mid60) / mid60 * 100) < 3.0

            row = {
                "ticker": t,
                "price": px,
                "gap_pct": gap_pct,
                "px_source": px_source,
                "rsi14": float(rsi14.iloc[-1]),
                "px_vs_sma50": (px / float(sma50.iloc[-1]) - 1) * 100,
                "px_vs_sma200": (px / float(sma200.iloc[-1]) - 1) * 100,
                "weekly_up": weekly_up,
                "weekly_r1": w_r1,
                "weekly_s1": w_s1,
                "room_to_weekly_r1_pct": float(room_to_weekly_r1_pct),
                "daily_break_level": float(daily_break_level),
                "break_diff": float(px - daily_break_level),
                "break_diff_pct": float((px / daily_break_level - 1) * 100.0) if daily_break_level else 0.0,
                "daily_breakout": bool(daily_breakout),
                "daily_retest": bool(daily_retest),
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
        "ticker", "grade", "score", "px_source", "price", "daily_break_level", "break_diff", "break_diff_pct", "gap_pct", "rsi14",
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