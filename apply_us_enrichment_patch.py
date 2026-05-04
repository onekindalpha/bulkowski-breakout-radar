#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""US enrichment patch for Bulkowski Breakout Radar.

Run from repo root:
  python apply_us_enrichment_patch.py

What it does:
- Replaces build_ticker_master_us.py with a richer metadata builder.
- Makes US workflow build ticker metadata with a larger yfinance lookup budget.
- Replaces US/KR research links so US uses Yahoo/Finviz/TradingView/SEC/Nasdaq/ETF.com etc.
- Adds a Company Snapshot block to the selected ticker detail when US metadata exists.
"""
from __future__ import annotations
from pathlib import Path
import re

ROOT = Path.cwd()
APP = ROOT / "bulkowski_breakout_radar" / "streamlit_app.py"
US_BUILDER = ROOT / "bulkowski_breakout_radar" / "build_ticker_master_us.py"
US_WORKFLOW = ROOT / ".github" / "workflows" / "us-breakout-scan.yml"

US_BUILDER_CODE = r'''#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Build ticker_master_us.csv for Bulkowski Breakout Radar.

Metadata priority:
1. Existing ticker_master_us.csv rows with real names are reused.
2. Finviz group members, if available, fill Company / Sector / Industry.
3. Built-in map fills common stocks/ETFs quickly.
4. yfinance fills remaining names and optional company snapshot fields.

The script is intentionally best-effort. It always writes a valid CSV even when
external metadata endpoints fail or rate-limit.
"""
from __future__ import annotations

import argparse
import re
import time
from pathlib import Path
from typing import Any

import pandas as pd

try:
    import yfinance as yf
except Exception:
    yf = None

BAD_NAMES = {"", "UNMAPPED", "종목명 조회 필요", "NAN", "NONE"}

ETF_TICKERS = {
    "SPY","QQQ","DIA","IWM","MDY","TQQQ","SQQQ","QLD","QID","UPRO","SPXU","SPXL","SPXS","SSO","SDS",
    "SOXL","SOXS","SOXX","SMH","XSD","TECL","TECS","FNGU","FNGD","BULZ","BERZ","USD",
    "XLE","XOP","OIH","XLB","XLK","XLU","XLI","XLV","XLP","XLY","IYE","IYM","IYG","IYW","IYR","IYH","IYZ","IYV",
    "ERX","ERY","GUSH","DRIP","DIG","UCO","SCO","BOIL","KOLD","USO","BNO","UNG",
    "GLD","IAU","GLDM","SLV","SIVR","GDX","GDXJ","NUGT","DUST","UGL","GLL","AGQ","SIL","SILJ",
    "TLT","IEF","VIXY","HYG","LQD","UUP","JETS","PAVE","GRID","IBB","AIQ","BOTZ","LIT","BATT","COPX","DBC","PDBC","GSG","COM",
    "IBIT","FBTC","ARKB","BITB","HODL","BRRR","EZBC","GBTC","ETHA","FETH","ETH","EZET","ETHV","ETHW","ETHE","QETH",
    "LABU","LABD","CURE","DRN","DRV","FAS","FAZ","TMF","TMV","UBOT","AIBU","AIBD","NVDL","NVDU","NVDD",
}

BUILTIN: dict[str, tuple[str, str, str]] = {
    # Mega-cap / AI / semis
    "NVDA": ("NVIDIA Corporation", "Technology", "Semiconductors"),
    "AVGO": ("Broadcom Inc.", "Technology", "Semiconductors"),
    "AMD": ("Advanced Micro Devices", "Technology", "Semiconductors"),
    "TSM": ("Taiwan Semiconductor Manufacturing", "Technology", "Semiconductors"),
    "ASML": ("ASML Holding", "Technology", "Semiconductor Equipment"),
    "MU": ("Micron Technology", "Technology", "Memory Semiconductors"),
    "TXN": ("Texas Instruments", "Technology", "Semiconductors"),
    "NXPI": ("NXP Semiconductors", "Technology", "Semiconductors"),
    "MCHP": ("Microchip Technology", "Technology", "Semiconductors"),
    "ADI": ("Analog Devices", "Technology", "Semiconductors"),
    "INTC": ("Intel Corporation", "Technology", "Semiconductors"),
    "ANET": ("Arista Networks", "Technology", "Networking"),
    "VRT": ("Vertiv Holdings", "Industrials", "Power / Data Center Infrastructure"),
    "SMCI": ("Super Micro Computer", "Technology", "AI Servers"),
    "MSFT": ("Microsoft Corporation", "Technology", "Software / Cloud"),
    "AAPL": ("Apple Inc.", "Technology", "Consumer Electronics"),
    "GOOGL": ("Alphabet Inc. Class A", "Communication Services", "Internet / Search"),
    "GOOG": ("Alphabet Inc. Class C", "Communication Services", "Internet / Search"),
    "META": ("Meta Platforms", "Communication Services", "Social / AI"),
    "AMZN": ("Amazon.com", "Consumer Discretionary", "E-commerce / Cloud"),
    "TSLA": ("Tesla Inc.", "Consumer Discretionary", "EV / Energy"),
    "SBUX": ("Starbucks Corporation", "Consumer Discretionary", "Restaurants"),
    "NFLX": ("Netflix", "Communication Services", "Streaming Entertainment"),
    "ADBE": ("Adobe", "Technology", "Software"),
    "CRM": ("Salesforce", "Technology", "Software"),
    "ORCL": ("Oracle", "Technology", "Software / Cloud"),
    "CSCO": ("Cisco Systems", "Technology", "Networking Equipment"),
    "DDOG": ("Datadog", "Technology", "Observability Software"),
    "SNPS": ("Synopsys", "Technology", "EDA Software"),
    "CDNS": ("Cadence Design Systems", "Technology", "EDA Software"),
    # Industrials / power / infra
    "PWR": ("Quanta Services", "Industrials", "Grid Infrastructure"),
    "ETN": ("Eaton", "Industrials", "Electrical Equipment"),
    "GEV": ("GE Vernova", "Industrials", "Power Equipment"),
    "VST": ("Vistra Corp.", "Utilities", "Power Generation"),
    "CEG": ("Constellation Energy", "Utilities", "Nuclear / Power"),
    "NRG": ("NRG Energy", "Utilities", "Power Generation"),
    "CAT": ("Caterpillar", "Industrials", "Construction Machinery"),
    "DE": ("Deere & Company", "Industrials", "Agricultural Machinery"),
    "JBL": ("Jabil", "Technology", "Electronics Manufacturing Services"),
    "FLEX": ("Flex", "Technology", "Electronics Manufacturing Services"),
    "FIX": ("Comfort Systems USA", "Industrials", "Building Systems"),
    "MTZ": ("MasTec", "Industrials", "Infrastructure Construction"),
    "STLD": ("Steel Dynamics", "Basic Materials", "Steel"),
    "NUE": ("Nucor", "Basic Materials", "Steel"),
    "SLB": ("Schlumberger", "Energy", "Oilfield Services"),
    "FANG": ("Diamondback Energy", "Energy", "Oil & Gas E&P"),
    "ET": ("Energy Transfer", "Energy", "Midstream Energy"),
    "WMB": ("Williams Companies", "Energy", "Midstream Energy"),
    # Financials / health / staples
    "BRK-B": ("Berkshire Hathaway", "Financial Services", "Financial Conglomerate"),
    "JPM": ("JPMorgan Chase", "Financial Services", "Banking"),
    "V": ("Visa Inc.", "Financial Services", "Payments"),
    "MA": ("Mastercard", "Financial Services", "Payments"),
    "COST": ("Costco Wholesale", "Consumer Staples", "Retail / Wholesale"),
    "WMT": ("Walmart", "Consumer Staples", "Retail"),
    "LLY": ("Eli Lilly", "Healthcare", "Pharmaceuticals"),
    "ABBV": ("AbbVie", "Healthcare", "Pharmaceuticals"),
    "UNH": ("UnitedHealth Group", "Healthcare", "Healthcare Plans"),
    "ELV": ("Elevance Health", "Healthcare", "Healthcare Plans"),
    "MO": ("Altria Group", "Consumer Staples", "Tobacco"),
    # ETFs / funds
    "SPY": ("SPDR S&P 500 ETF", "ETF", "S&P 500 ETF"),
    "QQQ": ("Invesco QQQ Trust", "ETF", "Nasdaq 100 ETF"),
    "TQQQ": ("ProShares UltraPro QQQ", "ETF", "Nasdaq 100 leveraged ETF"),
    "SQQQ": ("ProShares UltraPro Short QQQ", "ETF", "Nasdaq 100 inverse leveraged ETF"),
    "SOXL": ("Direxion Daily Semiconductor Bull 3X", "ETF", "Semiconductor leveraged ETF"),
    "SOXS": ("Direxion Daily Semiconductor Bear 3X", "ETF", "Semiconductor inverse leveraged ETF"),
    "SMH": ("VanEck Semiconductor ETF", "ETF", "Semiconductor ETF"),
    "SOXX": ("iShares Semiconductor ETF", "ETF", "Semiconductor ETF"),
    "XLK": ("Technology Select Sector SPDR Fund", "ETF", "Technology sector ETF"),
    "XLU": ("Utilities Select Sector SPDR Fund", "ETF", "Utilities sector ETF"),
    "XLE": ("Energy Select Sector SPDR Fund", "ETF", "Energy sector ETF"),
    "OIH": ("VanEck Oil Services ETF", "ETF", "Oil services ETF"),
    "IYW": ("iShares U.S. Technology ETF", "ETF", "US technology ETF"),
    "LIT": ("Global X Lithium & Battery Tech ETF", "ETF", "Lithium/Battery ETF"),
    "DBC": ("Invesco DB Commodity Index Tracking Fund", "ETF", "Commodity ETF"),
    "COM": ("Direxion Auspice Broad Commodity Strategy ETF", "ETF", "Commodity ETF"),
    "NVDL": ("GraniteShares 2x Long NVDA Daily ETF", "ETF", "Single-stock leveraged NVDA ETF"),
    "NVDU": ("Direxion Daily NVDA Bull 2X", "ETF", "Single-stock leveraged NVDA ETF"),
    "IBIT": ("iShares Bitcoin Trust", "ETF", "Spot Bitcoin ETF"),
    "ETHA": ("iShares Ethereum Trust ETF", "ETF", "Spot Ethereum ETF"),
    "QETH": ("Ether ETF proxy", "ETF", "Ethereum ETF / proxy"),
}

OUT_COLUMNS = [
    "ticker", "name", "asset_type", "sector", "industry", "exchange", "quote_type", "market_cap",
    "beta", "trailing_pe", "forward_pe", "dividend_yield", "revenue_growth", "earnings_growth",
    "target_mean_price", "recommendation", "website", "source",
]


def read_csv(path: Path) -> pd.DataFrame:
    if path is None or not path.exists():
        return pd.DataFrame()
    try:
        return pd.read_csv(path, comment="#")
    except Exception:
        return pd.read_csv(path, comment="#", engine="python")


def split_tokens(s: str) -> list[str]:
    return [x for x in re.split(r"[\s,;]+", str(s)) if x]


def clean_ticker(t: Any) -> str:
    t = str(t).strip().upper().lstrip("$")
    if t == "BF.B":
        t = "BF-B"
    return t


def valid_us_ticker(t: str) -> bool:
    if not t or t in {"TICKER", "SOURCE", "S&P", "CARS.COM"}:
        return False
    if re.search(r"\.(KS|KQ)$", t):
        return False
    if len(t) > 20:
        return False
    return bool(re.match(r"^[A-Z0-9\^=\.\-]+$", t))


def load_tickers(paths: list[Path]) -> list[str]:
    out, seen = [], set()
    for p in paths:
        if not p or not p.exists():
            continue
        vals: list[str] = []
        if p.suffix.lower() == ".csv":
            df = read_csv(p)
            col = None
            for c in ["ticker", "Ticker", "symbol", "Symbol"]:
                if c in df.columns:
                    col = c
                    break
            if col:
                vals = df[col].dropna().astype(str).tolist()
        else:
            for line in p.read_text(encoding="utf-8", errors="ignore").splitlines():
                line = line.split("#", 1)[0].strip()
                if line:
                    vals.extend(split_tokens(line))
        for v in vals:
            t = clean_ticker(v)
            if valid_us_ticker(t) and t not in seen:
                seen.add(t)
                out.append(t)
    return out


def good_name(name: Any, ticker: str) -> bool:
    s = str(name or "").strip()
    if not s:
        return False
    if s.upper() in BAD_NAMES:
        return False
    if s.upper() == ticker.upper():
        return False
    return True


def to_float_or_blank(v: Any):
    try:
        if v is None or pd.isna(v):
            return ""
        return float(v)
    except Exception:
        return ""


def finviz_map(path: Path) -> dict[str, dict]:
    df = read_csv(path)
    out: dict[str, dict] = {}
    if df.empty or "Ticker" not in df.columns:
        return out
    for _, r in df.iterrows():
        t = clean_ticker(r.get("Ticker", ""))
        if not valid_us_ticker(t):
            continue
        name = str(r.get("Company", "") or "").strip()
        sector = str(r.get("Sector", "") or "").strip()
        industry = str(r.get("Industry", "") or "").strip()
        if good_name(name, t):
            out[t] = {
                "ticker": t,
                "name": name,
                "sector": sector or "분류 미확인",
                "industry": industry or "업종 미확인",
                "source": "finviz_members",
            }
    return out


def asset_type_for(ticker: str, name: str = "", sector: str = "", quote_type: str = "") -> str:
    n = str(name or "").upper()
    sec = str(sector or "").upper()
    qt = str(quote_type or "").upper()
    if ticker.upper() in ETF_TICKERS or qt in {"ETF", "MUTUALFUND"} or sec == "ETF" or " ETF" in n or n.endswith("ETF"):
        return "ETF"
    if "=F" in ticker or ticker.startswith("^"):
        return "INDEX/FUTURE"
    return "STOCK"


def row_from_builtin(t: str) -> dict | None:
    if t not in BUILTIN:
        return None
    name, sector, industry = BUILTIN[t]
    return {
        "ticker": t,
        "name": name,
        "sector": sector,
        "industry": industry,
        "asset_type": asset_type_for(t, name, sector),
        "source": "builtin",
    }


def yf_lookup(ticker: str) -> dict | None:
    if yf is None or "=F" in ticker or ticker.startswith("^"):
        return None
    try:
        info = yf.Ticker(ticker).get_info()
        if not isinstance(info, dict) or not info:
            return None
        name = info.get("shortName") or info.get("longName") or ""
        if not good_name(name, ticker):
            return None
        quote_type = str(info.get("quoteType") or "").upper()
        sector = info.get("sector") or ""
        industry = info.get("industry") or ""
        if quote_type in {"ETF", "MUTUALFUND"}:
            sector = sector or "ETF"
            industry = industry or "ETF / Fund"
        return {
            "ticker": ticker,
            "name": str(name).strip(),
            "sector": str(sector or "분류 미확인").strip(),
            "industry": str(industry or "업종 미확인").strip(),
            "exchange": info.get("exchange") or info.get("fullExchangeName") or "",
            "quote_type": quote_type,
            "market_cap": to_float_or_blank(info.get("marketCap")),
            "beta": to_float_or_blank(info.get("beta")),
            "trailing_pe": to_float_or_blank(info.get("trailingPE")),
            "forward_pe": to_float_or_blank(info.get("forwardPE")),
            "dividend_yield": to_float_or_blank(info.get("dividendYield")),
            "revenue_growth": to_float_or_blank(info.get("revenueGrowth")),
            "earnings_growth": to_float_or_blank(info.get("earningsGrowth")),
            "target_mean_price": to_float_or_blank(info.get("targetMeanPrice")),
            "recommendation": info.get("recommendationKey") or "",
            "website": info.get("website") or "",
            "source": "yfinance",
        }
    except Exception:
        return None


def normalize_row(row: dict, ticker: str) -> dict:
    name = str(row.get("name", "") or "").strip()
    if not good_name(name, ticker):
        name = "종목명 조회 필요"
    sector = str(row.get("sector", "") or "").strip() or "분류 미확인"
    industry = str(row.get("industry", "") or "").strip() or "업종 미확인"
    quote_type = str(row.get("quote_type", "") or "")
    asset = str(row.get("asset_type", "") or asset_type_for(ticker, name, sector, quote_type)).strip()
    out = {c: "" for c in OUT_COLUMNS}
    out.update(row)
    out.update({"ticker": ticker, "name": name, "sector": sector, "industry": industry, "asset_type": asset})
    return {c: out.get(c, "") for c in OUT_COLUMNS}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--report", default="data/us/report_v2.csv")
    ap.add_argument("--tickers", default="data/us/tickers.txt")
    ap.add_argument("--premarket", default="data/us/premarket_auto.csv")
    ap.add_argument("--finviz-members", default="")
    ap.add_argument("--out", default="data/us/ticker_master_us.csv")
    ap.add_argument("--max-yf", type=int, default=1500)
    ap.add_argument("--force-refresh", action="store_true")
    args = ap.parse_args()

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    existing: dict[str, dict] = {}
    if out_path.exists() and not args.force_refresh:
        old = read_csv(out_path)
        if not old.empty and "ticker" in old.columns:
            for _, r in old.iterrows():
                t = clean_ticker(r.get("ticker", ""))
                if t and good_name(r.get("name", ""), t):
                    existing[t] = r.to_dict()

    tickers = load_tickers([Path(args.report), Path(args.tickers), Path(args.premarket)])
    fmap = finviz_map(Path(args.finviz_members)) if args.finviz_members else {}

    rows = []
    yf_count = 0
    unresolved = []
    for i, t in enumerate(tickers, 1):
        row = None
        if t in existing:
            row = existing[t]
            row["source"] = row.get("source", "existing") or "existing"
        elif t in fmap:
            row = fmap[t]
        elif (b := row_from_builtin(t)) is not None:
            row = b
        elif yf_count < args.max_yf:
            row = yf_lookup(t)
            yf_count += 1
            time.sleep(0.04)
        if row is None:
            b = row_from_builtin(t)
            row = b if b is not None else {"ticker": t, "name": "종목명 조회 필요", "sector": "분류 미확인", "industry": "업종 미확인", "source": "fallback"}
            unresolved.append(t)
        rows.append(normalize_row(row, t))
        if i == 1 or i % 50 == 0 or i == len(tickers):
            print(f"... us metadata {i}/{len(tickers)} {t} -> {rows[-1]['name']}", flush=True)

    df = pd.DataFrame(rows, columns=OUT_COLUMNS).drop_duplicates("ticker", keep="last")
    df.to_csv(out_path, index=False)
    bad = df["name"].astype(str).eq("종목명 조회 필요").sum()
    print(f"Saved: {out_path} ({len(df)} rows)")
    print(f"yfinance lookups: {yf_count}")
    print(f"unresolved names: {bad}")
    if bad:
        print("unresolved sample:", ", ".join(df.loc[df["name"].eq("종목명 조회 필요"), "ticker"].head(30).tolist()))


if __name__ == "__main__":
    main()
'''

NEW_EXTERNAL_LINKS = r'''def render_external_links(row: pd.Series):
    from urllib.parse import quote_plus

    st.markdown("#### Research Links")
    ticker = str(row.get("ticker", "") or "").strip().upper()
    name = str(row.get("name", "") or ticker).strip()
    asset = str(row.get("asset_type", "") or "").upper()
    current_market = globals().get("market", "Korea")

    if current_market == "US":
        q = quote_plus(f"{ticker} {name}")
        pdf_q = quote_plus(f"{ticker} {name} investor presentation annual report pdf")
        sec_q = quote_plus(ticker)
        links = [
            ("Yahoo Finance", f"https://finance.yahoo.com/quote/{ticker}"),
            ("Finviz", f"https://finviz.com/quote.ashx?t={ticker}"),
            ("TradingView", f"https://www.tradingview.com/symbols/{ticker}/"),
            ("SEC EDGAR", f"https://www.sec.gov/edgar/search/#/q={sec_q}"),
            ("Nasdaq", f"https://www.nasdaq.com/market-activity/stocks/{ticker.lower()}"),
            ("Google PDF Report", f"https://www.google.com/search?q={pdf_q}"),
        ]
        if asset == "ETF":
            links += [
                ("ETF.com", f"https://www.etf.com/{ticker}"),
                ("ETF Holdings Search", f"https://www.google.com/search?q={quote_plus(ticker + ' ETF holdings')}")
            ]
            if ticker in {"XLB","XLC","XLE","XLF","XLI","XLK","XLP","XLRE","XLU","XLV","XLY"}:
                links.append(("Sector SPDR", f"https://www.sectorspdrs.com/mainfund/{ticker}"))
        else:
            links += [
                ("StockAnalysis", f"https://stockanalysis.com/stocks/{ticker.lower()}/"),
                ("StockRow Snapshot", f"https://stockrow.com/{ticker}"),
                ("TipRanks", f"https://www.tipranks.com/stocks/{ticker.lower()}"),
            ]
        cols = st.columns(4)
        for i, (label, url) in enumerate(links):
            cols[i % 4].link_button(label, url, use_container_width=True)
        return

    links = [
        ("네이버 종목", naver_url(row.get("ticker", ""))),
        ("네이버 리서치", naver_research_url(row.get("ticker", ""))),
        ("FnGuide Snapshot", fnguide_url(row.get("ticker", ""), "main")),
        ("FnGuide Consensus", fnguide_url(row.get("ticker", ""), "consensus")),
        ("DART 공시", dart_url(row)),
        ("한경컨센서스", hankyung_url(row.get("ticker", ""))),
        ("Google PDF 리포트", google_pdf_url(row)),
        ("KRX KIND 검색", krx_kind_url(row)),
    ]
    cols = st.columns(4)
    for i, (label, url) in enumerate(links):
        if url:
            cols[i % 4].link_button(label, url, use_container_width=True)
'''

NEW_RENDER_DETAIL = r'''def _fmt_large_number(v):
    x = to_float(v, None)
    if x is None:
        return "-"
    try:
        if abs(x) >= 1_000_000_000_000:
            return f"${x/1_000_000_000_000:.2f}T"
        if abs(x) >= 1_000_000_000:
            return f"${x/1_000_000_000:.2f}B"
        if abs(x) >= 1_000_000:
            return f"${x/1_000_000:.2f}M"
        return f"${x:,.0f}"
    except Exception:
        return "-"


def _fmt_ratio(v):
    x = to_float(v, None)
    return "-" if x is None else f"{x:.2f}"


def _fmt_growth(v):
    x = to_float(v, None)
    if x is None:
        return "-"
    # yfinance usually returns 0.1234 for 12.34%.
    if abs(x) <= 3:
        x *= 100
    return f"{x:+.1f}%"


def render_company_snapshot(row: pd.Series):
    current_market = globals().get("market", "Korea")
    if current_market != "US":
        return
    st.markdown("#### Company Snapshot")
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Market Cap", _fmt_large_number(row.get("market_cap")))
    c2.metric("Beta", _fmt_ratio(row.get("beta")))
    c3.metric("Trailing P/E", _fmt_ratio(row.get("trailing_pe")))
    c4.metric("Forward P/E", _fmt_ratio(row.get("forward_pe")))
    c5, c6, c7, c8 = st.columns(4)
    c5.metric("Revenue Growth", _fmt_growth(row.get("revenue_growth")))
    c6.metric("Earnings Growth", _fmt_growth(row.get("earnings_growth")))
    c7.metric("Target Mean", fmt_num(row.get("target_mean_price")))
    c8.metric("Recommendation", str(row.get("recommendation", "") or "-"))
    website = str(row.get("website", "") or "").strip()
    exchange = str(row.get("exchange", "") or "").strip()
    quote_type = str(row.get("quote_type", "") or "").strip()
    meta = " · ".join([x for x in [exchange, quote_type, website] if x])
    if meta:
        st.caption(meta)


def render_detail(row: pd.Series):
    ticker = str(row.get("ticker", ""))
    name = str(row.get("name", "") or "").strip()
    current_market = globals().get("market", "Korea")
    if current_market == "Korea" and (name == ticker or not name):
        live_name = fetch_naver_name(ticker)
        if live_name:
            name = live_name
    st.markdown(f"### {ticker} · {name}")
    st.markdown(state_html(row["entry_state"]), unsafe_allow_html=True)
    st.caption(f"{row.get('sector','')} · {row.get('industry','')} · {row.get('asset_type','')} · {row.get('bucket','')}")
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Price", fmt_num(row.get("price")))
    c2.metric("Break Level", fmt_num(row.get("daily_break_level")))
    c3.metric("Distance", fmt_pct(row.get("dist_pct")))
    c4.metric("Room", fmt_pct(row.get("room_to_weekly_r1_pct")))
    c5, c6, c7, c8 = st.columns(4)
    c5.metric("RSI14", fmt_num(row.get("rsi14")))
    c6.metric("10DMA", fmt_pct(row.get("px_vs_sma10")))
    c7.metric("40DMA", fmt_pct(row.get("px_vs_sma40")))
    c8.metric("200DMA", fmt_pct(row.get("px_vs_sma200")))
    st.write("**Why:**", row.get("why", ""))
    st.write("**Manage:**", row.get("manage", ""))
    if str(row.get("warnings", "")).strip():
        st.warning(row.get("warnings"))
    if str(row.get("notes", "")).strip():
        st.info(f"Overlay notes: {row.get('notes')}")
    render_company_snapshot(row)
'''


def replace_function(s: str, fn_name: str, new_code: str, next_fn_names: list[str]) -> str:
    start = s.find(f"def {fn_name}(")
    if start == -1:
        raise RuntimeError(f"Could not find function {fn_name}")
    candidates = []
    for n in next_fn_names:
        pos = s.find(f"\ndef {n}", start + 1)
        if pos != -1:
            candidates.append(pos)
    if not candidates:
        raise RuntimeError(f"Could not find boundary after {fn_name}")
    end = min(candidates)
    return s[:start] + new_code.rstrip() + "\n\n" + s[end+1:]


def patch_app():
    if not APP.exists():
        raise SystemExit(f"Missing {APP}. Run from repo root.")
    s = APP.read_text(encoding="utf-8")
    APP.with_suffix(".py.bak_us_enrichment").write_text(s, encoding="utf-8")
    s = replace_function(s, "render_external_links", NEW_EXTERNAL_LINKS, ["render_chart"])
    s = replace_function(s, "render_detail", NEW_RENDER_DETAIL, ["default_path"])
    APP.write_text(s, encoding="utf-8")
    print(f"patched {APP}")


def patch_workflow():
    if not US_WORKFLOW.exists():
        print(f"WARNING: missing {US_WORKFLOW}; skipping workflow patch")
        return
    s = US_WORKFLOW.read_text(encoding="utf-8")
    # Finviz group scan should be non-fatal.
    s = s.replace(
        "          python finviz_top_groups_auto_mixed_v2.py\n",
        """          if ! python finviz_top_groups_auto_mixed_v2.py --top-industries 10 --top-sectors 3 --max-per-group 30; then\n            echo \"Finviz group scan failed or selected no groups. Continuing with existing/manual ticker universe.\"\n            if [ ! -f finviz_top_groups_auto_mixed.txt ]; then touch finviz_top_groups_auto_mixed.txt; fi\n          fi\n"""
    )
    # Larger lookup budget for US names; do not force-refresh every run because existing good rows are reused.
    s = re.sub(
        r'(python bulkowski_breakout_radar/build_ticker_master_us\.py \\\n(?:\s+--.*\n)+?)',
        lambda m: m.group(1) if "--max-yf" in m.group(1) else m.group(1).rstrip() + " \\\n            --max-yf 2000\n",
        s,
        count=1,
    )
    US_WORKFLOW.write_text(s, encoding="utf-8")
    print(f"patched {US_WORKFLOW}")


def main():
    US_BUILDER.write_text(US_BUILDER_CODE, encoding="utf-8")
    print(f"wrote {US_BUILDER}")
    patch_app()
    patch_workflow()
    print("Done. Run: python -m py_compile bulkowski_breakout_radar/streamlit_app.py bulkowski_breakout_radar/build_ticker_master_us.py")


if __name__ == "__main__":
    main()
