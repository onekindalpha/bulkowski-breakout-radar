#!/usr/bin/env python3
from __future__ import annotations

import argparse
import io
from pathlib import Path
from typing import Iterable

import pandas as pd


SAFE_WATCHLIST_SIZE = 25
HARD_EXCLUDE = {
    "^TNX", "^VIX", "TMV", "TMF", "TBT", "TLT",
    "QID", "SQQQ", "SOXS", "FAZ", "SCO", "KOLD",
    "BITI", "GLL", "BOIL", "DIG", "ERX", "GUSH",
    "WTI", "OIL", "GOLD",
}


def is_true(x) -> bool:
    return str(x).strip().lower() in {"true", "1", "y", "yes"}


def load_csv_flexible(path: str) -> pd.DataFrame:
    text = Path(path).read_text(encoding="utf-8", errors="ignore")
    lines = text.splitlines()

    # If the file contains metadata/header lines before the real CSV header,
    # start reading from the first line that looks like the real header.
    header_idx = None
    for i, ln in enumerate(lines):
        s = ln.strip().lower()
        if s.startswith("ticker,") or s.startswith("ticker\t"):
            header_idx = i
            break

    if header_idx is not None:
        csv_text = "\n".join(lines[header_idx:])
        return pd.read_csv(io.StringIO(csv_text))

    return pd.read_csv(io.StringIO(text), comment="#")


def to_numeric(df: pd.DataFrame, cols: Iterable[str]) -> pd.DataFrame:
    for c in cols:
        if c in df.columns:
            df[c] = pd.to_numeric(df[c], errors="coerce")
    return df


def build_safe_watchlist(df: pd.DataFrame, limit: int = SAFE_WATCHLIST_SIZE) -> pd.DataFrame:
    out = df.copy()
    out["gap_pct"] = pd.to_numeric(out["gap_pct"], errors="coerce")
    out["score"] = pd.to_numeric(out["score"], errors="coerce")
    grade_rank = {"A": 0, "B": 1, "C": 2}
    out["grade_rank"] = out["grade"].map(grade_rank).fillna(99)
    out = out.sort_values(["grade_rank", "score", "gap_pct"], ascending=[True, False, True])
    return out.head(limit).copy()


def safe_warning_labels(row: pd.Series) -> list[str]:
    warnings: list[str] = []

    rsi = row.get("rsi14")
    room = row.get("room_to_weekly_r1_pct")
    gap = row.get("gap_pct")

    if pd.notna(rsi):
        if rsi > 70:
            warnings.append(f"RSI>70 ({rsi:.2f})")
        elif rsi < 40:
            warnings.append(f"RSI low ({rsi:.2f})")

    if pd.notna(room):
        if room < 0.7:
            warnings.append(f"weekly room small ({room:.2f}%)")
        elif room < 1.5:
            warnings.append(f"weekly room limited ({room:.2f}%)")

    if pd.notna(gap):
        if abs(gap) >= 4:
            warnings.append(f"large gap ({gap:.2f}%)")
        elif abs(gap) >= 2:
            warnings.append(f"noticeable gap ({gap:.2f}%)")

    return warnings


def legacy_bucket(row: pd.Series, max_ext_buy: float, max_dist_near: float) -> tuple[str, list[str]]:
    reasons: list[str] = []

    t = str(row.get("ticker", "")).upper()
    breakout = is_true(row.get("daily_breakout"))
    retest = is_true(row.get("daily_retest"))

    price = row.get("price")
    break_level = row.get("daily_break_level")
    sma50 = row.get("px_vs_sma50")
    sma200 = row.get("px_vs_sma200")
    rsi = row.get("rsi14")

    ext_pct = row.get("ext_pct")
    dist_pct = row.get("dist_to_break_pct")

    if t in HARD_EXCLUDE:
        return "REJECT", ["macro / inverse / hedge ticker"]

    if pd.isna(price) or pd.isna(break_level):
        return "REJECT", ["missing price or break_level"]

    if pd.isna(sma50) or sma50 <= 0:
        reasons.append("below or near SMA50")
    if pd.isna(sma200) or sma200 <= 0:
        reasons.append("below or near SMA200")

    if breakout and retest and pd.notna(ext_pct) and 0 <= ext_pct <= max_ext_buy and pd.notna(sma50) and sma50 > 0 and pd.notna(sma200) and sma200 > 0:
        if pd.notna(rsi) and rsi > 72:
            reasons.append(f"late / hot RSI ({rsi:.2f})")
            return "BUY+WARNING", reasons
        reasons.append("breakout confirmed and retest present")
        reasons.append("still close to break_level")
        return "BUY NOW", reasons

    if breakout and retest:
        if pd.notna(ext_pct) and ext_pct > max_ext_buy:
            reasons.append(f"already extended (+{ext_pct:.2f}%)")
        else:
            reasons.append("breakout confirmed but quality not clean")
        if pd.notna(rsi) and rsi > 72:
            reasons.append(f"RSI hot ({rsi:.2f})")
        return "WATCH / CHASE", reasons

    if (not breakout) and retest and pd.notna(dist_pct) and 0 <= dist_pct <= max_dist_near and pd.notna(sma50) and sma50 > 0 and pd.notna(sma200) and sma200 > 0:
        reasons.append("just below break_level")
        reasons.append("retest/trigger setup looks clean")
        return "NEAR BREAKOUT", reasons

    if breakout and not retest:
        reasons.append("breakout without retest")
        return "WATCH / CHASE", reasons

    if retest and pd.notna(dist_pct):
        reasons.append(f"retest only; {dist_pct:.2f}% below break")
        return "WATCH", reasons

    if pd.notna(dist_pct):
        reasons.append(f"{dist_pct:.2f}% below break")
    if pd.notna(ext_pct) and ext_pct > max_ext_buy:
        reasons.append(f"extended breakout (+{ext_pct:.2f}%)")

    return "REJECT", reasons or ["no clean breakout setup"]


def fmt_bool(v) -> str:
    return "Y" if is_true(v) else "N"


def format_line(row: pd.Series, bucket: str, reasons: list[str], safe_labels: list[str], in_safe: bool) -> str:
    price = row.get("price")
    brk = row.get("daily_break_level")
    ext = row.get("ext_pct")
    dist = row.get("dist_to_break_pct")
    room = row.get("room_to_weekly_r1_pct")
    rsi = row.get("rsi14")
    score = row.get("score")
    grade = row.get("grade")
    t = row.get("ticker")

    core = (
        f"{t} | {bucket} | safe={'Y' if in_safe else 'N'} | "
        f"grade={grade} score={score} | "
        f"breakout={fmt_bool(row.get('daily_breakout'))} "
        f"retest={fmt_bool(row.get('daily_retest'))} | "
        f"price={price:.2f} break={brk:.2f}"
    )

    if pd.notna(ext):
        core += f" | ext={ext:+.2f}%"
    if pd.notna(dist):
        core += f" | dist={dist:+.2f}%"
    if pd.notna(rsi):
        core += f" | rsi={rsi:.2f}"
    if pd.notna(room):
        core += f" | room={room:.2f}%"

    flags = []
    flags.append(f"sma50={'Y' if pd.notna(row.get('px_vs_sma50')) and row.get('px_vs_sma50') > 0 else 'N'}")
    flags.append(f"sma200={'Y' if pd.notna(row.get('px_vs_sma200')) and row.get('px_vs_sma200') > 0 else 'N'}")
    core += " | " + " ".join(flags)

    if reasons:
        core += f"\n    why: " + "; ".join(reasons)
    if safe_labels:
        core += f"\n    safe warning: " + "; ".join(safe_labels)

    return core


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--report", default="report_v2.csv")
    ap.add_argument("--manual", default="premarket_manual.csv")
    ap.add_argument("--safe-limit", type=int, default=SAFE_WATCHLIST_SIZE)
    ap.add_argument("--max-ext-buy", type=float, default=2.0)
    ap.add_argument("--max-dist-near", type=float, default=2.5)
    args = ap.parse_args()

    report = load_csv_flexible(args.report)
    manual = load_csv_flexible(args.manual)

    if report.empty:
        print("report_v2.csv is empty.")
        return 1
    if manual.empty:
        print("premarket_manual.csv is empty.")
        return 1

    report["ticker"] = report["ticker"].astype(str).str.upper()
    manual["ticker"] = manual["ticker"].astype(str).str.upper()

    report = to_numeric(
        report,
        [
            "price", "gap_pct", "rsi14", "px_vs_sma50", "px_vs_sma200",
            "room_to_weekly_r1_pct", "weekly_r1", "daily_break_level", "score",
        ],
    )

    report["ext_pct"] = (report["price"] / report["daily_break_level"] - 1.0) * 100.0
    report["dist_to_break_pct"] = (report["daily_break_level"] / report["price"] - 1.0) * 100.0

    safe_top = build_safe_watchlist(report, limit=args.safe_limit)
    safe_set = set(safe_top["ticker"].tolist())
    manual_set = set(manual["ticker"].tolist())

    merged_manual = report[report["ticker"].isin(manual_set)].copy()
    if merged_manual.empty:
        print("No manual tickers found inside report_v2.csv.")
        return 1

    sections = {
        "MANUAL ∩ SAFE": merged_manual[merged_manual["ticker"].isin(safe_set)].copy(),
        "MANUAL ONLY": merged_manual[~merged_manual["ticker"].isin(safe_set)].copy(),
        "SAFE ONLY": safe_top[~safe_top["ticker"].isin(manual_set)].copy(),
    }

    print("=== LEGACY REVIEW WITH SAFE WARNINGS ===")
    print(f"manual tickers in report: {len(merged_manual)} | safe top: {len(safe_top)}")
    print()

    for title, sec in sections.items():
        print(f"--- {title} ---")
        if sec.empty:
            print("(none)\n")
            continue

        # Sort manual sections by bucket priority then proximity to break.
        rows = []
        for _, row in sec.iterrows():
            bucket, reasons = legacy_bucket(row, args.max_ext_buy, args.max_dist_near)
            safe_labels = safe_warning_labels(row)
            rows.append((row, bucket, reasons, safe_labels))

        bucket_rank = {
            "BUY NOW": 0,
            "BUY+WARNING": 1,
            "NEAR BREAKOUT": 2,
            "WATCH / CHASE": 3,
            "WATCH": 4,
            "REJECT": 5,
        }

        rows.sort(
            key=lambda x: (
                bucket_rank.get(x[1], 99),
                abs(x[0].get("ext_pct")) if pd.notna(x[0].get("ext_pct")) else 999,
                abs(x[0].get("dist_to_break_pct")) if pd.notna(x[0].get("dist_to_break_pct")) else 999,
                -float(x[0].get("score")) if pd.notna(x[0].get("score")) else 999,
            )
        )

        for row, bucket, reasons, safe_labels in rows:
            print(format_line(row, bucket, reasons, safe_labels, row["ticker"] in safe_set))
        print()

    print("Legend:")
    print("- BUY NOW: breakout+retest+SMA structure clean and not too extended")
    print("- BUY+WARNING: legacy buy, but safe warns on RSI / weekly room / gap")
    print("- NEAR BREAKOUT: just below break_level, cleaner trigger setup")
    print("- WATCH / CHASE: breakout happened but position is late / messy / weak retest")
    print("- SAFE warning = why safe may demote a name even if legacy still likes it")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
