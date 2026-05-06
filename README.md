# Bulkowski Breakout Radar

**Prior-high breakout candidate dashboard based on Bulkowski-style chart pattern logic.**

불코우스키 『차트 패턴』의 전고점 돌파 / 리테스트 아이디어를 기반으로, 한국장과 미국장의 돌파 후보를 자동으로 스캔하고 Streamlit 대시보드로 보여주는 프로젝트입니다.

## Live Dashboard

- Streamlit Dashboard: https://bulkowski-breakout-radar-dfb5nsywarnwlaetmhdsts.streamlit.app/

---

## Supported Markets

| Market | Status | Data Folder |
|---|---:|---|
| Korea | Supported | `bulkowski_breakout_radar/data/kr/` |
| US | Supported | `bulkowski_breakout_radar/data/us/` |

The dashboard uses the sidebar `Market` selector to switch between Korea and US.

---

## What this project does

Bulkowski Breakout Radar scans a ticker universe and finds stocks or ETFs that are close to a meaningful prior-high breakout setup.

The model is not simply looking for stocks that went up today. It tries to separate:

- Valid breakout candidates
- Near-breakout / retest setups
- Tight-room setups where only small size is appropriate
- Overheated names that should not be chased
- Watch-only names

The final dashboard classifies candidates into practical action states.

| State | Meaning |
|---|---|
| `ENTRY OK` | Valid new-entry candidate |
| `SMALL SIZE` | Entry is possible, but size should be reduced |
| `AVOID NEW` | Do not open a new position at the current price |
| `WATCH` | Monitor only |
| `HOLD ONLY` | Manage existing position only |
| `REJECT` | Not a priority setup |

---

## Core breakout logic

The core scanner uses daily OHLCV data and calculates a prior-high breakout level.

### 1. 60-day prior high

The daily breakout level is based on the recent 60-trading-day high.

Key fields:

- `daily_break_level`: rolling 60-day high
- `daily_breakout`: current price is above the breakout level
- `daily_retest`: current price is near the breakout level

This means the model is mainly looking for stocks that are either:

1. Breaking above a recent 60-day high, or
2. Sitting just below / around that high in a retest or trigger zone.

### 2. Retest / trigger zone

The scanner does not only look for confirmed breakouts. It also looks for names that are close enough to the breakout level.

This is useful because many breakout candidates are most actionable before the breakout is fully extended.

### 3. Weekly resistance room

A breakout candidate is less attractive if there is no room left above.

The scanner calculates:

- `weekly_r1`
- `room_to_weekly_r1_pct`

If the room to weekly resistance is too small, the setup can be downgraded to:

- `SMALL SIZE`
- `HOLD ONLY`
- `AVOID NEW`

### 4. Moving-average structure

The dashboard displays price distance from key moving averages:

- 10DMA
- 40DMA
- 50DMA
- 200DMA

The 40DMA is used as a timing / structure filter. The 10DMA is used mainly for short-term execution and management.

If a stock is too far above moving averages, it can be considered overheated. If it is below important averages, the setup can be downgraded.

### 5. RSI filter

The scanner calculates `RSI14`.

| RSI Zone | Interpretation |
|---|---|
| 50–65 | Healthier momentum zone |
| 65–70 | Usable but getting warm |
| 70+ | Overbought / chase risk increases |
| Very low RSI | Weak structure warning |

A ticker can still be near breakout but become `AVOID NEW` if RSI or moving-average extension is too hot.

---

## Meaning of `AVOID NEW`

`AVOID NEW` does **not** mean the company is bad.

It means:

> The current price is not an attractive fresh-entry point.

Common reasons:

- Too hot / overextended
- RSI is too high
- Weak score
- Weekly resistance room is too small
- Price is already near or above the first target zone
- Manual / thesis overlay says avoid new entry

A ticker marked `AVOID NEW` can still be useful for monitoring, managing an existing position, waiting for a reset, waiting for a tighter base, or watching for a cleaner breakout setup later.

---

## Dashboard features

The Streamlit dashboard shows:

- Breakout candidates
- Entry state
- Grade and score
- Distance to breakout level
- Room to weekly resistance
- RSI14
- 10 / 40 / 50 / 200DMA distance
- Ticker name and sector metadata
- Avoid-new board
- Metadata gaps
- Raw scan data
- Selected ticker detail view
- Chart view
- Market-specific research links
- Last successful update timestamp

Example timestamp display:

```text
Last updated · Korea: YYYY-MM-DD HH:MM:SS KST
Last updated · US: YYYY-MM-DD HH:MM:SS KST
```

---

## Automation

The dashboard data is automatically refreshed with GitHub Actions.

Manual execution is also available:

```text
Actions → Korea Breakout Scan → Run workflow
Actions → US Breakout Scan → Run workflow
```

Automatic scheduled runs are configured around each market session.

### Korea schedule

Korea scans run around the Korean market session.

| Purpose | Scheduled Time |
|---|---:|
| Pre-market / pre-open check | 07:17 KST |
| Before regular market open | 08:57 KST |
| After market close | 16:17 KST |

Workflow file:

```text
.github/workflows/korea-breakout-scan.yml
```

### US schedule

US scans run around the US market session using New York market time.

The workflow uses `America/New_York`, so daylight saving time and standard time are handled by the workflow schedule.

| Purpose | New York Time | Summer Time in KST | Standard Time in KST |
|---|---:|---:|---:|
| Pre-market start check | 03:50 ET | 16:50 KST | 17:50 KST |
| Before regular market open | 09:20 ET | 22:20 KST | 23:20 KST |
| After market close | 16:20 ET | Next day 05:20 KST | Next day 06:20 KST |

Workflow file:

```text
.github/workflows/us-breakout-scan.yml
```

---

## How to confirm scheduled runs

In GitHub:

```text
Actions → Korea Breakout Scan
Actions → US Breakout Scan
```

Check the `Event` column:

| Event | Meaning |
|---|---|
| `schedule` | Automatic scheduled run |
| `workflow_dispatch` | Manual run from the GitHub UI |

A successful scheduled run should also update the dashboard timestamp.

---

## Repository structure

```text
repo/
  charts_today/
    # scanner scripts and input ticker files

  bulkowski_breakout_radar/
    streamlit_app.py
    build_ticker_master_korea.py
    build_ticker_master_us.py
    repair_us_master.py
    data/
      kr/
        report_v2.csv
        premarket_auto_korea.csv
        ticker_master_korea.csv
        last_updated_kst.txt
      us/
        report_v2.csv
        premarket_auto.csv
        ticker_master_us.csv
        last_updated_kst.txt

  .github/workflows/
    korea-breakout-scan.yml
    us-breakout-scan.yml

  requirements.txt
  requirements-actions.txt
```

---

## Local run

From the repository root:

```bash
cd /Users/velocitygoal/github/bulkowski-breakout-radar
cd bulkowski_breakout_radar

source .venv/bin/activate
pip install -r ../requirements.txt
streamlit run streamlit_app.py
```

If the virtual environment does not exist yet:

```bash
cd /Users/velocitygoal/github/bulkowski-breakout-radar/bulkowski_breakout_radar

python -m venv .venv
source .venv/bin/activate
pip install -r ../requirements.txt
streamlit run streamlit_app.py
```

---

## Korea ticker master refresh

If Korean ticker names or industries are missing:

```bash
cd /Users/velocitygoal/github/bulkowski-breakout-radar

python bulkowski_breakout_radar/build_ticker_master_korea.py \
  --report bulkowski_breakout_radar/data/kr/report_v2.csv \
  --tickers bulkowski_breakout_radar/data/kr/tickers_korea.txt \
  --out bulkowski_breakout_radar/data/kr/ticker_master_korea.csv \
  --force-refresh
```

Korea name resolution order:

1. Built-in / seed metadata
2. pykrx KRX ticker names
3. pykrx ETF names
4. Naver Finance fallback

---

## US ticker master refresh

If US names, sectors, or industries are missing:

```bash
cd /Users/velocitygoal/github/bulkowski-breakout-radar

python bulkowski_breakout_radar/build_ticker_master_us.py \
  --report bulkowski_breakout_radar/data/us/report_v2.csv \
  --tickers bulkowski_breakout_radar/data/us/tickers.txt \
  --premarket bulkowski_breakout_radar/data/us/premarket_auto.csv \
  --finviz-members bulkowski_breakout_radar/data/us/finviz_top_groups_members.csv \
  --out bulkowski_breakout_radar/data/us/ticker_master_us.csv \
  --max-yf 2000

python bulkowski_breakout_radar/repair_us_master.py \
  --path bulkowski_breakout_radar/data/us/ticker_master_us.csv
```

---

## Data caveats

This project depends on public or semi-public market data sources such as Yahoo Finance / yfinance, Naver Finance, Finviz, pykrx, and exchange-related pages.

Possible limitations:

- Delayed prices
- Missing tickers
- Empty metadata
- Changed ticker symbols
- ETF / futures / index symbols requiring manual mapping
- Scheduled workflow delays during GitHub Actions load

Always verify important candidates manually before making any investment decision.

---

## Disclaimer

This project is for research, screening, and educational purposes only.

It is not financial advice. All trading decisions are the responsibility of the user.
