import sys
import time
import platform
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import pandas as pd
import yfinance as yf

KST = ZoneInfo("Asia/Seoul")

INTERVAL = "5m"
PERIOD = "2d"
POLL_SEC = 20
HEARTBEAT_SEC = 60
STALE_SEC = 30 * 60  # 30분 이상 갱신 없으면 지연

# 너 포지션 중심: 국장/홍콩/미국 이동을 위해 "LIVE 잘 뜨는 것" 우선
SYMS = {
    "ZN": "ZN=F",
    "NQ": "NQ=F",
    "KRW": "KRW=X",
    "HK": "7709.HK",

    # ✅ 너 보유 종목/ETF 추가
    "SCD": "000250.KQ",  # 삼천당제약(코스닥)  :contentReference[oaicite:1]{index=1}
    "TIGER2XSEMI": "488080.KS",  # TIGER 반도체TOP10레버리지

    "SOX": "^SOX",
    "SMH": "SMH",
    "MU": "MU",
    "SOXL": "SOXL",
}
# 5분봉 기준 임계값 (너무 민감하면 숫자만 키우면 됨)
TH = {
    "WATCH": {"ZN": -0.10, "NQ": -0.35, "SEMI": -0.50},
    "RISK":  {"ZN": -0.25, "NQ": -0.80, "SEMI": -1.20, "MU": -1.00},
    "HK_HARD": -1.20,
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

def pct_change(new: float, old: float) -> float:
    return (new / old - 1.0) * 100.0

def download_all(tickers: list[str]) -> pd.DataFrame:
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

def extract_close_series(df: pd.DataFrame, ticker: str) -> pd.Series:
    if df is None or df.empty:
        return pd.Series(dtype=float)

    if isinstance(df.columns, pd.MultiIndex):
        if (ticker, "Close") in df.columns:
            s = df[(ticker, "Close")]
        elif ("Close", ticker) in df.columns:
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

def latest_ret(series: pd.Series, bars: int = 1):
    if series.empty or len(series) < bars + 2:
        return None

    ts = series.index[-1]
    cur = float(series.iloc[-1])
    prev = float(series.iloc[-1 - bars])

    ts_utc = ts.to_pydatetime().astimezone(timezone.utc)
    age = (datetime.now(timezone.utc) - ts_utc).total_seconds()
    stale = age >= STALE_SEC

    return {"stale": stale, "ret": pct_change(cur, prev), "age": age}

def live(mv):
    return mv is not None and not mv["stale"]

def main():
    tickers = list(SYMS.values())
    last_print = 0.0
    last_state = None

    while True:
        try:
            raw = download_all(tickers)

            mv = {}
            for k, tkr in SYMS.items():
                s = extract_close_series(raw, tkr)
                mv[k] = latest_ret(s, bars=1)  # 5분 변화

            # 핵심 4개는 무조건 보여주되, stale이면 "?" 처리
            def core(label, key):
                  return f"{label}:데이터없음"
              if mv[key]["stale"]:
                  return f"{label}:지연"
              return f"{label}:{fmt_pct(mv[key]['ret'])}"

            zn = mv["ZN"]; nq = mv["NQ"]; krw = mv["KRW"]; hk = mv["HK"]

            # 반도체 프록시: LIVE면 SOX > SMH > SOXL 순서로 사용
            semi_key = None
            for k in ["SOX", "SMH", "SOXL"]:
                if live(mv[k]):
                    semi_key = k
                    break

            mu_ok = live(mv["MU"])

            state = "OK"
            color = GREEN

            # 0) 홍콩 세션에서 7709 급락하면 즉시 WATCH
            if live(hk) and hk["ret"] <= TH["HK_HARD"]:
                state = "WATCH(홍콩7709)"
                color = YELLOW

            # 1) WATCH: ZN↓ + NQ↓ + (SEMI가 LIVE이면 SEMI↓ 확인)
            if live(zn) and live(nq):
                macro_watch = (zn["ret"] <= TH["WATCH"]["ZN"]) and (nq["ret"] <= TH["WATCH"]["NQ"])

                if macro_watch:
                    if semi_key is None:
                        # 반도체 데이터가 지연이면 "매크로 경계"만 띄움
                        state = "WATCH(매크로)"
                        color = YELLOW
                    else:
                        if mv[semi_key]["ret"] <= TH["WATCH"]["SEMI"]:
                            state = "WATCH"
                            color = YELLOW

            # 2) RISK-OFF: ZN↓(더큼) + NQ↓(더큼) + SEMI↓ + MU↓ (모두 LIVE일 때만 확정)
            if live(zn) and live(nq) and semi_key is not None and mu_ok:
                if (zn["ret"] <= TH["RISK"]["ZN"]) and (nq["ret"] <= TH["RISK"]["NQ"]) and (mv[semi_key]["ret"] <= TH["RISK"]["SEMI"]) and (mv["MU"]["ret"] <= TH["RISK"]["MU"]):
                    state = "RISK-OFF"
                    color = RED

            # 출력(진짜 최소): 상태 + 핵심 4개 + (반도체 LIVE면 SEMI만 추가)
            msg = (
                f"[{now_kst_str()}] {state} | "
                f"{core('ZN','ZN')} {core('NQ','NQ')} {core('KRW','KRW')} {core('7709','HK')} "
                f"{core('000250','SCD')} {core('488080','TIGER2XSEMI')}"
            )
            if semi_key is not None:
                msg += f" | SEMI({semi_key})={fmt_pct(mv[semi_key]['ret'])}"
            else:
              msg += " | SEMI=지연"

            if semi_key is not None:
                msg += f" | SEMI({semi_key})={fmt_pct(mv[semi_key]['ret'])}"
            else:
                # 판단 방해라 길게 안 쓰고, 딱 한 단어로만
                msg += " | SEMI=지연"

            now = time.time()
            state_changed = (state != last_state)

            if state_changed or (now - last_print >= HEARTBEAT_SEC):
                print(color + msg + RESET)
                last_print = now
                last_state = state
                if state != "OK":
                    mac_notify(state, msg)

        except Exception as e:
            print(f"[{now_kst_str()}] ERROR: {e}", file=sys.stderr)

        time.sleep(POLL_SEC)

if __name__ == "__main__":
    main()