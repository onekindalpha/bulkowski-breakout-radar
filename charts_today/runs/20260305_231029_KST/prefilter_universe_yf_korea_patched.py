#!/usr/bin/env python3
"""
prefilter_universe_yf_korea.py

Purpose
- Apply "Finviz-like" baseline filters to your txt universe BEFORE running the pipeline.
- Outputs a filtered universe file you can feed into update/scan stages.

Default filters (editable via flags):
- min_price: 5.0
- min_avg_volume_50d: 500_000
- min_rel_volume: 1.0   (proxy: last_day_volume / avg50_volume)
- require price >= SMA50 and SMA200
- min_market_cap: 300_000_000 (300M)  (skipped if unknown and --allow-unknown-mcap)

Notes
- Market cap is fetched per ticker (fast_info/info). We only fetch for tickers that pass the cheap OHLCV/SMA filters.
- If marketCap is missing for ETF/MUTUALFUND, we fall back to totalAssets (AUM) as a size proxy.
- "Relative volume" here is a *daily proxy*. Intraday relvol requires broker data or heavier per-ticker intraday pulls.

Outputs
- universe_filtered.txt (one ticker per line)
- prefilter_report.csv  (metrics + pass/fail reasons)
"""
import argparse
from pathlib import Path
from datetime import datetime
from zoneinfo import ZoneInfo
import json
import contextlib, io

import pandas as pd
import numpy as np

KST = ZoneInfo("Asia/Seoul")
MCAP_CACHE = Path(".mcap_cache_korea.json")

def now_kst():
    return datetime.now(KST).strftime("%Y-%m-%d %H:%M:%S KST")

def read_universe(path: str) -> list[str]:
    p = Path(path)
    if not p.exists():
        return []
    out = []
    for line in p.read_text(encoding="utf-8", errors="ignore").splitlines():
        s = line.strip()
        if not s or s.startswith("#"):
            continue
        if "#" in s:
            s = s.split("#", 1)[0].strip()
        # allow comma/space separated
        for tok in s.replace(",", " ").split():
            t = tok.strip().upper()
            if t:
                out.append(t)
    # dedupe keep order
    seen=set()
    uniq=[]
    for t in out:
        if t not in seen:
            seen.add(t)
            uniq.append(t)
    return uniq

def load_mcap_cache() -> dict:
    if not MCAP_CACHE.exists():
        return {}
    try:
        return json.loads(MCAP_CACHE.read_text(encoding="utf-8"))
    except Exception:
        return {}

def save_mcap_cache(cache: dict):
    try:
        MCAP_CACHE.write_text(json.dumps(cache, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:
        pass

def get_market_cap(yf, ticker: str, cache: dict) -> tuple[int | None, str]:
    """Return (market_cap_like, source).

    Sources (best-effort):
    - fast_info.market_cap
    - info.marketCap
    - info.totalAssets (ETF/MUTUALFUND AUM proxy; used when marketCap is missing)
    - shares*price (fallback approximation)
    """
    if ticker in cache:
        cv = cache[ticker]
        if isinstance(cv, dict):
            return cv.get("mcap"), cv.get("source", "cache")
        # legacy cache format: number/None
        return cv, "cache_legacy"

    mcap = None
    source = "none"
    quote_type = None
    aum = None

    try:
        tk = yf.Ticker(ticker)

        # 1) fast_info: quickest when available
        fi = getattr(tk, "fast_info", None)
        if fi:
            mcap = fi.get("market_cap") or fi.get("marketCap")
            if mcap is not None:
                source = "fast_info.market_cap"

        # 2) info.marketCap (common for stocks)
        info = None
        if mcap is None:
            info = tk.info
            quote_type = info.get("quoteType")
            mcap = info.get("marketCap")
            if mcap is not None:
                source = "info.marketCap"

        # 3) ETF/MUTUALFUND: use AUM (totalAssets) as a size proxy if marketCap is missing
        if mcap is None:
            if info is None:
                info = tk.info
                quote_type = info.get("quoteType")
            if quote_type in ("ETF", "MUTUALFUND"):
                aum = info.get("totalAssets")
                if aum is not None:
                    mcap = aum
                    source = "info.totalAssets"

        # 4) Last resort: sharesOutstanding * price
        if mcap is None:
            if info is None:
                info = tk.info
                quote_type = info.get("quoteType")
            shares = info.get("sharesOutstanding")
            price = None
            if fi:
                price = fi.get("last_price") or fi.get("lastPrice")
            if price is None:
                price = info.get("regularMarketPrice") or info.get("currentPrice")
            if shares is not None and price is not None:
                mcap = float(shares) * float(price)
                source = "shares*price"

    except Exception:
        mcap = None
        source = "error"

    # normalize numeric
    if isinstance(mcap, (int, float)) and np.isfinite(mcap):
        mcap_int = int(mcap)
    else:
        mcap_int = None
        if source not in ("error",):
            source = "none"

    # cache as dict (backward compatible loader above)
    cache[ticker] = {
        "mcap": mcap_int,
        "source": source,
        "quoteType": quote_type,
        "aum": int(aum) if isinstance(aum, (int, float)) and np.isfinite(aum) else None,
    }
    return mcap_int, source

def batch_daily_download(yf, tickers: list[str], period="1y"):
    # group_by="ticker" to get multiindex columns, then slice by ticker
    return yf.download(
        tickers=tickers,
        period=period,
        interval="1d",
        auto_adjust=False,
        group_by="ticker",
        threads=True,
        progress=False,
    )

def slice_ohlcv(df_all: pd.DataFrame, ticker: str) -> pd.DataFrame:
    # yfinance returns either:
    # 1) columns like ('AAPL','Open') ... with MultiIndex
    # 2) columns like 'Open','High' if single ticker
    if df_all is None or df_all.empty:
        return pd.DataFrame()
    if isinstance(df_all.columns, pd.MultiIndex):
        # usually level0=ticker
        if ticker in df_all.columns.get_level_values(0):
            sub = df_all[ticker].copy()
        elif ticker in df_all.columns.get_level_values(1):
            sub = df_all.xs(ticker, axis=1, level=1).copy()
        else:
            return pd.DataFrame()
        # normalize columns
        sub.columns = [str(c).title() for c in sub.columns]
        return sub
    # single ticker case
    sub = df_all.copy()
    sub.columns = [str(c).title() for c in sub.columns]
    return sub

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--universe", default="tickers_korea.txt", help="input universe file (default tickers.txt)")
    ap.add_argument("--out", default="universe_filtered_korea.txt", help="output filtered universe file")
    ap.add_argument("--report", default="prefilter_report_korea.csv", help="output report csv")
    ap.add_argument("--min-price", type=float, default=1000.0)
    ap.add_argument("--min-avgvol", type=float, default=200_000)
    ap.add_argument("--min-relvol", type=float, default=1.0)
    ap.add_argument("--min-mcap", type=float, default=300_000_000_000)
    ap.add_argument("--allow-unknown-mcap", action="store_true", default=True)
    ap.add_argument("--no-allow-unknown-mcap", action="store_false", dest="allow_unknown_mcap")
    ap.add_argument("--require-sma50", action="store_true", default=True)
    ap.add_argument("--no-require-sma50", action="store_false", dest="require_sma50")
    ap.add_argument("--require-sma200", action="store_true", default=True)
    ap.add_argument("--no-require-sma200", action="store_false", dest="require_sma200")
    ap.add_argument("--no-relvol", action="store_true", help="disable relvol filter")
    args = ap.parse_args()

    import yfinance as yf

    uni = read_universe(args.universe)
    print(f"KST_NOW: {now_kst()}")
    print(f"Universe input: {len(uni)}  ({args.universe})")

    if not uni:
        Path(args.out).write_text("", encoding="utf-8")
        pd.DataFrame().to_csv(args.report, index=False)
        print("No tickers found.")
        return

    # Batch daily download for SMA/volume/price filters
    with contextlib.redirect_stderr(io.StringIO()):
        df_all = batch_daily_download(yf, uni, period="1y")

    rows=[]
    passed=[]
    # compute cheap metrics first
    for t in uni:
        df = slice_ohlcv(df_all, t)
        if df is None or df.empty or "Close" not in df.columns:
            rows.append({"ticker": t, "status":"no_data"})
            continue
        df = df.dropna()
        if len(df) < 210:  # for SMA200
            rows.append({"ticker": t, "status":"insufficient_history", "n_days": len(df)})
            continue

        close = df["Close"]
        vol = df["Volume"] if "Volume" in df.columns else None

        last_close = float(close.iloc[-1])
        sma50 = float(close.rolling(50).mean().iloc[-1])
        sma200 = float(close.rolling(200).mean().iloc[-1])

        avgvol50 = float(vol.rolling(50).mean().iloc[-1]) if vol is not None else float("nan")
        last_vol = float(vol.iloc[-1]) if vol is not None else float("nan")
        relvol = (last_vol / avgvol50) if (np.isfinite(last_vol) and np.isfinite(avgvol50) and avgvol50>0) else float("nan")

        reasons=[]
        if last_close < args.min_price:
            reasons.append("price<min")
        if np.isfinite(avgvol50) and avgvol50 < args.min_avgvol:
            reasons.append("avgvol50<min")
        if (not args.no_relvol) and np.isfinite(relvol) and relvol < args.min_relvol:
            reasons.append("relvol<min")
        if args.require_sma50 and last_close < sma50:
            reasons.append("below_sma50")
        if args.require_sma200 and last_close < sma200:
            reasons.append("below_sma200")

        ok = (len(reasons)==0)

        rows.append({
            "ticker": t,
            "status": "pass_pre_mcap" if ok else "fail",
            "last_close": last_close,
            "sma50": sma50,
            "sma200": sma200,
            "avgvol50": avgvol50,
            "last_vol": last_vol,
            "relvol_proxy": relvol,
            "reasons": ",".join(reasons),
        })
        if ok:
            passed.append(t)

    # market cap filter on the reduced set
    cache = load_mcap_cache()
    final=[]
    for t in passed:
        mcap, mcap_src = get_market_cap(yf, t, cache)
        # allow ETFs/unknown
        if mcap is None:
            if args.allow_unknown_mcap:
                final.append(t)
                # annotate row
                for r in rows:
                    if r.get("ticker")==t:
                        r["market_cap"]=None
                        r["status"]="PASS"
                        r["mcap_source"]=mcap_src
                        cv = cache.get(t)
                        if isinstance(cv, dict):
                            r["quote_type"]=cv.get("quoteType")
                            r["aum_totalAssets"]=cv.get("aum")
                        break
            else:
                for r in rows:
                    if r.get("ticker")==t:
                        r["market_cap"]=None
                        r["status"]="FAIL_MCAP_UNKNOWN"
                        r["mcap_source"]=mcap_src
                        cv = cache.get(t)
                        if isinstance(cv, dict):
                            r["quote_type"]=cv.get("quoteType")
                            r["aum_totalAssets"]=cv.get("aum")
                        r["reasons"]=(r.get("reasons","") + ("," if r.get("reasons") else "") + "mcap_unknown").strip(",")
                        break
        else:
            if mcap >= args.min_mcap:
                final.append(t)
                for r in rows:
                    if r.get("ticker")==t:
                        r["market_cap"]=mcap
                        r["status"]="PASS"
                        r["mcap_source"]=mcap_src
                        cv = cache.get(t)
                        if isinstance(cv, dict):
                            r["quote_type"]=cv.get("quoteType")
                            r["aum_totalAssets"]=cv.get("aum")
                        break
            else:
                for r in rows:
                    if r.get("ticker")==t:
                        r["market_cap"]=mcap
                        r["status"]="FAIL_MCAP_LOW"
                        r["mcap_source"]=mcap_src
                        cv = cache.get(t)
                        if isinstance(cv, dict):
                            r["quote_type"]=cv.get("quoteType")
                            r["aum_totalAssets"]=cv.get("aum")
                        r["reasons"]=(r.get("reasons","") + ("," if r.get("reasons") else "") + "mcap<min").strip(",")
                        break

    save_mcap_cache(cache)

    # write outputs
    Path(args.out).write_text("\n".join(final) + ("\n" if final else ""), encoding="utf-8")
    pd.DataFrame(rows).to_csv(args.report, index=False)

    print(f"Prefilter PASS: {len(final)} / {len(uni)}")
    print(f"Saved: {args.out}")
    print(f"Saved: {args.report}")

if __name__ == "__main__":
    main()