#!/usr/bin/env python3
from __future__ import annotations

import argparse
import io
from pathlib import Path
from typing import Iterable

import pandas as pd


HARD_EXCLUDE = {
    "^TNX", "^VIX", "TMV", "TMF", "TBT", "TLT",
    "QID", "SQQQ", "SOXS", "FAZ", "SCO", "KOLD",
    "BITI", "GLL", "BOIL", "DIG", "ERX", "GUSH",
    "WTI", "OIL", "GOLD",
}


def is_true(x) -> bool:
    return str(x).strip().lower() in {"true", "1", "y", "yes"}


def _read_csv_after_header(path: str | Path, header_prefix: str = "ticker,") -> pd.DataFrame:
    p = Path(path)
    text = p.read_text(encoding="utf-8", errors="ignore")
    lines = text.splitlines()
    start = None
    for i, line in enumerate(lines):
        if line.strip().lower().startswith(header_prefix):
            start = i
            break
    if start is None:
        # fallback: try regular csv parsing
        return pd.read_csv(p)
    body = "\n".join(lines[start:]) + "\n"
    return pd.read_csv(io.StringIO(body))


def _manual_tickers(path: str | Path) -> list[str]:
    df = _read_csv_after_header(path)
    if "ticker" not in df.columns:
        raise ValueError(f"Could not find ticker column in {path}")
    vals = [str(x).strip().upper() for x in df["ticker"].dropna().tolist()]
    out: list[str] = []
    for t in vals:
        if t and t not in out:
            out.append(t)
    return out


def _load_report(path: str | Path) -> pd.DataFrame:
    df = pd.read_csv(path)
    for col in [
        "price", "gap_pct", "rsi14", "room_to_weekly_r1_pct", "weekly_r1",
        "daily_break_level", "px_vs_sma50", "px_vs_sma200", "score"
    ]:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")

    df["ticker"] = df["ticker"].astype(str).str.strip().str.upper()
    df["daily_breakout_bool"] = df["daily_breakout"].map(is_true) if "daily_breakout" in df.columns else False
    df["daily_retest_bool"] = df["daily_retest"].map(is_true) if "daily_retest" in df.columns else False
    df["sma50_ok"] = df["px_vs_sma50"].fillna(-999) > 0
    df["sma200_ok"] = df["px_vs_sma200"].fillna(-999) > 0
    df["trend_ok"] = df["sma50_ok"] & df["sma200_ok"]
    df["ext_pct"] = (df["price"] / df["daily_break_level"] - 1.0) * 100.0
    df["dist_to_break_pct"] = (df["daily_break_level"] / df["price"] - 1.0) * 100.0
    return df


def classify_row(
    r: pd.Series,
    max_ext_buy: float,
    max_ext_watch: float,
    max_dist_near: float,
    max_rsi_buy: float,
) -> tuple[str, str]:
    t = str(r["ticker"])
    if t in HARD_EXCLUDE:
        return "REJECT", "macro/inverse/hedge ticker"

    if pd.isna(r.get("price")) or pd.isna(r.get("daily_break_level")):
        return "REJECT", "missing price or break_level"

    breakout = bool(r.get("daily_breakout_bool", False))
    retest = bool(r.get("daily_retest_bool", False))
    sma50 = bool(r.get("sma50_ok", False))
    sma200 = bool(r.get("sma200_ok", False))
    ext_pct = float(r.get("ext_pct", float("nan")))
    dist = float(r.get("dist_to_break_pct", float("nan")))
    rsi = float(r.get("rsi14", float("nan"))) if pd.notna(r.get("rsi14")) else float("nan")

    if breakout and retest and sma50 and sma200 and 0 <= ext_pct <= max_ext_buy:
        if pd.notna(rsi) and rsi > max_rsi_buy:
            return "WATCH", f"breakout+retest but RSI hot ({rsi:.2f})"
        return "BUY_NOW", "breakout+retest confirmed and still close to break"

    if breakout and sma50 and sma200 and 0 <= ext_pct <= max_ext_watch:
        if retest:
            return "WATCH", "breakout confirmed but a bit extended"
        return "WATCH", "breakout without retest confirmation"

    if (not breakout) and retest and sma50 and sma200 and 0 <= dist <= max_dist_near:
        return "NEAR_BREAKOUT", "just below break with retest; trigger is clear"

    reasons: list[str] = []
    if not sma50:
        reasons.append("below/weak vs sma50")
    if not sma200:
        reasons.append("below/weak vs sma200")
    if breakout and ext_pct > max_ext_watch:
        reasons.append(f"too extended (+{ext_pct:.2f}%)")
    if (not breakout) and dist > max_dist_near:
        reasons.append(f"too far below break ({dist:.2f}%)")
    if not retest:
        reasons.append("no retest")
    if pd.notna(rsi) and rsi > max_rsi_buy + 3:
        reasons.append(f"RSI hot ({rsi:.2f})")
    if pd.notna(rsi) and rsi < 40:
        reasons.append(f"RSI weak ({rsi:.2f})")

    if not reasons:
        reasons.append("does not fit legacy breakout template")
    return "REJECT", ", ".join(reasons)


def sort_bucket(df: pd.DataFrame, bucket: str) -> pd.DataFrame:
    if df.empty:
        return df
    if bucket == "BUY_NOW":
        return df.sort_values(["ext_pct", "rsi14"], ascending=[True, True])
    if bucket == "NEAR_BREAKOUT":
        return df.sort_values(["dist_to_break_pct", "rsi14"], ascending=[True, True])
    if bucket == "WATCH":
        return df.sort_values(["ext_pct", "score"], ascending=[True, False])
    return df.sort_values(["score", "ticker"], ascending=[False, True])


def format_row(r: pd.Series) -> str:
    price = r.get("price")
    level = r.get("daily_break_level")
    ext = r.get("ext_pct")
    dist = r.get("dist_to_break_pct")
    score = r.get("score")
    rsi = r.get("rsi14")
    room = r.get("room_to_weekly_r1_pct")
    return (
        f"- {r['ticker']}: price={price:.2f} | break={level:.2f} | "
        f"breakout={bool(r['daily_breakout_bool'])} | retest={bool(r['daily_retest_bool'])} | "
        f"sma50={bool(r['sma50_ok'])} | sma200={bool(r['sma200_ok'])} | "
        f"ext={ext:+.2f}% | dist={dist:+.2f}% | rsi={rsi:.2f} | "
        f"score={score:.1f} | room_to_r1={room:.2f}%\n"
        f"  why={r['legacy_reason']}"
    )


def main() -> int:
    ap = argparse.ArgumentParser(description="Legacy review for Bulkowski/manual 10 tickers")
    ap.add_argument("--report", default="report_v2.csv", help="path to report_v2.csv")
    ap.add_argument("--manual", default="premarket_manual.csv", help="path to premarket_manual.csv")
    ap.add_argument("--max-ext-buy", type=float, default=2.0, help="max extension percent for BUY_NOW")
    ap.add_argument("--max-ext-watch", type=float, default=5.0, help="max extension percent for WATCH")
    ap.add_argument("--max-dist-near", type=float, default=2.5, help="max distance below break for NEAR_BREAKOUT")
    ap.add_argument("--max-rsi-buy", type=float, default=72.0, help="max RSI for BUY_NOW")
    args = ap.parse_args()

    manual_tickers = _manual_tickers(args.manual)
    report = _load_report(args.report)
    sub = report[report["ticker"].isin(manual_tickers)].copy()

    # preserve manual input order
    order = {t: i for i, t in enumerate(manual_tickers)}
    sub["_order"] = sub["ticker"].map(order)

    found = set(sub["ticker"].tolist())
    missing = [t for t in manual_tickers if t not in found]

    if sub.empty and not missing:
        print("No manual tickers found in report.")
        return 1

    labels = []
    reasons = []
    for _, row in sub.iterrows():
        label, reason = classify_row(
            row,
            max_ext_buy=args.max_ext_buy,
            max_ext_watch=args.max_ext_watch,
            max_dist_near=args.max_dist_near,
            max_rsi_buy=args.max_rsi_buy,
        )
        labels.append(label)
        reasons.append(reason)

    sub["legacy_bucket"] = labels
    sub["legacy_reason"] = reasons

    print("=== MANUAL 10 LEGACY REVIEW ===")
    print(f"manual_tickers={len(manual_tickers)} | found_in_report={len(sub)} | missing_in_report={len(missing)}")
    if missing:
        print("missing:", ", ".join(missing))
    print()

    for bucket, title in [
        ("BUY_NOW", "BUY NOW"),
        ("NEAR_BREAKOUT", "NEAR BREAKOUT"),
        ("WATCH", "WATCH / CONDITIONAL"),
        ("REJECT", "REJECT / LOW PRIORITY"),
    ]:
        part = sub[sub["legacy_bucket"] == bucket].copy()
        part = sort_bucket(part, bucket)
        print(f"[{title}]")
        if part.empty:
            print("(none)")
        else:
            for _, r in part.iterrows():
                print(format_row(r))
        print()

    print("[ALL MANUAL TICKERS IN INPUT ORDER]")
    part = sub.sort_values("_order")
    if part.empty:
        print("(none)")
    else:
        for _, r in part.iterrows():
            print(f"{int(r['_order'])+1:>2}. {r['ticker']:<8} bucket={r['legacy_bucket']:<14} reason={r['legacy_reason']}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
