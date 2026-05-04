#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Patch an existing Bulkowski Breakout Radar repo to add US market support.

Run from repo root:
  python add_us_support.py
"""
from __future__ import annotations
from pathlib import Path
import re
import textwrap

ROOT = Path.cwd()
APP = ROOT / "bulkowski_breakout_radar" / "streamlit_app.py"
DASH = ROOT / "bulkowski_breakout_radar"
WORKFLOWS = ROOT / ".github" / "workflows"

US_MASTER = r'''#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Build ticker_master_us.csv for Bulkowski Breakout Radar.

Best-effort metadata builder:
1. Existing output file, if present, is reused first.
2. finviz_top_groups_members.csv supplies Company/Sector/Industry when available.
3. Built-in ETF / mega-cap seed map fills common names quickly.
4. Optional yfinance lookup fills unresolved rows.

This script intentionally degrades gracefully: if yfinance/rate limits fail, it still
writes a valid master with ticker fallback names so the dashboard can run.
"""
from __future__ import annotations
import argparse
import time
from pathlib import Path
import pandas as pd

try:
    import yfinance as yf
except Exception:
    yf = None

ETF_TICKERS = {
    "SPY","QQQ","DIA","IWM","MDY","TQQQ","SQQQ","QLD","QID","UPRO","SPXU","SPXL","SPXS","SSO","SDS",
    "SOXL","SOXS","SOXX","SMH","XSD","TECL","TECS","FNGU","FNGD","BULZ","BERZ","USD",
    "XLE","XOP","OIH","XLB","XLK","XLU","XLI","XLV","XLP","XLY","IYE","IYM","IYG","IYW","IYR","IYH","IYZ","IYV",
    "ERX","ERY","GUSH","DRIP","DIG","UCO","SCO","BOIL","KOLD","USO","BNO","UNG",
    "GLD","IAU","GLDM","SLV","SIVR","GDX","GDXJ","NUGT","DUST","UGL","GLL","AGQ","SIL","SILJ",
    "TLT","IEF","VIXY","HYG","LQD","UUP","JETS","PAVE","GRID","IBB","AIQ","BOTZ","LIT","BATT","COPX",
    "IBIT","FBTC","ARKB","BITB","HODL","BRRR","EZBC","GBTC","ETHA","FETH","ETH","EZET","ETHV","ETHW","ETHE","QETH",
    "LABU","LABD","CURE","DRN","DRV","FAS","FAZ","TMF","TMV","UBOT","AIBU","AIBD","NVDL","NVDU","NVDD",
}

BUILTIN = {
    "NVDA": ("NVIDIA Corporation", "Technology", "Semiconductors"),
    "AVGO": ("Broadcom Inc.", "Technology", "Semiconductors"),
    "AMD": ("Advanced Micro Devices", "Technology", "Semiconductors"),
    "TSM": ("Taiwan Semiconductor Manufacturing", "Technology", "Semiconductors"),
    "ASML": ("ASML Holding", "Technology", "Semiconductor Equipment"),
    "MU": ("Micron Technology", "Technology", "Memory Semiconductors"),
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
    "BRK-B": ("Berkshire Hathaway", "Financials", "Financial Conglomerate"),
    "JPM": ("JPMorgan Chase", "Financials", "Banking"),
    "V": ("Visa Inc.", "Financials", "Payments"),
    "MA": ("Mastercard", "Financials", "Payments"),
    "COST": ("Costco Wholesale", "Consumer Staples", "Retail / Wholesale"),
    "WMT": ("Walmart", "Consumer Staples", "Retail"),
    "LLY": ("Eli Lilly", "Health Care", "Pharmaceuticals"),
    "ABBV": ("AbbVie", "Health Care", "Pharmaceuticals"),
    "VST": ("Vistra Corp.", "Utilities", "Power Generation"),
    "CEG": ("Constellation Energy", "Utilities", "Nuclear / Power"),
    "GEV": ("GE Vernova", "Industrials", "Power Equipment"),
    "ETN": ("Eaton", "Industrials", "Electrical Equipment"),
    "PWR": ("Quanta Services", "Industrials", "Grid Infrastructure"),
    "SPY": ("SPDR S&P 500 ETF", "ETF", "S&P 500 ETF"),
    "QQQ": ("Invesco QQQ Trust", "ETF", "Nasdaq 100 ETF"),
    "TQQQ": ("ProShares UltraPro QQQ", "ETF", "Nasdaq 100 leveraged ETF"),
    "SQQQ": ("ProShares UltraPro Short QQQ", "ETF", "Nasdaq 100 inverse leveraged ETF"),
    "SOXL": ("Direxion Daily Semiconductor Bull 3X", "ETF", "Semiconductor leveraged ETF"),
    "SOXS": ("Direxion Daily Semiconductor Bear 3X", "ETF", "Semiconductor inverse leveraged ETF"),
    "SMH": ("VanEck Semiconductor ETF", "ETF", "Semiconductor ETF"),
    "SOXX": ("iShares Semiconductor ETF", "ETF", "Semiconductor ETF"),
    "NVDL": ("GraniteShares 2x Long NVDA Daily ETF", "ETF", "Single-stock leveraged NVDA ETF"),
    "NVDU": ("Direxion Daily NVDA Bull 2X", "ETF", "Single-stock leveraged NVDA ETF"),
    "IBIT": ("iShares Bitcoin Trust", "ETF", "Spot Bitcoin ETF"),
    "ETHA": ("iShares Ethereum Trust ETF", "ETF", "Spot Ethereum ETF"),
    "QETH": ("Ether ETF proxy", "ETF", "Ethereum ETF / proxy"),
}


def read_csv(path: Path) -> pd.DataFrame:
    if path is None or not path.exists():
        return pd.DataFrame()
    try:
        return pd.read_csv(path, comment="#")
    except Exception:
        return pd.read_csv(path, comment="#", engine="python")


def load_tickers(paths: list[Path]) -> list[str]:
    out, seen = [], set()
    for p in paths:
        if not p or not p.exists():
            continue
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
                vals = []
        else:
            vals = []
            for line in p.read_text(encoding="utf-8", errors="ignore").splitlines():
                line = line.split("#", 1)[0].strip()
                if not line:
                    continue
                vals += re_split(line)
        for v in vals:
            t = str(v).strip().upper()
            if not t or t in {"TICKER", "SOURCE", "S&P"}:
                continue
            if t not in seen:
                seen.add(t)
                out.append(t)
    return out


def re_split(s: str) -> list[str]:
    import re
    return [x for x in re.split(r"[\s,;]+", s) if x]


def asset_type_for(ticker: str, name: str = "", sector: str = "") -> str:
    n = str(name or "").upper()
    sec = str(sector or "").upper()
    if ticker.upper() in ETF_TICKERS or "ETF" in n or "TRUST" in n and ticker.upper() in ETF_TICKERS or sec == "ETF":
        return "ETF"
    if "=F" in ticker or ticker.startswith("^"):
        return "INDEX/FUTURE"
    return "STOCK"


def finviz_map(path: Path) -> dict[str, dict]:
    df = read_csv(path)
    out = {}
    if df.empty or "Ticker" not in df.columns:
        return out
    for _, r in df.iterrows():
        t = str(r.get("Ticker", "")).strip().upper()
        if not t:
            continue
        out[t] = {
            "ticker": t,
            "name": str(r.get("Company", "") or "").strip(),
            "sector": str(r.get("Sector", "") or "").strip(),
            "industry": str(r.get("Industry", "") or "").strip(),
            "source": "finviz_members",
        }
    return out


def yf_lookup(ticker: str) -> dict | None:
    if yf is None or "=F" in ticker or ticker.startswith("^"):
        return None
    try:
        info = yf.Ticker(ticker).get_info()
        if not isinstance(info, dict) or not info:
            return None
        name = info.get("shortName") or info.get("longName") or ""
        sector = info.get("sector") or ""
        industry = info.get("industry") or ""
        quote_type = str(info.get("quoteType") or "").upper()
        if quote_type in {"ETF", "MUTUALFUND"}:
            sector = sector or "ETF"
            industry = industry or "ETF / Fund"
        if not name:
            return None
        return {"name": name, "sector": sector, "industry": industry, "source": "yfinance"}
    except Exception:
        return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--report", default="data/us/report_v2.csv")
    ap.add_argument("--tickers", default="data/us/tickers.txt")
    ap.add_argument("--premarket", default="data/us/premarket_auto.csv")
    ap.add_argument("--finviz-members", default="")
    ap.add_argument("--out", default="data/us/ticker_master_us.csv")
    ap.add_argument("--max-yf", type=int, default=250)
    ap.add_argument("--force-refresh", action="store_true")
    args = ap.parse_args()

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    existing = {}
    if out_path.exists() and not args.force_refresh:
        old = read_csv(out_path)
        if not old.empty and "ticker" in old.columns:
            for _, r in old.iterrows():
                existing[str(r["ticker"]).strip().upper()] = r.to_dict()

    tickers = load_tickers([Path(args.report), Path(args.tickers), Path(args.premarket)])
    fmap = finviz_map(Path(args.finviz_members)) if args.finviz_members else {}

    rows = []
    yf_count = 0
    unresolved = []
    for i, t in enumerate(tickers, 1):
        row = None
        if t in existing and str(existing[t].get("name", "")).strip() and str(existing[t].get("name", "")).strip().upper() != t:
            row = existing[t]
            row["source"] = row.get("source", "existing")
        elif t in fmap:
            row = fmap[t]
        elif t in BUILTIN:
            n, s, ind = BUILTIN[t]
            row = {"ticker": t, "name": n, "sector": s, "industry": ind, "source": "builtin"}
        elif yf_count < args.max_yf:
            got = yf_lookup(t)
            yf_count += 1
            time.sleep(0.03)
            if got:
                row = {"ticker": t, **got}
        if row is None:
            row = {"ticker": t, "name": t, "sector": "Unmapped", "industry": "Unmapped", "source": "fallback"}
            unresolved.append(t)
        name = str(row.get("name", "") or t).strip()
        sector = str(row.get("sector", "") or "Unmapped").strip()
        industry = str(row.get("industry", "") or "Unmapped").strip()
        asset = str(row.get("asset_type", "") or asset_type_for(t, name, sector)).strip()
        rows.append({"ticker": t, "name": name, "asset_type": asset, "sector": sector, "industry": industry, "source": row.get("source", "")})
        if i == 1 or i % 50 == 0 or i == len(tickers):
            print(f"... us metadata {i}/{len(tickers)} {t} -> {name}", flush=True)

    df = pd.DataFrame(rows).drop_duplicates("ticker", keep="last")
    df.to_csv(out_path, index=False)
    print(f"Saved: {out_path} ({len(df)} rows)")
    print(f"yfinance lookups: {yf_count}")
    print(f"unresolved names: {len(unresolved)}")
    if unresolved[:30]:
        print("unresolved sample:", ", ".join(unresolved[:30]))

if __name__ == "__main__":
    main()
'''

US_WORKFLOW = r'''name: US Breakout Scan

on:
  workflow_dispatch:
  schedule:
    # US premarket / market checks, UTC 기준.
    # 대략 KST 21:00, 23:00, 01:00 / ET premarket-open 이후 대응용.
    - cron: "0 12,14,16 * * 1-5"

permissions:
  contents: write

concurrency:
  group: us-breakout-scan
  cancel-in-progress: true

jobs:
  scan:
    runs-on: ubuntu-latest
    timeout-minutes: 120

    steps:
      - name: Checkout repo
        uses: actions/checkout@v4

      - name: Set up Python
        uses: actions/setup-python@v5
        with:
          python-version: "3.10"

      - name: Install dependencies
        run: |
          python -m pip install --upgrade pip
          pip install -r requirements-actions.txt

      - name: Run US scan pipeline
        working-directory: charts_today
        run: |
          python finviz_top_groups_auto_mixed_v2.py
          python sync_tickers_txt_v4.py --coin etc
          python update_premarket_yf_auto_debug_v2.py
          python scan_candidates_v2_safe_v6_us_sma1040.py --premarket premarket_auto.csv
          python manual10_legacy_review_v3_dmaall_color.py \
            --manual premarket_manual.csv \
            --report report_v2.csv \
            --detail-top 10 | tee manual_review_us_latest.txt

      - name: Copy generated US data to dashboard
        run: |
          mkdir -p bulkowski_breakout_radar/data/us
          cp charts_today/report_v2.csv bulkowski_breakout_radar/data/us/report_v2.csv
          cp charts_today/premarket_auto.csv bulkowski_breakout_radar/data/us/premarket_auto.csv
          cp charts_today/premarket_auto_debug.csv bulkowski_breakout_radar/data/us/premarket_auto_debug.csv
          cp charts_today/scan_skipped.log bulkowski_breakout_radar/data/us/scan_skipped.log
          cp charts_today/tickers.txt bulkowski_breakout_radar/data/us/tickers.txt
          cp charts_today/manual_review_us_latest.txt bulkowski_breakout_radar/data/us/manual_review_us_latest.txt
          if [ -f charts_today/thesis_overlay_master.csv ]; then cp charts_today/thesis_overlay_master.csv bulkowski_breakout_radar/data/us/thesis_overlay_master.csv; fi
          if [ -f charts_today/finviz_top_groups_members.csv ]; then cp charts_today/finviz_top_groups_members.csv bulkowski_breakout_radar/data/us/finviz_top_groups_members.csv; fi

      - name: Build US ticker master
        run: |
          python bulkowski_breakout_radar/build_ticker_master_us.py \
            --report bulkowski_breakout_radar/data/us/report_v2.csv \
            --tickers bulkowski_breakout_radar/data/us/tickers.txt \
            --premarket bulkowski_breakout_radar/data/us/premarket_auto.csv \
            --finviz-members bulkowski_breakout_radar/data/us/finviz_top_groups_members.csv \
            --out bulkowski_breakout_radar/data/us/ticker_master_us.csv

      - name: Commit updated US data
        run: |
          git config user.name "github-actions[bot]"
          git config user.email "41898282+github-actions[bot]@users.noreply.github.com"
          git add bulkowski_breakout_radar/data/us/
          git add charts_today/report_v2.csv charts_today/premarket_auto.csv charts_today/premarket_auto_debug.csv charts_today/scan_skipped.log charts_today/manual_review_us_latest.txt charts_today/tickers.txt || true
          if git diff --cached --quiet; then
            echo "No changes to commit."
          else
            git commit -m "Update US breakout scan data"
            git push
          fi
'''


def write_file(path: Path, content: str):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    print(f"wrote {path}")


def patch_app():
    if not APP.exists():
        raise SystemExit(f"Missing {APP}. Run from repo root.")
    s = APP.read_text(encoding="utf-8")
    bak = APP.with_suffix(".py.bak_us_support")
    bak.write_text(s, encoding="utf-8")
    print(f"backup saved: {bak}")

    # Top data dirs
    if "DATA_DIRS" not in s:
        s = re.sub(
            r'APP_SUBTITLE = "Prior-High Breakout Candidates from Chart Pattern Logic"\n(?:APP_DIR = .*?\n)?(?:KR_DIR = .*?\n)+',
            'APP_SUBTITLE = "Prior-High Breakout Candidates from Chart Pattern Logic"\n'
            'APP_DIR = Path(__file__).resolve().parent\n'
            'DATA_DIRS = {"Korea": APP_DIR / "data" / "kr", "US": APP_DIR / "data" / "us"}\n'
            'KR_DIR = DATA_DIRS["Korea"]\n'
            'US_DIR = DATA_DIRS["US"]\n',
            s,
            count=1,
            flags=re.S,
        )
    else:
        # Ensure US_DIR exists if app was partially patched.
        if "US_DIR" not in s:
            s = s.replace('KR_DIR = DATA_DIRS["Korea"]\n', 'KR_DIR = DATA_DIRS["Korea"]\nUS_DIR = DATA_DIRS["US"]\n')

    # Metadata loader uses active DATA_DIR.
    s = s.replace('seed = KR_DIR / "ticker_master_korea_seed.csv"', 'seed = DATA_DIR / ("ticker_master_korea_seed.csv" if market == "Korea" else "ticker_master_us_seed.csv")')
    s = s.replace('full_master = KR_DIR / "ticker_master_korea.csv"', 'full_master = DATA_DIR / meta_default')

    # default_path uses active DATA_DIR.
    s = re.sub(
        r'def default_path\(filename: str\) -> Optional\[Path\]:\n\s+p = .*?\n\s+return p if p\.exists\(\) else None',
        'def default_path(filename: str) -> Optional[Path]:\n    p = DATA_DIR / filename\n    return p if p.exists() else None',
        s,
        count=1,
        flags=re.S,
    )

    # Sidebar data-source block.
    m = re.search(r'with st\.sidebar:\n\s+st\.header\("Data Source"\).*?\nreport_source = ', s, flags=re.S)
    if not m:
        print("WARNING: could not locate first sidebar block; app may need manual patch")
    else:
        new_block = '''with st.sidebar:
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

report_source = '''
        s = s[:m.start()] + new_block + s[m.end():]

    # Generic data sources.
    s = re.sub(
        r'report_source = .*?\noverlay_source = .*?\n',
        'report_source = report_file if report_file is not None else default_path("report_v2.csv")\n'
        'premarket_source = premarket_file if premarket_file is not None else default_path(premarket_default)\n'
        'overlay_source = overlay_file if overlay_file is not None else default_path("thesis_overlay_master.csv")\n',
        s,
        count=1,
        flags=re.S,
    )

    # Metadata sidebar block.
    m = re.search(r'with st\.sidebar:\n\s+st\.divider\(\)\n\s+st\.subheader\("Metadata"\).*?\nmeta = load_metadata\(meta_file\)', s, flags=re.S)
    if not m:
        print("WARNING: could not locate metadata sidebar block; app may need manual patch")
    else:
        new_meta = '''with st.sidebar:
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

meta = load_metadata(meta_file)'''
        s = s[:m.start()] + new_meta + s[m.end():]

    # Metadata tab labels/files generic, if present.
    s = s.replace('Download ticker_master_korea template', 'Download ticker master template')
    s = s.replace('file_name="ticker_master_korea_template.csv"', 'file_name=f"{meta_default.replace(\'.csv\', \'_template.csv\')}"')
    s = s.replace('file_name="bulkowski_breakout_radar_korea_processed.csv"', 'file_name=f"bulkowski_breakout_radar_{market.lower()}_processed.csv"')

    APP.write_text(s, encoding="utf-8")
    print(f"patched {APP}")


def main():
    write_file(DASH / "build_ticker_master_us.py", US_MASTER)
    write_file(WORKFLOWS / "us-breakout-scan.yml", US_WORKFLOW)
    patch_app()
    print("\nDone. Next: python -m py_compile bulkowski_breakout_radar/streamlit_app.py bulkowski_breakout_radar/build_ticker_master_us.py")

if __name__ == "__main__":
    main()
