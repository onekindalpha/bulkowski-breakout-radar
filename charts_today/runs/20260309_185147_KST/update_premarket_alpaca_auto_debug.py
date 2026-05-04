import os, re, csv
from pathlib import Path
import requests
from datetime import datetime, timedelta
try:
    from zoneinfo import ZoneInfo
except Exception:
    try:
        from backports.zoneinfo import ZoneInfo
    except Exception:
        ZoneInfo = None

BASE = "https://data.alpaca.markets"
HEADERS = {
    "APCA-API-KEY-ID": os.getenv("APCA_API_KEY_ID", ""),
    "APCA-API-SECRET-KEY": os.getenv("APCA_API_SECRET_KEY", ""),
    "Accept": "application/json",
}

FEED = os.getenv("APCA_DATA_FEED", "iex").strip().lower() or "iex"

def load_tickers(path="tickers.txt"):
    text = Path(path).read_text(encoding="utf-8", errors="ignore").strip()
    tokens = re.split(r"[\s,;]+", text)
    out, seen = [], set()
    for t in tokens:
        t = t.strip().upper()
        if not t: 
            continue
        # 진짜 티커만(1~5 글자) 남기기: 헤더/설명문 자동 제거
        if not re.fullmatch(r"[A-Z]{1,5}", t):
            continue
        if t not in seen:
            seen.add(t)
            out.append(t)
    return out

def chunk(lst, n=100):
    for i in range(0, len(lst), n):
        yield lst[i:i+n]

def get_latest_trades(symbols):
    url = f"{BASE}/v2/stocks/trades/latest"
    params = {"symbols": ",".join(symbols), "feed": FEED}  # 기본: iex (무료), 필요시 env로 sip
    r = requests.get(url, headers=HEADERS, params=params, timeout=15)
    r.raise_for_status()
    return r.json().get("trades", {})

def get_latest_quotes(symbols):
    url = f"{BASE}/v2/stocks/quotes/latest"
    params = {"symbols": ",".join(symbols), "feed": FEED}
    r = requests.get(url, headers=HEADERS, params=params, timeout=15)
    r.raise_for_status()
    return r.json().get("quotes", {})

def midpoint(q):
    bp, ap = q.get("bp"), q.get("ap")
    if bp and ap and bp > 0 and ap > 0:
        return (bp + ap) / 2.0
    if bp and bp > 0:
        return float(bp)
    if ap and ap > 0:
        return float(ap)
    return None

# 그룹으로 분리된 티커 파일들 (프로젝트 루트 또는 스크립트와 동일 폴더에 위치)
GROUP_FILES = [
    "macro_watch_yahoo.txt",
    "tickers_core.txt",
    "tickers_leverage2x.txt",
]

def load_grouped_tickers(group_files):
    """
    각 그룹 파일을 읽어 {group_name: [tickers...]} 형태로 반환.
    파일이 없으면 건너뜀. group_name은 파일명에서 확장자 제거한 것.
    """
    out = {}
    for p in group_files:
        path = Path(p)
        if not path.exists():
            continue
        name = path.stem  # 파일명에서 확장자 제거
        toks = load_tickers(str(path))
        out[name] = toks
    return out

def load_manual_prices(path="premarket_manual.csv"):
    """
    수동 CSV에서 ticker->price 매핑을 반환.
    파일이 없거나 값이 비어있으면 무시.
    """
    p = Path(path)
    if not p.exists():
        return {}
    out = {}
    with p.open(encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            t = (row.get("ticker") or "").strip().upper()
            v = (row.get("premarket") or "").strip()
            if not t or not v:
                continue
            try:
                out[t] = float(v)
            except Exception:
                # 숫자 파싱 실패하면 무시
                continue
    return out

def main():
    if not HEADERS["APCA-API-KEY-ID"] or not HEADERS["APCA-API-SECRET-KEY"]:
        raise SystemExit("Set APCA_API_KEY_ID / APCA_API_SECRET_KEY first.")

    # 그룹별로 티커 로드 (txt들이 존재하면 그걸 기준으로)
    grouped = load_grouped_tickers(GROUP_FILES)
    # fallback: 기존 tickers.txt가 있으면 하나의 그룹으로 처리
    if not grouped and Path("tickers.txt").exists():
        grouped = {"tickers": load_tickers("tickers.txt")}

    # 모든 티커(그룹 순서 유지, 그룹 내 순서 유지), 중복 제거
    all_tickers = []
    seen = set()
    for grp in grouped.values():
        for t in grp:
            if t not in seen:
                seen.add(t)
                all_tickers.append(t)

    if not all_tickers:
        raise SystemExit("No tickers found in group files or tickers.txt.")

    prices = {}
    meta = {}  # sym -> {'src': 'trade|quote|manual', 'ts': <alpaca timestamp>}
    missing = set(all_tickers)

    # 1) 최신 체결가 우선
    for syms in chunk(all_tickers, 100):
        trades = get_latest_trades(syms)
        for sym, tr in trades.items():
            p = tr.get("p")
            if p and p > 0:
                prices[sym] = float(p)
                meta[sym] = {"src": "trade", "ts": tr.get("t")}
                missing.discard(sym)

    # 2) 체결 없으면 최신 호가 midpoint로 보완
    if missing:
        for syms in chunk(sorted(missing), 100):
            quotes = get_latest_quotes(syms)
            for sym, q in quotes.items():
                p = midpoint(q)
                if p and p > 0:
                    prices[sym] = float(p)
                    meta[sym] = {"src": "quote", "ts": q.get("t")}
                    missing.discard(sym)

    # 추가 보완: 수동 CSV에서 채우기
    if missing:
        manual_prices = load_manual_prices("premarket_manual.csv")
        filled_from_manual = 0
        for sym in list(missing):
            if sym in manual_prices:
                prices[sym] = manual_prices[sym]
                meta[sym] = {"src": "manual", "ts": None}
                missing.discard(sym)
                filled_from_manual += 1
        if filled_from_manual:
            print(f"Filled {filled_from_manual} tickers from premarket_manual.csv")

    # 저장: 맨 윗줄에 한국시간 타임스탬프 추가, 그룹별은 주석형 헤더로 출력
    if ZoneInfo is not None:
        now_kst = datetime.now(ZoneInfo("Asia/Seoul")).strftime("%Y-%m-%d %H:%M:%S KST")
    else:
        now_kst = (datetime.utcnow() + timedelta(hours=9)).strftime("%Y-%m-%d %H:%M:%S KST")

    # raw write: comment-style group header ("# group: name") + regular CSV header per group
    with open("premarket_auto.csv", "w", newline="", encoding="utf-8") as f:
        # 첫 줄에 저장 시각(KST)을 남깁니다. CSV 파서가 필요하면 comment 줄을 건너뛰세요.
        f.write(f"# saved_at_kr,{now_kst}\n")
        for group_name, tickers in grouped.items():
            f.write(f"# group: {group_name}\n")
            f.write("ticker,premarket\n")
            for sym in tickers:
                val = prices.get(sym)
                if val is None:
                    f.write(f"{sym},\n")
                else:
                    f.write(f"{sym},{round(val, 2)}\n")


    # 디버그용: 각 심볼의 가격 출처(trade/quote/manual)와 Alpaca 타임스탬프를 같이 저장
    with open("premarket_auto_debug.csv", "w", newline="", encoding="utf-8") as f:
        f.write(f"# saved_at_kr,{now_kst}\n")
        f.write(f"# feed,{FEED}\n")
        f.write("group,ticker,premarket,src,alpaca_ts\n")
        for group_name, tickers in grouped.items():
            for sym in tickers:
                val = prices.get(sym)
                info = meta.get(sym, {})
                src = info.get("src", "")
                ts = info.get("ts", "")
                if val is None:
                    f.write(f"{group_name},{sym},,{src},{ts}\n")
                else:
                    f.write(f"{group_name},{sym},{round(val, 2)},{src},{ts}\n")

    out_path = Path("premarket_auto.csv").resolve()
    print(f"Saved {out_path} ({len(all_tickers)} tickers). Missing: {len(missing)}. feed={FEED}. saved_at_kr={now_kst}")
    if missing:
        print("Missing (no IEX trade/quote and no manual value):", ", ".join(sorted(missing)))

if __name__ == "__main__":
    main()