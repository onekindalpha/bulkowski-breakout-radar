#!/usr/bin/env python3
"""
Yahoo Finance (yfinance) 기반 premarket 자동 갱신 스크립트.

- Alpaca IEX처럼 프리마켓/종목커버리지 이슈로 "전일 값"이 남는 문제를 피하려고
  Yahoo(= yfinance)만 사용합니다.
- Alpaca debug 출력 수준(그룹/소스/타임스탬프)을 유지/확장해서 premarket_auto_debug.csv를 같이 저장합니다.

출력:
- premarket_auto.csv        : 그룹 헤더(# group) + ticker,premarket
- premarket_auto_debug.csv  : group,ticker,yahoo_symbol,premarket,src,yf_ts_et,interval,age_min,error

티커 소스:
- GROUP_FILES에 있는 txt들을 우선 사용 (없으면 tickers.txt)

특징:
- 매크로/지수/선물 등 자주 쓰는 "별칭"을 Yahoo 심볼로 자동 매핑 (WTI→CL=F, VIX→^VIX 등)
- 6자리 숫자 티커(국내 종목/ETF)는 .KS / .KQ 자동 시도 (TIGER ETF 대부분은 6자리.KS 형태)
"""

import re
import time
from pathlib import Path
from datetime import datetime, timedelta, time as dtime
from zoneinfo import ZoneInfo

import pandas as pd
import yfinance as yf

TZ_KST = ZoneInfo("Asia/Seoul")
TZ_ET = ZoneInfo("America/New_York")

# US premarket window (ET)
PRE_START = dtime(4, 0)     # 04:00 ET
PRE_END   = dtime(9, 30)    # 09:30 ET
REGULAR_START = dtime(9, 30)

# 파일 기반 그룹 로딩 (Alpaca debug 스크립트와 동일한 이름 유지)
GROUP_FILES = [
    "macro_watch_yahoo.txt",
    "tickers_core.txt",
    "tickers_leverage2x.txt",
]

# 자주 쓰는 매크로 표기 -> Yahoo Finance 심볼 매핑
# (네 파일에 이런 토큰이 있으면 자동으로 Yahoo 심볼로 바꿔서 조회)
ALIASES = {
    "WTI": "CL=F",          # WTI Crude Oil Futures
    "CRUDE": "CL=F",
    "BRENT": "BZ=F",        # Brent Crude Oil Futures
    "GAS": "NG=F",          # Natural Gas Futures
    "NATGAS": "NG=F",
    "GOLD": "GC=F",         # Gold Futures
    "SILVER": "SI=F",       # Silver Futures
    "COPPER": "HG=F",       # Copper Futures
    "DXY": "DX-Y.NYB",      # US Dollar Index
    "USDIDX": "DX-Y.NYB",
    "VIX": "^VIX",
    "TNX": "^TNX",          # 10Y yield index
}

# Yahoo 심볼로 쓰기 위해 허용할 문자(=, ^ 포함)
_ALLOWED = re.compile(r"[^A-Z0-9\.\-\=\^]+")

def load_tickers(path: str) -> list[str]:
    """
    - 주석(#) 라인 무시
    - 공백/쉼표/세미콜론 구분
    - 대문자화 + 불필요한 문자 제거(단, ^, =, ., - 는 유지)
    """
    p = Path(path)
    if not p.exists():
        return []
    out, seen = [], set()
    for line in p.read_text(encoding="utf-8", errors="ignore").splitlines():
        s = line.strip()
        if not s or s.startswith("#"):
            continue
        for tok in re.split(r"[\s,;]+", s):
            tok = tok.strip().upper()
            if not tok or tok.startswith("#"):
                continue
            tok = _ALLOWED.sub("", tok)
            if not tok:
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
        out[p.stem] = load_tickers(str(p))
    return out

def resolve_yahoo_candidates(raw: str) -> list[str]:
    """
    입력 티커(raw)에 대해 yfinance로 시도할 후보 심볼 리스트를 반환.
    - ALIASES 매핑
    - 6자리 숫자면 .KS → .KQ → 원본 순으로 시도
    """
    t = raw.strip().upper()
    if t in ALIASES:
        return [ALIASES[t]]
    if re.fullmatch(r"\d{6}", t):
        return [f"{t}.KS", f"{t}.KQ", t]
    return [t]

def _to_et_index(df: pd.DataFrame) -> pd.DataFrame:
    if df is None or df.empty:
        return df
    idx = df.index
    # yfinance는 보통 tz-aware로 오지만, 예외 케이스 방어
    if getattr(idx, "tz", None) is None:
        df = df.tz_localize("UTC").tz_convert(TZ_ET)
    else:
        df = df.tz_convert(TZ_ET)
    return df

def _pick_last_price_us_premarket(df_et: pd.DataFrame, now_et: datetime) -> tuple[float | None, datetime | None]:
    """
    US 기준:
    - 정규장 시작 전: 오늘(ET)의 04:00~09:30 마지막 Close
    - 정규장 시작 후: 오늘(ET) 전체 데이터 마지막 Close
    """
    if df_et is None or df_et.empty:
        return None, None

    today = now_et.date()
    today_data = df_et[df_et.index.date == today]
    if today_data.empty:
        # 오늘 데이터가 없으면 가장 최근 값 사용(단, 신선도 체크는 밖에서)
        last_ts = df_et.index[-1].to_pydatetime()
        last_px = float(df_et["Close"].dropna().iloc[-1]) if "Close" in df_et else None
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

def fetch_last_price(raw_ticker: str, retry: int = 2, pause: float = 0.8):
    """
    반환: (price, yahoo_symbol_used, src, ts_et, interval_used, age_minutes, error)
    """
    now_et = datetime.now(TZ_ET)
    candidates = resolve_yahoo_candidates(raw_ticker)

    # 인터벌/기간 폴백 순서: 빠른 것 → 느린 것
    attempts = [
        ("1m",  "2d"),
        ("5m",  "5d"),
        ("15m", "5d"),
        ("60m", "1mo"),
        ("1d",  "3mo"),  # 마지막 보루(프리마켓은 못 잡지만 "너무 오래된 값"은 걸러냄)
    ]

    last_error = ""
    for sym in candidates:
        for interval, period in attempts:
            for _ in range(retry + 1):
                try:
                    tk = yf.Ticker(sym)
                    df = tk.history(period=period, interval=interval, prepost=True, auto_adjust=False)

                    if df is None or df.empty or "Close" not in df.columns:
                        time.sleep(pause)
                        continue

                    # KR 심볼은 US 프리마켓 필터 의미가 없으니 단순 최신 Close 사용
                    if sym.endswith(".KS") or sym.endswith(".KQ"):
                        close = df["Close"].dropna()
                        if close.empty:
                            time.sleep(pause)
                            continue
                        ts = df.index[-1]
                        # tz-awareness 보정 (한국은 대개 Asia/Seoul로 오지만 방어)
                        if getattr(ts, "tzinfo", None) is None:
                            # yfinance daily는 보통 tz-naive인 케이스가 있어 UTC로 가정 후 KST→ET 변환
                            ts = ts.tz_localize("UTC").tz_convert(TZ_ET)
                        else:
                            ts = ts.tz_convert(TZ_ET)
                        px = float(close.iloc[-1])
                        if not (px > 0):
                            time.sleep(pause)
                            continue
                        age_min = int((now_et - ts.to_pydatetime()).total_seconds() / 60)
                        return px, sym, f"yf_close({interval})", ts.to_pydatetime(), interval, age_min, ""

                    # US(및 글로벌) 심볼: ET로 맞춘 뒤 premarket/당일 마지막가 선택
                    df_et = _to_et_index(df)
                    px, ts = _pick_last_price_us_premarket(df_et, now_et)
                    if px is None or ts is None:
                        time.sleep(pause)
                        continue

                    # "너무 오래된 값" 방지 (OIL처럼 2023에 멈춘 종목 등)
                    age = now_et - ts.replace(tzinfo=TZ_ET) if ts.tzinfo is None else now_et - ts
                    # 주말/휴일 고려해서 10일 이상은 무조건 stale로 간주
                    if age > timedelta(days=10):
                        last_error = f"stale({age.days}d) interval={interval}"
                        time.sleep(pause)
                        continue

                    age_min = int(age.total_seconds() / 60)
                    return float(px), sym, f"yf_close({interval})", ts, interval, age_min, ""

                except Exception as e:
                    last_error = f"{type(e).__name__}: {e}"
                    time.sleep(pause)
            # next retry set
        # next candidate
    return None, "", "", None, "", None, last_error

def main():
    grouped = load_grouped_tickers(GROUP_FILES)
    if not grouped and Path("tickers.txt").exists():
        grouped = {"tickers": load_tickers("tickers.txt")}

    if not grouped:
        raise SystemExit("No tickers found. Provide group files or tickers.txt")

    # 그룹 순서 유지 + 그룹 내 순서 유지하며 전체 목록 구축
    all_tickers: list[str] = []
    seen: set[str] = set()
    for grp in grouped.values():
        for t in grp:
            if t not in seen:
                seen.add(t)
                all_tickers.append(t)

    now_kst = datetime.now(TZ_KST).strftime("%Y-%m-%d %H:%M:%S KST")
    now_et = datetime.now(TZ_ET)
    mode = "premarket (04:00~09:30 ET)" if now_et.time() < REGULAR_START else "last price (regular/extended)"
    print(f"Mode: {mode} | saved_at_kr={now_kst}")

    prices: dict[str, float] = {}
    meta: dict[str, dict] = {}
    missing: set[str] = set(all_tickers)

    for i, raw in enumerate(all_tickers, 1):
        px, sym, src, ts_et, interval, age_min, err = fetch_last_price(raw)
        if px is not None:
            prices[raw] = float(px)
            meta[raw] = {
                "yahoo_symbol": sym,
                "src": src,
                "ts_et": ts_et.isoformat() if ts_et else "",
                "interval": interval,
                "age_min": age_min if age_min is not None else "",
                "error": err,
            }
            missing.discard(raw)
        else:
            meta[raw] = {
                "yahoo_symbol": sym,
                "src": src,
                "ts_et": ts_et.isoformat() if ts_et else "",
                "interval": interval,
                "age_min": age_min if age_min is not None else "",
                "error": err or "no_data",
            }

        time.sleep(0.18)  # 레이트리밋 완화
        if i % 10 == 0:
            print(f"... {i}/{len(all_tickers)} processed")

    # 1) premarket_auto.csv (그룹 헤더 포함)
    with open("premarket_auto.csv", "w", newline="", encoding="utf-8") as f:
        f.write(f"# saved_at_kr,{now_kst}\n")
        f.write(f"# mode,{mode}\n")
        f.write("# provider,yahoo(yfinance)\n")
        for group_name, tickers in grouped.items():
            f.write(f"# group: {group_name}\n")
            f.write("ticker,premarket\n")
            for t in tickers:
                val = prices.get(t)
                if val is None:
                    f.write(f"{t},\n")
                else:
                    f.write(f"{t},{round(val, 2)}\n")

    # 2) premarket_auto_debug.csv (알파카 debug보다 정보 더 넣음)
    with open("premarket_auto_debug.csv", "w", newline="", encoding="utf-8") as f:
        f.write(f"# saved_at_kr,{now_kst}\n")
        f.write(f"# mode,{mode}\n")
        f.write("# provider,yahoo(yfinance)\n")
        f.write("group,ticker,yahoo_symbol,premarket,src,yf_ts_et,interval,age_min,error\n")
        for group_name, tickers in grouped.items():
            for t in tickers:
                info = meta.get(t, {})
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
    print(f"Total: {len(all_tickers)} | Missing: {len(missing)}")

    if missing:
        # TIGER(브랜드명) 문자열이 들어간 경우 + 6자리(국내 종목/ETF)만 따로 보여줌
        tiger_like = sorted([t for t in missing if "TIGER" in t])
        kr_like = sorted([t for t in missing if re.fullmatch(r'\d{6}(\.KS|\.KQ)?', t)])
        print("Missing tickers:", ", ".join(sorted(missing)))
        if tiger_like:
            print("Missing TIGER-like tokens:", ", ".join(tiger_like))
        if kr_like:
            print("Missing 6-digit KR tickers (check .KS/.KQ):", ", ".join(kr_like))

if __name__ == "__main__":
    main()
