#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Build ticker_master_korea.csv for Bulkowski Breakout Radar.

Goal: never leave the dashboard showing bare numeric tickers as the display name
when a Korean name can be resolved.

Resolution order:
  1) strong built-in/seed metadata
  2) generated/existing metadata, only when it is not a failed fallback
  3) pykrx market ticker names + ETF names
  4) Naver Finance item page name fallback

Outputs columns:
  ticker,name,market,asset_type,sector,industry,source
"""
from __future__ import annotations

import argparse
from datetime import date, timedelta
from pathlib import Path
import re
import time

import pandas as pd

try:
    import requests
    from bs4 import BeautifulSoup
except Exception:
    requests = None
    BeautifulSoup = None

ETF_NAME_KEYS = [
    "KODEX", "TIGER", "SOL", "ACE", "KBSTAR", "ARIRANG", "HANARO", "KOSEF", "TIMEFOLIO", "RISE",
    "PLUS", "WOORI", "BNK", "마이티", "TREX", "FOCUS", "마이다스", "KINDEX", "ETF", "ETN"
]
PREFERRED_HINTS = ["우", "우B", "2우B", "3우B"]
BAD_STRINGS = {"", "NAN", "NONE", "NULL", "UNMAPPED", "분류 미확인", "업종 미확인"}

# Covers the visible/high-impact names from the current KR scan and common macro proxies.
# The script still tries pykrx/Naver for everything else.
BUILTIN_META = {
    "000145.KS": ("하이트진로홀딩스우", "KOSPI", "PREFERRED", "Consumer Staples", "Beverages / Holding preferred"),
    "000150.KS": ("두산", "KOSPI", "STOCK", "Industrials", "Holding company / Energy equipment"),
    "000155.KS": ("두산우", "KOSPI", "PREFERRED", "Industrials", "Holding company preferred"),
    "000157.KS": ("두산2우B", "KOSPI", "PREFERRED", "Industrials", "Holding company preferred"),
    "000660.KS": ("SK하이닉스", "KOSPI", "STOCK", "Information Technology", "Semiconductors / Memory"),
    "002840.KS": ("미원상사", "KOSPI", "STOCK", "Materials", "Specialty chemicals"),
    "003670.KS": ("포스코퓨처엠", "KOSPI", "STOCK", "Materials", "Battery materials / Cathode"),
    "004370.KS": ("농심", "KOSPI", "STOCK", "Consumer Staples", "Food / Noodles"),
    "005490.KS": ("POSCO홀딩스", "KOSPI", "STOCK", "Materials", "Steel / Holding company"),
    "005930.KS": ("삼성전자", "KOSPI", "STOCK", "Information Technology", "Semiconductors / Electronics"),
    "005935.KS": ("삼성전자우", "KOSPI", "PREFERRED", "Information Technology", "Semiconductors / Electronics preferred"),
    "006260.KS": ("LS", "KOSPI", "STOCK", "Industrials", "Holding / Electrical infrastructure"),
    "006400.KS": ("삼성SDI", "KOSPI", "STOCK", "Industrials", "Battery cells"),
    "009150.KS": ("삼성전기", "KOSPI", "STOCK", "Information Technology", "Electronic components / MLCC"),
    "009155.KS": ("삼성전기우", "KOSPI", "PREFERRED", "Information Technology", "Electronic components preferred"),
    "010120.KS": ("LS ELECTRIC", "KOSPI", "STOCK", "Industrials", "Electrical equipment / Grid"),
    "011070.KS": ("LG이노텍", "KOSPI", "STOCK", "Information Technology", "Electronic components / Camera module"),
    "012450.KS": ("한화에어로스페이스", "KOSPI", "STOCK", "Industrials", "Defense / Aerospace"),
    "033780.KS": ("KT&G", "KOSPI", "STOCK", "Consumer Staples", "Tobacco"),
    "034020.KS": ("두산에너빌리티", "KOSPI", "STOCK", "Industrials", "Power equipment / Nuclear"),
    "036890.KQ": ("진성티이씨", "KOSDAQ", "STOCK", "Industrials", "Construction machinery parts"),
    "042700.KS": ("한미반도체", "KOSPI", "STOCK", "Information Technology", "Semiconductor equipment"),
    "051910.KS": ("LG화학", "KOSPI", "STOCK", "Materials", "Chemicals / Battery materials"),
    "051915.KS": ("LG화학우", "KOSPI", "PREFERRED", "Materials", "Chemicals / Battery materials preferred"),
    "060370.KQ": ("레고켐바이오", "KOSDAQ", "STOCK", "Health Care", "Biotech / ADC"),
    "062040.KS": ("산일전기", "KOSPI", "STOCK", "Industrials", "Transformer / Power equipment"),
    "069500.KS": ("KODEX 200", "KOSPI", "ETF", "ETF", "KOSPI 200 ETF"),
    "091160.KS": ("KODEX 반도체", "KOSPI", "ETF", "ETF", "Korea semiconductor ETF"),
    "091230.KS": ("TIGER 반도체", "KOSPI", "ETF", "ETF", "Korea semiconductor ETF"),
    "092190.KQ": ("서울바이오시스", "KOSDAQ", "STOCK", "Information Technology", "LED / Optoelectronics"),
    "097950.KS": ("CJ제일제당", "KOSPI", "STOCK", "Consumer Staples", "Food processing"),
    "102110.KS": ("TIGER 200", "KOSPI", "ETF", "ETF", "KOSPI 200 ETF"),
    "103590.KS": ("일진전기", "KOSPI", "STOCK", "Industrials", "Electric wire / Power equipment"),
    "122630.KS": ("KODEX 레버리지", "KOSPI", "ETF", "ETF", "KOSPI 200 leveraged ETF"),
    "227840.KS": ("현대코퍼레이션홀딩스", "KOSPI", "STOCK", "Industrials", "Trading / Holdings"),
    "230360.KQ": ("에코마케팅", "KOSDAQ", "STOCK", "Communication Services", "Advertising / Marketing"),
    "266370.KS": ("KODEX IT", "KOSPI", "ETF", "ETF", "Korea IT sector ETF"),
    "267770.KS": ("배럴", "KOSPI", "STOCK", "Consumer Discretionary", "Apparel / Leisure"),
    "298040.KS": ("효성중공업", "KOSPI", "STOCK", "Industrials", "Power equipment / Heavy industry"),
    "305540.KS": ("TIGER 2차전지테마", "KOSPI", "ETF", "ETF", "Korea battery theme ETF"),
    "305720.KS": ("KODEX 2차전지산업", "KOSPI", "ETF", "ETF", "Korea battery industry ETF"),
    "360750.KS": ("TIGER 미국S&P500", "KOSPI", "ETF", "ETF", "US S&P 500 ETF"),
    "381180.KS": ("TIGER 미국필라델피아반도체나스닥", "KOSPI", "ETF", "ETF", "US semiconductor ETF"),
    "390390.KS": ("KODEX 미국반도체MV", "KOSPI", "ETF", "ETF", "US semiconductor ETF"),
    "395160.KS": ("KODEX AI반도체", "KOSPI", "ETF", "ETF", "Korea AI semiconductor ETF"),
    "395270.KS": ("HANARO K-반도체", "KOSPI", "ETF", "ETF", "Korea semiconductor ETF"),
    "396500.KS": ("TIGER 반도체TOP10", "KOSPI", "ETF", "ETF", "Korea semiconductor TOP10 ETF"),
    "409820.KS": ("KODEX 미국나스닥100레버리지(합성 H)", "KOSPI", "ETF", "ETF", "US Nasdaq 100 leveraged ETF"),
    "454320.KS": ("HANARO CAPEX설비투자iSelect", "KOSPI", "ETF", "ETF", "Korea capex/equipment ETF"),
    "469150.KS": ("ACE AI반도체TOP3+", "KOSPI", "ETF", "ETF", "Korea AI semiconductor ETF"),
    "471760.KS": ("TIGER AI반도체핵심공정", "KOSPI", "ETF", "ETF", "Korea AI semiconductor process ETF"),
    "471990.KS": ("KODEX AI반도체핵심장비", "KOSPI", "ETF", "ETF", "Korea semiconductor equipment ETF"),
    "475310.KS": ("SOL 반도체후공정", "KOSPI", "ETF", "ETF", "Korea semiconductor back-end ETF"),
    "488080.KS": ("TIGER 반도체TOP10레버리지", "KOSPI", "ETF", "ETF", "Korea semiconductor leveraged ETF"),
    "494310.KS": ("KODEX 반도체레버리지", "KOSPI", "ETF", "ETF", "Korea semiconductor leveraged ETF"),
}


def extract_code(ticker: str) -> str:
    m = re.match(r"^(\d{6})", str(ticker or "").strip().upper())
    return m.group(1) if m else ""


def normalize_ticker(v: str) -> str:
    s = str(v or "").strip().upper()
    if re.fullmatch(r"\d{6}", s):
        s = s + ".KS"
    return s


def is_bad(v, ticker: str | None = None) -> bool:
    s = str(v or "").strip()
    if ticker and s.upper() == str(ticker).upper():
        return True
    return s.upper() in BAD_STRINGS


def read_tickers(report_path: str | None, tickers_path: str | None) -> list[str]:
    vals: list[str] = []
    if report_path and Path(report_path).exists():
        df = pd.read_csv(report_path, comment="#")
        col = "ticker" if "ticker" in df.columns else ("symbol" if "symbol" in df.columns else None)
        if col:
            vals += df[col].astype(str).str.strip().str.upper().tolist()
    if tickers_path and Path(tickers_path).exists():
        for line in Path(tickers_path).read_text(encoding="utf-8", errors="ignore").splitlines():
            s = line.strip()
            if not s or s.startswith("#"):
                continue
            if "#" in s:
                s = s.split("#", 1)[0].strip()
            for tok in re.split(r"[\s,;]+", s):
                tok = tok.strip().upper()
                if tok:
                    vals.append(tok)
    out, seen = [], set()
    for v in vals:
        v = normalize_ticker(v)
        if re.match(r"^\d{6}\.(KS|KQ)$", v) and v not in seen:
            seen.add(v)
            out.append(v)
    return out


def recent_ymd(days: int = 60) -> list[str]:
    today = date.today()
    return [(today - timedelta(days=i)).strftime("%Y%m%d") for i in range(days)]


def load_pykrx_maps() -> tuple[dict[str, dict], dict[str, str], str, str]:
    """Return code -> basic info and ETF code -> name."""
    try:
        from pykrx import stock
    except Exception as e:
        return {}, {}, "", f"pykrx_unavailable:{type(e).__name__}"

    code_map: dict[str, dict] = {}
    etf_name_map: dict[str, str] = {}
    selected = ""
    status = "no_data"

    for ds in recent_ymd(60):
        local: dict[str, dict] = {}
        ok_any = False
        for market in ["KOSPI", "KOSDAQ", "KONEX"]:
            # basic ticker list/name is more reliable than sector classifications
            try:
                codes = stock.get_market_ticker_list(ds, market=market)
                if codes:
                    ok_any = True
                for code in codes:
                    c = str(code).zfill(6)
                    try:
                        name = str(stock.get_market_ticker_name(c) or "").strip()
                    except Exception:
                        name = ""
                    if name:
                        local.setdefault(c, {})
                        local[c].update({"name": name, "market": market})
            except Exception:
                pass
            try:
                sdf = stock.get_market_sector_classifications(ds, market=market)
                if sdf is not None and not sdf.empty:
                    ok_any = True
                    for code, r in sdf.iterrows():
                        c = str(code).zfill(6)
                        name = str(r.get("종목명", "") or "").strip()
                        industry = str(r.get("업종명", "") or "").strip()
                        local.setdefault(c, {})
                        if name:
                            local[c]["name"] = name
                        local[c]["market"] = str(r.get("시장구분", market) or market).strip() or market
                        if industry:
                            local[c]["sector"] = industry
                            local[c]["industry"] = industry
            except Exception:
                pass
        try:
            etfs = stock.get_etf_ticker_list(ds)
            for code in etfs:
                c = str(code).zfill(6)
                try:
                    nm = str(stock.get_etf_ticker_name(c) or "").strip()
                except Exception:
                    nm = ""
                if nm:
                    etf_name_map[c] = nm
                    local.setdefault(c, {})
                    local[c].update({"name": nm, "market": "KOSPI", "asset_type": "ETF", "sector": "ETF", "industry": nm})
        except Exception:
            pass

        if ok_any or etf_name_map:
            selected = ds
            code_map = local
            status = "ok"
            break
    return code_map, etf_name_map, selected, status


def fetch_naver_name(code: str, timeout: int = 8) -> str:
    if not code or requests is None or BeautifulSoup is None:
        return ""
    url = f"https://finance.naver.com/item/main.naver?code={code}"
    try:
        r = requests.get(url, headers={"User-Agent": "Mozilla/5.0", "Referer": "https://finance.naver.com/"}, timeout=timeout)
        r.raise_for_status()
        r.encoding = r.apparent_encoding or "euc-kr"
        soup = BeautifulSoup(r.text, "html.parser")
        selectors = ["div.wrap_company h2", "div.wrap_company h2 a", "h2"]
        for sel in selectors:
            node = soup.select_one(sel)
            if node:
                name = node.get_text(" ", strip=True)
                if name and not re.search(r"네이버|증권", name):
                    return name.strip()
        title = soup.find("title")
        if title:
            text = title.get_text(" ", strip=True)
            for sep in [":", "-"]:
                if sep in text:
                    cand = text.split(sep, 1)[0].strip()
                    if cand:
                        return cand
        og = soup.find("meta", attrs={"property": "og:title"})
        if og and og.get("content"):
            return str(og.get("content")).split(":")[0].strip()
    except Exception:
        return ""
    return ""


def infer_asset_type(name: str, ticker: str, code: str, etf_name_map: dict[str, str]) -> str:
    n = str(name or "").upper()
    if code in etf_name_map or any(k.upper() in n for k in ETF_NAME_KEYS):
        return "ETF"
    if ticker.endswith("K.KS") or ticker.endswith("5.KS") or any(h in str(name or "") for h in PREFERRED_HINTS):
        return "PREFERRED"
    return "STOCK"


def load_existing_meta(paths: list[Path]) -> dict[str, dict]:
    out: dict[str, dict] = {}
    for p in paths:
        if not p.exists():
            continue
        try:
            df = pd.read_csv(p)
        except Exception:
            continue
        if df.empty or "ticker" not in df.columns:
            continue
        for _, r in df.iterrows():
            t = normalize_ticker(r.get("ticker"))
            if not re.match(r"^\d{6}\.(KS|KQ)$", t):
                continue
            row = {k: ("" if pd.isna(v) else str(v).strip()) for k, v in r.to_dict().items()}
            row["ticker"] = t
            out[t] = {**out.get(t, {}), **row}
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--report", default="data/kr/report_v2.csv")
    ap.add_argument("--tickers", default="data/kr/tickers_korea.txt")
    ap.add_argument("--out", default="data/kr/ticker_master_korea.csv")
    ap.add_argument("--seed", default="data/kr/ticker_master_korea_seed.csv")
    ap.add_argument("--no-naver", action="store_true")
    ap.add_argument("--sleep", type=float, default=0.03)
    ap.add_argument("--force-refresh", action="store_true", help="ignore failed existing rows such as name==ticker/Unmapped")
    args = ap.parse_args()

    tickers = read_tickers(args.report, args.tickers)
    if not tickers:
        raise SystemExit("No Korea tickers found from report/tickers input.")

    out_path = Path(args.out)
    seed_path = Path(args.seed)
    existing = load_existing_meta([seed_path, out_path])
    pykrx_map, etf_name_map, selected_date, pykrx_status = load_pykrx_maps()

    rows = []
    naver_fetches = 0
    naver_hits = 0
    for idx, t in enumerate(tickers, 1):
        code = extract_code(t)
        builtin = BUILTIN_META.get(t)
        prev = existing.get(t, {})
        info = pykrx_map.get(code, {})

        name = ""
        market = ""
        asset_type = ""
        sector = ""
        industry = ""
        source = ""

        if builtin:
            name, market, asset_type, sector, industry = builtin
            source = "builtin"

        # Existing metadata is accepted only if it is not a known failed fallback.
        if not name and not is_bad(prev.get("name"), t):
            name = str(prev.get("name", "")).strip()
            market = str(prev.get("market", "") or market).strip()
            asset_type = str(prev.get("asset_type", "") or asset_type).strip()
            sector = str(prev.get("sector", "") or sector).strip()
            industry = str(prev.get("industry", "") or industry).strip()
            source = "existing"

        if is_bad(name, t):
            name = str(info.get("name", "") or "").strip()
            if name:
                source = "pykrx"
        if not market:
            market = str(info.get("market", "") or "").strip()
        if not sector and not is_bad(info.get("sector")):
            sector = str(info.get("sector", "") or "").strip()
        if not industry and not is_bad(info.get("industry")):
            industry = str(info.get("industry", "") or "").strip()
        if not asset_type:
            asset_type = str(info.get("asset_type", "") or "").strip()

        if code in etf_name_map and etf_name_map[code]:
            name = etf_name_map[code]
            asset_type = "ETF"
            sector = "ETF"
            industry = industry if not is_bad(industry) else name
            source = "pykrx_etf"

        if (is_bad(name, t) or args.force_refresh) and not args.no_naver:
            # In force mode, still avoid overriding good built-in names unless name is bad.
            if is_bad(name, t):
                naver_fetches += 1
                nv = fetch_naver_name(code)
                if nv:
                    name = nv
                    source = "naver"
                    naver_hits += 1
                if args.sleep > 0:
                    time.sleep(args.sleep)

        if not asset_type:
            asset_type = infer_asset_type(name, t, code, etf_name_map)
        if asset_type == "ETF":
            sector = "ETF"
            industry = industry if not is_bad(industry) else (name if not is_bad(name, t) else "ETF")
        else:
            sector = sector if not is_bad(sector) else "분류 미확인"
            industry = industry if not is_bad(industry) else "업종 미확인"

        if is_bad(name, t):
            name = f"종목명 조회 필요({code})"
            source = source or "unresolved"

        rows.append({
            "ticker": t,
            "name": name,
            "market": market,
            "asset_type": asset_type,
            "sector": sector,
            "industry": industry,
            "source": source or "unknown",
        })
        if idx == 1 or idx % 50 == 0 or idx == len(tickers):
            print(f"... metadata {idx}/{len(tickers)} {t} -> {name}", flush=True)

    out = pd.DataFrame(rows).drop_duplicates("ticker", keep="last")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out.to_csv(out_path, index=False, encoding="utf-8-sig")

    missing_name = int(out["name"].astype(str).str.contains("종목명 조회 필요", na=False).sum())
    missing_industry = int(out["industry"].isin(["업종 미확인", "분류 미확인", "Unmapped", ""]).sum())
    print(f"Saved: {args.out} ({len(out)} rows)")
    print(f"pykrx status: {pykrx_status}; KRX date used: {selected_date or 'unknown'}")
    print(f"Naver fallback: requests={naver_fetches}, hits={naver_hits}")
    print(f"Unresolved names: {missing_name}")
    print(f"Weak industries: {missing_industry}")
    if missing_name:
        print(out.loc[out["name"].astype(str).str.contains("종목명 조회 필요", na=False), ["ticker", "name", "asset_type", "sector", "industry", "source"]].head(30).to_string(index=False))


if __name__ == "__main__":
    main()
