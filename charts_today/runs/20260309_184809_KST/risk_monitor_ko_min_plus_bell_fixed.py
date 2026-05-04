import sys
import time
import platform
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import pandas as pd
import yfinance as yf

# =========================
# Settings
# =========================
KST = ZoneInfo("Asia/Seoul")
HKT = ZoneInfo("Asia/Hong_Kong")
USEAST = ZoneInfo("America/New_York")  # for US symbols if needed

INTRADAY_INTERVAL = "5m"
INTRADAY_PERIOD = "2d"
DAILY_PERIOD = "9mo"          # for 60d high + MA calculations
POLL_SEC = 20
HEARTBEAT_SEC = 60

# Treat data as stale if last bar older than 30 min (intraday)
STALE_SEC = 30 * 60

# Sell rules
STOP_LOSS_PCT = -3.0
HIGH_WINDOW = 60
TRAIL_FROM_HIGH_PCT = -3.0    # 60d high breakdown threshold

# Candle close-location grading (Bulkowski-ish "close near high" strength)
# close_pos = (C-L)/(H-L)
BELL_STRONG_POS = 0.85        # 🔔 if close in top 15% of range
BELL_NEUTRAL_POS = 0.55       # ⚪ if close in upper half
# Additionally, treat "close ~ high" as bell if within this pct of high
BELL_NEAR_HIGH_PCT = 0.20     # <= 0.20% from high

# Positions (your inputs)
POSITIONS = [
    {"name": "488080", "ticker": "488080.KS", "entry": 45515.0,  "qty": 260,  "ccy": "KRW", "tz": KST},
    {"name": "000250", "ticker": "000250.KQ", "entry": 799571.0, "qty": 7,    "ccy": "KRW", "tz": KST},
    {"name": "7709",   "ticker": "7709.HK",   "entry": 30.12,    "qty": 3300, "ccy": "HKD", "tz": HKT},
]

SUMMARY_TICKERS = {
    "ZN": "ZN=F",
    "NQ": "NQ=F",
    "KRW": "KRW=X",
    "HK": "7709.HK",
    "SCD": "000250.KQ",
    "TIGER": "488080.KS",
    # Optional semi proxies (often stale during KR daytime)
    "SOX": "^SOX",
    "SMH": "SMH",
    "MU": "MU",
    "SOXL": "SOXL",
}

# =========================
# Terminal colors
# =========================
GREEN = "\033[92m"
RESET = "\033[0m"
UP_RED = "\033[91m"      # 한국식: 상승 빨강
DOWN_BLUE = "\033[94m"   # 하락 파랑

def now_kst_str():
    return datetime.now(KST).strftime("%Y-%m-%d %H:%M:%S")

def fmt_num(x: float, ccy: str) -> str:
    if x is None:
        return "?"
    if ccy == "KRW":
        return f"{x:,.0f}원"
    if ccy == "HKD":
        return f"{x:,.2f}HKD"
    return f"{x:,.2f}"

def pct_change(new: float, old: float) -> float:
    return (new / old - 1.0) * 100.0

def fmt_pct_kor_color(x: float, base_color: str) -> str:
    """Return ▲/▼ with red/blue, then restore base_color."""
    if x > 0:
        return f"{UP_RED}▲{x:.2f}%{base_color}"
    if x < 0:
        return f"{DOWN_BLUE}▼{abs(x):.2f}%{base_color}"
    return f"—0.00%{base_color}"

def yf_download(tickers: list[str], interval: str | None, period: str):
    return yf.download(
        tickers=tickers,
        interval=interval,
        period=period,
        group_by="ticker",
        auto_adjust=False,
        prepost=True,
        progress=False,
        threads=False,
    )

def extract_field_series(df: pd.DataFrame, ticker: str, field: str) -> pd.Series:
    if df is None or df.empty:
        return pd.Series(dtype=float)

    if isinstance(df.columns, pd.MultiIndex):
        cols = df.columns
        if (ticker, field) in cols:
            s = df[(ticker, field)]
        elif (field, ticker) in cols:
            s = df[(field, ticker)]
        else:
            try:
                sub = df[ticker]
                if isinstance(sub, pd.DataFrame) and field in sub.columns:
                    s = sub[field]
                else:
                    return pd.Series(dtype=float)
            except Exception:
                return pd.Series(dtype=float)
    else:
        if field not in df.columns:
            return pd.Series(dtype=float)
        s = df[field]

    if isinstance(s, pd.DataFrame):
        if s.shape[1] == 0:
            return pd.Series(dtype=float)
        s = s.iloc[:, 0]

    return pd.to_numeric(s, errors="coerce").dropna()

def latest_intraday_ohlc(raw_intra: pd.DataFrame, ticker: str, tz: ZoneInfo):
    """
    Returns dict with:
    last_close, ret (vs prev close), stale, day_high, day_low, close_pos, hc_pct, bell_emoji
    """
    s_close = extract_field_series(raw_intra, ticker, "Close")
    s_high  = extract_field_series(raw_intra, ticker, "High")
    s_low   = extract_field_series(raw_intra, ticker, "Low")

    if s_close.empty or len(s_close) < 3:
        return None

    ts = s_close.index[-1]
    cur = float(s_close.iloc[-1])
    prev = float(s_close.iloc[-2])

    ts_utc = ts.to_pydatetime().astimezone(timezone.utc)
    age = (datetime.now(timezone.utc) - ts_utc).total_seconds()
    stale = age >= STALE_SEC

    # Determine "today" in instrument's local timezone based on latest bar's local date
    try:
        idx_local = pd.DatetimeIndex(s_high.index).tz_convert(tz) if s_high.index.tz is not None else pd.DatetimeIndex(s_high.index).tz_localize("UTC").tz_convert(tz)
    except Exception:
        # fallback: treat as UTC
        idx_local = pd.DatetimeIndex(s_high.index)

    if len(idx_local) == 0 or s_high.empty or s_low.empty:
        day_high = None
        day_low = None
    else:
        today = idx_local[-1].date()
        mask = (idx_local.date == today)
        day_high = float(pd.Series(s_high.values, index=idx_local)[mask].max())
        day_low = float(pd.Series(s_low.values, index=idx_local)[mask].min())

    # Close position within day's range
    bell = "❔"
    close_pos = None
    hc_pct = None
    if day_high is not None and day_low is not None and day_high > day_low:
        close_pos = (cur - day_low) / (day_high - day_low)
        hc_pct = (day_high - cur) / cur * 100.0

        # Emoji grading
        if hc_pct <= BELL_NEAR_HIGH_PCT or close_pos >= BELL_STRONG_POS:
            bell = "🔔"   # close near high
        elif close_pos >= BELL_NEUTRAL_POS:
            bell = "⚪"   # neutral/okay
        else:
            bell = "⚠️"   # sold off from high
    else:
        bell = "❔"

    return {
        "last": cur,
        "ret": pct_change(cur, prev),
        "stale": stale,
        "age": age,
        "day_high": day_high,
        "day_low": day_low,
        "close_pos": close_pos,
        "hc_pct": hc_pct,
        "bell": bell,
    }

def compute_high60(close_daily: pd.Series) -> float | None:
    if close_daily is None or close_daily.empty:
        return None
    c = close_daily.dropna()
    if c.empty:
        return None
    if len(c) >= HIGH_WINDOW:
        return float(c.iloc[-HIGH_WINDOW:].max())
    return float(c.max())

def sell_plan(qty: int, pl_pct: float, e_signal: bool, t_signal: bool):
    if not (e_signal or t_signal):
        return 0, "HOLD"

    if e_signal and t_signal:
        frac = 0.75
    elif e_signal:
        frac = 0.50
    else:
        frac = 0.25

    if pl_pct <= -7.0:
        frac = 1.0
    elif pl_pct <= -5.0:
        frac = max(frac, 0.75)

    sell_qty = int(round(qty * frac))
    sell_qty = max(1, min(qty, sell_qty))
    return sell_qty, f"SELL {sell_qty}주({int(frac*100)}%)"

def core(label: str, mv: dict | None, base_color: str):
    if mv is None:
        return f"{label}:?"
    if mv["stale"]:
        return f"{label}:지연"
    return f"{label}:{fmt_pct_kor_color(mv['ret'], base_color)}"

def main():
    last_print = 0.0

    intraday_list = list(SUMMARY_TICKERS.values())
    daily_list = list({p["ticker"] for p in POSITIONS})

    while True:
        try:
            raw_intra = yf_download(intraday_list, interval=INTRADAY_INTERVAL, period=INTRADAY_PERIOD)

            # Intraday summary moves (simple close-to-close %)
            intra = {}
            for key, tkr in SUMMARY_TICKERS.items():
                # For summary, only need Close
                s_close = extract_field_series(raw_intra, tkr, "Close")
                if s_close.empty or len(s_close) < 3:
                    intra[key] = None
                    continue

                ts = s_close.index[-1]
                cur = float(s_close.iloc[-1])
                prev = float(s_close.iloc[-2])

                ts_utc = ts.to_pydatetime().astimezone(timezone.utc)
                age = (datetime.now(timezone.utc) - ts_utc).total_seconds()
                stale = age >= STALE_SEC

                intra[key] = {"last": cur, "ret": pct_change(cur, prev), "stale": stale, "age": age}

            # Daily for 60d high (sell rule T)
            raw_daily = yf_download(daily_list, interval="1d", period=DAILY_PERIOD)
            daily_close = {}
            for tkr in daily_list:
                daily_close[tkr] = extract_field_series(raw_daily, tkr, "Close")

            # Semi display
            semi_key = None
            for k in ["SOX", "SMH", "MU", "SOXL"]:
                mv = intra.get(k)
                if mv is not None and (not mv["stale"]):
                    semi_key = k
                    break
            semi_txt = "SEMI=지연" if semi_key is None else f"SEMI({semi_key})={fmt_pct_kor_color(intra[semi_key]['ret'], GREEN)}"

            # State (simple macro watch)
            state = "OK"
            zn = intra.get("ZN")
            nq = intra.get("NQ")
            if zn and nq and (not zn["stale"]) and (not nq["stale"]):
                if (zn["ret"] <= -0.10) and (nq["ret"] <= -0.35):
                    state = "WATCH(매크로)"

            # ---------- Line 1 (GREEN)
            line1 = (
                f"[{now_kst_str()}] {state} | "
                f"{core('ZN', intra.get('ZN'), GREEN)} {core('NQ', intra.get('NQ'), GREEN)} {core('KRW', intra.get('KRW'), GREEN)} "
                f"{core('7709', intra.get('HK'), GREEN)} {core('000250', intra.get('SCD'), GREEN)} {core('488080', intra.get('TIGER'), GREEN)} | "
                f"{semi_txt}"
            )

            # ---------- Line 2 (white) PNL + candle bell
            pnl_parts = []
            action_parts = []
            sell_parts = []
            candle_parts = []
            total_pnl = {"KRW": 0.0, "HKD": 0.0}

            for p in POSITIONS:
                tkr = p["ticker"]

                # Candle stats (today H/L, last close, bell)
                candle_mv = latest_intraday_ohlc(raw_intra, tkr, p["tz"])

                # Current price: prefer intraday close if not stale, else daily close
                cur_price = None
                if candle_mv is not None and (not candle_mv["stale"]):
                    cur_price = candle_mv["last"]
                else:
                    dc = daily_close.get(tkr)
                    cur_price = float(dc.iloc[-1]) if dc is not None and not dc.empty else (candle_mv["last"] if candle_mv else None)

                if cur_price is None:
                    pnl_parts.append(f"{p['name']}:가격없음")
                    action_parts.append(f"{p['name']}:?")
                    sell_parts.append(f"{p['name']}:?")
                    candle_parts.append(f"{p['name']}:❔")
                    continue

                pl_pct = (cur_price / p["entry"] - 1.0) * 100.0
                pnl_amt = (cur_price - p["entry"]) * p["qty"]
                total_pnl[p["ccy"]] += pnl_amt

                # Sell signals
                e_signal = (pl_pct <= STOP_LOSS_PCT)

                high60 = compute_high60(daily_close.get(tkr))
                t_signal = False
                if high60 is not None and high60 > 0:
                    dd_from_high = (cur_price / high60 - 1.0) * 100.0
                    t_signal = (dd_from_high <= TRAIL_FROM_HIGH_PCT)

                sell = e_signal or t_signal
                reason = ("E" if e_signal else "") + ("T" if t_signal else "")
                if reason == "":
                    reason = "-"

                # ACTION: only if sell signal
                if sell:
                    _, action = sell_plan(p["qty"], pl_pct, e_signal, t_signal)
                else:
                    action = "HOLD"

                pnl_parts.append(
                    f"{p['name']} {fmt_num(cur_price, p['ccy'])} {fmt_pct_kor_color(pl_pct, RESET)}({fmt_num(pnl_amt, p['ccy'])})"
                )
                action_parts.append(f"{p['name']}:{action}")
                sell_parts.append(f"{p['name']}={1 if sell else 0}{reason}")

                # Candle: show today's High/Close and (H-C)/C
                if candle_mv and candle_mv["day_high"] is not None:
                    h = candle_mv["day_high"]
                    c = candle_mv["last"]
                    hc = candle_mv["hc_pct"]
                    bell = candle_mv["bell"]
                    # Use bell icon if strong, otherwise keep icon as is
                    candle_parts.append(
                        f"{p['name']}{bell} H{fmt_num(h, p['ccy'])} C{fmt_num(c, p['ccy'])} (H−C {hc:.2f}%)"
                    )
                else:
                    candle_parts.append(f"{p['name']}:❔")

            line2 = (
                "PNL | " + " | ".join(pnl_parts) +
                f" | TOTAL KRW:{total_pnl['KRW']:,.0f}원 HKD:{total_pnl['HKD']:,.2f}HKD" +
                " | ACTION " + " ".join(action_parts) +
                " | sell_signal=" + " ".join(sell_parts) +
                " | CANDLE " + " || ".join(candle_parts)
            )

            now = time.time()
            if now - last_print >= HEARTBEAT_SEC:
                # First line always green, second line default color (white)
                print(GREEN + line1 + RESET)
                print(line2)
                last_print = now

        except Exception as e:
            print(f"[{now_kst_str()}] ERROR: {e}", file=sys.stderr)

        time.sleep(POLL_SEC)

if __name__ == "__main__":
    main()