#!/usr/bin/env python3
"""
build_kr_core_liquid_v3.py

Preferred:
- Build kr_core_liquid.txt from KOSPI200 + KOSDAQ150 via pykrx

Fallback:
- If pykrx fails in your environment, copy existing tickers_core_korea.txt
  into kr_core_liquid.txt so the pipeline still works.
"""
from __future__ import annotations
from datetime import date, timedelta
from pathlib import Path

try:
    from pykrx import stock
except Exception:
    stock = None

def ymd(d: date) -> str:
    return d.strftime("%Y%m%d")

def recent_dates(n: int = 40):
    today = date.today()
    return [ymd(today - timedelta(days=i)) for i in range(n)]

def _suffix_map(ds: str):
    out = {}
    if stock is None:
        return out
    for market, suffix in [("KOSPI", ".KS"), ("KOSDAQ", ".KQ")]:
        try:
            ticks = stock.get_market_ticker_list(ds, market=market)
        except Exception:
            ticks = []
        for t in ticks:
            out[str(t)] = suffix
    return out

def _index_code_by_names(ds: str):
    out = {}
    if stock is None:
        return out
    for market in ("KOSPI", "KOSDAQ"):
        try:
            codes = stock.get_index_ticker_list(ds, market=market)
        except Exception:
            codes = []
        for c in codes:
            try:
                name = str(stock.get_index_ticker_name(c))
            except Exception:
                continue
            out[name] = str(c)
    return out

def _find_working_ds():
    if stock is None:
        return ""
    for ds in recent_dates(40):
        try:
            if stock.get_market_ticker_list(ds, market="KOSPI") or stock.get_market_ticker_list(ds, market="KOSDAQ"):
                return ds
        except Exception:
            pass
    return ""

def _get_pdf(code: str):
    if stock is None:
        return []
    for ds in recent_dates(20):
        try:
            return list(stock.get_index_portfolio_deposit_file(code, ds))
        except TypeError:
            break
        except Exception:
            continue
    try:
        return list(stock.get_index_portfolio_deposit_file(code))
    except Exception:
        return []

def main():
    out = Path("kr_core_liquid.txt")
    ds = _find_working_ds()
    if ds:
        names = _index_code_by_names(ds)
        smap = _suffix_map(ds)
        target_codes = []
        for k, v in names.items():
            if "KOSPI 200" in k or k == "코스피 200":
                target_codes.append(v)
            elif "KOSDAQ 150" in k or k == "코스닥 150":
                target_codes.append(v)

        seen = set()
        tickers = []
        for code in target_codes:
            for t in _get_pdf(code):
                t = str(t)
                suffix = smap.get(t)
                if suffix:
                    sym = t + suffix
                    if sym not in seen:
                        seen.add(sym)
                        tickers.append(sym)
        if tickers:
            out.write_text("\n".join(tickers) + "\n", encoding="utf-8")
            print(f"Saved: kr_core_liquid.txt ({len(tickers)} tickers) [pykrx]")
            return

    fallback = Path("tickers_core_korea.txt")
    if fallback.exists():
        out.write_text(fallback.read_text(encoding="utf-8", errors="ignore"), encoding="utf-8")
        print("[WARN] pykrx core build failed; copied tickers_core_korea.txt -> kr_core_liquid.txt")
        return

    out.write_text("", encoding="utf-8")
    raise SystemExit("Could not build kr_core_liquid.txt and no tickers_core_korea.txt fallback exists.")

if __name__ == "__main__":
    main()
