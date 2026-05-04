import sys
import time
import platform
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import pandas as pd
import yfinance as yf

# ====== 기본 설정 ======
KST = ZoneInfo("Asia/Seoul")

INTERVAL = "5m"       # 1m은 무료 데이터가 자주 끊겨서 5m 권장
PERIOD = "2d"
POLL_SEC = 20
HEARTBEAT_SEC = 60
STALE_SEC = 30 * 60   # 30분 이상 갱신 없으면 "지연" 취급

# ====== 감시 대상 (너 포지션 반영) ======
SYMS = {
    "금리대용(10Y선물_ZN)": "ZN=F",      # 금리↑면 보통 ZN 가격↓
    "나스닥선물(NQ)": "NQ=F",
    "반도체지수(SOX)": "^SOX",           # 대표성 좋지만 장외/무료데이터 지연 가능
    "반도체ETF(SMH)": "SMH",             # 거래되는 가격(감시용 안정)
    "메모리(MU)": "MU",                  # 하이닉스/삼전 연관 강화
    "SOXL(3x)": "SOXL",                  # 너가 옮길 대상
    "달러원(USDKRW)": "KRW=X",
    "홍콩하이닉스2x(7709)": "7709.HK",
}

# ====== 임계값 (5분봉 기준: 너무 잦으면 숫자 키워) ======
TH = {
    "WATCH": {"ZN": -0.10, "NQ": -0.35, "SEMI": -0.50},     # 5분 내
    "RISK":  {"ZN": -0.25, "NQ": -0.80, "SEMI": -1.20, "MU": -1.00},
    "HK_HARD": -1.20,   # 7709가 5분에 -1.2% 이하 급락이면 HK 경계
}

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

def fmt_age(sec: float) -> str:
    if sec < 60: return f"{int(sec)}초"
    return f"{int(sec//60)}분{int(sec%60)}초"

def pct_change(new: float, old: float) -> float:
    return (new / old - 1.0) * 100.0

# ---- 핵심: MultiIndex/단일 컬럼 모두에서 Close를 안전하게 뽑기
def extract_close_series(df: pd.DataFrame, ticker: str) -> pd.Series:
    if df is None or df.empty:
        return pd.Series(dtype=float)

    # MultiIndex columns: (Ticker, Field) or (Field, Ticker)
    if isinstance(df.columns, pd.MultiIndex):
        cols = df.columns
        if (ticker, "Close") in cols:
            s = df[(ticker, "Close")]
        elif ("Close", ticker) in cols:
            s = df[("Close", ticker)]
        else:
            # fallback 1: df[ticker]["Close"]
            try:
                sub = df[ticker]
                if isinstance(sub, pd.DataFrame) and "Close" in sub.columns:
                    s = sub["Close"]
                else:
                    # fallback 2: xs close at any level
                    close = df.xs("Close", level=0, axis=1, drop_level=False) if "Close" in cols.get_level_values(0) else None
                    if close is None or close.empty:
                        return pd.Series(dtype=float)
                    # close is DataFrame; try pick first column
                    s = close.iloc[:, 0]
            except Exception:
                return pd.Series(dtype=float)
    else:
        # Single-index
        if "Close" in df.columns:
            s = df["Close"]
        else:
            return pd.Series(dtype=float)

    # s can still be DataFrame if duplicate label -> take first col
    if isinstance(s, pd.DataFrame):
        if s.shape[1] == 0:
            return pd.Series(dtype=float)
        s = s.iloc[:, 0]

    s = pd.to_numeric(s, errors="coerce").dropna()
    return s

def latest_ret(series: pd.Series, bars: int = 1):
    # 5m interval에서 bars=1 => 최근 5분 변화
    if series.empty or len(series) < bars + 2:
        return None

    ts = series.index[-1]
    cur = float(series.iloc[-1])
    prev = float(series.iloc[-1 - bars])

    ts_utc = ts.to_pydatetime().astimezone(timezone.utc)
    age = (datetime.now(timezone.utc) - ts_utc).total_seconds()
    stale = age >= STALE_SEC

    return {"ts": ts_utc, "age": age, "stale": stale, "ret": pct_change(cur, prev)}

def download_all():
    tickers = list(SYMS.values())
    return yf.download(
        tickers=tickers,
        period=PERIOD,
        interval=INTERVAL,
        group_by="ticker",
        auto_adjust=False,
        prepost=True,
        progress=False,
        threads=False,
    )

def part(label: str, mv):
    if mv is None:
        return f"{label}: 데이터없음"
    tag = "LIVE" if not mv["stale"] else "지연"
    return f"{label} {fmt_pct(mv['ret'])} ({tag}, 마지막갱신 {fmt_age(mv['age'])} 전)"

def main():
    last_print = 0.0
    last_state = None

    while True:
        try:
            raw = download_all()

            moves = {}
            for name, tkr in SYMS.items():
                s = extract_close_series(raw, tkr)
                moves[name] = latest_ret(s, bars=1)

            zn   = moves["금리대용(10Y선물_ZN)"]
            nq   = moves["나스닥선물(NQ)"]
            sox  = moves["반도체지수(SOX)"]
            smh  = moves["반도체ETF(SMH)"]
            mu   = moves["메모리(MU)"]
            soxl = moves["SOXL(3x)"]
            krw  = moves["달러원(USDKRW)"]
            hk   = moves["홍콩하이닉스2x(7709)"]

            def live(mv): 
                return mv is not None and not mv["stale"]

            # 반도체 프록시 선택: SOX가 LIVE면 SOX 우선, 아니면 SMH, 둘 다 아니면 SOXL
            semi = sox if live(sox) else (smh if live(smh) else soxl)

            state = "OK"
            color = GREEN
            reasons = []

            # 1) 홍콩 전용 경계: 7709가 급락하면 즉시 WATCH
            if live(hk) and hk["ret"] <= TH["HK_HARD"]:
                state = "WATCH(홍콩7709 급락)"
                color = YELLOW
                reasons.append("7709 급락")

            # 2) WATCH: 금리↑(ZN↓) + NQ↓ + 반도체↓
            if live(zn) and live(nq) and semi is not None and live(semi):
                if (zn["ret"] <= TH["WATCH"]["ZN"]) and (nq["ret"] <= TH["WATCH"]["NQ"]) and (semi["ret"] <= TH["WATCH"]["SEMI"]):
                    state = "WATCH"
                    color = YELLOW
                    reasons = ["금리↑(ZN↓)+NQ↓+반도체↓"]

            # 3) RISK-OFF: 더 강한 동시 붕괴 + MU까지 확인
            if live(zn) and live(nq) and semi is not None and live(semi) and live(mu):
                if (zn["ret"] <= TH["RISK"]["ZN"]) and (nq["ret"] <= TH["RISK"]["NQ"]) and (semi["ret"] <= TH["RISK"]["SEMI"]) and (mu["ret"] <= TH["RISK"]["MU"]):
                    state = "RISK-OFF"
                    color = RED
                    reasons = ["강한 금리↑+NQ↓+반도체↓+MU↓"]

            line = (
                f"[{now_kst_str()}] 상태={state} | "
                f"{part('ZN(금리대용)', zn)} | {part('NQ', nq)} | {part('SOX', sox)} | {part('SMH', smh)} | "
                f"{part('MU', mu)} | {part('SOXL', soxl)} | {part('USDKRW', krw)} | {part('7709', hk)}"
            )
            if reasons:
                line += " | 근거=" + ",".join(reasons)

            now = time.time()
            state_changed = (state != last_state)

            # 상태 바뀌면 즉시 출력, 아니면 60초마다 생존 출력
            if state_changed or (now - last_print >= HEARTBEAT_SEC):
                print(color + line + RESET)
                last_print = now
                last_state = state
                if state != "OK":
                    mac_notify(state, line)

        except Exception as e:
            print(f"[{now_kst_str()}] ERROR: {e}", file=sys.stderr)

        time.sleep(POLL_SEC)

if __name__ == "__main__":
    main()