#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Bulkowski Breakout Radar
Local Streamlit dashboard for Korea/US prior-high breakout candidates.

Run:
  pip install -r requirements.txt
  streamlit run streamlit_app.py
"""

from __future__ import annotations

from pathlib import Path
import re
import subprocess
import sys
from typing import Optional
from urllib.parse import quote_plus

import numpy as np
import pandas as pd
import streamlit as st

try:
    import yfinance as yf
except Exception:  # optional runtime dependency
    yf = None

try:
    import plotly.graph_objects as go
    from plotly.subplots import make_subplots
except Exception:  # optional runtime dependency
    go = None
    make_subplots = None

try:
    import requests
    from bs4 import BeautifulSoup
except Exception:  # optional runtime dependency
    requests = None
    BeautifulSoup = None


APP_TITLE = "Bulkowski Breakout Radar"
APP_SUBTITLE = "Prior-High Breakout Candidates from Chart Pattern Logic"
APP_DIR = Path(__file__).resolve().parent
DATA_DIRS = {"Korea": APP_DIR / "data" / "kr", "US": APP_DIR / "data" / "us"}
KR_DIR = DATA_DIRS["Korea"]
US_DIR = DATA_DIRS["US"]

TRUE_SET = {"true", "1", "y", "yes", "t", "on"}
ENTRY_ORDER = {
    "PREBREAK OK": 0,
    "ENTRY OK": 1,
    "SMALL SIZE": 2,
    "HOLD ONLY": 3,
    "AVOID NEW": 4,
    "WATCH": 5,
    "REJECT": 6,
}
LABEL_ORDER = {"BUY NOW": 0, "NEAR BREAKOUT": 1, "WATCH": 2, "WATCH / CHASE": 3, "REJECT": 4}

ETF_NAME_KEYS = ["KODEX", "TIGER", "SOL", "ACE", "KBSTAR", "ARIRANG", "HANARO", "KOSEF"]
ETF_TICKERS = {
    "069500.KS", "102110.KS", "122630.KS", "252670.KS", "229200.KQ", "233740.KS", "251340.KS",
    "091160.KS", "091230.KS", "117680.KS", "139220.KS", "139230.KS", "266370.KS", "266420.KS",
    "305540.KS", "305720.KS", "360750.KS", "364970.KS", "364980.KS", "381180.KS", "390390.KS",
    "395160.KS", "395270.KS", "396500.KS", "409820.KS", "454320.KS", "469150.KS", "471460.KS",
    "471760.KS", "471990.KS", "475310.KS", "488080.KS", "494310.KS",
}

BUILTIN_META = {
    "000145.KS": ("하이트진로홀딩스우", "Consumer Staples", "Beverages / Holding preferred"),
    "230360.KQ": ("에코마케팅", "Communication Services", "Advertising / Marketing"),
    "051915.KS": ("LG화학우", "Materials", "Chemicals / Battery materials preferred"),
    "002840.KS": ("미원상사", "Materials", "Specialty chemicals"),
    "000157.KS": ("두산2우B", "Industrials", "Holding company preferred"),
    "033780.KS": ("KT&G", "Consumer Staples", "Tobacco"),
    "009150.KS": ("삼성전기", "Information Technology", "Electronic components / MLCC"),
    "298040.KS": ("효성중공업", "Industrials", "Power equipment / Heavy industry"),
    "010120.KS": ("LS ELECTRIC", "Industrials", "Electrical equipment / Grid"),
    "062040.KS": ("산일전기", "Industrials", "Transformer / Power equipment"),
    "103590.KS": ("일진전기", "Industrials", "Electric wire / Power equipment"),
    "227840.KS": ("현대코퍼레이션홀딩스", "Industrials", "Trading / Holdings"),
    "051910.KS": ("LG화학", "Materials", "Chemicals / Battery materials"),
    "005930.KS": ("삼성전자", "Information Technology", "Semiconductors / Electronics"),
    "036890.KQ": ("진성티이씨", "Industrials", "Construction machinery parts"),
    "092190.KQ": ("서울바이오시스", "Information Technology", "LED / Optoelectronics"),
    "006260.KS": ("LS", "Industrials", "Holding / Electrical infrastructure"),
    "000150.KS": ("두산", "Industrials", "Holding company / Energy equipment"),
    "409820.KS": ("KODEX 미국나스닥100레버리지(합성 H)", "ETF", "US Nasdaq 100 leveraged ETF"),
    "102110.KS": ("TIGER 200", "ETF", "KOSPI 200 ETF"),
    "122630.KS": ("KODEX 레버리지", "ETF", "KOSPI 200 leveraged ETF"),
    "251340.KS": ("KODEX 코스닥150선물인버스", "ETF", "KOSDAQ150 inverse ETF"),
    "381180.KS": ("TIGER 미국필라델피아반도체나스닥", "ETF", "US semiconductor ETF"),
    "390390.KS": ("KODEX 미국반도체MV", "ETF", "US semiconductor ETF"),
    "305720.KS": ("KODEX 2차전지산업", "ETF", "Korea battery industry ETF"),
    "395160.KS": ("KODEX AI반도체", "ETF", "Korea AI semiconductor ETF"),
    "395270.KS": ("HANARO K-반도체", "ETF", "Korea semiconductor ETF"),
    "396500.KS": ("TIGER 반도체TOP10", "ETF", "Korea semiconductor TOP10 ETF"),
    "469150.KS": ("ACE AI반도체TOP3+", "ETF", "Korea AI semiconductor ETF"),
    "471460.KS": ("KODEX K-방산", "ETF", "Korea defense industry ETF"),
    "471990.KS": ("KODEX AI반도체핵심장비", "ETF", "Korea semiconductor equipment ETF"),
    "488080.KS": ("TIGER 반도체TOP10레버리지", "ETF", "Korea semiconductor leveraged ETF"),
    "494310.KS": ("KODEX 반도체레버리지", "ETF", "Korea semiconductor leveraged ETF"),
    "097950.KS": ("CJ제일제당", "Consumer Staples", "Food processing"),
    "042700.KS": ("한미반도체", "Information Technology", "Semiconductor equipment"),
    "267770.KS": ("배럴", "Consumer Discretionary", "Apparel / Leisure"),
    "004370.KS": ("농심", "Consumer Staples", "Food / Noodles"),
    "000155.KS": ("두산우", "Industrials", "Holding company preferred"),
    "000660.KS": ("SK하이닉스", "Information Technology", "Semiconductors / Memory"),
    "003670.KS": ("포스코퓨처엠", "Materials", "Battery materials / Cathode"),
    "005490.KS": ("POSCO홀딩스", "Materials", "Steel / Holding company"),
    "005935.KS": ("삼성전자우", "Information Technology", "Semiconductors / Electronics preferred"),
    "009155.KS": ("삼성전기우", "Information Technology", "Electronic components preferred"),
    "011070.KS": ("LG이노텍", "Information Technology", "Electronic components / Camera module"),
    "012450.KS": ("한화에어로스페이스", "Industrials", "Defense / Aerospace"),
    "034020.KS": ("두산에너빌리티", "Industrials", "Power equipment / Nuclear"),
    "042660.KS": ("한화오션", "Industrials", "Shipbuilding / Defense"),
    "329180.KS": ("HD현대중공업", "Industrials", "Shipbuilding"),
    "035420.KS": ("NAVER", "Communication Services", "Internet platform"),
    "035720.KS": ("카카오", "Communication Services", "Internet platform"),
    "000270.KS": ("기아", "Consumer Discretionary", "Automobiles"),
    "005380.KS": ("현대차", "Consumer Discretionary", "Automobiles"),
    "005387.KS": ("현대차2우B", "Consumer Discretionary", "Automobiles preferred"),
    "006400.KS": ("삼성SDI", "Industrials", "Battery cells"),
    "207940.KS": ("삼성바이오로직스", "Health Care", "Biologics / CMO"),
    "068270.KS": ("셀트리온", "Health Care", "Biopharma"),
    "105560.KS": ("KB금융", "Financials", "Banking / Financial holding"),
    "055550.KS": ("신한지주", "Financials", "Banking / Financial holding"),
    "086790.KS": ("하나금융지주", "Financials", "Banking / Financial holding"),
    "024110.KS": ("기업은행", "Financials", "Banking"),
    "015760.KS": ("한국전력", "Utilities", "Electric utility"),
    "047810.KS": ("한국항공우주", "Industrials", "Aerospace / Defense"),
    "064350.KS": ("현대로템", "Industrials", "Defense / Rail"),
    "028260.KS": ("삼성물산", "Industrials", "Trading / Construction / Holding"),
    "028050.KS": ("삼성E&A", "Industrials", "Plant engineering"),
    "066570.KS": ("LG전자", "Consumer Discretionary", "Consumer electronics"),
    "034730.KS": ("SK", "Industrials", "Holding company"),
    "03473K.KS": ("SK우", "Industrials", "Holding company preferred"),
    "010130.KS": ("고려아연", "Materials", "Non-ferrous metals"),
    "009830.KS": ("한화솔루션", "Materials", "Chemicals / Solar"),
    "096770.KS": ("SK이노베이션", "Energy", "Refining / Battery"),
    "010950.KS": ("S-Oil", "Energy", "Refining"),
    "018260.KS": ("삼성에스디에스", "Information Technology", "IT services"),
    "030200.KS": ("KT", "Communication Services", "Telecom"),
    "017670.KS": ("SK텔레콤", "Communication Services", "Telecom"),
    "032640.KS": ("LG유플러스", "Communication Services", "Telecom"),
    "047050.KS": ("포스코인터내셔널", "Industrials", "Trading / Energy"),
    "042670.KS": ("HD현대인프라코어", "Industrials", "Construction machinery"),
    "010140.KS": ("삼성중공업", "Industrials", "Shipbuilding"),
    "009540.KS": ("HD한국조선해양", "Industrials", "Shipbuilding holding"),
    "000080.KS": ("하이트진로", "Consumer Staples", "Beverages"),
    "000087.KS": ("하이트진로2우B", "Consumer Staples", "Beverages preferred"),
}

def read_last_update_for_market(market_key: str) -> str:
    from pathlib import Path
    from datetime import datetime
    from zoneinfo import ZoneInfo

    app_dir = Path(__file__).resolve().parent
    data_dir = app_dir / "data" / market_key

    kst_path = data_dir / "last_updated_kst.txt"
    if kst_path.exists():
        txt = kst_path.read_text(encoding="utf-8", errors="ignore").strip()
        if txt:
            return txt

    report_path = data_dir / "report_v2.csv"
    if report_path.exists():
        ts = datetime.fromtimestamp(report_path.stat().st_mtime, ZoneInfo("Asia/Seoul"))
        return ts.strftime("%Y-%m-%d %H:%M:%S KST") + " · file mtime fallback"

    return "not available"


def render_last_update_panel(market_key: str, market_label: str):
    updated = read_last_update_for_market(market_key)
    st.info(f"🕒 Last updated · {market_label}: **{updated}**")




st.set_page_config(
    page_title=APP_TITLE,
    page_icon="📈",
    layout="wide",
    initial_sidebar_state="expanded",
)

st.markdown(
    """
    <style>
    .main .block-container { padding-top: 1.25rem; }
    div[data-testid="stMetricValue"] { font-size: 1.75rem; }
    .state-pill {
        display: inline-block;
        padding: 0.18rem 0.55rem;
        border-radius: 999px;
        font-size: 0.78rem;
        font-weight: 700;
        border: 1px solid rgba(0,0,0,.08);
        white-space: nowrap;
    }
    .entry { background: #dcfce7; color: #166534; }
    .prebreak { background: #dbeafe; color: #1d4ed8; }
    .small { background: #fef3c7; color: #92400e; }
    .avoid { background: #fee2e2; color: #991b1b; }
    .hold { background: #f3e8ff; color: #6b21a8; }
    .watch { background: #f1f5f9; color: #334155; }
    .reject { background: #e5e7eb; color: #374151; }
    .hero {
        padding: 1.2rem 1.3rem;
        border: 1px solid #e5e7eb;
        border-radius: 1.25rem;
        background: linear-gradient(135deg, #0f172a 0%, #1e293b 55%, #334155 100%);
        color: white;
        margin-bottom: 1rem;
    }
    .hero h1 { margin: 0; font-size: 2rem; }
    .hero p { margin: .35rem 0 0 0; opacity: .85; }
    .mini-note { color: #94a3b8; font-size: .82rem; }
    </style>
    """,
    unsafe_allow_html=True,
)


def read_csv_flexible(path_or_file, comment="#") -> pd.DataFrame:
    if path_or_file is None:
        return pd.DataFrame()
    try:
        return pd.read_csv(path_or_file, comment=comment)
    except Exception:
        try:
            return pd.read_csv(path_or_file, comment=comment, engine="python")
        except Exception:
            return pd.DataFrame()


def load_manual_tickers_from_premarket(path_or_file) -> set[str]:
    """premarket_auto_korea.csv has repeated ticker,premarket headers grouped by comments."""
    if path_or_file is None:
        return set()
    if hasattr(path_or_file, "getvalue"):
        text = path_or_file.getvalue().decode("utf-8", errors="ignore")
    else:
        p = Path(path_or_file)
        if not p.exists():
            return set()
        text = p.read_text(encoding="utf-8", errors="ignore")

    out = set()
    for line in text.splitlines():
        s = line.strip()
        if not s or s.startswith("#") or s.lower().startswith("ticker,"):
            continue
        t = s.split(",", 1)[0].strip().upper()
        if t and t != "TICKER":
            out.add(t)
    return out


def is_true(v) -> bool:
    return str(v).strip().lower() in TRUE_SET


def to_float(v, default=None):
    try:
        if pd.isna(v):
            return default
        return float(v)
    except Exception:
        return default


def clamp_num(v, lo=0.0, hi=2.0):
    x = to_float(v, 0.0)
    if x is None:
        x = 0.0
    return max(lo, min(hi, x))


def fmt_pct(v):
    if v is None or (isinstance(v, float) and np.isnan(v)):
        return ""
    return f"{v:+.2f}%"


def fmt_num(v):
    if v is None or (isinstance(v, float) and np.isnan(v)):
        return ""
    if abs(v) >= 1000:
        return f"{v:,.0f}"
    return f"{v:,.2f}"


def load_overlay(path_or_file) -> dict[str, dict]:
    df = read_csv_flexible(path_or_file)
    if df.empty or "ticker" not in df.columns:
        return {}
    df["ticker"] = df["ticker"].astype(str).str.strip().str.upper()
    return {r["ticker"]: r.to_dict() for _, r in df.iterrows()}


def overlay_info(overlay_by_ticker: dict[str, dict], ticker: str) -> dict:
    row = overlay_by_ticker.get(ticker)
    if not row:
        return {
            "theme_strength": 0.0, "cycle_strength": 0.0, "news_strength": 0.0,
            "flow_strength": 0.0, "company_quality": 0.0, "prebreak_ok": False,
            "must_wait_breakout": False, "avoid_new": False, "notes": "",
            "overlay_score": 0.0, "thesis_state": "NO OVERLAY",
        }
    theme = clamp_num(row.get("theme_strength"))
    cycle = clamp_num(row.get("cycle_strength"))
    news = clamp_num(row.get("news_strength"))
    flow = clamp_num(row.get("flow_strength"))
    quality = clamp_num(row.get("company_quality"))
    score = theme + cycle + news + flow + quality
    avoid = is_true(row.get("avoid_new"))
    if avoid:
        thesis = "AVOID"
    elif score >= 8:
        thesis = "INSTITUTIONAL STORY"
    elif score >= 6:
        thesis = "STRONG STORY"
    elif score >= 3:
        thesis = "OK STORY"
    else:
        thesis = "NO OVERLAY"
    return {
        "theme_strength": theme, "cycle_strength": cycle, "news_strength": news,
        "flow_strength": flow, "company_quality": quality,
        "prebreak_ok": is_true(row.get("prebreak_ok")),
        "must_wait_breakout": is_true(row.get("must_wait_breakout")),
        "avoid_new": avoid,
        "notes": str(row.get("notes", "") or "").strip(),
        "overlay_score": score,
        "thesis_state": thesis,
    }


def infer_asset_type(ticker: str, name: str = "") -> str:
    n = str(name or "").upper()
    if ticker in ETF_TICKERS or any(k in n for k in ETF_NAME_KEYS):
        return "ETF"
    if ticker.endswith("K.KS") or ticker.endswith("5.KS") or "우" in str(name or ""):
        return "PREFERRED"
    return "STOCK"


def load_metadata(path_or_file=None) -> pd.DataFrame:
    frames = []
    seed = DATA_DIR / ("ticker_master_korea_seed.csv" if market == "Korea" else "ticker_master_us_seed.csv")
    if seed.exists():
        frames.append(pd.read_csv(seed))
    full_master = DATA_DIR / meta_default
    if full_master.exists():
        frames.append(pd.read_csv(full_master))
    if path_or_file is not None:
        df = read_csv_flexible(path_or_file)
        if not df.empty and "ticker" in df.columns:
            frames.append(df)
    if not frames:
        rows = []
        for t, (n, s, i) in BUILTIN_META.items():
            rows.append({"ticker": t, "name": n, "sector": s, "industry": i})
        return pd.DataFrame(rows)
    meta = pd.concat(frames, ignore_index=True, sort=False)
    meta["ticker"] = meta["ticker"].astype(str).str.strip().str.upper()
    return meta.drop_duplicates("ticker", keep="last")




def extract_kr_code(ticker: str) -> str:
    m = re.match(r"^(\d{6})", str(ticker or "").strip().upper())
    return m.group(1) if m else ""


@st.cache_data(ttl=60 * 60 * 24, show_spinner=False)
def fetch_naver_name(ticker: str) -> str:
    """Fetch only the selected ticker's Korean display name from Naver Finance.

    This is intentionally not used for all 498 rows by default, because mass
    fetching can make the dashboard feel frozen. For full names/industries,
    use data/kr/ticker_master_korea.csv.
    """
    code = extract_kr_code(ticker)
    if not code or requests is None or BeautifulSoup is None:
        return ""
    url = f"https://finance.naver.com/item/main.naver?code={code}"
    try:
        r = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=8)
        r.raise_for_status()
        r.encoding = r.apparent_encoding or "euc-kr"
        soup = BeautifulSoup(r.text, "html.parser")
        h2 = soup.select_one("div.wrap_company h2")
        if h2:
            return h2.get_text(" ", strip=True)
        og = soup.find("meta", attrs={"property": "og:title"})
        if og and og.get("content"):
            return str(og.get("content")).split(":")[0].strip()
    except Exception:
        return ""
    return ""




def fill_visible_missing_names(df: pd.DataFrame, max_rows: int = 80) -> pd.DataFrame:
    """Best-effort live name fill for the table currently being shown."""
    if df.empty or "name" not in df.columns:
        return df
    out = df.copy()
    mask = (
        out["name"].astype(str).str.strip().isin(["", "종목명 조회 필요"])
        | out["name"].astype(str).str.contains("조회 필요", na=False)
        | out["name"].astype(str).str.upper().eq(out["ticker"].astype(str).str.upper())
    )
    idxs = out.index[mask].tolist()[:max_rows]
    for idx in idxs:
        t = str(out.at[idx, "ticker"])
        nm = fetch_naver_name(t)
        if nm:
            out.at[idx, "name"] = nm
            if str(out.at[idx, "sector"]) in ["", "Unmapped", "분류 미확인"]:
                out.at[idx, "sector"] = "분류 미확인"
            if str(out.at[idx, "industry"]) in ["", "Unmapped", "업종 미확인"]:
                out.at[idx, "industry"] = "업종 미확인"
    return out


@st.cache_data(ttl=60 * 10, show_spinner=False)
def fetch_chart_data(ticker: str, period: str = "6mo", interval: str = "1d") -> pd.DataFrame:
    if yf is None:
        return pd.DataFrame()
    try:
        raw = yf.download(ticker, period=period, interval=interval, auto_adjust=False, progress=False, threads=False)
    except Exception:
        return pd.DataFrame()
    if raw is None or raw.empty:
        return pd.DataFrame()
    df = raw.copy()
    if isinstance(df.columns, pd.MultiIndex):
        # yfinance sometimes returns MultiIndex. Use the first matching layer.
        if ticker in set(df.columns.get_level_values(-1)):
            df = df.xs(ticker, level=-1, axis=1).copy()
        else:
            df.columns = [str(c[0]) for c in df.columns]
    df.columns = [str(c).strip().title() for c in df.columns]
    needed = {"Open", "High", "Low", "Close"}
    if not needed.issubset(set(df.columns)):
        return pd.DataFrame()
    df = df.dropna(subset=["Open", "High", "Low", "Close"]).copy()
    for n in [10, 40, 50, 200]:
        df[f"SMA{n}"] = pd.to_numeric(df["Close"], errors="coerce").rolling(n).mean()
    return df


def naver_url(ticker: str) -> str:
    code = extract_kr_code(ticker)
    return f"https://finance.naver.com/item/main.naver?code={code}" if code else ""


def naver_research_url(ticker: str) -> str:
    code = extract_kr_code(ticker)
    return f"https://finance.naver.com/research/company_list.naver?searchType=itemCode&itemCode={code}" if code else ""


def fnguide_url(ticker: str, page: str = "main") -> str:
    code = extract_kr_code(ticker)
    if not code:
        return ""
    if page == "consensus":
        return f"https://comp.fnguide.com/SVO2/ASP/SVD_Consensus.asp?pGB=1&gicode=A{code}"
    return f"https://comp.fnguide.com/SVO2/ASP/SVD_Main.asp?pGB=1&gicode=A{code}&cID=&MenuYn=Y&ReportGB=&NewMenuID=101&stkGb=701"


def dart_url(row: pd.Series) -> str:
    code = extract_kr_code(row.get("ticker", ""))
    nm = str(row.get("name", "") or "").strip()
    keyword = quote_plus(nm if nm and nm != row.get("ticker") else code)
    return f"https://dart.fss.or.kr/dsab007/main.do?option=corpName&keyword={keyword}"


def hankyung_url(ticker: str) -> str:
    code = extract_kr_code(ticker)
    return f"https://markets.hankyung.com/stock/{code}" if code else ""


def google_pdf_url(row: pd.Series) -> str:
    ticker = str(row.get("ticker", ""))
    name = str(row.get("name", "") or "")
    query = quote_plus(f"{ticker} {name} 증권사 리포트 PDF")
    return f"https://www.google.com/search?q={query}"


def krx_kind_url(row: pd.Series) -> str:
    code = extract_kr_code(row.get("ticker", ""))
    name = str(row.get("name", "") or "")
    query = quote_plus(f"KRX KIND {code} {name} 공시")
    return f"https://www.google.com/search?q={query}"



def render_external_links(row):
    import urllib.parse

    ticker = str(row.get("ticker", "")).strip().upper()
    name = str(row.get("name", "")).strip()
    asset_type = str(row.get("asset_type", "")).strip().upper()
    sector = str(row.get("sector", "")).strip()
    industry = str(row.get("industry", "")).strip()

    if not ticker:
        return

    st.subheader("Research Links")

    is_kr = ticker.endswith(".KS") or ticker.endswith(".KQ")
    is_etf = asset_type == "ETF" or sector.upper() == "ETF" or "ETF" in industry.upper()

    if is_kr:
        code = ticker.split(".")[0]
        q_name = urllib.parse.quote_plus(name or code)

        links = [
            ("네이버 종목", f"https://finance.naver.com/item/main.naver?code={code}"),
            ("네이버 리서치", f"https://finance.naver.com/research/company_list.naver?searchType=itemCode&itemCode={code}"),
            ("FnGuide Snapshot", f"https://comp.fnguide.com/SVO2/ASP/SVD_Main.asp?pGB=1&gicode=A{code}"),
            ("FnGuide Consensus", f"https://comp.fnguide.com/SVO2/ASP/SVD_Consensus.asp?pGB=1&gicode=A{code}"),
            ("DART 공시", f"https://dart.fss.or.kr/dsab007/main.do?textCrpNm={q_name}"),
            ("한경컨센서스", f"https://consensus.hankyung.com/apps.analysis/analysis.list?skinType=business&sdate=&edate=&search_value={q_name}"),
            ("Google PDF 리포트", f"https://www.google.com/search?q={q_name}+{code}+%EB%A6%AC%ED%8F%AC%ED%8A%B8+PDF"),
            ("KRX KIND 검색", f"https://kind.krx.co.kr/disclosure/searchtotalinfo.do?searchText={q_name}"),
        ]
    else:
        t = ticker.replace(".", "-")
        q = urllib.parse.quote_plus(ticker)
        q_name = urllib.parse.quote_plus(name or ticker)

        links = [
            ("Yahoo Finance", f"https://finance.yahoo.com/quote/{ticker}"),
            ("Finviz", f"https://finviz.com/quote.ashx?t={ticker}"),
            ("TradingView", f"https://www.tradingview.com/symbols/{ticker}/"),
            ("SEC EDGAR", f"https://www.sec.gov/edgar/search/#/q={q}"),
            ("Nasdaq", f"https://www.nasdaq.com/market-activity/stocks/{ticker.lower()}"),
            ("Google PDF Report", f"https://www.google.com/search?q={q_name}+{q}+investor+presentation+annual+report+pdf"),
        ]

        if is_etf:
            links += [
                ("ETF.com", f"https://www.etf.com/{ticker}"),
                ("ETF Holdings Search", f"https://www.google.com/search?q={q}+ETF+holdings"),
            ]
            if ticker in {"XLB", "XLC", "XLE", "XLF", "XLI", "XLK", "XLP", "XLRE", "XLU", "XLV", "XLY"}:
                links.append(("Sector SPDR", f"https://www.sectorspdrs.com/mainfund/{ticker}"))
        else:
            links += [
                ("StockAnalysis", f"https://stockanalysis.com/stocks/{ticker.lower()}/"),
                ("StockRow Snapshot", f"https://stockrow.com/{ticker}/snapshot"),
                ("TipRanks", f"https://www.tipranks.com/stocks/{ticker.lower()}"),
            ]

    cols = st.columns(3)
    for i, (label, url) in enumerate(links):
        with cols[i % 3]:
            st.link_button(label, url, use_container_width=True)

def render_chart(row: pd.Series, period: str, interval: str):
    st.markdown("### Chart")
    ticker = str(row.get("ticker", ""))
    if yf is None or go is None or make_subplots is None:
        st.warning("차트 기능을 쓰려면 requirements.txt 업데이트 후 `pip install -r requirements.txt`를 다시 실행해야 한다.")
        return
    df = fetch_chart_data(ticker, period=period, interval=interval)
    if df.empty:
        st.warning(f"{ticker} 차트 데이터를 불러오지 못했다. 네트워크/yfinance 응답 문제일 수 있다.")
        return
    fig = make_subplots(rows=2, cols=1, shared_xaxes=True, vertical_spacing=0.03, row_heights=[0.72, 0.28])
    fig.add_trace(
        go.Candlestick(
            x=df.index, open=df["Open"], high=df["High"], low=df["Low"], close=df["Close"], name="OHLC"
        ),
        row=1, col=1,
    )
    for n in [10, 40, 50, 200]:
        col = f"SMA{n}"
        if col in df.columns and df[col].notna().any():
            fig.add_trace(go.Scatter(x=df.index, y=df[col], mode="lines", name=col, line=dict(width=1)), row=1, col=1)
    brk = to_float(row.get("daily_break_level"), None)
    wr1 = to_float(row.get("weekly_r1"), None)
    if brk is not None:
        fig.add_hline(y=brk, line_dash="dash", annotation_text="Break", row=1, col=1)
    if wr1 is not None:
        fig.add_hline(y=wr1, line_dash="dot", annotation_text="Weekly R1", row=1, col=1)
    if "Volume" in df.columns:
        fig.add_trace(go.Bar(x=df.index, y=df["Volume"], name="Volume"), row=2, col=1)
    fig.update_layout(
        height=650,
        margin=dict(l=10, r=10, t=35, b=10),
        xaxis_rangeslider_visible=False,
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="left", x=0),
    )
    fig.update_yaxes(title_text="Price", row=1, col=1)
    fig.update_yaxes(title_text="Volume", row=2, col=1)
    st.plotly_chart(fig, use_container_width=True)


def render_report_card(row: pd.Series):
    st.markdown("### Report Card")
    rows = [
        ("State", row.get("entry_state", "")),
        ("Label", row.get("label", "")),
        ("Thesis", f"{row.get('thesis_state','')} / overlay {fmt_num(row.get('overlay_score'))}"),
        ("Why", row.get("why", "")),
        ("Manage", row.get("manage", "")),
        ("Warnings", row.get("warnings", "")),
        ("Breakout", "Y" if is_true(row.get("daily_breakout")) else "N"),
        ("Retest", "Y" if is_true(row.get("daily_retest")) else "N"),
        ("Price / Break / Weekly R1", f"{fmt_num(row.get('price'))} / {fmt_num(row.get('daily_break_level'))} / {fmt_num(row.get('weekly_r1'))}"),
        ("Distance / Room", f"{fmt_pct(row.get('dist_pct'))} / {fmt_pct(row.get('room_to_weekly_r1_pct'))}"),
        ("RSI / 10DMA / 40DMA / 50DMA / 200DMA", f"{fmt_num(row.get('rsi14'))} / {fmt_pct(row.get('px_vs_sma10'))} / {fmt_pct(row.get('px_vs_sma40'))} / {fmt_pct(row.get('px_vs_sma50'))} / {fmt_pct(row.get('px_vs_sma200'))}"),
    ]
    st.dataframe(pd.DataFrame(rows, columns=["field", "value"]), use_container_width=True, hide_index=True)

def classify_row(row: pd.Series, overlay: dict, near_max_dist=2.5, chase_ext_max=1.5) -> dict:
    price = to_float(row.get("price"), 0.0)
    brk = to_float(row.get("daily_break_level"), 0.0)
    room = to_float(row.get("room_to_weekly_r1_pct"), None)
    weekly_r1 = to_float(row.get("weekly_r1"), None)
    rsi = to_float(row.get("rsi14"), None)
    px10 = to_float(row.get("px_vs_sma10"), None)
    px40 = to_float(row.get("px_vs_sma40"), None)
    px50 = to_float(row.get("px_vs_sma50"), None)
    px200 = to_float(row.get("px_vs_sma200"), None)

    breakout = is_true(row.get("daily_breakout"))
    retest = is_true(row.get("daily_retest"))
    sma50_ok = px50 is not None and px50 > 0
    sma200_ok = px200 is not None and px200 > 0
    dist = (brk / price - 1.0) * 100.0 if brk and price else None
    ext = (price / brk - 1.0) * 100.0 if brk and price else None
    score = to_float(row.get("score_total"), None)
    if score is None:
        score = to_float(row.get("score"), None)

    warnings = []
    if room is not None and room < 0:
        warnings.append(f"weekly_r1 below current price ({room:.2f}%)")
    elif room is not None and room < 2:
        warnings.append(f"weekly room small ({room:.2f}%)")
    if rsi is not None and rsi > 70:
        warnings.append(f"RSI>70 ({rsi:.2f})")
    if px40 is not None and px40 < 0:
        warnings.append(f"below 40DMA ({px40:.2f}%)")
    if px10 is not None and px10 < 0:
        warnings.append(f"below 10DMA ({px10:.2f}%)")
    if not sma50_ok:
        warnings.append("below or near SMA50")
    if not sma200_ok:
        warnings.append("below or near SMA200")

    if breakout and retest and sma50_ok and sma200_ok:
        if ext is not None and ext > chase_ext_max:
            label = "WATCH / CHASE"
            why = f"already extended ({ext:+.2f}%)"
        else:
            label = "BUY NOW"
            why = "breakout confirmed and retest present; still close to break_level"
    elif retest and dist is not None and dist <= near_max_dist and sma50_ok and sma200_ok:
        label = "NEAR BREAKOUT"
        why = "just below break_level; retest/trigger setup looks clean"
    elif retest and sma50_ok and sma200_ok:
        label = "WATCH"
        why = "retest present, but still not close enough to break"
    else:
        label = "REJECT"
        why = "structure below key moving averages" if (not sma50_ok or not sma200_ok) else "not close enough to break / trigger"

    target_passed = room is not None and room < 0
    room_small = room is not None and 0 <= room < 2
    overbought = rsi is not None and rsi >= 70
    low_score = score is not None and score <= 0
    mild_late = room is not None and -2 <= room < 0

    if label in {"BUY NOW", "NEAR BREAKOUT"}:
        if overlay["avoid_new"] or overbought or low_score:
            state = "AVOID NEW"
            if overlay["avoid_new"]:
                manage = "overlay says avoid -> do not open new position"
            elif overbought and low_score:
                manage = "too hot + weak score -> avoid new entry, wait for reset"
            elif overbought:
                manage = "too hot -> avoid new entry, wait for reset or tight base"
            else:
                manage = "weak score -> avoid new entry until quality improves"
        elif overlay["must_wait_breakout"] and not breakout:
            state = "HOLD ONLY"
            manage = "story may be good but wait for true breakout confirmation"
        elif overlay["prebreak_ok"] and overlay["overlay_score"] >= 8 and not overbought and not low_score and (
            (not target_passed) or (mild_late and retest and sma50_ok and sma200_ok and (rsi is None or rsi <= 58))
        ):
            state = "PREBREAK OK"
            manage = "strong company/theme/cycle/news/flow story -> starter allowed before break"
        elif target_passed:
            state = "HOLD ONLY"
            manage = "late / target1 passed -> prefer hold-only or wait for fresh setup"
        elif room_small:
            state = "SMALL SIZE"
            manage = "room is tight -> size smaller or require stronger volume"
        else:
            state = "ENTRY OK"
            manage = "valid setup -> confirm volume and keep break as control line"
    elif label.startswith("WATCH"):
        state = "WATCH"
        manage = "not a priority setup now"
    else:
        state = "REJECT"
        manage = "not a priority setup now"

    if state in {"PREBREAK OK", "ENTRY OK", "SMALL SIZE"} and px40 is not None:
        if px40 <= -3:
            state = {"PREBREAK OK": "ENTRY OK", "ENTRY OK": "SMALL SIZE", "SMALL SIZE": "HOLD ONLY"}[state]
            manage += " | below 40DMA -> one-step downgrade"
        elif px40 < 0 and str(row.get("sma40_slope", "")) == "DN" and overlay["overlay_score"] < 8:
            state = {"PREBREAK OK": "ENTRY OK", "ENTRY OK": "SMALL SIZE", "SMALL SIZE": "SMALL SIZE"}[state]
            manage += " | slightly below 40DMA with weak slope -> be selective"

    if state in {"PREBREAK OK", "ENTRY OK", "SMALL SIZE", "HOLD ONLY"} and px10 is not None and px10 < 0:
        manage += " | 10DMA lost -> short-term execution caution"

    return {
        "label": label, "entry_state": state, "dist_pct": dist, "ext_pct": ext,
        "why": why, "manage": manage, "warnings": "; ".join(warnings),
        "room_pct": room, "weekly_r1_calc": weekly_r1, **overlay,
    }


def process_report(report: pd.DataFrame, manual_set: set[str], overlay_by_ticker: dict, meta: pd.DataFrame, near_max_dist: float = 2.5) -> pd.DataFrame:
    if report.empty:
        return report

    df = report.copy()
    df["ticker"] = df["ticker"].astype(str).str.strip().str.upper()

    infos = []
    for _, r in df.iterrows():
        ov = overlay_info(overlay_by_ticker, r["ticker"])
        infos.append(classify_row(r, ov, near_max_dist=near_max_dist))
    info_df = pd.DataFrame(infos)
    out = pd.concat([df.reset_index(drop=True), info_df.reset_index(drop=True)], axis=1)

    meta2 = meta.copy()
    if not meta2.empty:
        meta2["ticker"] = meta2["ticker"].astype(str).str.strip().str.upper()
        out = out.merge(meta2, on="ticker", how="left", suffixes=("", "_meta"))
    else:
        out["name"] = ""
        out["sector"] = ""
        out["industry"] = ""

    # Normalize weak metadata before applying stronger built-in fallbacks.
    # A generated master can legitimately contain name == ticker when the lookup failed;
    # treat that as missing so it does not override seed/built-in Korean names.
    if "name" not in out.columns:
        out["name"] = ""
    if "sector" not in out.columns:
        out["sector"] = ""
    if "industry" not in out.columns:
        out["industry"] = ""
    out["name"] = out.get("name", "").fillna("").astype(str).str.strip()
    out["sector"] = out.get("sector", "").fillna("").astype(str).str.strip()
    out["industry"] = out.get("industry", "").fillna("").astype(str).str.strip()
    out.loc[out["name"].eq(out["ticker"]), "name"] = ""
    out.loc[out["sector"].isin(["", "nan", "None"]), "sector"] = ""
    out.loc[out["industry"].isin(["", "nan", "None"]), "industry"] = ""

    for t, (n, s, i) in BUILTIN_META.items():
        mask = out["ticker"].eq(t)
        out.loc[mask & out["name"].eq(""), "name"] = n
        out.loc[mask & out["sector"].isin(["", "Unmapped"]), "sector"] = s
        out.loc[mask & out["industry"].isin(["", "Unmapped"]), "industry"] = i

    out["metadata_missing"] = (out["name"].eq("") | out["name"].eq(out["ticker"]) | out["name"].astype(str).str.contains("조회 필요", na=False) | out["industry"].isin(["", "Unmapped", "업종 미확인"]))
    # Do not show bare numeric tickers as if they were company names.
    out["name"] = np.where(out["name"].eq(""), "종목명 조회 필요", out["name"])
    out["sector"] = out["sector"].replace({"Unmapped": "분류 미확인", "": "분류 미확인"})
    out["industry"] = out["industry"].replace({"Unmapped": "업종 미확인", "": "업종 미확인"})
    out["asset_type"] = out.apply(lambda r: r.get("asset_type") if pd.notna(r.get("asset_type", np.nan)) and str(r.get("asset_type")) else infer_asset_type(r["ticker"], r.get("name", "")), axis=1)
    out["bucket"] = np.where(out["ticker"].isin(manual_set), "MANUAL ∩ SAFE", "SAFE ONLY")
    out["avoid_reason"] = np.where(
        out["entry_state"].eq("AVOID NEW"),
        (out.get("manage", "").astype(str) + np.where(out.get("warnings", "").astype(str).str.strip().ne(""), " | " + out.get("warnings", "").astype(str), "")),
        "",
    )

    out["entry_rank"] = out["entry_state"].map(ENTRY_ORDER).fillna(99)
    out["label_rank"] = out["label"].map(LABEL_ORDER).fillna(99)
    out["grade_rank"] = out["grade"].astype(str).str.upper().map({"A": 0, "B": 1, "C": 2}).fillna(9)
    out["sort_score"] = pd.to_numeric(out.get("score"), errors="coerce").fillna(-999)
    out = out.sort_values(
        ["entry_rank", "label_rank", "bucket", "grade_rank", "sort_score", "ticker"],
        ascending=[True, True, True, True, False, True],
    ).reset_index(drop=True)
    return out


def state_html(state: str) -> str:
    klass = {
        "PREBREAK OK": "prebreak", "ENTRY OK": "entry", "SMALL SIZE": "small",
        "AVOID NEW": "avoid", "HOLD ONLY": "hold", "WATCH": "watch", "REJECT": "reject"
    }.get(state, "watch")
    return f'<span class="state-pill {klass}">{state}</span>'


def _fmt_large_number(v):
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


def _fmt_large_number(v):
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

def default_path(filename: str) -> Optional[Path]:
    p = DATA_DIR / filename
    return p if p.exists() else None


st.markdown(
    f"""
    <div class="hero">
      <h1>{APP_TITLE}</h1>
      <p>{APP_SUBTITLE}</p>
      <p>불코우스키 『차트 패턴』 기반 전고점 돌파 후보 자동 스캐너</p>
    </div>
    """,
    unsafe_allow_html=True,
)

with st.sidebar:
    st.header("Data Source")
    market = st.radio("Market", ["Korea", "US"], horizontal=True)
    DATA_DIR = DATA_DIRS[market]

    if market == "Korea":
        data_label = "data/kr"
        premarket_default = "premarket_auto_korea.csv"
        meta_default = "ticker_master_korea.csv"
        ticker_list_default = "tickers_korea.txt"
        metadata_script = "build_ticker_master_korea.py"
        metadata_button = "Build / refresh Korean names"
        live_fill_default = True
    else:
        data_label = "data/us"
        premarket_default = "premarket_auto.csv"
        meta_default = "ticker_master_us.csv"
        ticker_list_default = "tickers.txt"
        metadata_script = "build_ticker_master_us.py"
        metadata_button = "Build / refresh US names"
        live_fill_default = False

    st.caption(f"기본값은 {data_label} 폴더의 최신 CSV를 읽는다. 다른 결과를 보려면 아래에서 업로드하면 된다.")

    report_file = st.file_uploader("report_v2.csv", type=["csv"], key=f"report_{market}")
    premarket_file = st.file_uploader(premarket_default, type=["csv"], key=f"premarket_{market}")
    overlay_file = st.file_uploader("thesis_overlay_master.csv", type=["csv"], key=f"overlay_{market}")
    meta_file = st.file_uploader(f"{meta_default} (optional)", type=["csv"], key=f"meta_{market}")

    near_max_dist = st.slider("Near breakout max distance %", 0.5, 5.0, 2.5, 0.1)
    show_rejects = st.checkbox("Show REJECT rows", value=False)
    st.divider()
    chart_period = st.selectbox("Chart period", ["3mo", "6mo", "1y", "2y", "5y"], index=1)
    chart_interval = st.selectbox("Chart interval", ["1d", "1wk"], index=0)
    if market == "Korea":
        live_fill_names = st.checkbox("Auto-fill visible Korean names", value=live_fill_default, help="표에 보이는 일부 숫자 이름을 Naver에서 즉시 조회한다. 많으면 약간 느릴 수 있다.")
    else:
        live_fill_names = False

report_source = report_file if report_file is not None else default_path("report_v2.csv")
premarket_source = premarket_file if premarket_file is not None else default_path(premarket_default)
overlay_source = overlay_file if overlay_file is not None else default_path("thesis_overlay_master.csv")

report = read_csv_flexible(report_source)
manual_set = load_manual_tickers_from_premarket(premarket_source)
overlay_by_ticker = load_overlay(overlay_source)

if report.empty or "ticker" not in report.columns:
    st.error("report_v2.csv를 찾지 못했거나 ticker 컬럼이 없습니다.")
    st.stop()

with st.sidebar:
    st.divider()
    st.subheader("Metadata")
    st.caption("종목명/업종명이 약하면 아래 버튼으로 현재 마켓의 ticker master를 재생성한다.")
    if st.button(metadata_button, use_container_width=True):
        if report_file is not None:
            st.warning("업로드 파일 모드에서는 먼저 CSV를 data 폴더에 저장한 뒤 실행하는 편이 안전하다.")
        else:
            script = Path(__file__).with_name(metadata_script)
            out_path = DATA_DIR / meta_default
            tickers_path = default_path(ticker_list_default)
            cmd = [sys.executable, str(script), "--report", str(report_source), "--out", str(out_path), "--force-refresh"]
            if tickers_path is not None:
                cmd += ["--tickers", str(tickers_path)]
            if market == "US":
                pm = default_path(premarket_default)
                if pm is not None:
                    cmd += ["--premarket", str(pm)]
                fm = default_path("finviz_top_groups_members.csv")
                if fm is not None:
                    cmd += ["--finviz-members", str(fm)]
            with st.spinner(f"{market} ticker master 생성 중..."):
                try:
                    cp = subprocess.run(cmd, capture_output=True, text=True, timeout=900)
                    if cp.returncode == 0:
                        st.success(cp.stdout or f"Saved {out_path}")
                        st.cache_data.clear()
                        st.rerun()
                    else:
                        st.error(cp.stderr or cp.stdout or "ticker master generation failed")
                except Exception as e:
                    st.error(f"metadata build failed: {type(e).__name__}: {e}")

meta = load_metadata(meta_file)
processed = process_report(report, manual_set, overlay_by_ticker, meta, near_max_dist=near_max_dist)

if not show_rejects:
    processed_view = processed[processed["entry_state"] != "REJECT"].copy()
else:
    processed_view = processed.copy()

states = ["ALL", "PREBREAK OK", "ENTRY OK", "SMALL SIZE", "HOLD ONLY", "AVOID NEW", "WATCH", "REJECT"]
buckets = ["ALL", "MANUAL ∩ SAFE", "SAFE ONLY"]

f1, f2, f3, f4 = st.columns([1.1, 1.1, 1.2, 2.0])
state_filter = f1.selectbox("State", states)
bucket_filter = f2.selectbox("Bucket", buckets)
asset_filter = f3.multiselect("Asset", sorted(processed_view["asset_type"].dropna().unique().tolist()), default=[])
query = f4.text_input("Search ticker / name / sector / industry", "")

filtered = processed_view.copy()
if state_filter != "ALL":
    filtered = filtered[filtered["entry_state"] == state_filter]
if bucket_filter != "ALL":
    filtered = filtered[filtered["bucket"] == bucket_filter]
if asset_filter:
    filtered = filtered[filtered["asset_type"].isin(asset_filter)]
if query:
    q = query.lower()
    cols = ["ticker", "name", "sector", "industry", "thesis_state"]
    mask = pd.Series(False, index=filtered.index)
    for c in cols:
        if c in filtered.columns:
            mask |= filtered[c].astype(str).str.lower().str.contains(q, na=False)
    filtered = filtered[mask]

if live_fill_names:
    filtered = fill_visible_missing_names(filtered, max_rows=100)

m1, m2, m3, m4, m5 = st.columns(5)
m1.metric("Total SAFE", len(processed))
m2.metric("Actionable", int(processed["entry_state"].isin(["PREBREAK OK", "ENTRY OK"]).sum()))
m3.metric("Small Size", int((processed["entry_state"] == "SMALL SIZE").sum()))
m4.metric("Avoid New", int((processed["entry_state"] == "AVOID NEW").sum()))
m5.metric("Manual ∩ SAFE", int((processed["bucket"] == "MANUAL ∩ SAFE").sum()))

tab1, tab2, tab3, tab4 = 

# --- visible last update panel ---
try:
    _market_key_for_update = "kr" if str(market).lower().startswith("korea") else "us"
    _market_label_for_update = "Korea" if _market_key_for_update == "kr" else "US"
    render_last_update_panel(_market_key_for_update, _market_label_for_update)
except Exception as _e:
    st.caption(f"Last update unavailable: {_e}")

st.tabs(["Radar", "Avoid Board", "Metadata Gaps", "Raw Data"])

with tab1:
    selected_row = None

    display_cols = [
        "ticker", "name", "sector", "industry", "asset_type", "bucket", "entry_state", "label",
        "grade", "score", "price", "daily_break_level", "dist_pct", "room_to_weekly_r1_pct",
        "rsi14", "px_vs_sma10", "px_vs_sma40", "px_vs_sma50", "px_vs_sma200", "thesis_state", "manage"
    ]
    display_cols = [c for c in display_cols if c in filtered.columns]

    st.subheader("Breakout Candidates")
    st.caption(
        "왼쪽 detail 체크박스를 누르면 아래 Selected Ticker Detail 영역에 해당 종목의 상세/차트/리포트 카드가 표시된다."
    )

    if filtered.empty:
        st.info("No rows after filters.")
    else:
        table_df = filtered[display_cols].copy()
        table_df.insert(0, "detail", False)

        edited = st.data_editor(
            table_df,
            use_container_width=True,
            hide_index=True,
            height=600,
            disabled=[c for c in table_df.columns if c != "detail"],
            column_config={
                "detail": st.column_config.CheckboxColumn(
                    "detail",
                    help="체크하면 아래 상세 패널에 표시",
                    default=False,
                ),
                "name": st.column_config.TextColumn("name"),
                "price": st.column_config.NumberColumn("price", format="%.2f"),
                "daily_break_level": st.column_config.NumberColumn("break", format="%.2f"),
                "dist_pct": st.column_config.NumberColumn("dist %", format="%.2f"),
                "room_to_weekly_r1_pct": st.column_config.NumberColumn("room %", format="%.2f"),
                "px_vs_sma10": st.column_config.NumberColumn("10DMA %", format="%.2f"),
                "px_vs_sma40": st.column_config.NumberColumn("40DMA %", format="%.2f"),
                "px_vs_sma50": st.column_config.NumberColumn("50DMA %", format="%.2f"),
                "px_vs_sma200": st.column_config.NumberColumn("200DMA %", format="%.2f"),
            },
            key="radar_candidate_picker",
        )

        picked = edited[edited["detail"] == True].copy()

        if not picked.empty:
            picked_ticker = str(picked.iloc[0]["ticker"])
            selected_row = filtered[filtered["ticker"].astype(str) == picked_ticker].iloc[0]
            st.session_state["selected_ticker_from_table"] = picked_ticker
        elif "selected_ticker_from_table" in st.session_state and st.session_state["selected_ticker_from_table"] in set(filtered["ticker"].astype(str)):
            picked_ticker = st.session_state["selected_ticker_from_table"]
            selected_row = filtered[filtered["ticker"].astype(str) == picked_ticker].iloc[0]
        else:
            selected_row = filtered.iloc[0]
            st.session_state["selected_ticker_from_table"] = str(selected_row["ticker"])

        st.divider()
        st.subheader("Selected Ticker Detail")

        st.caption(
            f"Selected: {selected_row.get('ticker', '')} · {selected_row.get('name', '')}"
        )

        render_detail(selected_row)
        render_external_links(selected_row)

        st.divider()
        render_chart(selected_row, chart_period, chart_interval)
        render_report_card(selected_row)

with tab2:
    st.subheader("Avoid New Board")
    with st.expander("AVOID NEW 해석", expanded=True):
        st.markdown(
            """
            **AVOID NEW는 ‘나쁜 종목’이라는 뜻이 아니라, 지금 가격에서 신규 진입 기대값이 낮다는 뜻**이다.  
            보통 아래 중 하나 때문에 붙는다.

            - **too hot**: RSI가 높거나 10/40/50/200DMA 이격이 커서 이미 많이 달린 상태
            - **weak score**: 주간 추세, RSI, room, breakout/retest 조합 점수가 약함
            - **room 부족**: weekly R1까지 남은 공간이 너무 좁거나 이미 목표 구간을 지나감
            - **overlay avoid**: 수동 overlay에서 신규 회피 플래그가 켜짐

            실전 해석은 **신규 매수 금지/보류**에 가깝다. 이미 보유 중이면 HOLD 관점으로 관리할 수 있지만, 새 진입은 **리셋·타이트 베이스·40DMA 근처 안정·거래량 재돌파**가 다시 나올 때까지 기다리는 쪽이 맞다.
            """
        )
    avoid = processed[processed["entry_state"] == "AVOID NEW"].copy()
    avoid_cols = [
        "ticker", "name", "sector", "industry", "asset_type", "bucket", "grade", "score",
        "price", "daily_break_level", "dist_pct", "room_to_weekly_r1_pct", "rsi14",
        "px_vs_sma10", "px_vs_sma40", "px_vs_sma50", "px_vs_sma200", "thesis_state", "avoid_reason", "manage", "warnings"
    ]
    avoid_cols = [c for c in avoid_cols if c in avoid.columns]
    st.dataframe(avoid[avoid_cols], use_container_width=True, hide_index=True)

with tab3:
    st.subheader("Missing Name / Industry")
    gaps = processed[processed.get("metadata_missing", False)].copy()
    st.caption('여기에 뜨는 행은 종목명/산업 매핑이 아직 약한 행이다. 숫자 이름을 한글명으로 강제 재조회하고, 산업이 안 나오면 "업종 미확인"으로 표시한다.')
    st.dataframe(gaps[["ticker", "entry_state", "grade", "score", "price"]], use_container_width=True, hide_index=True)
    template = processed[["ticker"]].drop_duplicates().copy()
    template["name"] = ""
    template["market"] = ""
    template["asset_type"] = ""
    template["sector"] = ""
    template["industry"] = ""
    st.download_button(
        "Download ticker master template",
        template.to_csv(index=False).encode("utf-8-sig"),
        file_name=f"{meta_default.replace('.csv', '_template.csv')}",
        mime="text/csv",
    )

with tab4:
    st.subheader("Processed")
    st.dataframe(processed, use_container_width=True, hide_index=True)
    st.download_button(
        "Download processed CSV",
        processed.to_csv(index=False).encode("utf-8-sig"),
        file_name=f"bulkowski_breakout_radar_{market.lower()}_processed.csv",
        mime="text/csv",
    )


# --- Last update sidebar block ---
def _read_market_last_update(_market_key: str) -> str:
    from pathlib import Path
    from datetime import datetime
    from zoneinfo import ZoneInfo

    _app_dir = Path(__file__).resolve().parent
    _data_dir = _app_dir / "data" / _market_key

    _stamp = _data_dir / "last_updated_kst.txt"
    if _stamp.exists():
        txt = _stamp.read_text(encoding="utf-8", errors="ignore").strip()
        if txt:
            return txt

    # fallback: report_v2.csv modified time
    _report = _data_dir / "report_v2.csv"
    if _report.exists():
        ts = datetime.fromtimestamp(_report.stat().st_mtime, ZoneInfo("Asia/Seoul"))
        return ts.strftime("%Y-%m-%d %H:%M:%S KST") + " (file mtime)"

    return "not available"


def _render_last_update_sidebar():
    try:
        st.sidebar.markdown("---")
        st.sidebar.markdown("### Last update")
        st.sidebar.caption("Latest successful GitHub Actions data refresh")

        kr_updated = _read_market_last_update("kr")
        us_updated = _read_market_last_update("us")

        st.sidebar.write(f"🇰🇷 **Korea**: {kr_updated}")
        st.sidebar.write(f"🇺🇸 **US**: {us_updated}")
    except Exception as e:
        try:
            st.sidebar.caption(f"Last update unavailable: {e}")
        except Exception:
            pass


_render_last_update_sidebar()
