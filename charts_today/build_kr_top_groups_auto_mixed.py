#!/usr/bin/env python3
from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Iterable

try:
    import pandas as pd
    from pykrx import stock
except Exception as e:  # pragma: no cover
    raise SystemExit(
        "This script requires 'pykrx' and 'pandas'. Install with: pip install pykrx pandas\n"
        f"Original import error: {e}"
    )

DENY_SUBSTRINGS = [
    "코스피", "코스닥", "KRX", "KTOP", "200", "150", "100", "50",
    "대형주", "중형주", "소형주", "배당", "가치", "성장", "로우볼", "퀄리티", "ESG",
    "테마", "전략", "선물", "인버스", "레버리지",
]


@dataclass
class GroupRow:
    index_ticker: str
    market: str
    name: str
    ret_1d: float
    ret_1w: float
    ret_1m: float
    member_count: int
    score: float = 0.0


def _suffix_lookup() -> dict[str, str]:
    out: dict[str, str] = {}
    for t in stock.get_market_ticker_list(market="KOSPI"):
        out[str(t).zfill(6)] = ".KS"
    for t in stock.get_market_ticker_list(market="KOSDAQ"):
        out[str(t).zfill(6)] = ".KQ"
    return out


def _is_sector_like(name: str, count: int) -> bool:
    if count < 5 or count > 80:
        return False
    if any(bad in name for bad in DENY_SUBSTRINGS):
        return False
    return True


def _series_returns(close: pd.Series) -> tuple[float, float, float] | None:
    close = close.dropna().astype(float)
    if len(close) < 22:
        return None
    last = close.iloc[-1]
    r1d = (last / close.iloc[-2] - 1.0) * 100.0 if len(close) >= 2 else 0.0
    r1w = (last / close.iloc[-6] - 1.0) * 100.0 if len(close) >= 6 else None
    r1m = (last / close.iloc[-22] - 1.0) * 100.0 if len(close) >= 22 else None
    if r1w is None or r1m is None:
        return None
    return r1d, r1w, r1m


def _get_index_rows() -> list[GroupRow]:
    start = (datetime.today() - timedelta(days=60)).strftime("%Y%m%d")
    end = datetime.today().strftime("%Y%m%d")
    rows: list[GroupRow] = []
    for market in ("KOSPI", "KOSDAQ"):
        for idx in stock.get_index_ticker_list(market=market):
            name = stock.get_index_ticker_name(idx)
            members = stock.get_index_portfolio_deposit_file(idx)
            if not _is_sector_like(name, len(members)):
                continue
            df = stock.get_index_ohlcv_by_date(start, end, idx)
            if df is None or df.empty:
                continue
            close_col = "종가" if "종가" in df.columns else df.columns[-1]
            rets = _series_returns(df[close_col])
            if rets is None:
                continue
            r1d, r1w, r1m = rets
            rows.append(GroupRow(idx, market, name, r1d, r1w, r1m, len(members)))
    return rows


def _rank_score(values: pd.Series) -> pd.Series:
    return values.rank(method="average", pct=True, ascending=True)


def _latest_market_cap_map(trading_day: str) -> dict[str, float]:
    try:
        cap = stock.get_market_cap_by_ticker(trading_day)
        col = "시가총액" if "시가총액" in cap.columns else cap.columns[0]
        return {str(idx).zfill(6): float(val) for idx, val in cap[col].items()}
    except Exception:
        return {}


def _top_members_for_group(group_ticker: str, suffix_map: dict[str, str], max_per_group: int) -> list[dict]:
    start = (datetime.today() - timedelta(days=60)).strftime("%Y%m%d")
    end = datetime.today().strftime("%Y%m%d")
    codes = [str(c).zfill(6) for c in stock.get_index_portfolio_deposit_file(group_ticker)]
    rows = []
    last_trade_day = None
    for code in codes:
        suffix = suffix_map.get(code)
        if not suffix:
            continue
        df = stock.get_market_ohlcv_by_date(start, end, code)
        if df is None or df.empty:
            continue
        close_col = "종가" if "종가" in df.columns else df.columns[-1]
        rets = _series_returns(df[close_col])
        if rets is None:
            continue
        r1d, r1w, r1m = rets
        if last_trade_day is None:
            last_trade_day = df.index[-1].strftime("%Y%m%d")
        rows.append({
            "code": code,
            "ticker": code + suffix,
            "name": stock.get_market_ticker_name(code),
            "ret_1d": r1d,
            "ret_1w": r1w,
            "ret_1m": r1m,
        })
    if not rows:
        return []
    mdf = pd.DataFrame(rows)
    mdf["score"] = 0.5 * _rank_score(mdf["ret_1d"]) + 0.3 * _rank_score(mdf["ret_1w"]) + 0.2 * _rank_score(mdf["ret_1m"])
    if last_trade_day:
        cap_map = _latest_market_cap_map(last_trade_day)
        mdf["market_cap"] = mdf["code"].map(cap_map).fillna(0.0)
    else:
        mdf["market_cap"] = 0.0
    mdf = mdf.sort_values(["score", "market_cap"], ascending=[False, False]).head(max_per_group)
    return mdf.to_dict("records")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="kr_top_groups_auto_mixed.txt")
    ap.add_argument("--groups-csv", default="kr_top_groups_auto_mixed_groups.csv")
    ap.add_argument("--members-csv", default="kr_top_groups_auto_mixed_members.csv")
    ap.add_argument("--top-groups", type=int, default=6)
    ap.add_argument("--max-per-group", type=int, default=8)
    ap.add_argument("--wt-day", type=float, default=0.5)
    ap.add_argument("--wt-week", type=float, default=0.3)
    ap.add_argument("--wt-month", type=float, default=0.2)
    args = ap.parse_args()

    suffix_map = _suffix_lookup()
    rows = _get_index_rows()
    if not rows:
        raise SystemExit("No eligible KRX sector-like groups found. Check pykrx connectivity or date availability.")

    gdf = pd.DataFrame([r.__dict__ for r in rows])
    gdf["rank_1d"] = _rank_score(gdf["ret_1d"])
    gdf["rank_1w"] = _rank_score(gdf["ret_1w"])
    gdf["rank_1m"] = _rank_score(gdf["ret_1m"])
    gdf["score"] = args.wt_day * gdf["rank_1d"] + args.wt_week * gdf["rank_1w"] + args.wt_month * gdf["rank_1m"]
    gdf = gdf.sort_values("score", ascending=False).head(args.top_groups).reset_index(drop=True)
    gdf.to_csv(args.groups_csv, index=False, encoding="utf-8-sig")

    member_rows: list[dict] = []
    out_lines = [
        "# kr_top_groups_auto_mixed.txt",
        "# Auto-generated from KRX sector-like indices via pykrx.",
        "# Mixed score = 0.5*d1 + 0.3*w1 + 0.2*m1 on group returns, then top members by the same logic.",
        "",
    ]
    seen: set[str] = set()

    for i, r in gdf.iterrows():
        out_lines.append(f"# group {i+1}: {r['name']} | market={r['market']} | score={r['score']:.4f} | d1={r['ret_1d']:.2f}% | w1={r['ret_1w']:.2f}% | m1={r['ret_1m']:.2f}%")
        members = _top_members_for_group(r["index_ticker"], suffix_map, args.max_per_group)
        for m in members:
            member_rows.append({
                "group_name": r["name"],
                "group_market": r["market"],
                **m,
            })
            if m["ticker"] not in seen:
                seen.add(m["ticker"])
                out_lines.append(f"{m['ticker']}  # {m['name']} | {r['name']} | d1={m['ret_1d']:.2f}% w1={m['ret_1w']:.2f}% m1={m['ret_1m']:.2f}%")
        out_lines.append("")

    Path(args.out).write_text("\n".join(out_lines).rstrip() + "\n", encoding="utf-8")
    pd.DataFrame(member_rows).to_csv(args.members_csv, index=False, encoding="utf-8-sig")

    print(f"Saved: {args.out} ({len(seen)} tickers)")
    print(f"Saved: {args.groups_csv} ({len(gdf)} groups)")
    print(f"Saved: {args.members_csv} ({len(member_rows)} rows before final dedupe)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
