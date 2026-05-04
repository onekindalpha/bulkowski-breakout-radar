#!/usr/bin/env python3
"""
Yahoo Finance (yfinance) 기반 premarket 자동 갱신 스크립트 (무소음/빠른 실패).

목표:
- 프리마켓을 Yahoo(yfinance)로 조회하되,
- "없는 티커/잘못된 토큰" 때문에 콘솔이 수십 줄로 도배되거나, 불필요한 재시도 때문에 느려지는 문제를 제거.

출력:
- premarket_auto.csv        : 그룹 헤더(# group) + ticker,premarket
- premarket_auto_debug.csv  : group,ticker,yahoo_symbol,premarket,src,yf_ts_et,interval,age_min,error

티커 소스:
- GROUP_FILES에 있는 txt들을 우선 사용 (없으면 tickers.txt)

추가:
- .yahoo_skiplist.txt : 한 번이라도 no_data/404 등으로 실패한 raw ticker를 저장해 다음 실행부터는 "조회 자체"를 건너뜁니다.
  (원치 않으면 이 파일을 지우면 됩니다.)
"""

import re
import time
from pathlib import Path
from datetime import datetime, timedelta, time as dtime
from zoneinfo import ZoneInfo

import contextlib
import io
import logging
import warnings

import pandas as pd
import yfinance as yf

TZ_KST = ZoneInfo("Asia/Seoul")
TZ_ET = ZoneInfo("America/New_York")

# US premarket window (ET)
PRE_START = dtime(4, 0)     # 04:00 ET
PRE_END   = dtime(9, 30)    # 09:30 ET
REGULAR_START = dtime(9, 30)

# 파일 기반 그룹 로딩
GROUP_FILES = [
    "finviz_manual.txt",
    "macro_watch_yahoo.txt",
    "tickers_core.txt",
    "tickers_leverage2x.txt",
    "tickers_leverage_global.txt",
    "sp69_tickers_only.txt", 
    "finviz_top_groups_auto_mixed.txt",
]


SKIPLIST_PATH = Path(".yahoo_skiplist.txt")

# 자주 쓰는 매크로 표기 -> Yahoo Finance 심볼 매핑
ALIASES = {
    "WTI": "CL=F",
    "CRUDE": "CL=F",
    "CRUDEOIL": "CL=F",
    "OIL": "CL=F",          # 'OIL' 자체는 delisted/혼동이 많아서 선물로 통일
    "BRENT": "BZ=F",
    "GAS": "NG=F",
    "NATGAS": "NG=F",
    "NATURALGAS": "NG=F",
    "GOLD": "GC=F",
    "SILVER": "SI=F",
    "COPPER": "HG=F",
    "DXY": "DX-Y.NYB",
    "USDIDX": "DX-Y.NYB",
    "VIX": "^VIX",
    "TNX": "^TNX",
    "GASOLINE": "RB=F",     # RBOB Gasoline Futures
    "RBOB": "RB=F",
    "RB": "RB=F",
    "SPX": "^GSPC",
    "SP500": "^GSPC",
    "NASDAQ": "^IXIC",
    "NDX": "^NDX",
}

# Yahoo 심볼 허용 문자(=, ^ 포함)
_ALLOWED = re.compile(r"[^A-Z0-9\.\-\=\^]+")

# 콘솔 스팸 완전 차단 (warnings/logs/print_once)
warnings.filterwarnings("ignore")
for name in ("yfinance", "urllib3", "requests"):
    logging.getLogger(name).setLevel(logging.CRITICAL)

# yfinance 내부에서 print_once로 찍는 메시지까지 죽이기
try:
    import yfinance.utils as _yfu
    def _noop(*args, **kwargs):  # noqa: ANN001
        return None
    if hasattr(_yfu, "print_once"):
        _yfu.print_once = _noop
except Exception:
    pass

@contextlib.contextmanager
def _suppress_all_output():
    """yfinance가 stdout/stderr로 찍는 메시지를 강제로 무음 처리."""
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
        yield

def load_skiplist() -> set[str]:
    if not SKIPLIST_PATH.exists():
        return set()
    out = set()
    for line in SKIPLIST_PATH.read_text(encoding="utf-8", errors="ignore").splitlines():
        s = line.strip().upper()
        if s and not s.startswith("#"):
            out.add(s)
    return out

def add_skiplist(raw: str) -> None:
    raw = raw.strip().upper()
    if not raw:
        return
    # append-only (중복은 허용, 다음 로드에서 set으로 정리)
    with SKIPLIST_PATH.open("a", encoding="utf-8") as f:
        f.write(raw + "\n")

def load_tickers(path: str, mode: str = "default") -> list[str]:
    """
    txt 로딩:
    - 주석(#) 라인 무시
    - default: 공백/쉼표/세미콜론 구분
    - macro: 라인이 문장일 수 있어서, 라인 전체에서 "티커처럼 보이는 토큰"만 추출
    """
    p = Path(path)
    if not p.exists():
        return []
    out, seen = [], set()

    lines = p.read_text(encoding="utf-8", errors="ignore").splitlines()
    for line in lines:
        s = line.strip()
        if not s or s.startswith("#"):
            continue

        if mode == "macro":
            # 라인에서 후보 토큰을 먼저 추출 (너무 짧거나 숫자 몇 자리 등은 제외)
            # 예: "S&P 500 Futures" 같은 라인은 여기서 아무것도 안 뽑히거나, 뽑혀도 아래에서 걸러짐
            raw_tokens = re.split(r"[\s,;]+", s)
        else:
            raw_tokens = re.split(r"[\s,;]+", s)

        for tok in raw_tokens:
            tok = tok.strip().upper()
            if not tok or tok.startswith("#"):
                continue
            tok = _ALLOWED.sub("", tok)
            if not tok:
                continue
            # 너무 잡음이 많은 토큰 제거 (특히 macro 파일에서)
            if tok == "-" or tok == "--":
                continue
            if tok.isdigit() and len(tok) < 6:
                # "500", "100", "10" 같은 헤딩 잡음 제거
                continue
            if mode == "macro":
                # macro 파일은 '별칭' 또는 '야후 심볼처럼 생긴 것'만 통과
                if tok not in ALIASES and not looks_like_yahoo_symbol(tok):
                    continue

            if tok not in seen:
                seen.add(tok)
                out.append(tok)
    return out

def load_grouped_tickers(group_files: list[str]) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for fname in group_files:
        p = Path(fname)
        if not p.exists():
            continue
        mode = "macro" if p.name == "macro_watch_yahoo.txt" else "default"
        out[p.stem] = load_tickers(str(p), mode=mode)
    return out

def looks_like_yahoo_symbol(t: str) -> bool:
    """macro 그룹에서만 쓰는 '야후 심볼처럼 생겼나' 필터."""
    if not t:
        return False
    if t.startswith("^"):
        return True
    if "=" in t:          # CL=F, RB=F 등
        return True
    if "." in t:          # DX-Y.NYB, 329200.KS 등
        return True
    if re.fullmatch(r"\d{6}(\.(KS|KQ))?", t):
        return True
    return False

def resolve_yahoo_candidates(raw: str) -> list[str]:
    """
    입력 티커(raw)에 대해 yfinance로 시도할 후보 심볼 리스트를 반환.
    - ALIASES 매핑
    - 6자리 숫자면 .KS → .KQ → 원본 순으로 시도
    """
    t = raw.strip().upper()
    t = t.lstrip('$')
    if ':' in t:
        t = t.split(':')[-1]
    if t in ALIASES:
        return [ALIASES[t]]
    if re.fullmatch(r"\d{6}\.(KS|KQ)", t):
        return [t]
    if re.fullmatch(r"\d{6}", t):
        return [f"{t}.KS", f"{t}.KQ", t]
    return [t]

def _to_et_index(df: pd.DataFrame) -> pd.DataFrame:
    if df is None or df.empty:
        return df
    idx = df.index
    if getattr(idx, "tz", None) is None:
        df = df.tz_localize("UTC").tz_convert(TZ_ET)
    else:
        df = df.tz_convert(TZ_ET)
    return df

def _pick_last_price_us_premarket(df_et: pd.DataFrame, now_et: datetime) -> tuple[float | None, datetime | None]:
    """US 기준: 정규장 시작 전이면 오늘(ET) 04:00~09:30 마지막 Close, 이후면 오늘 데이터 마지막 Close."""
    if df_et is None or df_et.empty or "Close" not in df_et.columns:
        return None, None

    today = now_et.date()
    today_data = df_et[df_et.index.date == today]
    if today_data.empty:
        last_ts = df_et.index[-1].to_pydatetime()
        last_px = float(df_et["Close"].dropna().iloc[-1]) if not df_et["Close"].dropna().empty else None
        return (last_px if last_px and last_px > 0 else None), last_ts

    if now_et.time() < REGULAR_START:
        filtered = today_data[(today_data.index.time >= PRE_START) & (today_data.index.time < PRE_END)]
    else:
        filtered = today_data

    if filtered.empty:
        return None, None

    close = filtered["Close"].dropna()
    if close.empty:
        return None, None
    last_ts = filtered.index[-1].to_pydatetime()
    last_px = float(close.iloc[-1])
    return (last_px if last_px > 0 else None), last_ts

def is_kr_symbol(sym: str) -> bool:
    return sym.endswith(".KS") or sym.endswith(".KQ") or bool(re.fullmatch(r"\d{6}(\.(KS|KQ))?", sym))

def fetch_last_price_once(raw_ticker: str):
    """
    '없는 티커'에 시간 쓰지 않도록:
    - 후보 심볼(candidates) 중에서 "딱 1번"만 조회합니다.
    - 실패하면 바로 no_data 처리 (폴백 기간/인터벌 없음)
    반환: (price, yahoo_symbol_used, src, ts_et, interval_used, age_minutes, error)
    """
    now_et = datetime.now(TZ_ET)
    candidates = resolve_yahoo_candidates(raw_ticker)

    # 가장 가능성이 높은 것 1개만 시도 (6자리면 .KS가 먼저, 별칭이면 이미 매핑됨)
    sym = candidates[0] if candidates else ""
    if not sym:
        return None, "", "", None, "", None, "invalid"

    # interval/period: 속도 우선 (국내는 5m/5d, US는 1m/2d)
    if is_kr_symbol(sym):
        interval, period = "5m", "5d"
    else:
        interval, period = "1m", "2d"

    try:
        with _suppress_all_output():
            tk = yf.Ticker(sym)
            df = tk.history(period=period, interval=interval, prepost=True, auto_adjust=False)
    except Exception as e:
        return None, sym, "", None, interval, None, f"{type(e).__name__}: {e}"

    if df is None or df.empty or "Close" not in df.columns:
        return None, sym, "", None, interval, None, "no_data"

    # KR: 최신 Close
    if is_kr_symbol(sym):
        close = df["Close"].dropna()
        if close.empty:
            return None, sym, "", None, interval, None, "no_data"
        ts = df.index[-1]
        if getattr(ts, "tzinfo", None) is None:
            ts = ts.tz_localize("UTC").tz_convert(TZ_ET)
        else:
            ts = ts.tz_convert(TZ_ET)
        px = float(close.iloc[-1])
        if not (px > 0):
            return None, sym, "", None, interval, None, "no_data"
        age = datetime.now(TZ_ET) - ts.to_pydatetime()
        if age > timedelta(days=10):
            return None, sym, "", ts.to_pydatetime(), interval, None, f"stale({age.days}d)"
        age_min = int(age.total_seconds() / 60)
        return px, sym, f"yf_close({interval})", ts.to_pydatetime(), interval, age_min, ""

    # US(및 글로벌): 프리마켓/당일 마지막가
    df_et = _to_et_index(df)
    px, ts = _pick_last_price_us_premarket(df_et, now_et)
    if px is None or ts is None:
        return None, sym, "", None, interval, None, "no_data"

    age = datetime.now(TZ_ET) - (ts.replace(tzinfo=TZ_ET) if ts.tzinfo is None else ts)
    if age > timedelta(days=10):
        return None, sym, "", ts, interval, None, f"stale({age.days}d)"
    age_min = int(age.total_seconds() / 60)
    return float(px), sym, f"yf_close({interval})", ts, interval, age_min, ""

def main():
    grouped = load_grouped_tickers(GROUP_FILES)
    if not grouped and Path("tickers.txt").exists():
        grouped = {"tickers": load_tickers("tickers.txt")}

    if not grouped:
        raise SystemExit("No tickers found. Provide group files or tickers.txt")

    skip = load_skiplist()

    # 그룹 순서 유지 + 그룹 내 순서 유지하며 전체 목록 구축
    all_tickers: list[tuple[str, str]] = []  # (group, ticker)
    seen: set[str] = set()
    for group_name, grp in grouped.items():
        for t in grp:
            if t not in seen:
                seen.add(t)
                all_tickers.append((group_name, t))

    now_kst = datetime.now(TZ_KST).strftime("%Y-%m-%d %H:%M:%S KST")
    now_et = datetime.now(TZ_ET)
    mode = "premarket (04:00~09:30 ET)" if now_et.time() < REGULAR_START else "last price (regular/extended)"
    print(f"Mode: {mode} | saved_at_kr={now_kst}")

    prices: dict[str, float] = {}
    meta: dict[str, dict] = {}

    # 콘솔 진행 출력은 최소화 (속도 저하/스팸 방지)
    total = len(all_tickers)
    for i, (group_name, raw) in enumerate(all_tickers, 1):
        raw_u = raw.strip().upper()

        # skiplist에 있으면 조회 자체를 건너뜀
        if raw_u in skip:
            meta[raw] = {
                "group": group_name,
                "yahoo_symbol": "",
                "src": "",
                "ts_et": "",
                "interval": "",
                "age_min": "",
                "error": "skipped(no_data_cached)",
            }
            continue

        px, sym, src, ts_et, interval, age_min, err = fetch_last_price_once(raw)
        if px is not None:
            prices[raw] = float(px)
            meta[raw] = {
                "group": group_name,
                "yahoo_symbol": sym,
                "src": src,
                "ts_et": ts_et.isoformat() if ts_et else "",
                "interval": interval,
                "age_min": age_min if age_min is not None else "",
                "error": "",
            }
        else:
            # no_data 계열이면 다음부터는 아예 조회하지 않도록 캐시
            if err in ("no_data", "invalid") or err.startswith("HTTPError") or "404" in err:
                add_skiplist(raw_u)
                skip.add(raw_u)

            meta[raw] = {
                "group": group_name,
                "yahoo_symbol": sym,
                "src": src,
                "ts_et": ts_et.isoformat() if ts_et else "",
                "interval": interval,
                "age_min": age_min if age_min is not None else "",
                "error": err or "no_data",
            }

        # 너무 빡세게 두드리지 않도록 아주 짧게만 쉼
        time.sleep(0.03)

        if i in (1, total) or (i % 25 == 0):
            print(f"... {i}/{total} processed")

    # 1) premarket_auto.csv
    with open("premarket_auto.csv", "w", newline="", encoding="utf-8") as f:
        f.write(f"# saved_at_kr,{now_kst}\n")
        f.write(f"# mode,{mode}\n")
        f.write("# provider,yahoo(yfinance)\n")
        for group_name, tickers in grouped.items():
            f.write(f"# group: {group_name}\n")
            f.write("ticker,premarket\n")
            for t in tickers:
                val = prices.get(t)
                f.write(f"{t},{'' if val is None else round(val, 2)}\n")

    # 2) premarket_auto_debug.csv
    with open("premarket_auto_debug.csv", "w", newline="", encoding="utf-8") as f:
        f.write(f"# saved_at_kr,{now_kst}\n")
        f.write(f"# mode,{mode}\n")
        f.write("# provider,yahoo(yfinance)\n")
        f.write("group,ticker,yahoo_symbol,premarket,src,yf_ts_et,interval,age_min,error\n")
        for group_name, tickers in grouped.items():
            for t in tickers:
                info = meta.get(t, {"group": group_name, "error": "not_processed"})
                val = prices.get(t)
                f.write(
                    f"{group_name},{t},{info.get('yahoo_symbol','')},"
                    f"{'' if val is None else round(val,2)},"
                    f"{info.get('src','')},{info.get('ts_et','')},{info.get('interval','')},"
                    f"{info.get('age_min','')},{info.get('error','')}\n"
                )

    out_path = Path("premarket_auto.csv").resolve()
    dbg_path = Path("premarket_auto_debug.csv").resolve()
    print(f"Saved {out_path} and {dbg_path}")

if __name__ == "__main__":
    main()
