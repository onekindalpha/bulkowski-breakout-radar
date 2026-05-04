# Bulkowski Breakout Radar

[Live Dashboard](https://bulkowski-breakout-radar-dfb5nsywarnwlaetmhdsts.streamlit.app/)

Automated Korea / US prior-high breakout radar based on Bulkowski-style chart pattern logic.

불코우스키 『차트 패턴』의 전고점 돌파 / 리테스트 아이디어를 기반으로, 한국장과 미국장의 돌파 후보를 자동으로 스캔하고 대시보드로 보여주는 프로젝트입니다.

현재는 Korea market scan이 먼저 구현되어 있고, US market scan은 다음 단계로 확장 예정입니다.

---

## What this project does

Bulkowski Breakout Radar scans a ticker universe and finds stocks or ETFs that are close to a meaningful prior-high breakout setup.

The model is not simply looking for stocks that went up today.  
It tries to separate:

- Valid breakout candidates
- Near-breakout retest setups
- Tight-room / small-size setups
- Overheated names that should not be chased
- Watch-only names

The final dashboard classifies each ticker into practical action states:

| State | Meaning |
|---|---|
| ENTRY OK | Valid new-entry candidate |
| SMALL SIZE | Entry possible, but position size should be reduced |
| AVOID NEW | Do not open a new position at the current price |
| WATCH | Monitor only |
| HOLD ONLY | Manage existing position only, not recommended for new entry |
| REJECT | Not a priority setup |

---

## Core breakout logic

The core scanner uses daily OHLCV data and calculates a prior-high breakout level.

### 1. 60-day prior high

The daily breakout level is based on the recent 60-trading-day high.

In the scanner, this is calculated from the rolling high:

- `daily_break_level` = rolling 60-day high
- `daily_breakout` = current scan price is above the 60-day high
- `daily_retest` = current scan price is close enough to the 60-day high

This means the model is mainly looking for stocks that are either:

1. Breaking above a recent 60-day high, or
2. Sitting just below / around that high in a retest or trigger zone.

This is the main “prior-high breakout candidate” idea.

---

### 2. Retest tolerance

The model does not only look for confirmed breakouts.  
It also looks for names that are close to the breakout level.

The scanner marks a ticker as a retest setup when the current price is within a tolerance band around the 60-day high.

This helps catch candidates before or near the actual breakout instead of only after the move has already happened.

---

### 3. Weekly resistance room

A prior-high breakout is not useful if there is no room left above.

The scanner calculates a weekly resistance level called `weekly_r1`.

Then it calculates:

- `room_to_weekly_r1_pct`

This tells how much upside room remains from current price to the next weekly resistance area.

If room is too small, the setup may be downgraded to:

- `SMALL SIZE`
- `HOLD ONLY`
- `AVOID NEW`

This is why some strong-looking stocks can still be rejected for new entry.

---

### 4. Moving-average structure

The model checks price location relative to major moving averages:

- 10DMA
- 40DMA
- 50DMA
- 200DMA

The dashboard displays:

- `px_vs_sma10`
- `px_vs_sma40`
- `px_vs_sma50`
- `px_vs_sma200`

The 40DMA is used as a timing filter.  
The 10DMA is used as a short-term execution / management reference.

If a ticker is too far above moving averages, it can be considered overheated.  
If it is below important averages, the structure may be downgraded.

---

### 5. RSI filter

The scanner calculates RSI14.

RSI is used to avoid chasing overheated names.

General interpretation:

- RSI around 50–65: healthier momentum zone
- RSI 65–70: still usable but getting warm
- RSI above 70: overbought risk increases
- Very low RSI: weak structure warning

A ticker can still be near breakout but become `AVOID NEW` if RSI is too hot.

---

## Scoring and grading

The raw scanner assigns:

- `grade`
- `score`

The score rewards conditions such as:

- Weekly uptrend
- Price above SMA50 / SMA200
- Healthy RSI
- Breakout or retest condition
- Enough room to weekly resistance

The grade is roughly:

| Grade | Meaning |
|---|---|
| A | Better breakout structure with enough room |
| B | Usable but less ideal |
| C | Lower quality or watch-only candidate |

The final action state is not based on grade alone.  
A ticker can have a good grade but still become `AVOID NEW` if it is too extended or has poor reward-to-risk at the current price.

---

## Final overlay review

After the raw scan, the review layer combines:

- Manual universe
- Safe scan result
- Thesis overlay
- Technical state
- Room
- RSI
- Moving-average extension

The review script ranks states in this order:

1. PREBREAK OK
2. ENTRY OK
3. SMALL SIZE
4. HOLD ONLY
5. AVOID NEW
6. WATCH
7. REJECT

The final dashboard focuses mainly on actionable states:

- ENTRY OK
- SMALL SIZE
- AVOID NEW
- WATCH

---

## Meaning of AVOID NEW

`AVOID NEW` does not mean the company is bad.

It means:

The current price is not an attractive new-entry point.

Common reasons:

- Too hot / overextended
- RSI is too high
- Weak score
- Weekly resistance room is too small
- Price has already passed the first target zone
- Manual overlay says avoid new entry

A ticker marked as `AVOID NEW` may still be useful for:

- Monitoring
- Existing position management
- Waiting for a reset
- Waiting for a tighter base
- Watching for a fresh breakout setup later

But it is not a fresh-buy candidate at the current price.

---

## Korea scan pipeline

The Korea pipeline runs these scripts:

1. `build_macro_watch_yahoo_korea.py`

Builds a macro / benchmark / sector proxy watchlist for Korea.

2. `build_kr_foreign_naver_auto_v6_dateanchor_patched3.py --top 30`

Builds a foreign net-buy overlay from Naver Finance.

3. `build_kr_strong_stocks_auto_v5.py`

Finds strong stocks from seed tickers using recent momentum.

4. `sync_tickers_txt_korea_v3.py`

Creates the final Korea ticker universe.

5. `update_premarket_yf_auto_debug_korea_v5_dedup.py`

Fetches latest Korea prices through yfinance and writes premarket / last-price CSV files.

6. `scan_candidates_v2_safe_v6_sma1040.py`

Runs the main breakout scan using price, RSI, moving averages, weekly resistance, breakout and retest logic.

7. `manual10_legacy_review_korea_v12_3_dmaall_color_brightstates.py`

Combines the scan result with manual / overlay logic and produces the final actionable review.

---

## Automation

Korea scan is automated with GitHub Actions.

Scheduled runs:

- KST 07:00
- KST 12:00
- KST 16:00

Manual run:

Actions → Korea Breakout Scan → Run workflow

The workflow updates the latest scan data in:

- `bulkowski_breakout_radar/data/kr/report_v2.csv`
- `bulkowski_breakout_radar/data/kr/premarket_auto_korea.csv`
- `bulkowski_breakout_radar/data/kr/ticker_master_korea.csv`
- `bulkowski_breakout_radar/data/kr/manual_review_korea_latest.txt`

---

## Dashboard

The Streamlit dashboard displays:

- Breakout candidates
- Entry state
- Grade and score
- Distance to breakout level
- Room to weekly resistance
- RSI14
- 10 / 40 / 50 / 200DMA distance
- Ticker name and sector metadata
- Avoid-new board
- Raw scan data
- Metadata gaps
- Selected ticker detail view

Live dashboard:

https://bulkowski-breakout-radar-dfb5nsywarnwlaetmhdsts.streamlit.app/

---

## Current status

| Market | Status |
|---|---|
| Korea | Supported |
| US | Supported |

---

## Disclaimer

This project is for research, screening, and educational purposes only.

It is not financial advice.  
All trading decisions are the responsibility of the user.
