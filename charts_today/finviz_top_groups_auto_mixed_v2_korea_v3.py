
#!/usr/bin/env python3
"""
finviz_top_groups_auto_mixed_v2_korea_v3.py

Korea strong-group / strong-stock overlay builder.

Order:
1) Try pykrx KRX sector-like indices (best)
2) Fallback to yfinance momentum ranking over Korea seed tickers (robust)
3) Emergency fallback: write top seed tickers so overlay is never silently empty

Outputs:
- kr_top_groups_auto_mixed.txt
- kr_top_groups_mixed_groups.csv
- kr_top_groups_mixed_members.csv
"""
from __future__ import annotations
import argparse
import re
import time
from datetime import date, timedelta
from pathlib import Path

import pandas as pd

try:
    import yfinance as yf
except Exception:
    yf = None

try:
    from pykrx import stock
except Exception:
    stock = None


def ymd(d: date) -> str:
    return d.strftime("%Y%m%d")


def recent_dates(n: int = 40) -> list[str]:
    today = date.today()
    return [ymd(today - timedelta(days=i)) for i in range(n)]


def rank_desc(series: pd.Series) -> pd.Series:
    s = pd.to_numeric(series, errors="coerce")
    n = max(len(s.dropna()), 1)
    return (1.0 - s.rank(method="average", ascending=False, pct=True) + (1.0 / n)).fillna(0.0)


def _safe_print_group(i: int, name: str, score, chg, w, m):
    def _fmt(x):
        return "NA" if pd.isna(x) else f"{float(x):.2f}%"
    print(f"[group] #{i} {name}  score={float(score):.4f}  chg={_fmt(chg)}  w={_fmt(w)}  m={_fmt(m)}")


def _pykrx_working_market_tickers(market: str, ds_override: str | None = None) -> tuple[str, list[str]]:
    if stock is None:
        return "", []
    dates = [ds_override] if ds_override else recent_dates(40)
    for ds in dates:
        if not ds:
            continue
        try:
            ticks = stock.get_market_ticker_list(ds, market=market)
            if ticks:
                return ds, list(ticks)
        except Exception:
            pass
    return "", []


def _pykrx_suffix_map(ds: str) -> dict[str, str]:
    out = {}
    if stock is None or not ds:
        return out
    for market, suffix in [("KOSPI", ".KS"), ("KOSDAQ", ".KQ")]:
        try:
            ticks = stock.get_market_ticker_list(ds, market=market)
        except Exception:
            ticks = []
        for t in ticks:
            out[str(t)] = suffix
    return out


def _pykrx_index_codes_with_names(ds: str, market: str) -> list[tuple[str, str]]:
    if stock is None or not ds:
        return []
    try:
        codes = stock.get_index_ticker_list(ds, market=market)
    except Exception:
        return []
    out = []
    for c in codes:
        try:
            name = str(stock.get_index_ticker_name(c))
        except Exception:
            continue
        out.append((str(c), name))
    return out


def _is_sector_like(name: str) -> bool:
    bad = [
        "코스피", "코스닥", "200", "150", "100", "50",
        "대형주", "중형주", "소형주", "스타", "테마",
        "KRX 300", "선물", "인버스", "레버리지"
    ]
    return not any(b in name for b in bad)


def _pykrx_period_return(code: str, end_ds: str, days_back: int) -> float | None:
    if stock is None:
        return None
    end_d = pd.to_datetime(end_ds).date()
    start_ds = ymd(end_d - timedelta(days=days_back * 2))
    try:
        df = stock.get_index_ohlcv(start_ds, end_ds, code)
        if df is None or df.empty:
            return None
        col = "종가" if "종가" in df.columns else df.columns[-1]
        s = pd.to_numeric(df[col], errors="coerce").dropna()
        if len(s) < 2:
            return None
        return float((s.iloc[-1] / s.iloc[0] - 1.0) * 100.0)
    except Exception:
        return None


def _pykrx_get_pdf(code: str) -> list[str]:
    if stock is None:
        return []
    for ds in recent_dates(15):
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


def _read_seed_tickers(paths: list[str]) -> list[str]:
    ticker_re = re.compile(r"^[0-9]{6}\.(KS|KQ)$")
    out, seen = [], set()
    for path in paths:
        p = Path(path)
        if not p.exists():
            continue
        for line in p.read_text(encoding="utf-8", errors="ignore").splitlines():
            s = line.strip()
            if not s or s.startswith("#"):
                continue
            if "#" in s:
                s = s.split("#", 1)[0].strip()
            for tok in re.split(r"[\s,;]+", s):
                t = tok.strip().upper()
                if not t:
                    continue
                if re.fullmatch(r"\d{6}", t):
                    t = t + ".KS"
                if ticker_re.match(t) and t not in seen:
                    seen.add(t)
                    out.append(t)
    return out


def _extract_close_series(df):
    if df is None or len(df) == 0:
        return pd.Series(dtype=float)
    # yfinance sometimes returns MultiIndex columns even for a single ticker
    cols = df.columns
    if isinstance(cols, pd.MultiIndex):
        for key in cols:
            if (isinstance(key, tuple) and len(key) >= 1 and str(key[0]).lower() == "close") or str(key).lower() == "close":
                s = df[key]
                return pd.to_numeric(s, errors="coerce").dropna()
        # fallback: first column with "close" anywhere
        for key in cols:
            if "close" in " ".join(map(str, key)).lower():
                s = df[key]
                return pd.to_numeric(s, errors="coerce").dropna()
        return pd.Series(dtype=float)
    else:
        for c in cols:
            if str(c).lower() == "close":
                return pd.to_numeric(df[c], errors="coerce").dropna()
        for c in cols:
            if "close" in str(c).lower():
                return pd.to_numeric(df[c], errors="coerce").dropna()
        return pd.Series(dtype=float)


def _fetch_one_symbol(sym: str) -> tuple[float|None, float|None, float|None]:
    if yf is None:
        return None, None, None
    # method 1: download
    try:
        df = yf.download(sym, period="3mo", interval="1d", progress=False, auto_adjust=False, threads=False)
        s = _extract_close_series(df)
        if len(s) >= 22:
            chg = float((s.iloc[-1] / s.iloc[-2] - 1.0) * 100.0) if len(s) >= 2 else None
            w = float((s.iloc[-1] / s.iloc[-6] - 1.0) * 100.0) if len(s) >= 6 else None
            m = float((s.iloc[-1] / s.iloc[-22] - 1.0) * 100.0)
            return chg, w, m
    except Exception:
        pass
    # method 2: Ticker.history
    try:
        tk = yf.Ticker(sym)
        df = tk.history(period="3mo", interval="1d", auto_adjust=False)
        s = _extract_close_series(df)
        if len(s) >= 22:
            chg = float((s.iloc[-1] / s.iloc[-2] - 1.0) * 100.0) if len(s) >= 2 else None
            w = float((s.iloc[-1] / s.iloc[-6] - 1.0) * 100.0) if len(s) >= 6 else None
            m = float((s.iloc[-1] / s.iloc[-22] - 1.0) * 100.0)
            return chg, w, m
    except Exception:
        pass
    return None, None, None


def _fetch_momentum_rows(tickers: list[str]) -> pd.DataFrame:
    rows = []
    for i, sym in enumerate(tickers, 1):
        chg, w, m = _fetch_one_symbol(sym)
        if chg is None and w is None and m is None:
            continue
        rows.append({"ticker": sym, "change": chg, "perf_week": w, "perf_month": m})
        if i % 20 == 0:
            print(f"... fallback momentum {i}/{len(tickers)}")
        time.sleep(0.03)
    return pd.DataFrame(rows)


def build_overlay_via_pykrx(ds_override: str | None, top_sectors: int, max_per_group: int, wt_change: float, wt_w: float, wt_m: float):
    ds, _ = _pykrx_working_market_tickers("KOSPI", ds_override)
    ds2, _ = _pykrx_working_market_tickers("KOSDAQ", ds_override)
    ref_ds = ds or ds2
    if not ref_ds:
        return False

    smap = _pykrx_suffix_map(ref_ds)
    idx = []
    for market in ("KOSPI", "KOSDAQ"):
        idx.extend(_pykrx_index_codes_with_names(ref_ds, market))
    idx = [(c, n) for c, n in idx if _is_sector_like(n)]

    rows = []
    for code, name in idx:
        chg = _pykrx_period_return(code, ref_ds, 1)
        pw = _pykrx_period_return(code, ref_ds, 7)
        pm = _pykrx_period_return(code, ref_ds, 30)
        if chg is None and pw is None and pm is None:
            continue
        rows.append({"index_code": code, "group_name": name, "change": chg, "perf_week": pw, "perf_month": pm})

    if not rows:
        return False

    df = pd.DataFrame(rows)
    df["rank_change"] = rank_desc(df["change"])
    df["rank_perf_week"] = rank_desc(df["perf_week"])
    df["rank_perf_month"] = rank_desc(df["perf_month"])
    tot = wt_change + wt_w + wt_m
    df["score"] = (wt_change*df["rank_change"] + wt_w*df["rank_perf_week"] + wt_m*df["rank_perf_month"]) / tot
    df = df.sort_values(["score", "change", "perf_week", "perf_month", "group_name"],
                        ascending=[False, False, False, False, True]).reset_index(drop=True)
    selected = df.head(top_sectors).copy()

    members_rows = []
    final_tickers = []
    seen = set()
    for i, r in selected.iterrows():
        code, name = str(r["index_code"]), str(r["group_name"])
        _safe_print_group(i + 1, name, r["score"], r["change"], r["perf_week"], r["perf_month"])
        pdf = _pykrx_get_pdf(code)
        count = 0
        for t in pdf:
            t = str(t)
            suffix = smap.get(t)
            if not suffix:
                continue
            sym = t + suffix
            members_rows.append({"group_name": name, "index_code": code, "ticker": sym})
            if sym not in seen:
                seen.add(sym)
                final_tickers.append(sym)
                count += 1
            if count >= max_per_group:
                break

    Path("kr_top_groups_auto_mixed.txt").write_text(
        "\n".join(final_tickers) + ("\n" if final_tickers else ""),
        encoding="utf-8"
    )
    selected.to_csv("kr_top_groups_mixed_groups.csv", index=False)
    pd.DataFrame(members_rows).to_csv("kr_top_groups_mixed_members.csv", index=False)
    print(f"Saved: kr_top_groups_auto_mixed.txt ({len(final_tickers)} tickers)")
    print(f"Saved: kr_top_groups_mixed_groups.csv ({len(selected)} groups)")
    print(f"Saved: kr_top_groups_mixed_members.csv ({len(members_rows)} rows before final dedupe)")
    return True


def build_overlay_fallback(top_n: int, seed_paths: list[str], wt_change: float, wt_w: float, wt_m: float):
    seeds = _read_seed_tickers(seed_paths)
    if not seeds:
        Path("kr_top_groups_auto_mixed.txt").write_text("", encoding="utf-8")
        pd.DataFrame(columns=["group_name","score","change","perf_week","perf_month"]).to_csv("kr_top_groups_mixed_groups.csv", index=False)
        pd.DataFrame(columns=["group_name","ticker"]).to_csv("kr_top_groups_mixed_members.csv", index=False)
        raise SystemExit("No seed tickers found for fallback overlay.")

    df = _fetch_momentum_rows(seeds)
    if not df.empty:
        df["rank_change"] = rank_desc(df["change"])
        df["rank_perf_week"] = rank_desc(df["perf_week"])
        df["rank_perf_month"] = rank_desc(df["perf_month"])
        tot = wt_change + wt_w + wt_m
        df["score"] = (wt_change*df["rank_change"] + wt_w*df["rank_perf_week"] + wt_m*df["rank_perf_month"]) / tot
        df = df.sort_values(["score", "change", "perf_week", "perf_month", "ticker"],
                            ascending=[False, False, False, False, True]).reset_index(drop=True)
        selected = df.head(top_n).copy()
        selected["group_name"] = "FALLBACK_MOMENTUM"
        Path("kr_top_groups_auto_mixed.txt").write_text(
            "\n".join(selected["ticker"].tolist()) + ("\n" if len(selected) else ""),
            encoding="utf-8"
        )
        selected[["group_name","score","change","perf_week","perf_month"]].to_csv("kr_top_groups_mixed_groups.csv", index=False)
        selected[["group_name","ticker"]].to_csv("kr_top_groups_mixed_members.csv", index=False)
        print("[WARN] pykrx sector overlay failed; used fallback mixed-momentum stock overlay instead.")
        print(f"Saved: kr_top_groups_auto_mixed.txt ({len(selected)} tickers)")
        print(f"Saved: kr_top_groups_mixed_groups.csv ({len(selected)} rows)")
        print(f"Saved: kr_top_groups_mixed_members.csv ({len(selected)} rows)")
        return

    # emergency fallback: write first N seeds to avoid empty overlay
    selected = pd.DataFrame({"group_name": ["FALLBACK_SEEDS"] * min(top_n, len(seeds)), "ticker": seeds[:top_n]})
    Path("kr_top_groups_auto_mixed.txt").write_text(
        "\n".join(selected["ticker"].tolist()) + ("\n" if len(selected) else ""),
        encoding="utf-8"
    )
    pd.DataFrame([{"group_name":"FALLBACK_SEEDS","score":"","change":"","perf_week":"","perf_month":""}]).to_csv("kr_top_groups_mixed_groups.csv", index=False)
    selected.to_csv("kr_top_groups_mixed_members.csv", index=False)
    print("[WARN] pykrx sector overlay failed AND yfinance fallback returned no rows; wrote seed fallback overlay instead.")
    print(f"Saved: kr_top_groups_auto_mixed.txt ({len(selected)} tickers) [seed fallback]")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", default=None, help="optional YYYYMMDD override for pykrx queries")
    ap.add_argument("--top-sectors", type=int, default=5)
    ap.add_argument("--max-per-group", type=int, default=15)
    ap.add_argument("--wt-change", type=float, default=0.5)
    ap.add_argument("--wt-perf-week", type=float, default=0.3)
    ap.add_argument("--wt-perf-month", type=float, default=0.2)
    ap.add_argument("--fallback-top", type=int, default=20, help="fallback stock overlay count if sector build fails")
    args = ap.parse_args()

    ok = build_overlay_via_pykrx(
        ds_override=args.date,
        top_sectors=args.top_sectors,
        max_per_group=args.max_per_group,
        wt_change=args.wt_change,
        wt_w=args.wt_perf_week,
        wt_m=args.wt_perf_month,
    )
    if ok:
        return

    seed_paths = [
        "kr_core_liquid.txt",
        "tickers_core_korea.txt",
        "kr_manual_conviction.txt",
        "finviz_manual_korea.txt",
        "kr_tactical_leverage.txt",
        "tickers_leverage2x_korea.txt",
    ]
    build_overlay_fallback(
        top_n=args.fallback_top,
        seed_paths=seed_paths,
        wt_change=args.wt_change,
        wt_w=args.wt_perf_week,
        wt_m=args.wt_perf_month,
    )


if __name__ == "__main__":
    main()
