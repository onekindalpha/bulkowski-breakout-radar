#!/usr/bin/env python3
"""
update_premarket_yf_auto_v2.py

Yahoo Finance (yfinance) 기반 프리/정규/애프터 "최근 1분봉 종가"를 뽑아
- premarket_auto.csv  : (주석형) saved_at_kr + 그룹별 ticker,premarket
- premarket_auto_debug.csv : 그룹/티커/야후심볼/세션/바 타임스탬프/스테일(분)/매핑정보까지 기록

✅ Alpaca 버전과 비슷하게:
- 그룹 파일 지원 (macro_watch_yahoo.txt, tickers_core.txt, tickers_leverage2x.txt)
- KST 타임스탬프를 CSV 맨 윗줄(주석)로 남김
- 디버그 파일 생성
- 프리/정규/애프터 세션별로 "지금 시각 기준"의 마지막 바를 선택
- (옵션) 매크로 약어 일부를 야후 심볼로 매핑 (WTI/BRENT/GAS/VIX)

주의:
- Yahoo 데이터는 "준실시간"이며, 종목/시간대에 따라 업데이트가 듬성듬성할 수 있습니다.
- 디버그의 bar_ts_et / stale_min을 보면 실제 최신성이 바로 확인됩니다.
"""

import re
import time
from pathlib import Path
from datetime import datetime, time as dtime, timedelta
from zoneinfo import ZoneInfo

import pandas as pd
import yfinance as yf

TZ_ET = ZoneInfo("America/New_York")
TZ_KST = ZoneInfo("Asia/Seoul")

# 세션 정의 (ET)
PRE_START = dtime(4, 0)
PRE_END   = dtime(9, 30)
REG_START = dtime(9, 30)
REG_END   = dtime(16, 0)
AFT_START = dtime(16, 0)
AFT_END   = dtime(20, 0)

# Alpaca 스크립트와 동일한 그룹 파일 이름(있으면 이것을 우선 사용)
GROUP_FILES = [
    "macro_watch_yahoo.txt",
    "tickers_core.txt",
    "tickers_leverage2x.txt",
]

# 야후 심볼 매핑 (프로젝트 성격상 "매크로 약어"에 한해 최소만 적용)
# - macro_watch_yahoo.txt 안에서 WTI/BRENT/GAS/VIX를 매크로로 쓰는 경우가 많아서 제공
YF_ALIAS = {
    "WTI": "CL=F",     # WTI Crude Oil Futures
    "BRENT": "BZ=F",   # Brent Crude Oil Futures
    "GAS": "NG=F",     # Natural Gas Futures
    "VIX": "^VIX",     # CBOE Volatility Index
    # 필요하면 여기에 추가
}

def now_kst_str() -> str:
    return datetime.now(TZ_KST).strftime("%Y-%m-%d %H:%M:%S KST")

def _clean_token(tok: str) -> str:
    tok = tok.strip()
    if not tok:
        return ""
    if tok.startswith("#"):
        return ""
    # 공백/콤마/세미콜론 등은 상위 split에서 처리
    return tok

def load_tickers(path: str) -> list[str]:
    """
    - 주석(#) 무시
    - 구분자: whitespace , ; 등
    - 야후 심볼 특수문자(^,=,-,.) 허용 (예: ^VIX, CL=F)
    """
    p = Path(path)
    text = p.read_text(encoding="utf-8", errors="ignore")
    tokens = re.split(r"[\s,;]+", text.strip())
    out, seen = [], set()
    for tok in tokens:
        tok = _clean_token(tok)
        if not tok:
            continue
        # 허용 문자만 남김
        tok = re.sub(r"[^A-Za-z0-9\.\-\^=]", "", tok)
        if not tok:
            continue
        tok = tok.upper()
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
        group_name = p.stem
        ticks = load_tickers(str(p))
        if ticks:
            out[group_name] = ticks
    return out

def _ensure_et_index(df: pd.DataFrame) -> pd.DataFrame:
    if df is None or df.empty:
        return df
    # yfinance는 tz-aware인 경우도/naive인 경우도 있으므로 방어
    idx = df.index
    try:
        tz = idx.tz
    except Exception:
        tz = None
    if tz is None:
        # yfinance 1m은 보통 UTC로 해석해도 무방(대부분 그렇게 제공됨)
        df = df.tz_localize("UTC").tz_convert(TZ_ET)
    else:
        df = df.tz_convert(TZ_ET)
    return df

def session_window(now_et: datetime):
    t = now_et.time()
    if t < PRE_START:
        return "overnight (before 04:00 ET)", None, None
    if PRE_START <= t < PRE_END:
        return "premarket (04:00~09:30 ET)", PRE_START, PRE_END
    if REG_START <= t < REG_END:
        return "regular (09:30~16:00 ET)", REG_START, REG_END
    if AFT_START <= t < AFT_END:
        return "afterhours (16:00~20:00 ET)", AFT_START, AFT_END
    return "post-afterhours (after 20:00 ET)", None, None

def pick_last_close_for_now(df_1m: pd.DataFrame, now_et: datetime):
    """
    반환: (price, bar_ts_et, session_name, stale_min)
    - 현재 세션(프리/정규/애프터)에 해당하는 '오늘' 마지막 1m봉 종가를 우선 사용
    - 없으면 가장 최근 Close(전일 포함)로 fallback
    """
    if df_1m is None or df_1m.empty:
        return None, None, None, None

    df = _ensure_et_index(df_1m)
    df = df.dropna(subset=["Close"])
    if df.empty:
        return None, None, None, None

    sess_name, start_t, end_t = session_window(now_et)
    today = now_et.date()
    today_df = df[df.index.date == today]

    def _last_in_window(_df):
        if _df.empty:
            return None
        if start_t is None or end_t is None:
            # 세션이 없으면(overnight/post-afterhours) 오늘 데이터 전체 마지막
            if _df.empty:
                return None
            return _df.iloc[-1]
        win = _df[(_df.index.time >= start_t) & (_df.index.time < end_t)]
        if win.empty:
            return None
        return win.iloc[-1]

    row = None
    if not today_df.empty:
        row = _last_in_window(today_df)

    if row is None:
        # 오늘 세션 데이터가 없으면 전체 데이터에서 마지막 바 (전일 포함)로 fallback
        row = df.iloc[-1]
        # sess_name 유지(현재 시간대 설명은 유효하니)
    px = float(row["Close"])
    ts_et = row.name
    stale = None
    try:
        stale = int((now_et - ts_et).total_seconds() // 60)
    except Exception:
        stale = None
    return px, ts_et, sess_name, stale

def map_to_yahoo_symbol(sym: str) -> tuple[str, str]:
    """
    (yf_symbol, src) 반환
    src:
      - "direct" (그대로)
      - "alias:WTI->CL=F" 같은 형태
    """
    if sym in YF_ALIAS:
        return YF_ALIAS[sym], f"alias:{sym}->{YF_ALIAS[sym]}"
    return sym, "direct"

def download_1m_prepost(yf_symbols: list[str], chunk_size: int = 40, pause: float = 0.25):
    """
    yfinance는 1m + prepost + 다수티커 요청에서 실패/빈값이 종종 발생.
    - chunk로 나눠 다운로드
    - 반환: dict[yf_symbol] = dataframe(1m)
    """
    out: dict[str, pd.DataFrame] = {}
    for i in range(0, len(yf_symbols), chunk_size):
        chunk = yf_symbols[i:i+chunk_size]
        try:
            # period 5d: 1m 데이터 제한에 대비. (2d로도 되지만 실패 빈도 줄이려고)
            data = yf.download(
                tickers=" ".join(chunk),
                period="5d",
                interval="1m",
                prepost=True,
                auto_adjust=False,
                group_by="ticker",
                threads=True,
                progress=False,
            )
        except Exception:
            data = None

        if data is None or (isinstance(data, pd.DataFrame) and data.empty):
            # 개별 fallback
            for sym in chunk:
                out[sym] = pd.DataFrame()
            time.sleep(pause)
            continue

        # multi vs single 처리
        if isinstance(data.columns, pd.MultiIndex):
            # group_by="ticker"면 보통 (Ticker, OHLCV) 형태이거나 (OHLCV, Ticker) 형태가 섞일 수 있어 방어
            lvl0 = list(map(str, data.columns.get_level_values(0)))
            lvl1 = list(map(str, data.columns.get_level_values(1)))
            # 케이스1: columns[0] level0가 티커
            if any(sym in lvl0 for sym in chunk):
                for sym in chunk:
                    if sym in data.columns.get_level_values(0):
                        out[sym] = data[sym].copy()
                    else:
                        out[sym] = pd.DataFrame()
            # 케이스2: columns level1이 티커
            elif any(sym in lvl1 for sym in chunk):
                for sym in chunk:
                    if sym in data.columns.get_level_values(1):
                        # (OHLCV, Ticker) -> xs로 추출
                        out[sym] = data.xs(sym, axis=1, level=1).copy()
                    else:
                        out[sym] = pd.DataFrame()
            else:
                for sym in chunk:
                    out[sym] = pd.DataFrame()
        else:
            # 단일 티커일 때: data는 OHLCV 컬럼만
            sym = chunk[0]
            out[sym] = data.copy()

        time.sleep(pause)
    return out

def fetch_individual_history(yf_symbol: str, retry=2, pause=0.8) -> pd.DataFrame:
    for _ in range(retry + 1):
        try:
            tk = yf.Ticker(yf_symbol)
            df = tk.history(period="5d", interval="1m", prepost=True, auto_adjust=False)
            if df is None or df.empty:
                time.sleep(pause)
                continue
            return df
        except Exception:
            time.sleep(pause)
    return pd.DataFrame()

def main():
    # 그룹 파일 우선
    grouped = load_grouped_tickers(GROUP_FILES)
    if not grouped and Path("tickers.txt").exists():
        grouped = {"tickers": load_tickers("tickers.txt")}

    if not grouped:
        raise SystemExit("No tickers found: create group files or tickers.txt")

    # 전체 심볼(원본) + 매핑된 야후 심볼 구축
    order_groups = list(grouped.keys())
    orig_symbols = []
    seen = set()
    for g in order_groups:
        for sym in grouped[g]:
            if sym not in seen:
                seen.add(sym)
                orig_symbols.append(sym)

    sym_to_yf = {}
    sym_src = {}
    for sym in orig_symbols:
        yf_sym, src = map_to_yahoo_symbol(sym)
        sym_to_yf[sym] = yf_sym
        sym_src[sym] = src

    unique_yf = []
    seen_yf = set()
    for yf_sym in sym_to_yf.values():
        if yf_sym not in seen_yf:
            seen_yf.add(yf_sym)
            unique_yf.append(yf_sym)

    now_et = datetime.now(TZ_ET)
    mode, _, _ = session_window(now_et)

    print(f"Mode: {mode} | now_et={now_et:%Y-%m-%d %H:%M:%S %Z} | now_kst={now_kst_str()}")

    # 1) bulk download 시도
    bulk = download_1m_prepost(unique_yf, chunk_size=35, pause=0.35)

    # 2) bulk가 비었거나 특정 심볼이 빈 DF면 개별 fallback
    for yf_sym in unique_yf:
        df = bulk.get(yf_sym)
        if df is None or df.empty:
            bulk[yf_sym] = fetch_individual_history(yf_sym, retry=2, pause=0.8)

    # 3) 가격 산출
    prices = {}  # orig -> price
    debug_rows = []
    missing = []

    for g in order_groups:
        for orig in grouped[g]:
            yf_sym = sym_to_yf.get(orig, orig)
            df = bulk.get(yf_sym, pd.DataFrame())
            px, ts_et, sess, stale = pick_last_close_for_now(df, now_et)
            if px is None:
                prices[orig] = None
                missing.append(orig)
                debug_rows.append({
                    "group": g,
                    "ticker": orig,
                    "yf_symbol": yf_sym,
                    "premarket": None,
                    "session": mode,
                    "bar_ts_et": None,
                    "bar_ts_utc": None,
                    "stale_min": None,
                    "src": sym_src.get(orig, "direct"),
                    "note": "no_1m_data",
                })
            else:
                prices[orig] = float(px)
                ts_utc = None
                try:
                    ts_utc = ts_et.tz_convert("UTC")
                except Exception:
                    ts_utc = None
                debug_rows.append({
                    "group": g,
                    "ticker": orig,
                    "yf_symbol": yf_sym,
                    "premarket": round(float(px), 2),
                    "session": sess or mode,
                    "bar_ts_et": ts_et.strftime("%Y-%m-%d %H:%M:%S %Z") if ts_et is not None else None,
                    "bar_ts_utc": ts_utc.strftime("%Y-%m-%d %H:%M:%S %Z") if ts_utc is not None else None,
                    "stale_min": stale,
                    "src": sym_src.get(orig, "direct"),
                    "note": "",
                })

    # 4) 저장 (Alpaca 스타일 주석 + 그룹 헤더)
    kst = now_kst_str()
    out_path = Path("premarket_auto.csv")
    with out_path.open("w", encoding="utf-8", newline="") as f:
        f.write(f"# saved_at_kr,{kst}\n")
        f.write(f"# mode,{mode}\n")
        for g in order_groups:
            f.write(f"# group: {g}\n")
            f.write("ticker,premarket\n")
            for orig in grouped[g]:
                v = prices.get(orig)
                if v is None:
                    f.write(f"{orig},\n")
                else:
                    f.write(f"{orig},{round(float(v), 2)}\n")

    dbg_path = Path("premarket_auto_debug.csv")
    dbg_df = pd.DataFrame(debug_rows)
    # 보기 좋게 컬럼 순서 고정
    cols = ["group","ticker","yf_symbol","premarket","session","bar_ts_et","bar_ts_utc","stale_min","src","note"]
    dbg_df = dbg_df[cols]
    dbg_path.write_text(f"# saved_at_kr,{kst}\n# mode,{mode}\n", encoding="utf-8")
    dbg_df.to_csv(dbg_path, mode="a", index=False)

    print(f"Saved {out_path.resolve()}  | groups={len(order_groups)} | tickers={len(orig_symbols)} | missing={len(missing)}")
    print(f"Saved {dbg_path.resolve()}")
    if missing:
        print("Missing:", ", ".join(sorted(set(missing))))

if __name__ == "__main__":
    main()
