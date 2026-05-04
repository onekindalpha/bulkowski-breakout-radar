#!/usr/bin/env python3
from __future__ import annotations

import argparse
import io
from pathlib import Path
from typing import Iterable
import pandas as pd

TRUTHY = {"true", "1", "y", "yes", "t"}
HARD_EXCLUDE = {
    "^TNX", "^VIX", "TMV", "TMF", "TBT", "TLT", "QID", "SQQQ", "SOXS", "FAZ",
    "SCO", "KOLD", "BITI", "GLL", "BOIL", "DIG", "ERX", "GUSH", "WTI", "OIL", "GOLD"
}


def is_true(x):
    return str(x).strip().lower() in TRUTHY


def safe_float(x, default=float("nan")):
    try:
        return float(x)
    except Exception:
        return default


def load_manual_csv(path: str) -> pd.DataFrame:
    p = Path(path)
    if not p.exists():
        return pd.DataFrame(columns=["ticker", "premarket"])

    text = p.read_text(encoding="utf-8", errors="ignore")
    lines = [ln.rstrip("\n") for ln in text.splitlines() if ln.strip()]

    header_idx = None
    for i, line in enumerate(lines):
        low = line.lower().replace(" ", "")
        if "ticker" in low and ("premarket" in low or ",price" in low or "price" in low):
            header_idx = i
            break

    if header_idx is None:
        try:
            df = pd.read_csv(p, comment="#")
            if "ticker" in df.columns:
                return df
        except Exception:
            pass
        return pd.DataFrame(columns=["ticker", "premarket"])

    csv_text = "\n".join(lines[header_idx:])
    try:
        df = pd.read_csv(io.StringIO(csv_text))
    except Exception:
        return pd.DataFrame(columns=["ticker", "premarket"])

    cols = {c.lower().strip(): c for c in df.columns}
    if "ticker" not in cols:
        return pd.DataFrame(columns=["ticker", "premarket"])

    out = pd.DataFrame()
    out["ticker"] = df[cols["ticker"]].astype(str).str.strip().str.upper()
    if "premarket" in cols:
        out["premarket"] = pd.to_numeric(df[cols["premarket"]], errors="coerce")
    elif "price" in cols:
        out["premarket"] = pd.to_numeric(df[cols["price"]], errors="coerce")
    else:
        out["premarket"] = pd.NA
    return out.dropna(subset=["ticker"])


def load_report(path: str) -> pd.DataFrame:
    df = pd.read_csv(path)
    df["ticker"] = df["ticker"].astype(str).str.strip().str.upper()
    for c in [
        "price", "gap_pct", "rsi14", "px_vs_sma50", "px_vs_sma200",
        "room_to_weekly_r1_pct", "weekly_r1", "weekly_s1", "daily_break_level", "score"
    ]:
        if c in df.columns:
            df[c] = pd.to_numeric(df[c], errors="coerce")
    for c in ["daily_breakout", "daily_retest", "weekly_up", "in_daily_box_middle"]:
        df[c] = df[c].map(is_true) if c in df.columns else False
    if "grade" not in df.columns:
        df["grade"] = "C"
    return df


def compute_watchlist(df: pd.DataFrame, top_n: int) -> pd.DataFrame:
    tmp = df.copy()
    tmp["grade_rank"] = tmp["grade"].map({"A": 0, "B": 1, "C": 2}).fillna(9)
    return tmp.sort_values(["grade_rank", "score", "gap_pct"], ascending=[True, False, True]).head(top_n).copy()


def classify_row(row: pd.Series, max_ext_buy: float, max_dist_near: float):
    t = str(row["ticker"]).upper()
    price = safe_float(row.get("price"))
    brk = safe_float(row.get("daily_break_level"))
    rsi = safe_float(row.get("rsi14"))
    sma50 = safe_float(row.get("px_vs_sma50"))
    sma200 = safe_float(row.get("px_vs_sma200"))
    breakout = bool(row.get("daily_breakout", False))
    retest = bool(row.get("daily_retest", False))

    ext_pct = ((price / brk) - 1.0) * 100.0 if price == price and brk == brk and brk > 0 else float("nan")
    dist_pct = ((brk / price) - 1.0) * 100.0 if price == price and brk == brk and price > 0 else float("nan")

    if t in HARD_EXCLUDE:
        return "REJECT", "macro/inverse/hedge 계열이라 우선순위 낮음"
    if sma50 <= 0 or sma200 <= 0:
        return "REJECT", "SMA50/200 추세 필터 불량"
    if breakout and retest and ext_pct == ext_pct and 0 <= ext_pct <= max_ext_buy:
        if rsi > 75:
            return "WATCH", "구조는 좋지만 RSI 과열"
        return "BUY NOW", "breakout+retest 성립, 돌파거리 과하지 않음"
    if breakout and retest and ext_pct == ext_pct and ext_pct > max_ext_buy:
        return "CHASE", "돌파는 맞지만 이미 너무 멀리 감"
    if breakout and not retest:
        return "WATCH", "돌파는 났지만 retest 부재"
    if (not breakout) and retest and dist_pct == dist_pct and 0 <= dist_pct <= max_dist_near:
        return "NEAR", "break level 바로 아래, 조건부 트리거가 깔끔"
    if (not breakout) and retest:
        return "WATCH", "retest는 있으나 break level까지 거리가 다소 있음"
    return "REJECT", "breakout/retest 구조가 현재 진입 트리거로 애매"


def fmt_pct(x: float) -> str:
    return "nan" if x != x else f"{x:+.2f}%"


def explain_row(row: pd.Series):
    price = safe_float(row.get("price"))
    brk = safe_float(row.get("daily_break_level"))
    ext_pct = ((price / brk) - 1.0) * 100.0 if price == price and brk == brk and brk > 0 else float("nan")
    dist_pct = ((brk / price) - 1.0) * 100.0 if price == price and brk == brk and price > 0 else float("nan")
    return {
        "price": price,
        "break": brk,
        "ext_pct": ext_pct,
        "dist_pct": dist_pct,
        "breakout": bool(row.get("daily_breakout", False)),
        "retest": bool(row.get("daily_retest", False)),
        "sma50": safe_float(row.get("px_vs_sma50")),
        "sma200": safe_float(row.get("px_vs_sma200")),
        "grade": row.get("grade", ""),
        "score": safe_float(row.get("score")),
        "rsi": safe_float(row.get("rsi14")),
        "room": safe_float(row.get("room_to_weekly_r1_pct")),
        "gap": safe_float(row.get("gap_pct")),
    }


def render_section(title: str, rows: Iterable[pd.Series], max_ext_buy: float, max_dist_near: float) -> str:
    lines = [f"=== {title} ==="]
    count = 0
    for row in rows:
        count += 1
        status, why = classify_row(row, max_ext_buy=max_ext_buy, max_dist_near=max_dist_near)
        d = explain_row(row)
        lines.append(f"{count}. {row['ticker']}  [{status}]  grade={d['grade']} score={d['score']:.1f}")
        lines.append(
            f"   price={d['price']:.2f} | break={d['break']:.2f} | ext={fmt_pct(d['ext_pct'])} | dist={fmt_pct(d['dist_pct'])}"
        )
        lines.append(
            f"   breakout={d['breakout']} | retest={d['retest']} | sma50={d['sma50']:+.2f}% | sma200={d['sma200']:+.2f}% | "
            f"rsi={d['rsi']:.2f} | gap={d['gap']:+.2f}% | room={d['room']:+.2f}%"
        )
        lines.append(f"   why={why}")
    if count == 0:
        lines.append("(none)")
    return "\n".join(lines)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--report", default="report_v2.csv")
    ap.add_argument("--manual", default="premarket_manual.csv")
    ap.add_argument("--safe-top", type=int, default=25)
    ap.add_argument("--max-ext-buy", type=float, default=2.0)
    ap.add_argument("--max-dist-near", type=float, default=2.5)
    args = ap.parse_args()

    report = load_report(args.report)
    manual = load_manual_csv(args.manual)
    if report.empty:
        raise SystemExit("report_v2.csv is empty or missing usable rows")

    manual_set = set(manual["ticker"].astype(str).str.upper()) if not manual.empty else set()
    watch = compute_watchlist(report, top_n=args.safe_top)
    watch_set = set(watch["ticker"].astype(str).str.upper())

    merged = report.copy()
    merged["in_manual"] = merged["ticker"].isin(manual_set)
    merged["in_safe"] = merged["ticker"].isin(watch_set)

    both = merged[(merged["in_manual"]) & (merged["in_safe"])].copy()
    manual_only = merged[(merged["in_manual"]) & (~merged["in_safe"])].copy()
    safe_only = merged[(~merged["in_manual"]) & (merged["in_safe"])].copy()

    sort_cols = ["grade", "score", "gap_pct"]
    asc = [True, False, True]
    if not both.empty:
        both = both.sort_values(sort_cols, ascending=asc)
    if not manual_only.empty:
        manual_only = manual_only.sort_values(sort_cols, ascending=asc)
    if not safe_only.empty:
        safe_only = safe_only.sort_values(sort_cols, ascending=asc)

    print("=== LEGACY REVIEW SUMMARY ===")
    print(f"manual_count={len(manual_set)} | safe_watchlist_count={len(watch_set)} | overlap={len(both)}")
    print("manual∩safe = 패턴 후보이면서 safe 상위에도 든 종목")
    print("manual-only = 패턴 후보였지만 safe 상위에선 밀린 종목")
    print("safe-only = 패턴 후보는 아니었지만 safe 상위에 든 종목")
    print()
    print(render_section("MANUAL ∩ SAFE", (r for _, r in both.iterrows()), args.max_ext_buy, args.max_dist_near))
    print()
    print(render_section("MANUAL ONLY", (r for _, r in manual_only.iterrows()), args.max_ext_buy, args.max_dist_near))
    print()
    print(render_section("SAFE ONLY", (r for _, r in safe_only.iterrows()), args.max_ext_buy, args.max_dist_near))


if __name__ == "__main__":
    main()
