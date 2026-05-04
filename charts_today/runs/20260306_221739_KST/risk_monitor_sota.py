import time
import sys
import platform
from dataclasses import dataclass
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import pandas as pd
import yfinance as yf

# =========================
#  CONFIG (너 기준: 레버리지 비중 큼 = 민감하지만 "확정"은 보수적으로)
# =========================

@dataclass
class Thresholds:
    # 금리 상승(퍼센트포인트, %p). 0.01%p = 1bp
    y_ppt_warn: float = 0.020   # 2bp
    y_ppt_risk: float = 0.040   # 4bp

    # 나스닥/반도체 급락(%) - 5분 기준이 체감이 좋음
    nq_warn_5m: float = -0.35
    nq_risk_5m: float = -0.80

    semi_warn_5m: float = -0.50
    semi_risk_5m: float = -1.20

    # 메모리 프록시(MU) 하락(%) - 하이닉스 연관 강화
    mu_warn_5m: float = -0.60
    mu_risk_5m: float = -1.40

    # 변동성(VIX) 상승(%) - 위험구간 확인
    vix_warn_5m: float = +2.5
    vix_risk_5m: float = +6.0

    # 원화 약세(USDKRW 상승, %) - 국장/외국인 수급 리스크
    krw_warn_15m: float = +0.25
    krw_risk_15m: float = +0.60

# 폴링/출력
POLL_SEC = 15
HEARTBEAT_SEC = 60
STALE_SEC = 180  # 3분 이상 업데이트 없으면 stale 취급

# 데이터(무료는 끊길 수 있으니 2d가 종종 더 안정적)
INTERVAL = "1m"
PERIOD = "2d"

TH = Thresholds()

# =========================
#  SYMBOLS (너 포지션 반영)
# =========================
SYMS = {
    # Rates
    "TNX": "^TNX",      # 10Y yield proxy (주의: Yahoo가 41.13 또는 4.113 형태로 줄 수 있음)
    # Equity risk
    "NQ": "NQ=F",       # Nasdaq futures (국장 갭/리스크온오프에 핵심)
    # Semis
    "SOX": "^SOX",      # 지수(대표성 좋지만 장외/데이터 stale 가능)
    "SMH": "SMH",       # ETF(거래되는 가격이라 감시용 안정)
    "MU": "MU",         # 메모리 프록시(하이닉스/삼전 연관 강)
    "NVDA": "NVDA",     # AI 심리(장외 영향 큰 날이 많음)
    "TSM": "TSM",       # 파운드리/공급망
    # Vol / FX
    "VIX": "^VIX",
    "USDKRW": "KRW=X",
    # Your HK exposure (2x ETP)
    "HYNIX2X_HK": "7709.HK",
    # Optional KR spot (if you want) - uncomment if needed:
    # "HYNIX_KR": "000660.KS",
}

# =========================
#  Console colors
# =========================
RED = "\033[91m"
YELLOW = "\033[93m"
GREEN = "\033[92m"
CYAN = "\033[96m"
RESET = "\033[0m"

def kst_now() -> datetime:
    return datetime.now(ZoneInfo("Asia/Seoul"))

def kst_now_str() -> str:
    return kst_now().strftime("%Y-%m-%d %H:%M:%S")

def mac_notify(title: str, message: str):
    if platform.system() != "Darwin":
        return
    import subprocess
    script = f'display notification "{message}" with title "{title}"'
    subprocess.run(["osascript", "-e", script], check=False)

def pct_change(new: float, old: float) -> float:
    return (new / old - 1.0) * 100.0

def tnx_to_yield_pct(v: float) -> float:
    """
    Yahoo ^TNX가 어떤 때는 41.13(=4.113%*10),
    어떤 때는 4.113(=이미 %)로 내려올 때가 있음.
    -> 10보다 크면 /10, 10보다 작으면 그대로 %로 취급.
    """
    return v / 10.0 if v > 10 else v

def fmt_pct(x: float) -> str:
    if x > 0: return f"▲{x:.2f}%"
    if x < 0: return f"▼{abs(x):.2f}%"
    return "—0.00%"

def fmt_ppt(x: float) -> str:
    # percent-point (%p)
    if x > 0: return f"▲{x:.3f}%p"
    if x < 0: return f"▼{abs(x):.3f}%p"
    return "—0.000%p"

def fmt_age(sec: float) -> str:
    if sec < 60: return f"{int(sec)}s"
    return f"{int(sec//60)}m{int(sec%60)}s"

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

def get_close_series(df: pd.DataFrame, ticker: str) -> pd.Series:
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
                s = sub["Close"] if "Close" in sub.columns else sub.iloc[:, 0]
            except Exception:
                return pd.Series(dtype=float)
    else:
        # single ticker only
        if "Close" not in df.columns:
            return pd.Series(dtype=float)
        s = df["Close"]

    return pd.to_numeric(s, errors="coerce").dropna()

def latest_return(series: pd.Series, bars: int):
    """
    bars=1 -> 1분 변화
    bars=5 -> 5분 변화 (1m 기준 5 bars)
    """
    if series.empty or len(series) < bars + 2:
        return None

    ts = series.index[-1]
    cur = float(series.iloc[-1])
    prev = float(series.iloc[-1 - bars])

    ts_utc = ts.to_pydatetime().astimezone(timezone.utc)
    age = (datetime.now(timezone.utc) - ts_utc).total_seconds()
    stale = age >= STALE_SEC
    return {"ts": ts_utc, "age": age, "stale": stale, "cur": cur, "prev": prev}

def score_and_state(data):
    """
    State logic (SOTA-ish):
    - WATCH: (금리↑ + NQ↓)가 5분 기준으로 성립 + (반도체/메모리/변동성 중 하나 이상 동조)
    - RISK-OFF: 금리↑ 강 + NQ↓ 강 + (반도체(sox/smh)↓ & MU↓) + VIX↑ 같은 '동시 붕괴' 확인
    """
    # --- helpers
    def ok(key): return data.get(key) is not None and not data[key]["stale"]
    def ret_5m(key):
        if not ok(key): return None
        return pct_change(data[key]["cur"], data[key]["prev_5m"])
    def ret_15m(key):
        if not ok(key): return None
        return pct_change(data[key]["cur"], data[key]["prev_15m"])

    # rates: use TNX 5m ppt if fresh
    y_cur = None
    y_ppt_5m = None
    if ok("TNX"):
        y_cur = tnx_to_yield_pct(data["TNX"]["cur"])
        y_prev = tnx_to_yield_pct(data["TNX"]["prev_5m"])
        y_ppt_5m = y_cur - y_prev  # %p

    nq_5m = ret_5m("NQ")
    sox_5m = ret_5m("SOX")
    smh_5m = ret_5m("SMH")
    mu_5m  = ret_5m("MU")
    vix_5m = ret_5m("VIX")
    krw_15m = ret_15m("USDKRW")
    hy2x_5m = ret_5m("HYNIX2X_HK")

    # semi proxy: prefer SOX if fresh else SMH
    semi_5m = sox_5m if sox_5m is not None else smh_5m

    # --- primary conditions
    rates_up_warn = (y_ppt_5m is not None) and (y_ppt_5m >= TH.y_ppt_warn)
    rates_up_risk = (y_ppt_5m is not None) and (y_ppt_5m >= TH.y_ppt_risk)

    nq_down_warn = (nq_5m is not None) and (nq_5m <= TH.nq_warn_5m)
    nq_down_risk = (nq_5m is not None) and (nq_5m <= TH.nq_risk_5m)

    semi_down_warn = (semi_5m is not None) and (semi_5m <= TH.semi_warn_5m)
    semi_down_risk = (semi_5m is not None) and (semi_5m <= TH.semi_risk_5m)

    mu_down_warn = (mu_5m is not None) and (mu_5m <= TH.mu_warn_5m)
    mu_down_risk = (mu_5m is not None) and (mu_5m <= TH.mu_risk_5m)

    vix_up_warn = (vix_5m is not None) and (vix_5m >= TH.vix_warn_5m)
    vix_up_risk = (vix_5m is not None) and (vix_5m >= TH.vix_risk_5m)

    krw_up_warn = (krw_15m is not None) and (krw_15m >= TH.krw_warn_15m)
    krw_up_risk = (krw_15m is not None) and (krw_15m >= TH.krw_risk_15m)

    # HK Hynix 2x: HK 세션에서는 이게 국장/하이닉스 체감 신호로 강함
    hy_down_hard = (hy2x_5m is not None) and (hy2x_5m <= TH.semi_risk_5m)  # -1.2% 같은 수준

    # --- state decision
    reasons = []
    state = "OK"
    color = GREEN

    # WATCH: 금리↑ + NQ↓ + (반도체/메모리/VIX/원화 중 하나 동조)
    if rates_up_warn and nq_down_warn and (semi_down_warn or mu_down_warn or vix_up_warn or krw_up_warn):
        state = "WATCH ⚠️"
        color = YELLOW
        reasons.append("rates_up + nq_down (confirm by semi/mu/vix/krw)")

    # RISK-OFF: 강한 금리↑ + 강한 NQ↓ + 반도체↓ + MU↓ (가능하면 VIX↑까지)
    if rates_up_risk and nq_down_risk and semi_down_risk and mu_down_risk and (vix_up_warn or krw_up_warn):
        state = "RISK-OFF 🚨"
        color = RED
        reasons.append("rates_up_strong + nq_down_strong + semi_down + mu_down (+vix/krw)")

    # HK-only safety: HK 세션에서 7709가 급락하면 WATCH로 올려줌 (미국 데이터 stale여도)
    if state == "OK" and hy_down_hard:
        state = "WATCH ⚠️ (HYNIX2X)"
        color = YELLOW
        reasons.append("HYNIX2X 급락(홍콩 세션)")

    return {
        "state": state,
        "color": color,
        "reasons": reasons,
        "y_cur": y_cur,
        "y_ppt_5m": y_ppt_5m,
        "nq_5m": nq_5m,
        "semi_5m": semi_5m,
        "mu_5m": mu_5m,
        "vix_5m": vix_5m,
        "krw_15m": krw_15m,
        "hy2x_5m": hy2x_5m,
    }

def main():
    tickers = list(SYMS.values())
    last_print = 0.0
    last_state = None

    # 심볼별 last ts(업데이트 감지)
    last_seen = {k: None for k in SYMS.keys()}

    while True:
        try:
            raw = download_all(tickers)

            # 각 심볼별로 최신 값/5m전/15m전 "따로" 가져옴 (교집합 dropna 절대 안 씀)
            data = {}
            any_new = False

            for name, tkr in SYMS.items():
                s = get_close_series(raw, tkr)

                # 최신
                last = latest_return(s, bars=1)
                if last is None:
                    data[name] = None
                    continue

                # 5m, 15m 기준 prev를 따로 저장
                last_5m = latest_return(s, bars=5)
                last_15m = latest_return(s, bars=15)

                if last_5m is None:
                    data[name] = None
                    continue

                # build record
                rec = {
                    "ts": last["ts"], "age": last["age"], "stale": last["stale"],
                    "cur": last["cur"],
                    "prev_5m": last_5m["prev"],
                    "prev_15m": (last_15m["prev"] if last_15m is not None else last_5m["prev"]),
                }
                data[name] = rec

                if last_seen[name] is None or rec["ts"] != last_seen[name]:
                    any_new = True
                    last_seen[name] = rec["ts"]

            res = score_and_state(data)

            # 라인 구성 (한눈에)
            # NOTE: stale인 애는 age 옆에 STALE이 붙도록 표시
            def part(name, label, val, age_key=None, stale_key=None):
                if val is None: return f"{label}:N/A"
                return f"{label}:{val}"

            # 금리표시
            if res["y_cur"] is None or res["y_ppt_5m"] is None:
                y_txt = "US10Y:N/A"
            else:
                bp = res["y_ppt_5m"] * 100.0  # 0.01%p = 1bp
                y_txt = f"US10Y {res['y_cur']:.3f}% (5m {fmt_ppt(res['y_ppt_5m'])}/{bp:+.1f}bp)"

            # 리턴 표시
            nq_txt = "NQ:N/A" if res["nq_5m"] is None else f"NQ 5m {fmt_pct(res['nq_5m'])}"
            semi_txt = "SEMI:N/A" if res["semi_5m"] is None else f"SEMI 5m {fmt_pct(res['semi_5m'])} (SOX>SMH)"
            mu_txt  = "MU:N/A" if res["mu_5m"] is None else f"MU 5m {fmt_pct(res['mu_5m'])}"
            vix_txt = "VIX:N/A" if res["vix_5m"] is None else f"VIX 5m {fmt_pct(res['vix_5m'])}"
            krw_txt = "KRW:N/A" if res["krw_15m"] is None else f"KRW 15m {fmt_pct(res['krw_15m'])}"
            hy_txt  = "7709:N/A" if res["hy2x_5m"] is None else f"7709 5m {fmt_pct(res['hy2x_5m'])}"

            # 데이터 freshness 표시(핵심 3개만: TNX, NQ, SEMI proxy)
            def freshness(key):
                if data.get(key) is None: return f"{key}:N/A"
                tag = "STALE" if data[key]["stale"] else "LIVE"
                return f"{key}:{tag}/{fmt_age(data[key]['age'])}"

            fresh_txt = f"{freshness('TNX')} {freshness('NQ')} {freshness('SMH')} {freshness('SOX')}"

            line = (
                f"[{kst_now_str()}] {res['state']} | "
                f"{y_txt} | {nq_txt} | {semi_txt} | {mu_txt} | {vix_txt} | {krw_txt} | {hy_txt} | "
                f"{CYAN}{fresh_txt}{RESET}"
            )

            now = time.time()
            state_changed = (last_state != res["state"])
            if any_new and state_changed:
                print(res["color"] + line + RESET)
                last_state = res["state"]
                last_print = now
                if res["state"] != "OK":
                    mac_notify(res["state"], line)

            elif now - last_print >= HEARTBEAT_SEC:
                # heartbeat(생존확인)
                print(line)
                last_print = now

        except Exception as e:
            print(f"[{kst_now_str()}] ERROR: {e}", file=sys.stderr)

        time.sleep(POLL_SEC)

if __name__ == "__main__":
    main()