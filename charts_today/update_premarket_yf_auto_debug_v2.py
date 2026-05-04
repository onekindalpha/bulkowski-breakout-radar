#!/usr/bin/env python3
"""
update_premarket_yf_auto_debug_v2.py

Yahoo Finance 기반 premarket 자동 갱신 스크립트.

Patch:
- Standard files are still used
- Any generated finviz_top_groups_auto*.txt file is automatically included
"""
import re
import time
from pathlib import Path
from datetime import datetime, timedelta, time as dtime
from zoneinfo import ZoneInfo

import contextlib
import io
import logging
import warnings

import pandas as pd
import yfinance as yf

TZ_KST = ZoneInfo("Asia/Seoul")
TZ_ET = ZoneInfo("America/New_York")

PRE_START = dtime(4, 0)
PRE_END   = dtime(9, 30)
REGULAR_START = dtime(9, 30)

STANDARD_GROUP_FILES = [
    "finviz_manual.txt",
    "macro_watch_yahoo.txt",
    "tickers_core.txt",
    "tickers_leverage2x.txt",
    "tickers_leverage_global.txt",
    "sp69_tickers_only.txt",
]
GROUP_GLOBS = [
    "finviz_top_groups_auto*.txt",
]

SKIPLIST_PATH = Path(".yahoo_skiplist.txt")

ALIASES = {
    "WTI": "CL=F",
    "CRUDE": "CL=F",
    "CRUDEOIL": "CL=F",
    "OIL": "CL=F",
    "BRENT": "BZ=F",
    "GAS": "NG=F",
    "NATGAS": "NG=F",
    "NATURALGAS": "NG=F",
    "GOLD": "GC=F",
    "SILVER": "SI=F",
    "COPPER": "HG=F",
    "DXY": "DX-Y.NYB",
    "USDIDX": "DX-Y.NYB",
    "VIX": "^VIX",
    "TNX": "^TNX",
    "GASOLINE": "RB=F",
    "RBOB": "RB=F",
    "RB": "RB=F",
    "SPX": "^GSPC",
    "SP500": "^GSPC",
    "NASDAQ": "^IXIC",
    "NDX": "^NDX",
}

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

def resolve_group_files() -> list[str]:
    here = Path(".")
    files = [f for f in STANDARD_GROUP_FILES if (here / f).exists()]
    extras = []
    for pat in GROUP_GLOBS:
        for p in sorted(here.glob(pat), key=lambda x: x.name):
            if p.suffix.lower() != ".txt":
                continue
            if p.name not in files and p.name not in extras:
                extras.append(p.name)
    return files + extras

def load_skiplist() -> set[str]:
    if not SKIPLIST_PATH.exists():
        return set()
    out = set()
    for line in SKIPLIST_PATH.read_text(encoding="utf-8", errors="ignore").splitlines():
        s = line.strip().upper()
        if s and not s.startswith("#"):
            out.add(s)
    return out

def add_skiplist(raw: str) -> None:
    raw = raw.strip().upper()
    if not raw:
        return
    with SKIPLIST_PATH.open("a", encoding="utf-8") as f:
        f.write(raw + "\n")

def load_tickers(path: str, mode: str = "default") -> list[str]:
    p = Path(path)
    if not p.exists():
        return []
    out, seen = [], set()
    lines = p.read_text(encoding="utf-8", errors="ignore").splitlines()
    for line in lines:
        s = line.strip()
        if not s or s.startswith("#"):
            continue
        raw_tokens = re.split(r"[\s,;]+", s)
        for tok in raw_tokens:
            tok = tok.strip().upper()
            if not tok or tok.startswith("#"):
                continue
            tok = _ALLOWED.sub("", tok)
            if not tok:
                continue
            if tok == "-" or tok == "--":
                continue
            if tok.isdigit() and len(tok) < 6:
                continue
            if mode == "macro":
                if tok not in ALIASES and not looks_like_yahoo_symbol(tok):
                    continue
            if tok not in seen:
                seen.add(tok)
                out.append(tok)
    return out

def load_grouped_tickers(group_files: list[str]) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for fname in group_files:
        p = Path(fname)
        if not p.exists():
            continue
        mode = "macro" if p.name == "macro_watch_yahoo.txt" else "default"
        out[p.stem] = load_tickers(str(p), mode=mode)
    return out

def looks_like_yahoo_symbol(t: str) -> bool:
    if not t:
        return False
    if t.startswith("^"):
        return True
    if "=" in t:
        return True
    if "." in t:
        return True
    if re.fullmatch(r"\d{6}(\.(KS|KQ))?", t):
        return True
    return False

def resolve_yahoo_candidates(raw: str) -> list[str]:
    t = raw.strip().upper()
    t = t.lstrip('$')
    if ':' in t:
        t = t.split(':')[-1]
    if t in ALIASES:
        return [ALIASES[t]]
    if re.fullmatch(r"\d{6}\.(KS|KQ)", t):
        return [t]
    if re.fullmatch(r"\d{6}", t):
        return [f"{t}.KS", f"{t}.KQ", t]
    return [t]

def _to_et_index(df: pd.DataFrame) -> pd.DataFrame:
    if df is None or df.empty:
        return df
    idx = df.index
    if getattr(idx, "tz", None) is None:
        df = df.tz_localize("UTC").tz_convert(TZ_ET)
    else:
        df = df.tz_convert(TZ_ET)
    return df

def _pick_last_price_us_premarket(df_et: pd.DataFrame, now_et: datetime):
    if df_et is None or df_et.empty or "Close" not in df_et.columns:
        return None, None
    today = now_et.date()
    today_data = df_et[df_et.index.date == today]
    if today_data.empty:
        last_ts = df_et.index[-1].to_pydatetime()
        last_px = float(df_et["Close"].dropna().iloc[-1]) if not df_et["Close"].dropna().empty else None
        return (last_px if last_px and last_px > 0 else None), last_ts
    if now_et.time() < REGULAR_START:
        filtered = today_data[(today_data.index.time >= PRE_START) & (today_data.index.time < PRE_END)]
    else:
        filtered = today_data
    if filtered.empty:
        return None, None
    close = filtered["Close"].dropna()
    if close.empty:
        return None, None
    last_ts = filtered.index[-1].to_pydatetime()
    last_px = float(close.iloc[-1])
    return (last_px if last_px > 0 else None), last_ts

def is_kr_symbol(sym: str) -> bool:
    return sym.endswith(".KS") or sym.endswith(".KQ") or bool(re.fullmatch(r"\d{6}(\.(KS|KQ))?", sym))

def fetch_last_price_once(raw_ticker: str):
    now_et = datetime.now(TZ_ET)
    candidates = resolve_yahoo_candidates(raw_ticker)
    sym = candidates[0] if candidates else ""
    if not sym:
        return None, "", "", None, "", None, "invalid"
    if is_kr_symbol(sym):
        interval, period = "5m", "5d"
    else:
        interval, period = "1m", "2d"
    try:
        with _suppress_all_output():
            tk = yf.Ticker(sym)
            df = tk.history(period=period, interval=interval, prepost=True, auto_adjust=False)
    except Exception as e:
        return None, sym, "", None, interval, None, f"{type(e).__name__}: {e}"
    if df is None or df.empty or "Close" not in df.columns:
        return None, sym, "", None, interval, None, "no_data"
    if is_kr_symbol(sym):
        close = df["Close"].dropna()
        if close.empty:
            return None, sym, "", None, interval, None, "no_data"
        ts = df.index[-1]
        if getattr(ts, "tzinfo", None) is None:
            ts = ts.tz_localize("UTC").tz_convert(TZ_ET)
        else:
            ts = ts.tz_convert(TZ_ET)
        px = float(close.iloc[-1])
        if not (px > 0):
            return None, sym, "", None, interval, None, "no_data"
        age = datetime.now(TZ_ET) - ts.to_pydatetime()
        if age > timedelta(days=10):
            return None, sym, "", ts.to_pydatetime(), interval, None, f"stale({age.days}d)"
        age_min = int(age.total_seconds() / 60)
        return px, sym, f"yf_close({interval})", ts.to_pydatetime(), interval, age_min, ""
    df_et = _to_et_index(df)
    px, ts = _pick_last_price_us_premarket(df_et, now_et)
    if px is None or ts is None:
        return None, sym, "", None, interval, None, "no_data"
    age = datetime.now(TZ_ET) - (ts.replace(tzinfo=TZ_ET) if ts.tzinfo is None else ts)
    if age > timedelta(days=10):
        return None, sym, "", ts, interval, None, f"stale({age.days}d)"
    age_min = int(age.total_seconds() / 60)
    return float(px), sym, f"yf_close({interval})", ts, interval, age_min, ""

def main():
    group_files = resolve_group_files()
    grouped = load_grouped_tickers(group_files)
    if not grouped and Path("tickers.txt").exists():
        grouped = {"tickers": load_tickers("tickers.txt")}
    if not grouped:
        raise SystemExit("No tickers found. Provide group files or tickers.txt")

    skip = load_skiplist()
    all_tickers = []
    seen = set()
    for group_name, grp in grouped.items():
        for t in grp:
            if t not in seen:
                seen.add(t)
                all_tickers.append((group_name, t))

    now_kst = datetime.now(TZ_KST).strftime("%Y-%m-%d %H:%M:%S KST")
    now_et = datetime.now(TZ_ET)
    mode = "premarket (04:00~09:30 ET)" if now_et.time() < REGULAR_START else "last price (regular/extended)"
    print(f"Mode: {mode} | saved_at_kr={now_kst}")

    prices = {}
    meta = {}
    total = len(all_tickers)
    for i, (group_name, raw) in enumerate(all_tickers, 1):
        raw_u = raw.strip().upper()
        if raw_u in skip:
            meta[raw] = {"group": group_name, "yahoo_symbol": "", "src": "", "ts_et": "", "interval": "", "age_min": "", "error": "skipped(no_data_cached)"}
            continue
        px, sym, src, ts_et, interval, age_min, err = fetch_last_price_once(raw)
        if px is not None:
            prices[raw] = float(px)
            meta[raw] = {"group": group_name, "yahoo_symbol": sym, "src": src, "ts_et": ts_et.isoformat() if ts_et else "", "interval": interval, "age_min": age_min if age_min is not None else "", "error": ""}
        else:
            if err in ("no_data", "invalid") or err.startswith("HTTPError") or "404" in err:
                add_skiplist(raw_u)
                skip.add(raw_u)
            meta[raw] = {"group": group_name, "yahoo_symbol": sym, "src": src, "ts_et": ts_et.isoformat() if ts_et else "", "interval": interval, "age_min": age_min if age_min is not None else "", "error": err or "no_data"}

        time.sleep(0.03)
        if i in (1, total) or (i % 25 == 0):
            print(f"... {i}/{total} processed")

    with open("premarket_auto.csv", "w", newline="", encoding="utf-8") as f:
        f.write(f"# saved_at_kr,{now_kst}\n")
        f.write(f"# mode,{mode}\n")
        f.write("# provider,yahoo(yfinance)\n")
        for group_name, tickers in grouped.items():
            f.write(f"# group: {group_name}\n")
            f.write("ticker,premarket\n")
            for t in tickers:
                val = prices.get(t)
                f.write(f"{t},{'' if val is None else round(val, 2)}\n")

    with open("premarket_auto_debug.csv", "w", newline="", encoding="utf-8") as f:
        f.write(f"# saved_at_kr,{now_kst}\n")
        f.write(f"# mode,{mode}\n")
        f.write("# provider,yahoo(yfinance)\n")
        f.write("group,ticker,yahoo_symbol,premarket,src,yf_ts_et,interval,age_min,error\n")
        for group_name, tickers in grouped.items():
            for t in tickers:
                info = meta.get(t, {"group": group_name, "error": "not_processed"})
                val = prices.get(t)
                f.write(
                    f"{group_name},{t},{info.get('yahoo_symbol','')},"
                    f"{'' if val is None else round(val,2)},"
                    f"{info.get('src','')},{info.get('ts_et','')},{info.get('interval','')},"
                    f"{info.get('age_min','')},{info.get('error','')}\n"
                )

    out_path = Path("premarket_auto.csv").resolve()
    dbg_path = Path("premarket_auto_debug.csv").resolve()
    print(f"Saved {out_path} and {dbg_path}")

if __name__ == "__main__":
    main()
