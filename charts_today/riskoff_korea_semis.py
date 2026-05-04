import time
import sys
import platform
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
import pandas as pd
import yfinance as yf

# ---- symbols
SYMS = {
    "TNX": "^TNX",      # US10Y proxy
    "NQ": "NQ=F",       # Nasdaq futures
    "SOX": "^SOX",      # Semiconductor index (may be stale off-hours)
    "SMH": "SMH",       # Tradable proxy (pre/post if available)
    "MU": "MU",         # Memory proxy
    "HYNIX2X": "7709.HK" # CSOP SK Hynix Daily (2x) Leveraged Product
}

INTERVAL = "1m"
PERIOD = "2d"
WINDOW_BARS = 1

POLL_SEC = 15
HEARTBEAT_SEC = 60
STALE_SEC = 180  # 3 min without new bar => stale

# thresholds (ultra sensitive; raise if too noisy)
YIELD_PPT_WARN = 0.020  # 0.02%p = 2bp
YIELD_PPT_RISK = 0.040  # 0.04%p = 4bp
RET_WARN = 0.15         # %
RET_RISK = 0.35         # %

GREEN = "\033[92m"; YELLOW = "\033[93m"; RED = "\033[91m"; RESET = "\033[0m"

def kst_now_str():
    return datetime.now(ZoneInfo("Asia/Seoul")).strftime("%Y-%m-%d %H:%M:%S")

def mac_notify(title: str, message: str):
    if platform.system() != "Darwin":
        return
    import subprocess
    script = f'display notification "{message}" with title "{title}"'
    subprocess.run(["osascript", "-e", script], check=False)

def tnx_to_yield_pct(v: float) -> float:
    # Yahoo ^TNX may come as 41.13 (=4.113%*10) OR 4.113 (=already %)
    return v/10.0 if v > 10 else v

def pct_change(new: float, old: float) -> float:
    return (new/old - 1.0) * 100.0

def fmt_pct(x: float) -> str:
    return f"▲{x:.2f}%" if x > 0 else (f"▼{abs(x):.2f}%" if x < 0 else "—0.00%")

def fmt_ppt(x: float) -> str:
    return f"▲{x:.3f}%p" if x > 0 else (f"▼{abs(x):.3f}%p" if x < 0 else "—0.000%p")

def fmt_age(age: float) -> str:
    if age < 60: return f"{int(age)}s"
    return f"{int(age//60)}m{int(age%60)}s"

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
        if "Close" not in df.columns:
            return pd.Series(dtype=float)
        s = df["Close"]
    return pd.to_numeric(s, errors="coerce").dropna()

def latest_move(series: pd.Series):
    if series.empty or len(series) < WINDOW_BARS + 2:
        return None
    ts = series.index[-1]
    cur = float(series.iloc[-1])
    prev = float(series.iloc[-1 - WINDOW_BARS])
    ts_utc = ts.to_pydatetime().astimezone(timezone.utc)
    age = (datetime.now(timezone.utc) - ts_utc).total_seconds()
    return ts_utc, age, cur, prev

def main():
    tickers = list(SYMS.values())
    last_print = 0.0
    last_seen = {k: None for k in SYMS.keys()}

    while True:
        try:
            raw = download_all(tickers)

            info = {}
            any_new = False
            for name, tkr in SYMS.items():
                s = get_close_series(raw, tkr)
                mv = latest_move(s)
                if mv is None:
                    info[name] = {"ok": False}
                    continue
                ts_utc, age, cur, prev = mv
                stale = age >= STALE_SEC
                info[name] = {"ok": True, "ts": ts_utc, "age": age, "stale": stale, "cur": cur, "prev": prev}
                if last_seen[name] is None or ts_utc != last_seen[name]:
                    any_new = True
                    last_seen[name] = ts_utc

            # ---- compute core numbers
            # yield
            y_line = "US10Y N/A"
            y_ppt = None
            y_ok = False
            if info["TNX"]["ok"]:
                y_cur = tnx_to_yield_pct(info["TNX"]["cur"])
                y_prev = tnx_to_yield_pct(info["TNX"]["prev"])
                y_ppt = y_cur - y_prev
                y_ok = not info["TNX"]["stale"]
                y_line = f"US10Y {y_cur:.3f}% (Δ {fmt_ppt(y_ppt)}, age={fmt_age(info['TNX']['age'])})" + (" STALE" if info["TNX"]["stale"] else "")

            def ret_line(key, label):
                if not info[key]["ok"]:
                    return label + " N/A", None, False
                r = pct_change(info[key]["cur"], info[key]["prev"])
                ok = not info[key]["stale"]
                line = f"{label} {fmt_pct(r)} (age={fmt_age(info[key]['age'])})" + (" STALE" if info[key]["stale"] else "")
                return line, r, ok

            nq_line, nq_ret, nq_ok = ret_line("NQ", "NQ")
            sox_line, sox_ret, sox_ok = ret_line("SOX", "SOX")
            smh_line, smh_ret, smh_ok = ret_line("SMH", "SMH")
            mu_line,  mu_ret,  mu_ok  = ret_line("MU",  "MU")
            hy_line,  hy_ret,  hy_ok  = ret_line("HYNIX2X", "7709.HK")

            # ---- logic (two-stage)
            # Stage A: Macro risk (yield up + NQ down)
            status = "OK"
            color = GREEN

            macro_watch = (y_ppt is not None and y_ok and nq_ret is not None and nq_ok and (y_ppt >= YIELD_PPT_WARN) and (nq_ret <= -RET_WARN))
            macro_risk  = (y_ppt is not None and y_ok and nq_ret is not None and nq_ok and (y_ppt >= YIELD_PPT_RISK) and (nq_ret <= -RET_RISK))

            # Stage B: Semi confirmation (SOX if live else SMH, plus MU for memory tilt)
            semi_proxy_ret = sox_ret if sox_ok else (smh_ret if smh_ok else None)
            semi_ok = sox_ok or smh_ok
            semi_down_warn = (semi_proxy_ret is not None and semi_ok and semi_proxy_ret <= -RET_WARN)
            semi_down_risk = (semi_proxy_ret is not None and semi_ok and semi_proxy_ret <= -RET_RISK)

            mem_down_warn = (mu_ret is not None and mu_ok and mu_ret <= -RET_WARN)
            mem_down_risk = (mu_ret is not None and mu_ok and mu_ret <= -RET_RISK)

            if macro_watch and (semi_down_warn or mem_down_warn):
                status = "WATCH ⚠️"
                color = YELLOW
            if macro_risk and semi_down_risk and mem_down_risk:
                status = "RISK-OFF 🚨"
                color = RED

            # Extra: Hynix2x live during HK hours — if it is tanking, treat as additional warning
            if status == "OK" and hy_ret is not None and hy_ok and hy_ret <= -RET_RISK:
                status = "WATCH ⚠️ (HYNIX2X)"
                color = YELLOW

            now = time.time()
            if any_new or (now - last_print >= HEARTBEAT_SEC):
                line = f"[{kst_now_str()}] {y_line} | {nq_line} | {sox_line} | {smh_line} | {mu_line} | {hy_line} | {status}"
                print(color + line + RESET)
                last_print = now

                if status.startswith("WATCH") or status.startswith("RISK-OFF"):
                    mac_notify(status, line)

        except Exception as e:
            print(f"[{kst_now_str()}] ERROR: {e}", file=sys.stderr)

        time.sleep(POLL_SEC)

if __name__ == "__main__":
    main()