
#!/usr/bin/env python3
"""
overseas_lynch_one_shot.py

Peter Lynch-style screen for a small overseas universe.
Default tickers:
- MO
- UVV
- FRT
- O

Data source:
- yfinance

Outputs:
- <prefix>_raw.csv
- <prefix>_filtered.csv
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from typing import Dict, Iterable, List, Optional

import numpy as np
import pandas as pd
import yfinance as yf


UNIVERSE: Dict[str, Dict[str, str]] = {
    "MO": {"name": "Altria Group", "group": "담배", "verdict": "메인"},
    "UVV": {"name": "Universal Corp.", "group": "담배", "verdict": "메인"},
    "FRT": {"name": "Federal Realty", "group": "REIT/부동산임대", "verdict": "보정필요"},
    "O": {"name": "Realty Income", "group": "REIT/부동산임대", "verdict": "보정필요"},
}


def log(msg: str) -> None:
    print(msg, flush=True)


def safe_float(x) -> float:
    try:
        if x is None:
            return np.nan
        if isinstance(x, str) and not x.strip():
            return np.nan
        return float(x)
    except Exception:
        return np.nan


def first_non_nan(values: Iterable[object]) -> float:
    for v in values:
        fv = safe_float(v)
        if not pd.isna(fv):
            return fv
    return np.nan


def growth_pct(new: float, old: float) -> float:
    if pd.isna(new) or pd.isna(old) or old == 0:
        return np.nan
    try:
        return (new / old - 1.0) * 100.0
    except Exception:
        return np.nan


def cagr_pct(end: float, start: float, years: int) -> float:
    if years <= 0 or pd.isna(end) or pd.isna(start) or start <= 0 or end <= 0:
        return np.nan
    try:
        return ((end / start) ** (1.0 / years) - 1.0) * 100.0
    except Exception:
        return np.nan


def normalize_statement(df: Optional[pd.DataFrame]) -> pd.DataFrame:
    if df is None or df.empty:
        return pd.DataFrame()
    out = df.copy()
    out.columns = [pd.to_datetime(c) for c in out.columns]
    out = out.sort_index(axis=1)
    out.index = [str(i).strip() for i in out.index]
    return out


def row_first(df: pd.DataFrame, candidates: Iterable[str]) -> Optional[pd.Series]:
    if df.empty:
        return None

    lower_map = {str(idx).strip().lower(): idx for idx in df.index}
    for cand in candidates:
        key = cand.strip().lower()
        if key in lower_map:
            return df.loc[lower_map[key]]

    idx_lower = [str(idx).strip().lower() for idx in df.index]
    for cand in candidates:
        key = cand.strip().lower()
        for orig, low in zip(df.index, idx_lower):
            if key in low:
                return df.loc[orig]
    return None


def value_at_year(row: Optional[pd.Series], year: int) -> float:
    if row is None or len(row) == 0:
        return np.nan
    s = row.copy()
    try:
        s.index = [pd.to_datetime(i) for i in s.index]
    except Exception:
        return np.nan
    matches = [v for dt, v in s.items() if getattr(dt, "year", None) == year]
    if not matches:
        return np.nan
    return safe_float(matches[-1])


def dps_for_year(dividends: pd.Series, year: int) -> float:
    if dividends is None or len(dividends) == 0:
        return np.nan
    s = dividends.copy()
    try:
        s.index = pd.to_datetime(s.index)
    except Exception:
        return np.nan
    vals = s[s.index.year == year]
    if len(vals) == 0:
        return np.nan
    return safe_float(vals.sum())


def latest_close(hist: pd.DataFrame) -> float:
    if hist is None or hist.empty or "Close" not in hist.columns:
        return np.nan
    s = hist["Close"].dropna()
    return safe_float(s.iloc[-1]) if len(s) else np.nan


def pick_shares(tk: yf.Ticker, bs: pd.DataFrame, price: float) -> float:
    shares = np.nan
    for attr_name in ("fast_info", "info"):
        try:
            obj = getattr(tk, attr_name)
            if isinstance(obj, dict):
                shares = first_non_nan([
                    obj.get("shares"),
                    obj.get("sharesOutstanding"),
                    obj.get("impliedSharesOutstanding"),
                ])
                if not pd.isna(shares):
                    return shares
        except Exception:
            pass

    row = row_first(bs, [
        "Ordinary Shares Number",
        "Share Issued",
        "Common Stock Shares Outstanding",
        "Shares Outstanding",
    ])
    if row is not None:
        vals = row.dropna()
        if len(vals):
            shares = safe_float(vals.iloc[-1])
            if not pd.isna(shares):
                return shares

    try:
        fi = tk.fast_info
        market_cap = first_non_nan([fi.get("marketCap")])
        if not pd.isna(market_cap) and not pd.isna(price) and price > 0:
            return market_cap / price
    except Exception:
        pass

    return np.nan


def fcf_latest(cf: pd.DataFrame) -> float:
    op = row_first(cf, [
        "Operating Cash Flow",
        "Cash Flow From Continuing Operating Activities",
        "Net Cash Provided By Operating Activities",
        "Net Cash Provided by Operating Activities",
    ])
    capex = row_first(cf, [
        "Capital Expenditure",
        "Capital Expenditures",
        "Purchase Of PPE",
        "Purchase of Property Plant and Equipment",
        "Investments In Property Plant And Equipment",
    ])
    op_v = safe_float(op.dropna().iloc[-1]) if op is not None and len(op.dropna()) else np.nan
    cap_v = safe_float(capex.dropna().iloc[-1]) if capex is not None and len(capex.dropna()) else np.nan
    if pd.isna(op_v) and pd.isna(cap_v):
        return np.nan
    if pd.isna(cap_v):
        return op_v
    if pd.isna(op_v):
        return np.nan
    return op_v - abs(cap_v)


def cash_like_latest(bs: pd.DataFrame) -> float:
    cash = row_first(bs, [
        "Cash And Cash Equivalents",
        "Cash Cash Equivalents And Short Term Investments",
        "Cash",
        "Cash Equivalents",
    ])
    sti = row_first(bs, [
        "Other Short Term Investments",
        "Available For Sale Securities",
        "Short Term Investments",
    ])
    cash_v = safe_float(cash.dropna().iloc[-1]) if cash is not None and len(cash.dropna()) else np.nan
    sti_v = safe_float(sti.dropna().iloc[-1]) if sti is not None and len(sti.dropna()) else np.nan
    if pd.isna(cash_v) and pd.isna(sti_v):
        return np.nan
    return np.nansum([cash_v, sti_v])


def long_debt_latest(bs: pd.DataFrame) -> float:
    ltd = row_first(bs, [
        "Long Term Debt",
        "Long Term Debt And Capital Lease Obligation",
        "Long Term Debt And Lease Obligation",
        "Non Current Debt",
    ])
    return safe_float(ltd.dropna().iloc[-1]) if ltd is not None and len(ltd.dropna()) else np.nan


def eps_by_year(fin: pd.DataFrame, year: int) -> float:
    row = row_first(fin, [
        "Diluted EPS",
        "Basic EPS",
        "Reported EPS",
        "Normalized EPS",
    ])
    return value_at_year(row, year)


def remarks_for_row(group: str, net_cash_ps: float, fcf_ps: float, g1: float, eps_y0: float) -> str:
    notes: List[str] = []
    if group.startswith("REIT"):
        notes.append("린치식 단독판정 비중 낮춤(REIT)")
    if not pd.isna(net_cash_ps) and net_cash_ps < 0:
        notes.append("주당순현금 음수")
    if not pd.isna(fcf_ps) and fcf_ps < 0:
        notes.append("주당FCF 음수")
    if not pd.isna(g1) and g1 > 100:
        notes.append("1년 점수 과열 가능")
    if not pd.isna(eps_y0) and eps_y0 <= 0:
        notes.append("EPS 음수/약함")
    return "; ".join(notes)


@dataclass
class Result:
    data: Dict[str, object]


def evaluate_ticker(ticker: str, bsns_year: int) -> Result:
    meta = UNIVERSE.get(ticker, {"name": ticker, "group": "기타", "verdict": "메인"})
    tk = yf.Ticker(ticker)

    hist = tk.history(period="1mo", auto_adjust=False)
    price = latest_close(hist)

    fin = normalize_statement(getattr(tk, "income_stmt", pd.DataFrame()))
    bs = normalize_statement(getattr(tk, "balance_sheet", pd.DataFrame()))
    cf = normalize_statement(getattr(tk, "cashflow", pd.DataFrame()))
    dividends = getattr(tk, "dividends", pd.Series(dtype=float))

    shares = pick_shares(tk, bs, price)

    eps_y0 = eps_by_year(fin, bsns_year)
    eps_y1 = eps_by_year(fin, bsns_year - 1)
    eps_y3 = eps_by_year(fin, bsns_year - 3)
    eps_y5 = eps_by_year(fin, bsns_year - 5)

    g1 = growth_pct(eps_y0, eps_y1)
    g3 = cagr_pct(eps_y0, eps_y3, 3)
    g5 = cagr_pct(eps_y0, eps_y5, 5)

    dps = dps_for_year(dividends, bsns_year)
    dividend_yield = (dps / price * 100.0) if not pd.isna(dps) and not pd.isna(price) and price > 0 else np.nan

    cash_like = cash_like_latest(bs)
    long_debt = long_debt_latest(bs)

    net_cash_ps = np.nan
    if not pd.isna(cash_like) and not pd.isna(long_debt) and not pd.isna(shares) and shares > 0:
        net_cash_ps = (cash_like - long_debt) / shares

    ex_cash_per = np.nan
    if not pd.isna(price) and not pd.isna(net_cash_ps) and not pd.isna(eps_y0) and eps_y0 != 0:
        ex_cash_per = (price - net_cash_ps) / eps_y0

    score1 = (g1 + dividend_yield) / ex_cash_per if not pd.isna(g1) and not pd.isna(dividend_yield) and not pd.isna(ex_cash_per) and ex_cash_per > 0 else np.nan
    score3 = (g3 + dividend_yield) / ex_cash_per if not pd.isna(g3) and not pd.isna(dividend_yield) and not pd.isna(ex_cash_per) and ex_cash_per > 0 else np.nan
    score5 = (g5 + dividend_yield) / ex_cash_per if not pd.isna(g5) and not pd.isna(dividend_yield) and not pd.isna(ex_cash_per) and ex_cash_per > 0 else np.nan

    fcf = fcf_latest(cf)
    fcf_ps = (fcf / shares) if not pd.isna(fcf) and not pd.isna(shares) and shares > 0 else np.nan
    fcf_yield = (fcf_ps / price * 100.0) if not pd.isna(fcf_ps) and not pd.isna(price) and price > 0 else np.nan

    remarks = remarks_for_row(meta["group"], net_cash_ps, fcf_ps, g1, eps_y0)

    priority_score = (
        (0 if pd.isna(score3) else score3 * 0.40) +
        (0 if pd.isna(score1) else score1 * 0.25) +
        (0 if pd.isna(score5) else score5 * 0.20) +
        (0 if pd.isna(fcf_yield) else fcf_yield * 0.10) +
        (0 if pd.isna(dividend_yield) else dividend_yield * 0.05)
    )

    row = {
        "티커": ticker,
        "종목명": meta["name"],
        "그룹": meta["group"],
        "판정구분": meta["verdict"],
        "현재가": price,
        f"현금배당금(FY{bsns_year} DPS)": dps,
        f"EPS(FY{bsns_year})": eps_y0,
        "주당순현금(린치식)": net_cash_ps,
        "순현금차감PER(린치식)": ex_cash_per,
        "연간이익증가율(1년,%)": g1,
        "연간이익증가율(3년CAGR,%)": g3,
        "연간이익증가율(5년CAGR,%)": g5,
        "배당수익률(%)": dividend_yield,
        "배당감안이익성장률(1년)": score1,
        "배당감안이익성장률(3년)": score3,
        "배당감안이익성장률(5년)": score5,
        "주당잉여현금흐름": fcf_ps,
        "잉여현금흐름수익률(%)": fcf_yield,
        "발행주식수": shares,
        "비고": remarks,
        "priority_score": priority_score,
    }
    return Result(row)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tickers", default="MO,UVV,FRT,O", help="Comma-separated tickers")
    ap.add_argument("--bsns-year", type=int, default=2025)
    ap.add_argument("--out", default="overseas_lynch.csv", help="Output prefix")
    args = ap.parse_args()

    tickers = [t.strip().upper() for t in args.tickers.split(",") if t.strip()]
    rows: List[Dict[str, object]] = []

    for i, t in enumerate(tickers, 1):
        log(f"[{i}/{len(tickers)}] Fetching {t}")
        try:
            rows.append(evaluate_ticker(t, args.bsns_year).data)
        except Exception as e:
            rows.append({
                "티커": t,
                "종목명": UNIVERSE.get(t, {}).get("name", t),
                "그룹": UNIVERSE.get(t, {}).get("group", "기타"),
                "판정구분": UNIVERSE.get(t, {}).get("verdict", "메인"),
                "비고": f"ERROR: {e}",
                "priority_score": np.nan,
            })

    df = pd.DataFrame(rows)

    strict = (
        (pd.to_numeric(df.get("배당감안이익성장률(1년)"), errors="coerce") >= 1.5) &
        (pd.to_numeric(df.get("배당감안이익성장률(3년)"), errors="coerce") >= 2.0) &
        (pd.to_numeric(df.get("배당감안이익성장률(5년)"), errors="coerce") >= 1.5) &
        (pd.to_numeric(df.get("주당잉여현금흐름"), errors="coerce") > 0) &
        (pd.to_numeric(df.get("잉여현금흐름수익률(%)"), errors="coerce") > 0)
    )

    raw_path = args.out.replace(".csv", "_raw.csv")
    filtered_path = args.out.replace(".csv", "_filtered.csv")

    df = df.sort_values(["priority_score"], ascending=False, na_position="last")
    df.to_csv(raw_path, index=False, encoding="utf-8-sig")
    df[strict].sort_values(["priority_score"], ascending=False, na_position="last").to_csv(
        filtered_path, index=False, encoding="utf-8-sig"
    )

    with pd.option_context("display.max_columns", None, "display.width", 220):
        print(df.to_string(index=False))

    print(f"\nSaved raw      -> {raw_path}")
    print(f"Saved filtered -> {filtered_path}")


if __name__ == "__main__":
    main()
