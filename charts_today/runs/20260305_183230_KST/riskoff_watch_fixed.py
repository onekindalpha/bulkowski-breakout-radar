import time
import sys
import platform
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
import pandas as pd
import yfinance as yf

# -----------------------------
# Config
# -----------------------------
TICKERS = ["^TNX", "NQ=F", "SMH"]
INTERVAL = "1m"
PERIOD = "2d"           # 1d보다 2d가 가끔 더 안정적
WINDOW_BARS = 1         # 1분 전 대비

POLL_SEC = 15
HEARTBEAT_SEC = 60

# "초밀착" 기준(원하면 더 민감/둔감 조절)
YIELD_PPT_WARN = 0.015  # 0.015%p = 1.5bp
YIELD_PPT_RISK = 0.025  # 0.025%p = 2.5bp
RET_WARN = 0.05         # %
RET_RISK = 0.10         # %

# 데이터가 이 이상 오래되면 stale로 취급
STALE_SEC = 180         # 3분

# 콘솔 색
RED = "\033[91m"
YELLOW = "\033[93m"
GREEN = "\033[92m"
RESET = "\033[0m"

def now_kst():
    return datetime.now(ZoneInfo("Asia/Seoul"))

def fmt_ts_kst(ts_utc: datetime) -> str:
    return ts_utc.astimezone(ZoneInfo("Asia/Seoul")).strftime("%Y-%m-%d %H:%M:%S")

def mac_notify(title: str, message: str):
    if platform.system() != "Darwin":
        return
    import subprocess
    script = f'display notification "{message}" with title "{title}"'
    subprocess.run(["osascript", "-e", script], check=False)

def pct_change(new: float, old: float) -> float:
    return (new / old - 1.0) * 100.0

def tnx_to_yield_pct(tnx_val: float) -> float:
    # Yahoo ^TNX가 41.13(=4.113%*10)로 오기도 하고 4.113(이미 %)로 오기도 함
    return (tnx_val / 10.0) if tnx_val > 10 else tnx_val

def fmt_ppt(x: float) -> str:
    if x > 0: return f"▲{x:.3f}%p"
    if x < 0: return f"▼{abs(x):.3f}%p"
    return "—0.000%p"

def fmt_pct(x: float) -> str:
    if x > 0: return f"▲{x:.2f}%"
    if x < 0: return f"▼{abs(x):.2f}%"
    return "—0.00%"

def fmt_age(age_sec: float) -> str:
    if age_sec < 60: return f"{int(age_sec)}s"
    return f"{int(age_sec//60)}m{int(age_sec%60)}s"

def extract_close_series(df: pd.DataFrame, ticker: str) -> pd.Series:
    """yf.download 멀티인덱스/단일인덱스 모두 처리해서 close series 반환"""
    if df is None or df.empty:
        return pd.Series(dtype=float)

    if isinstance(df.columns, pd.MultiIndex):
        # (Ticker, Field) or (Field, Ticker)
        if (ticker, "Close") in df.columns:
            s = df[(ticker, "Close")]
        elif ("Close", ticker) in df.columns:
            s = df[("Close", ticker)]
        else:
            # fallback: df[ticker]['Close']
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

    s = pd.to_numeric(s, errors="coerce").dropna()
    return s

def download_all(tickers: list[str]) -> pd.DataFrame:
    return yf.download(
        tickers=tickers,
        period=PERIOD,
        interval=INTERVAL,
        group_by="ticker",
        auto_adjust=False,
        prepost=True,     # 시장외 포함(가능한 범위 내)
        progress=False,
        threads=False,
    )

def main():
    last_print = 0.0
    last_seen_ts = {t: None for t in TICKERS}  # 심볼별 마지막 timestamp 기억

    while True:
        try:
            raw = download_all(TICKERS)
            series = {t: extract_close_series(raw, t) for t in TICKERS}

            # 각 심볼별 최신/이전값을 "따로" 계산 (교집합 dropna 제거!)
            out = {}
            any_new_bar = False
            now_utc = datetime.now(timezone.utc)

            for t, s in series.items():
                if s.empty or len(s) < WINDOW_BARS + 2:
                    out[t] = {"ok": False}
                    continue

                ts = s.index[-1]
                # pandas Timestamp -> aware datetime(UTC로 맞춤)
                ts_utc = ts.to_pydatetime().astimezone(timezone.utc)

                age = (now_utc - ts_utc).total_seconds()
                stale = age >= STALE_SEC

                cur = float(s.iloc[-1])
                prev = float(s.iloc[-1 - WINDOW_BARS])

                out[t] = {
                    "ok": True,
                    "ts_utc": ts_utc,
                    "age": age,
                    "stale": stale,
                    "cur": cur,
                    "prev": prev,
                }

                # 새 봉 감지(심볼별로)
                if last_seen_ts[t] is None or ts_utc != last_seen_ts[t]:
                    any_new_bar = True
                    last_seen_ts[t] = ts_utc

            # --- compute signals ---
            # US10Y
            y_str = "US10Y N/A"
            y_ppt = None
            y_bp = None
            y_fresh = False

            if out.get("^TNX", {}).get("ok"):
                y_cur = tnx_to_yield_pct(out["^TNX"]["cur"])
                y_prev = tnx_to_yield_pct(out["^TNX"]["prev"])
                y_ppt = y_cur - y_prev
                y_bp = y_ppt * 100.0  # 0.01%p = 1bp
                y_fresh = not out["^TNX"]["stale"]
                y_str = (
                    f"US10Y {y_cur:.3f}% (Δ {fmt_ppt(y_ppt)} / {y_bp:+.1f}bp, age={fmt_age(out['^TNX']['age'])})"
                    + (" STALE" if out["^TNX"]["stale"] else "")
                )

            # NQ
            nq_str = "NQ N/A"
            nq_ret = None
            nq_fresh = False
            if out.get("NQ=F", {}).get("ok"):
                nq_ret = pct_change(out["NQ=F"]["cur"], out["NQ=F"]["prev"])
                nq_fresh = not out["NQ=F"]["stale"]
                nq_str = (
                    f"NQ {fmt_pct(nq_ret)} (age={fmt_age(out['NQ=F']['age'])})"
                    + (" STALE" if out["NQ=F"]["stale"] else "")
                )

            # SMH
            smh_str = "SMH N/A"
            smh_ret = None
            smh_fresh = False
            if out.get("SMH", {}).get("ok"):
                smh_ret = pct_change(out["SMH"]["cur"], out["SMH"]["prev"])
                smh_fresh = not out["SMH"]["stale"]
                smh_str = (
                    f"SMH {fmt_pct(smh_ret)} (age={fmt_age(out['SMH']['age'])})"
                    + (" STALE" if out["SMH"]["stale"] else "")
                )

            # 상태 판정 로직:
            # - 기본은 "금리(%p) 상승 + NQ 하락"이면 WATCH
            # - SMH가 fresh일 때만 "금리 상승 + NQ 하락 + SMH 하락"으로 RISK-OFF 확정
            status = "OK"
            color = GREEN

            if y_ppt is not None and nq_ret is not None and y_fresh and nq_fresh:
                warn_core = (y_ppt >= YIELD_PPT_WARN) and (nq_ret <= -RET_WARN)
                risk_core = (y_ppt >= YIELD_PPT_RISK) and (nq_ret <= -RET_RISK)

                if warn_core:
                    status = "WATCH ⚠️"
                    color = YELLOW

                if risk_core and smh_ret is not None and smh_fresh and (smh_ret <= -RET_RISK):
                    status = "RISK-OFF 🚨"
                    color = RED

            # 출력 조건:
            # - 어떤 심볼이든 새 봉이 생기면 출력
            # - 아니면 HEARTBEAT_SEC마다 생존 출력
            now = time.time()
            if any_new_bar or (now - last_print >= HEARTBEAT_SEC):
                line = (
                    f"[{now_kst().strftime('%Y-%m-%d %H:%M:%S')}] "
                    f"{y_str} | {nq_str} | {smh_str} | {status}"
                )
                print(color + line + RESET)
                last_print = now

                # 알림(원하면)
                if status in ("WATCH ⚠️", "RISK-OFF 🚨"):
                    mac_notify(status, line)

        except Exception as e:
            print(f"[{now_kst().strftime('%Y-%m-%d %H:%M:%S')}] ERROR: {e}", file=sys.stderr)

        time.sleep(POLL_SEC)

if __name__ == "__main__":
    main()