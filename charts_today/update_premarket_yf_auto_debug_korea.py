#!/usr/bin/env python3
"""
Korea-specific Yahoo Finance updater.
Reads *_korea.txt inputs and writes *_korea.csv outputs.
"""
from __future__ import annotations

import re
import time
from pathlib import Path
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
import contextlib, io, logging, warnings
import pandas as pd
import yfinance as yf

TZ_KST = ZoneInfo("Asia/Seoul")
TZ_ET = ZoneInfo("America/New_York")

GROUP_FILES = [
    "kr_manual_conviction.txt",
    "macro_watch_yahoo_korea.txt",
    "kr_core_liquid.txt",
    "kr_tactical_leverage.txt",
    "kr_top_groups_auto_mixed.txt",
    # fallbacks
    "finviz_manual_korea.txt",
    "tickers_core_korea.txt",
    "tickers_leverage2x_korea.txt",
]

SKIPLIST_PATH = Path(".yahoo_skiplist_korea.txt")
_ALLOWED = re.compile(r"[^A-Z0-9\.\-\=\^]+")
warnings.filterwarnings("ignore")
for name in ("yfinance", "urllib3", "requests"):
    logging.getLogger(name).setLevel(logging.CRITICAL)

try:
    import yfinance.utils as _yfu
    def _noop(*args, **kwargs):
        return None
    if hasattr(_yfu, "print_once"):
        _yfu.print_once = _noop
except Exception:
    pass

@contextlib.contextmanager
def _suppress_all_output():
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
        yield

def load_skiplist() -> set[str]:
    if not SKIPLIST_PATH.exists():
        return set()
    return {ln.strip().upper() for ln in SKIPLIST_PATH.read_text(encoding='utf-8', errors='ignore').splitlines() if ln.strip() and not ln.startswith('#')}

def add_skiplist(raw: str) -> None:
    raw = raw.strip().upper()
    if raw:
        with SKIPLIST_PATH.open('a', encoding='utf-8') as f:
            f.write(raw + "\n")

def looks_like_yahoo_symbol(t: str) -> bool:
    if not t:
        return False
    return bool(re.fullmatch(r"\d{6}(\.(KS|KQ))?", t)) or t.startswith('^') or '=' in t or '.' in t

def load_tickers(path: str) -> list[str]:
    p = Path(path)
    if not p.exists():
        return []
    out, seen = [], set()
    for line in p.read_text(encoding='utf-8', errors='ignore').splitlines():
        s = line.strip()
        if not s or s.startswith('#'):
            continue
        if '#' in s:
            s = s.split('#',1)[0].strip()
        for tok in re.split(r"[\s,;]+", s):
            tok = _ALLOWED.sub('', tok.strip().upper())
            if not tok:
                continue
            if not looks_like_yahoo_symbol(tok):
                continue
            if re.fullmatch(r"\d{6}", tok):
                tok = tok + ".KS"
            if tok not in seen:
                seen.add(tok)
                out.append(tok)
    return out

def load_grouped_tickers(files: list[str]) -> dict[str, list[str]]:
    out = {}
    for fname in files:
        p = Path(fname)
        if not p.exists():
            continue
        ticks = load_tickers(str(p))
        if ticks:
            out[p.stem] = ticks
    return out

def is_kr_symbol(sym: str) -> bool:
    return bool(re.fullmatch(r"\d{6}(\.(KS|KQ))?", sym)) or sym.endswith('.KS') or sym.endswith('.KQ')

def resolve_yahoo_symbol(raw: str) -> str:
    t = raw.strip().upper()
    if re.fullmatch(r"\d{6}", t):
        return t + ".KS"
    return t

def fetch_last_price_once(raw_ticker: str):
    sym = resolve_yahoo_symbol(raw_ticker)
    if not sym:
        return None, sym, '', None, '', None, 'invalid'
    interval, period = ('5m', '5d') if is_kr_symbol(sym) else ('1m', '2d')
    try:
        with _suppress_all_output():
            tk = yf.Ticker(sym)
            df = tk.history(period=period, interval=interval, prepost=True, auto_adjust=False)
    except Exception as e:
        return None, sym, '', None, interval, None, f"{type(e).__name__}: {e}"
    if df is None or df.empty or 'Close' not in df.columns:
        return None, sym, '', None, interval, None, 'no_data'
    close = df['Close'].dropna()
    if close.empty:
        return None, sym, '', None, interval, None, 'no_data'
    ts = df.index[-1]
    if getattr(ts, 'tzinfo', None) is None:
        ts = ts.tz_localize('UTC').tz_convert(TZ_ET)
    else:
        ts = ts.tz_convert(TZ_ET)
    px = float(close.iloc[-1])
    if not (px > 0):
        return None, sym, '', None, interval, None, 'no_data'
    age = datetime.now(TZ_ET) - ts.to_pydatetime()
    age_min = int(age.total_seconds() / 60)
    if age > timedelta(days=10):
        return None, sym, '', ts.to_pydatetime(), interval, age_min, f"stale({age.days}d)"
    return px, sym, f"yf_close({interval})", ts.to_pydatetime(), interval, age_min, ''

def main():
    grouped = load_grouped_tickers(GROUP_FILES)
    if not grouped and Path('tickers_korea.txt').exists():
        grouped = {'tickers_korea': load_tickers('tickers_korea.txt')}
    if not grouped:
        raise SystemExit('No Korea tickers found. Create kr_*.txt or tickers_korea.txt first.')
    skip = load_skiplist()
    all_tickers, seen = [], set()
    for group_name, grp in grouped.items():
        for t in grp:
            if t not in seen:
                seen.add(t)
                all_tickers.append((group_name, t))
    now_kst = datetime.now(TZ_KST).strftime('%Y-%m-%d %H:%M:%S KST')
    print(f"Mode: korea_last_price | saved_at_kr={now_kst}")
    prices, meta = {}, {}
    total = len(all_tickers)
    for i, (group_name, raw) in enumerate(all_tickers, 1):
        raw_u = raw.strip().upper()
        if raw_u in skip:
            meta[raw] = {"group": group_name, "yahoo_symbol": '', "src": '', "ts_et": '', "interval": '', "age_min": '', "error": 'skipped(no_data_cached)'}
            continue
        px, sym, src, ts_et, interval, age_min, err = fetch_last_price_once(raw)
        if px is not None:
            prices[raw] = float(px)
            meta[raw] = {"group": group_name, "yahoo_symbol": sym, "src": src, "ts_et": ts_et.isoformat() if ts_et else '', "interval": interval, "age_min": age_min if age_min is not None else '', "error": ''}
        else:
            if err in ('no_data', 'invalid') or err.startswith('HTTPError') or '404' in err:
                add_skiplist(raw_u); skip.add(raw_u)
            meta[raw] = {"group": group_name, "yahoo_symbol": sym, "src": src, "ts_et": ts_et.isoformat() if ts_et else '', "interval": interval, "age_min": age_min if age_min is not None else '', "error": err or 'no_data'}
        time.sleep(0.03)
        if i in (1, total) or i % 25 == 0:
            print(f"... {i}/{total} processed")
    with open('premarket_auto_korea.csv', 'w', encoding='utf-8', newline='') as f:
        f.write(f"# saved_at_kr,{now_kst}\n")
        f.write('# mode,korea_last_price\n')
        f.write('# provider,yahoo(yfinance)\n')
        for group_name, tickers in grouped.items():
            f.write(f"# group: {group_name}\n")
            f.write('ticker,premarket\n')
            for t in tickers:
                val = prices.get(t)
                f.write(f"{t},{'' if val is None else round(val,2)}\n")
    with open('premarket_auto_debug_korea.csv', 'w', encoding='utf-8', newline='') as f:
        f.write(f"# saved_at_kr,{now_kst}\n")
        f.write('# mode,korea_last_price\n')
        f.write('# provider,yahoo(yfinance)\n')
        f.write('group,ticker,yahoo_symbol,premarket,src,yf_ts_et,interval,age_min,error\n')
        for group_name, tickers in grouped.items():
            for t in tickers:
                info = meta.get(t, {"group": group_name, "error": 'not_processed'})
                val = prices.get(t)
                f.write(f"{group_name},{t},{info.get('yahoo_symbol','')},{'' if val is None else round(val,2)},{info.get('src','')},{info.get('ts_et','')},{info.get('interval','')},{info.get('age_min','')},{info.get('error','')}\n")
    print(f"Saved {Path('premarket_auto_korea.csv').resolve()} and {Path('premarket_auto_debug_korea.csv').resolve()}")

if __name__ == '__main__':
    main()
