import sys
import time
import platform
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import pandas as pd
import yfinance as yf

KST = ZoneInfo("Asia/Seoul")

# ---------- 주기/신선도 ----------
INTRADAY_INTERVAL = "5m"
INTRADAY_PERIOD = "2d"
DAILY_PERIOD = "9mo"         # 60일 고점 계산용
POLL_SEC = 20
HEARTBEAT_SEC = 60
STALE_SEC = 30 * 60          # 30분 넘게 갱신 없으면 '지연'

# ---------- 손절 규칙 ----------
STOP_LOSS_PCT = -3.0         # E: 진입가 대비 -3%
HIGH_WINDOW = 60             # 60일 고점
TRAIL_FROM_HIGH_PCT = -3.0   # T: 60일 고점 대비 -3% 이상 하락하면 breakdown으로 간주(트레일링)

# ---------- 보유 포지션(네 입력 그대로) ----------
POSITIONS = [
    {"name": "488080", "ticker": "488080.KS", "entry": 45515.0,  "qty": 260,  "ccy": "KRW"},
    {"name": "000250", "ticker": "000250.KQ", "entry": 799571.0, "qty": 7,    "ccy": "KRW"},  # 코스닥 -> .KQ
    {"name": "7709",   "ticker": "7709.HK",   "entry": 30.12,    "qty": 3300, "ccy": "HKD"},
]

# ---------- 요약(원래 줄에 포함할 것들) ----------
SUMMARY_TICKERS = {
    "ZN": "ZN=F",
    "NQ": "NQ=F",
    "KRW": "KRW=X",
    "HK": "7709.HK",
    "SCD": "000250.KQ",
    "TIGER": "488080.KS",
    # SEMI 판단용(미국 장 열릴 때만 LIVE인 경우 많음)
    "SOX": "^SOX",
    "SMH": "SMH",
    "MU": "MU",
    "SOXL": "SOXL",
}

UP_RED = "\033[91m"      # 상승: 빨강(한국식)
DOWN_BLUE = "\033[94m"   # 하락: 파랑

def fmt_pct_kor_color(x: float, base_color: str) -> str:
    """x(%)를 ▲/▼로 표시하면서, 상승=빨강/하락=파랑. 찍고 나서 base_color로 복귀."""
    if x > 0:
        return f"{UP_RED}▲{x:.2f}%{base_color}"
    if x < 0:
        return f"{DOWN_BLUE}▼{abs(x):.2f}%{base_color}"
    return f"—0.00%{base_color}"

GREEN = "\033[92m"; YELLOW = "\033[93m"; RED = "\033[91m"; RESET = "\033[0m"

def now_kst_str():
    return datetime.now(KST).strftime("%Y-%m-%d %H:%M:%S")

def mac_notify(title: str, message: str):
    if platform.system() != "Darwin":
        return
    import subprocess
    script = f'display notification "{message}" with title "{title}"'
    subprocess.run(["osascript", "-e", script], check=False)

def fmt_pct(x: float) -> str:
    if x > 0: return f"▲{x:.2f}%"
    if x < 0: return f"▼{abs(x):.2f}%"
    return "—0.00%"

def fmt_num(x: float, ccy: str) -> str:
    if ccy == "KRW":
        return f"{x:,.0f}원"
    if ccy == "HKD":
        return f"{x:,.2f}HKD"
    return f"{x:,.2f}"

def pct_change(new: float, old: float) -> float:
    return (new / old - 1.0) * 100.0

# ---- yfinance: MultiIndex/단일 모두 안전하게 Close 시리즈 추출
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

def latest_intraday(series_close: pd.Series, series_high: pd.Series):
    """
    intraday(5m) 기준:
    - ret: 직전 bar 대비 변화율 (Close)
    - last: 현재가 (Close)
    - day_high: 오늘 고점 (intraday High의 당일 최대)
    - stale: 데이터 신선도
    """
    if series_close.empty or len(series_close) < 3:
        return None

    ts = series_close.index[-1]
    cur = float(series_close.iloc[-1])
    prev = float(series_close.iloc[-2])

    ts_utc = ts.to_pydatetime().astimezone(timezone.utc)
    age = (datetime.now(timezone.utc) - ts_utc).total_seconds()
    stale = age >= STALE_SEC

    # 오늘 날짜(거래소 로컬 기준이라 약간 차이 가능. 그래도 실전엔 충분)
    if series_high is not None and not series_high.empty:
        day = series_high.index[-1].date()
        day_high = float(series_high[series_high.index.date == day].max())
    else:
        day_high = None

    return {"last": cur, "ret": pct_change(cur, prev), "stale": stale, "day_high": day_high}
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
    """
    '몇 % 손실이면 몇 개 팔지' 룰:
    - E만: 50%
    - T만: 25%
    - E+T: 75%
    - 손실 심화: <= -5%면 최소 75%, <= -7%면 100%
    """
    if not (e_signal or t_signal):
        return 0, "HOLD"

    # 기본 비율
    if e_signal and t_signal:
        frac = 0.75
    elif e_signal:
        frac = 0.50
    else:
        frac = 0.25

    # 손실 심화 시 강제 상향
    if pl_pct <= -7.0:
        frac = 1.0
    elif pl_pct <= -5.0:
        frac = max(frac, 0.75)

    sell_qty = int(round(qty * frac))
    sell_qty = max(1, min(qty, sell_qty))
    return sell_qty, f"SELL {sell_qty}주({int(frac*100)}%)"

def core(label, mv):
    # 요약용 표기: LIVE면 % / stale면 지연 / None이면 ?
    if mv is None:
        return f"{label}:?"
    if mv["stale"]:
        return f"{label}:지연"
    return f"{label}:{fmt_pct_kor_color(mv['ret'], GREEN)}"

def main():
    last_print = 0.0

    intraday_list = list(SUMMARY_TICKERS.values())
    daily_list = list({p["ticker"] for p in POSITIONS})  # 중복 제거

    while True:
        try:
            # 1) intraday 5m: 요약 줄 + 포지션 현재가로도 사용
            raw_intra = yf_download(intraday_list, interval=INTRADAY_INTERVAL, period=INTRADAY_PERIOD)
            intra = {}
            for key, tkr in SUMMARY_TICKERS.items():
                s_close = extract_field_series(raw_intra, tkr, "Close")
                s_high  = extract_field_series(raw_intra, tkr, "High")
                intra[key] = latest_intraday(s_close, s_high)

            # 2) daily 1d: 60일 고점 계산용
            raw_daily = yf_download(daily_list, interval="1d", period=DAILY_PERIOD)
            daily_close = {}
            for tkr in daily_list:
                daily_close[tkr] = extract_field_series(raw_daily, tkr, "Close")

            # ---- SEMI 표시(라이브일 때만 %로, 아니면 지연)
            semi_key = None
            for k in ["SOX", "SMH", "MU", "SOXL"]:
                mv = intra.get(k)
                if mv is not None and (not mv["stale"]):
                    semi_key = k
                    break
            semi_txt = "SEMI=지연" if semi_key is None else f"SEMI({semi_key})={fmt_pct(intra[semi_key]['ret'])}"

            # ---- 상태(간단): 금리↑(ZN↓) + NQ↓면 WATCH
            state = "OK"
            color = GREEN
            zn = intra.get("ZN")
            nq = intra.get("NQ")
            if zn and nq and (not zn["stale"]) and (not nq["stale"]):
                if (zn["ret"] <= -0.10) and (nq["ret"] <= -0.35):
                    state = "WATCH(매크로)"
                    color = YELLOW

            # ---- (윗줄) 원래 줄(초록색만)
            line1 = (
                f"[{now_kst_str()}] {state} | "
                f"{core('ZN', intra.get('ZN'))} {core('NQ', intra.get('NQ'))} {core('KRW', intra.get('KRW'))} "
                f"{core('7709', intra.get('HK'))} {core('000250', intra.get('SCD'))} {core('488080', intra.get('TIGER'))} | "
                f"{semi_txt}"
            )

            # ---- (아랫줄) 흰줄: PnL + ACTION(팔면 수량) + sell_signal 맨 오른쪽
            pnl_parts = []
            action_parts = []
            sell_parts = []
            total_pnl = {"KRW": 0.0, "HKD": 0.0}

            for p in POSITIONS:
                tkr = p["ticker"]

                # intraday에서 현재가 가져오기(가능하면 LIVE 우선)
                mv = None
                if tkr == "488080.KS":
                    mv = intra.get("TIGER")
                elif tkr == "000250.KQ":
                    mv = intra.get("SCD")
                elif tkr == "7709.HK":
                    mv = intra.get("HK")

                if mv is not None and (not mv["stale"]):
                    cur_price = mv["last"]
                else:
                    # daily로 fallback
                    dc = daily_close.get(tkr)
                    cur_price = float(dc.iloc[-1]) if dc is not None and not dc.empty else None

                if cur_price is None:
                    pnl_parts.append(f"{p['name']}:가격없음")
                    action_parts.append(f"{p['name']}:?")
                    sell_parts.append(f"{p['name']}:?")
                    continue

                pl_pct = (cur_price / p["entry"] - 1.0) * 100.0
                pnl_amt = (cur_price - p["entry"]) * p["qty"]
                total_pnl[p["ccy"]] += pnl_amt

                # 손절 신호 E: 진입 -3%
                e_signal = (pl_pct <= STOP_LOSS_PCT)

                # 손절 신호 T: 60일 고점 대비 -3% 이상 하락(트레일링)
                high60 = compute_high60(daily_close.get(tkr))
                t_signal = False
                if high60 is not None and high60 > 0:
                    dd_from_high = (cur_price / high60 - 1.0) * 100.0
                    t_signal = (dd_from_high <= TRAIL_FROM_HIGH_PCT)

                sell = e_signal or t_signal
                reason = ("E" if e_signal else "") + ("T" if t_signal else "")
                if reason == "":
                    reason = "-"

                # ACTION(수량) — sell_signal이 1일 때만 SELL 플랜 표시
                if sell:
                    sell_qty, action = sell_plan(p["qty"], pl_pct, e_signal, t_signal)
                else:
                    sell_qty, action = 0, "HOLD"


                # --- 오늘 고점(day_high) 가져오기 (intraday에서)
                day_high = None
                if tkr == "488080.KS":
                    day_high = intra.get("TIGER", {}).get("day_high")
                elif tkr == "000250.KQ":
                    day_high = intra.get("SCD", {}).get("day_high")
                elif tkr == "7709.HK":
                    day_high = intra.get("HK", {}).get("day_high")

                # --- 돌파 여부
                if day_high is None:
                    brk = "BRK?"
                else:
                    brk = "BRK1" if cur_price >= day_high else "BRK0"

                pnl_parts.append(
                    f"{p['name']} {fmt_num(cur_price, p['ccy'])} {fmt_pct(pl_pct)}({fmt_num(pnl_amt, p['ccy'])}) {brk}"
                )
                action_parts.append(f"{p['name']}:{action}")
                sell_parts.append(f"{p['name']}={1 if sell else 0}{reason}")

            line2 = (
                "PNL | " + " | ".join(pnl_parts) +
                f" | TOTAL KRW:{total_pnl['KRW']:,.0f}원 HKD:{total_pnl['HKD']:,.2f}HKD" +
                " | ACTION " + " ".join(action_parts) +
                " | sell_signal=" + " ".join(sell_parts)
            )

            now = time.time()
            if now - last_print >= HEARTBEAT_SEC:
                # ✅ 첫 줄만 색(초록/노랑/빨강), 둘째 줄은 흰색
                print(GREEN + line1 + RESET)  # 첫 줄은 항상 초록
                print(line2)
                last_print = now

                # sell_signal 하나라도 1이면 알림
                if any("=1" in s for s in sell_parts):
                    mac_notify("SELL_SIGNAL", line2)

        except Exception as e:
            print(f"[{now_kst_str()}] ERROR: {e}", file=sys.stderr)

        time.sleep(POLL_SEC)

if __name__ == "__main__":
    main()
