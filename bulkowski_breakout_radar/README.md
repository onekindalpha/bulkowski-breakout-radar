# Bulkowski Breakout Radar — Korea

불코우스키 『차트 패턴』 기반 전고점 돌파 후보 대시보드.

## Local run

```bash
cd /Users/velocitygoal/bulkowski_breakout_radar
source .venv/bin/activate
pip install -r requirements.txt
streamlit run streamlit_app.py
```

## Fix Korean names / ticker master

숫자 티커가 이름 칸에 보이면 아래를 실행한다.

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

또는 대시보드 사이드바에서 **Build / refresh Korean names** 버튼을 누른다.

v5 name resolution order:
1. built-in/seed metadata
2. pykrx KRX ticker names
3. pykrx ETF names
4. Naver Finance fallback

## GitHub Actions

GitHub-hosted Actions는 내 Mac의 `/Users/velocitygoal/charts_today`를 직접 볼 수 없다. 클라우드에서 자동 실행하려면 리포지토리 구조를 아래처럼 맞춘다.

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

이 패키지의 `.github/workflows/korea-breakout-scan.yml`은 KST 평일 07:00, 12:00, 16:00에 실행되도록 되어 있다.
