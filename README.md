# Bulkowski Breakout Radar

Prior-high breakout and retest candidate dashboard based on Bulkowski-style chart pattern logic.

This project converts rule-based chart pattern ideas into an automated data pipeline and Streamlit dashboard. It supports Korea and US market screening workflows, stores structured screening outputs, and provides a visual interface for reviewing breakout, retest, and pattern-based candidate states.

---

## Live Dashboard

- **Streamlit Dashboard**: https://bulkowski-breakout-radar-dfb5nsywarnwlaetmhdsts.streamlit.app

---

## Supported Markets

- Korea market screening workflow
- US market screening workflow
- Prior-high breakout and retest candidate review
- Structured screening outputs for dashboard visualization

---

## Live Dashboard

- **Streamlit Dashboard**: https://bulkowski-breakout-radar-dfb5nsywarnwlaetmhdsts.streamlit.app

---

## Local Run

```bash
cd /Users/velocitygoal/bulkowski_breakout_radar
source .venv/bin/activate
pip install -r requirements.txt
streamlit run streamlit_app.py
```

---

## Ticker Metadata Refresh

The dashboard uses ticker metadata to display company names and related market information correctly.

### Korea Ticker Metadata

If numeric tickers appear in the company-name column for Korea results, rebuild the Korea ticker master file.

```bash
cd /Users/velocitygoal/bulkowski_breakout_radar
source .venv/bin/activate
pip install -r requirements.txt
python build_ticker_master_korea.py \
  --report data/kr/report_v2.csv \
  --tickers data/kr/tickers_korea.txt \
  --out data/kr/ticker_master_korea.csv \
  --force-refresh
```

Alternatively, use the **Build / refresh Korean names** button in the dashboard sidebar.

### Name Resolution Order

1. Built-in / seed metadata
2. `pykrx` KRX ticker names
3. `pykrx` ETF names
4. Naver Finance fallback

---

## GitHub Actions

GitHub-hosted Actions cannot directly access local Mac paths such as:

```text
/Users/velocitygoal/charts_today
```

To run the workflow automatically in the cloud, use the repository structure below.

```text
repo/
  charts_today/
    build_macro_watch_yahoo_korea.py
    build_kr_foreign_naver_auto_v6_dateanchor_patched3.py
    build_kr_strong_stocks_auto_v5.py
    sync_tickers_txt_korea_v3.py
    update_premarket_yf_auto_debug_korea_v5_dedup.py
    scan_candidates_v2_safe_v6_sma1040.py
    manual10_legacy_review_korea_v12_3_dmaall_color_brightstates.py
    tickers_core_korea.txt
    tickers_leverage2x_korea.txt
    thesis_overlay_master.csv
    ...

  bulkowski_breakout_radar/
    streamlit_app.py
    build_ticker_master_korea.py
    data/kr/

  .github/workflows/korea-breakout-scan.yml
```

The workflow file below is configured to run on Korean market weekdays at 07:00, 12:00, and 16:00 KST.

```text
.github/workflows/korea-breakout-scan.yml
```

---

## Disclaimer

This project is for educational, research, and software engineering portfolio purposes only.

It is not financial advice, an investment recommendation, or a trading signal service. All decisions based on the output are the user's responsibility.
