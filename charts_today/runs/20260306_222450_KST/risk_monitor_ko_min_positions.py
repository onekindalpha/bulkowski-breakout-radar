import sys
import time
import platform
from dataclasses import dataclass
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import pandas as pd
import yfinance as yf

KST = ZoneInfo("Asia/Seoul")

# ====== 사용자 보유(입력값 그대로 사용) ======
# 488080: KRW, 000250: KRW(코스닥 -> .KQ), 7709: HKD
POSITIONS = [
    {"name": "488080", "ticker": "488080.KS", "entry": 45515.0,  "qty": 260,  "ccy": "KRW"},
    {"name": "000250", "ticker": "000250.KQ", "entry": 799571.0, "qty": 7,    "ccy": "KRW"},
    {"name": "7709",   "ticker": "7709.HK",   "entry": 30.12,   "qty": 3300, "ccy": "HKD"},
]

# ====== 시장 감시(핵심만) ======
MARKET = {
    "ZN": "ZN=F",    # 10Y 국채선물(금리 프록시) : ZN 하락 = 금리 상승
    "NQ": "NQ=F",    # 나스닥 선물
    "KRW": "KRW=X",  # USDKRW
}

# ====== 설정 ======
INTRADAY_INTERVAL = "5m"   # 시장 요약용(무료 1m는 자주 끊김)
INTRADAY_PERIOD = "2d"

DAILY_PERIOD = "6mo"       # 60일 고점 계산용(일봉)
POLL_SEC = 20
HEARTBEAT_SEC = 60

# 신선도: 시장요약(intraday) 30분 넘게 갱신 없으면 stale로 간주
STALE_SEC = 30 * 60

# 손절 규칙
STOP_LOSS_PCT = -3.0       # 진입가 대비 -3% 손절
TRAIL_STOP_PCT = -3.0      # 60일 고점 대비 -3% 이탈(트레일링)
HIGH_WINDOW = 60           # 60거래일 고점

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

# ---------- yfinance 안전한 Close 추출 ----------
def extract_close_series(df: pd.DataFrame, ticker: str) -> pd.Series:
    if df is None or df.empty:
        return pd.Series(dtype=float)

    if isinstance(df.columns, pd.MultiIndex):
        cols = df.columns
        if (ticker, "Close") in cols:
            s = df[(ticker, "Close")]
        elif ("Close", ticker) in cols:
            s = df[("Close", ticker)]
        else:
            try:
                sub = df[ticker]
                if isinstance(sub, pd.DataFrame) and "Close" in sub.columns:
                    s = sub["Close"]
                else:
                    return pd.Series(dtype=float)
            except Exception:
                return pd.Series(dtype=float)
    else:
        if "Close" not in df.columns:
            return pd.Series(dtype=float)
        s = df["Close"]

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

def latest_intraday_ret(ticker: str, series: pd.Series):
    """
    intraday(5m) 기준: 최근 bar 대비 변화율
    stale 여부 포함
    """
    if series.empty or len(series) < 3:
        return None
    ts = series.index[-1]
    cur = float(series.iloc[-1])
    prev = float(series.iloc[-2])

    ts_utc = ts.to_pydatetime().astimezone(timezone.utc)
    age = (datetime.now(timezone.utc) - ts_utc).total_seconds()
    stale = age >= STALE_SEC
    return {"ret": pct_change(cur, prev), "stale": stale}

def compute_position_metrics(close_daily: pd.Series, entry: float):
    """
    일봉 기준:
    - 현재가: 마지막 Close
    - 60일 고점: rolling max
    - 진입 대비 수익률
    - 고점 대비 드로다운
    - sell_signal: (진입 -3%) OR (60일 고점 대비 -3%)
    """
    if close_daily is None or close_daily.empty:
        return None

    close_daily = close_daily.dropna()
    if close_daily.empty:
        return None

    cur = float(close_daily.iloc[-1])

    # 60거래일 고점 (데이터 부족 시 가능한 구간에서)
    if len(close_daily) >= HIGH_WINDOW:
        high60 = float(close_daily.iloc[-HIGH_WINDOW:].max())
    else:
        high60 = float(close_daily.max())

    pl_pct = (cur / entry - 1.0) * 100.0
    dd_from_high_pct = (cur / high60 - 1.0) * 100.0  # 음수면 고점 대비 하락

    hit_entry_stop = pl_pct <= STOP_LOSS_PCT
    hit_trail_stop = dd_from_high_pct <= TRAIL_STOP_PCT

    sell_signal = hit_entry_stop or hit_trail_stop
    # 원인 표기(오른쪽에 짧게)
    reason = []
    if hit_entry_stop:
        reason.append("E")   # Entry stop
    if hit_trail_stop:
        reason.append("T")   # Trailing(60d high) stop
    reason = "".join(reason) if reason else "-"

    return {
        "cur": cur,
        "high60": high60,
        "pl_pct": pl_pct,
        "dd_high_pct": dd_from_high_pct,
        "sell": sell_signal,
        "reason": reason,
    }

def main():
    last_print = 0.0
    last_state = None

    market_tickers = list(MARKET.values())
    pos_tickers = [p["ticker"] for p in POSITIONS]

    while True:
        try:
            # (1) 시장 요약: intraday 5m
            raw_mkt = yf_download(market_tickers, interval=INTRADAY_INTERVAL, period=INTRADAY_PERIOD)
            m = {}
            for k, tkr in MARKET.items():
                s = extract_close_series(raw_mkt, tkr)
                m[k] = latest_intraday_ret(tkr, s)

            # (2) 포지션: daily 1d (현재가/60d high/손절신호)
            raw_pos = yf_download(pos_tickers, interval="1d", period=DAILY_PERIOD)
            pos_metrics = []
            sell_map = []

            total_pnl = 0.0
            total_cost = 0.0
            total_pnl_ccy = {"KRW": 0.0, "HKD": 0.0}  # 통화별 합산

            for p in POSITIONS:
                s = extract_close_series(raw_pos, p["ticker"])
                met = compute_position_metrics(s, p["entry"])
                if met is None:
                    # 데이터가 진짜 없을 때만 표시
                    pos_metrics.append(f"{p['name']}:데이터없음")
                    sell_map.append(f"{p['name']}=?")
                    continue

                cur = met["cur"]
                pl_pct = met["pl_pct"]

                pnl_amt = (cur - p["entry"]) * p["qty"]
                cost_amt = p["entry"] * p["qty"]

                # 통화별 합산(환산은 안함. 원하면 나중에 환율로 환산 가능)
                total_pnl_ccy[p["ccy"]] += pnl_amt

                # 표시 문자열(짧게)
                pos_metrics.append(
                    f"{p['name']} {fmt_num(p['entry'], p['ccy'])}→{fmt_num(cur, p['ccy'])} "
                    f"({fmt_pct(pl_pct)}, {fmt_num(pnl_amt, p['ccy'])})"
                )

                sell_map.append(f"{p['name']}={1 if met['sell'] else 0}{met['reason']}")

            # 상태 판정(아주 단순): ZN↓ + NQ↓면 WATCH(매크로)
            # (너가 원하면 여기서 더 엄격/완화 가능)
            state = "OK"
            color = GREEN

            zn = m.get("ZN"); nq = m.get("NQ")
            if zn and nq and (not zn["stale"]) and (not nq["stale"]):
                # “감시기” 수준: 급락 경보가 아니라 ‘경계’만
                if (zn["ret"] <= -0.10) and (nq["ret"] <= -0.35):
                    state = "WATCH(매크로)"
                    color = YELLOW

            # 1줄(시장요약) — 지연이면 그냥 '지연'로 단순 표기
            def core(label, mv):
                if mv is None:
                    return f"{label}:?"
                if mv["stale"]:
                    return f"{label}:지연"
                return f"{label}:{fmt_pct(mv['ret'])}"

            line1 = (
                f"[{now_kst_str()}] {state} | "
                f"{core('ZN', m.get('ZN'))} {core('NQ', m.get('NQ'))} {core('KRW', m.get('KRW'))} {core('7709', m.get('HK'))}"
            )

            # 1줄(포지션/PnL + sell_signal 맨 오른쪽)
            # sell_signal: 488080=0-, 000250=1E, 7709=0T 이런 식
            sell_signal = "sell_signal=" + " ".join(sell_map)
            # 통화별 총 손익도 짧게
            total_txt = f"TOTAL(PnL) KRW:{total_pnl_ccy['KRW']:,.0f}원 HKD:{total_pnl_ccy['HKD']:,.2f}HKD"

            line2 = " | ".join(pos_metrics) + " | " + total_txt + " | " + sell_signal

            now = time.time()
            state_changed = (state != last_state)

            # 상태 바뀌면 즉시 2줄 출력, 아니면 60초마다 2줄만
            if state_changed or (now - last_print >= HEARTBEAT_SEC):
                print(color + line1 + RESET)
                print(line2)
                last_print = now
                last_state = state

                # sell_signal 중 1이 하나라도 있으면 알림
                if any("=1" in s for s in sell_map):
                    mac_notify("SELL_SIGNAL", line2)

        except Exception as e:
            print(f"[{now_kst_str()}] ERROR: {e}", file=sys.stderr)

        time.sleep(POLL_SEC)

if __name__ == "__main__":
    main()