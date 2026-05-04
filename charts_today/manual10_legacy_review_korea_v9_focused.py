#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from __future__ import annotations

import argparse
from typing import Optional
import pandas as pd

TRUE_SET = {"true", "1", "y", "yes", "t"}

GREEN = "\033[1;92m"
YELLOW = "\033[1;93m"
CYAN = "\033[1;96m"
RESET = "\033[0m"

ETF_TICKERS = {
    "069500.KS",
    "390390.KS",
    "471760.KS",
    "471990.KS",
    "475310.KS",
}
ETF_NAME_KEYS = ["KODEX", "TIGER", "SOL", "ACE", "KBSTAR", "ARIRANG", "HANARO", "KOSEF"]
NAME_COL_CANDIDATES = [
    "name", "name_kr", "company", "company_name", "stock_name", "종목명", "한글명", "name_kor"
]


def is_true(v) -> bool:
    return str(v).strip().lower() in TRUE_SET


def to_float(v, default=None):
    try:
        if pd.isna(v):
            return default
        return float(v)
    except Exception:
        return default


def find_col(df: pd.DataFrame, candidates: list[str]) -> Optional[str]:
    lower_map = {str(c).strip().lower(): c for c in df.columns}
    for cand in candidates:
        if cand.lower() in lower_map:
            return lower_map[cand.lower()]
    return None


def robust_read_csv(path: str) -> pd.DataFrame:
    try:
        return pd.read_csv(path, comment="#")
    except Exception:
        return pd.read_csv(path, comment="#", engine="python")


def fmt_num(v):
    return "" if v is None else f"{v:.2f}"


def fmt_pct(v):
    return "" if v is None else f"{v:+.2f}%"


def color_label(label: str) -> str:
    if label == "BUY NOW":
        return f"{GREEN}{label}{RESET}"
    if label == "NEAR BREAKOUT":
        return f"{CYAN}{label}{RESET}"
    if label.startswith("WATCH"):
        return f"{YELLOW}{label}{RESET}"
    return label


def load_manual_tickers(path: str) -> pd.DataFrame:
    df = robust_read_csv(path)
    df = df.dropna(axis=1, how="all").dropna(axis=0, how="all")
    if df.empty:
        raise SystemExit(f"manual file empty after cleaning: {path}")

    tcol = find_col(df, ["ticker", "symbol"])
    if tcol is None:
        tcol = df.columns[0]

    out = df.copy()
    out[tcol] = out[tcol].astype(str).str.strip().str.upper()
    out = out[out[tcol] != ""].copy()
    out = out.rename(columns={tcol: "ticker"})
    return out[["ticker"]].drop_duplicates().reset_index(drop=True)


def load_report(path: str) -> pd.DataFrame:
    df = robust_read_csv(path)
    tcol = find_col(df, ["ticker", "symbol"])
    if tcol is None:
        raise SystemExit("report file missing ticker/symbol column")
    df[tcol] = df[tcol].astype(str).str.strip().str.upper()
    return df.rename(columns={tcol: "ticker"})


def get_name_from_row(row: pd.Series) -> str:
    for c in NAME_COL_CANDIDATES:
        if c in row and pd.notna(row[c]):
            name = str(row[c]).strip()
            if name:
                return name
    return ""


def is_etf_ticker(ticker: str, row: pd.Series) -> bool:
    name = get_name_from_row(row).upper()
    if name and any(k in name for k in ETF_NAME_KEYS):
        return True
    return ticker in ETF_TICKERS


def grade_rank(grade: str) -> int:
    return {"A": 0, "B": 1, "C": 2}.get(str(grade).strip().upper(), 9)


def room_rank(room_pct: Optional[float]) -> int:
    if room_pct is None:
        return 9
    if room_pct >= 4:
        return 0
    if room_pct >= 2:
        return 1
    if room_pct >= 0:
        return 2
    return 3


def classify_row(r: pd.Series, near_max_dist: float, chase_ext_max: float):
    price = to_float(r.get("price"), 0.0)
    brk = to_float(r.get("daily_break_level"), 0.0)
    room_pct = to_float(r.get("room_to_weekly_r1_pct"), None)
    weekly_r1 = to_float(r.get("weekly_r1"), None)
    rsi = to_float(r.get("rsi14"), None)
    px50 = to_float(r.get("px_vs_sma50"), None)
    px200 = to_float(r.get("px_vs_sma200"), None)
    ext = to_float(r.get("ext_pct"), None)

    if ext is None and brk and price:
        ext = (price / brk - 1.0) * 100.0

    if weekly_r1 is None and room_pct is not None and price:
        weekly_r1 = price * (1.0 + room_pct / 100.0)

    room_left = None
    if weekly_r1 is not None and price:
        room_left = weekly_r1 - price

    breakout = is_true(r.get("daily_breakout"))
    retest = is_true(r.get("daily_retest"))
    sma50_ok = (px50 is not None and px50 > 0)
    sma200_ok = (px200 is not None and px200 > 0)

    dist = None
    if brk and price:
        dist = (brk / price - 1.0) * 100.0

    grade = str(r.get("grade", ""))
    score = r.get("score", r.get("score_total", ""))

    warnings = []
    if room_pct is not None and room_pct < 0:
        warnings.append(f"weekly_r1 already below current price ({room_pct:.2f}%)")
    elif room_pct is not None and room_pct < 2:
        warnings.append(f"weekly room small ({room_pct:.2f}%)")
    if rsi is not None and rsi > 70:
        warnings.append(f"RSI>70 ({rsi:.2f})")
    if rsi is not None and rsi < 40:
        warnings.append(f"RSI low ({rsi:.2f})")
    if not sma50_ok:
        warnings.append("below or near SMA50")
    if not sma200_ok:
        warnings.append("below or near SMA200")

    if breakout and retest and sma50_ok and sma200_ok:
        if ext is not None and ext > chase_ext_max:
            label = "WATCH / CHASE"
            why = f"already extended ({ext:+.2f}%)"
        else:
            label = "BUY NOW"
            why = "breakout confirmed and retest present; still close to break_level"
    elif retest and dist is not None and dist <= near_max_dist and sma50_ok and sma200_ok:
        label = "NEAR BREAKOUT"
        why = "just below break_level; retest/trigger setup looks clean"
    elif retest and sma50_ok and sma200_ok:
        label = "WATCH"
        why = "retest present, but still not close enough to break"
    else:
        label = "REJECT"
        if not sma50_ok or not sma200_ok:
            why = "structure below key moving averages"
        elif dist is not None:
            why = f"{dist:.2f}% below break"
        else:
            why = "not close enough to break / trigger"

    target_passed = (room_pct is not None and room_pct < 0)
    room_small = (room_pct is not None and 0 <= room_pct < 2)

    if target_passed:
        manage = "late / target1 passed -> prefer hold-only or wait for fresh setup"
    elif room_small:
        manage = "room is tight -> size smaller or require stronger volume"
    elif label in {"BUY NOW", "NEAR BREAKOUT"} and room_pct is not None and room_pct >= 2:
        manage = "valid setup -> confirm volume and keep break as control line"
    elif label in {"BUY NOW", "NEAR BREAKOUT"}:
        manage = "setup is valid but room is not wide -> smaller size / quicker take-profit"
    else:
        manage = "not a priority setup now"

    return {
        "label": label,
        "grade": grade,
        "score": score,
        "price": price,
        "break": brk,
        "ext": ext,
        "dist": dist,
        "rsi": rsi,
        "room_pct": room_pct,
        "weekly_r1": weekly_r1,
        "room_left": room_left,
        "sma50_ok": sma50_ok,
        "sma200_ok": sma200_ok,
        "why": why,
        "warnings": "; ".join(warnings),
        "breakout": breakout,
        "retest": retest,
        "target_passed": target_passed,
        "room_small": room_small,
        "manage": manage,
    }


def sort_key(ticker: str, row: pd.Series, info: dict):
    score_val = to_float(info["score"], -999)
    dist_abs = 999 if info["dist"] is None else abs(info["dist"])
    ext_abs = 999 if info["ext"] is None else abs(info["ext"])
    rsi_penalty = 999 if info["rsi"] is None else abs(info["rsi"] - 55)
    etf_penalty = 1 if is_etf_ticker(ticker, row) else 0
    target_penalty = 2 if info["target_passed"] else 0
    room_penalty = room_rank(info["room_pct"])

    if info["label"] == "BUY NOW":
        bucket = 0
    elif info["label"] == "NEAR BREAKOUT":
        bucket = 1
    elif info["label"] == "WATCH":
        bucket = 2
    elif info["label"] == "WATCH / CHASE":
        bucket = 3
    else:
        bucket = 4

    return (
        bucket,
        target_penalty,
        room_penalty,
        grade_rank(info["grade"]),
        0 if info["retest"] else 1,
        etf_penalty,
        dist_abs,
        ext_abs,
        rsi_penalty,
        -score_val,
        ticker,
    )


def category_name(ticker: str, row: pd.Series, info: dict) -> str:
    etf = is_etf_ticker(ticker, row)
    kind = "ETF" if etf else "STOCK"
    if info["label"] == "BUY NOW":
        return f"BUY NOW / {kind}"
    if info["label"] == "NEAR BREAKOUT":
        return f"NEAR BREAKOUT / {kind}"
    if info["label"] == "WATCH":
        return f"WATCH / {kind}"
    if info["label"] == "WATCH / CHASE":
        return f"WATCH-CHASE / {kind}"
    return f"REJECT / {kind}"


def ranked_items(tickers, report_by_ticker, near_max_dist, chase_ext_max):
    items = []
    for t in sorted(tickers):
        row = report_by_ticker[t]
        info = classify_row(row, near_max_dist, chase_ext_max)
        items.append((t, row, info))
    items.sort(key=lambda x: sort_key(x[0], x[1], x[2]))
    return items


def print_row(ticker: str, row: pd.Series, info: dict, safe_text: str):
    room_text = fmt_pct(info["room_pct"])
    room_left_text = "" if info["room_left"] is None else f"{info['room_left']:.2f}"
    weekly_r1_text = fmt_num(info["weekly_r1"])
    label_text = color_label(info["label"])
    name = get_name_from_row(row)
    name_text = f" | name={name}" if name else ""
    etf_text = " | asset=ETF" if is_etf_ticker(ticker, row) else " | asset=STOCK"

    print(
        f"{ticker}{name_text}{etf_text} | {label_text} | safe={safe_text} | grade={info['grade']} score={info['score']} | "
        f"breakout={'Y' if info['breakout'] else 'N'} retest={'Y' if info['retest'] else 'N'} | "
        f"price={fmt_num(info['price'])} break={fmt_num(info['break'])} | "
        f"ext={fmt_pct(info['ext'])} | dist={fmt_pct(info['dist'])} | "
        f"rsi={fmt_num(info['rsi'])} | room_pct={room_text} | "
        f"weekly_r1={weekly_r1_text} | room_left={room_left_text} | "
        f"sma50={'Y' if info['sma50_ok'] else 'N'} sma200={'Y' if info['sma200_ok'] else 'N'}"
    )
    print(f"    why: {info['why']}")
    print(f"    manage: {info['manage']}")
    if info["warnings"]:
        print(f"    safe warning: {info['warnings']}")


def print_quick_picks(title: str, items, topn: int):
    print(f"\n--- {title} ---")
    picks = []
    for t, row, info in items:
        if info["label"] in {"BUY NOW", "NEAR BREAKOUT"}:
            picks.append((t, row, info))
    if not picks:
        print("(none)")
        return []
    shown = []
    for i, (t, row, info) in enumerate(picks[:topn], start=1):
        name = get_name_from_row(row)
        asset = "ETF" if is_etf_ticker(t, row) else "STOCK"
        print(
            f"{i:02d}. {t} {name} [{asset}] | {info['label']} | grade={info['grade']} score={info['score']} | "
            f"price={fmt_num(info['price'])} break={fmt_num(info['break'])} | dist={fmt_pct(info['dist'])} room={fmt_pct(info['room_pct'])} | {info['manage']}"
        )
        shown.append(t)
    return shown


def print_focus_details(title: str, items, safe_text: str, detail_top: int):
    print(f"\n--- {title} ---")
    picks = [(t, row, info) for t, row, info in items if info["label"] in {"BUY NOW", "NEAR BREAKOUT", "WATCH"}]
    if not picks:
        print("(none)")
        return
    for idx, (t, row, info) in enumerate(picks[:detail_top], start=1):
        print(f"\n[{idx}순위 상세]")
        print_row(t, row, info, safe_text)
    if len(picks) > detail_top:
        print(f"\n(나머지 {len(picks) - detail_top}개 상세는 생략. 필요하면 --detail-top 값을 늘리거나 --full-details 사용)")


def print_grouped(title: str, items, safe_text: str, show_rejects: bool):
    print(f"\n--- {title} ---")
    if not items:
        print("(none)")
        return
    ordered_categories = [
        "BUY NOW / STOCK", "BUY NOW / ETF", "NEAR BREAKOUT / STOCK", "NEAR BREAKOUT / ETF",
        "WATCH / STOCK", "WATCH / ETF", "WATCH-CHASE / STOCK", "WATCH-CHASE / ETF",
        "REJECT / STOCK", "REJECT / ETF",
    ]
    by_cat = {k: [] for k in ordered_categories}
    for t, row, info in items:
        by_cat.setdefault(category_name(t, row, info), []).append((t, row, info))

    printed_any = False
    for cat in ordered_categories:
        rows = by_cat.get(cat, [])
        if not rows:
            continue
        if (not show_rejects) and cat.startswith("REJECT"):
            continue
        printed_any = True
        print(f"\n[{cat}]")
        for t, row, info in rows:
            print_row(t, row, info, safe_text)
    if not printed_any:
        print("(none)")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--manual", default="premarket_manual_korea.csv")
    ap.add_argument("--report", default="report_v2.csv")
    ap.add_argument("--near-max-dist", type=float, default=2.5)
    ap.add_argument("--chase-ext-max", type=float, default=1.5)
    ap.add_argument("--quick-top", type=int, default=10)
    ap.add_argument("--detail-top", type=int, default=1, help="show only top N detailed picks per pool")
    ap.add_argument("--show-rejects", action="store_true")
    ap.add_argument("--full-details", action="store_true", help="show full grouped details instead of focused top-N details")
    args = ap.parse_args()

    manual = load_manual_tickers(args.manual)
    report = load_report(args.report)

    manual_set = set(manual["ticker"].tolist())
    safe_set = set(report["ticker"].astype(str).str.upper().tolist())
    report_by_ticker = {r["ticker"]: r for _, r in report.iterrows()}

    inter = ranked_items(manual_set & safe_set, report_by_ticker, args.near_max_dist, args.chase_ext_max)
    sonly = ranked_items(safe_set - manual_set, report_by_ticker, args.near_max_dist, args.chase_ext_max)
    monly = sorted(manual_set - safe_set)

    print("=== V9 FOCUSED REVIEW ===")
    print(f"manual tickers in report: {len(manual_set & safe_set)} | safe top: {len(safe_set)}")
    print("Ranking priority: BUY NOW > NEAR BREAKOUT > target not passed > wider room > better grade > retest > STOCK > ETF > closer to break > less extended")
    print("Default view = top summary + top-N detail only. Use --full-details if you still want everything.")

    print_quick_picks("QUICK PICKS / MANUAL ∩ SAFE", inter, args.quick_top)
    print_quick_picks("QUICK PICKS / SAFE ONLY", sonly, args.quick_top)

    print("\n(기본은 상단 요약 + 각 풀 1순위 상세만 보여줌. 바쁠 때 한눈에 보라고 이렇게 줄였음)")

    if args.full_details:
        print_grouped("MANUAL ∩ SAFE", inter, "Y", show_rejects=args.show_rejects)
    else:
        print_focus_details("MANUAL ∩ SAFE / TOP DETAIL", inter, "Y", args.detail_top)

    print("\n--- MANUAL ONLY ---")
    if not monly:
        print("(none)")
    else:
        print("(manual-only tickers are shown as WATCH because SAFE metrics are unavailable in report_v2)")
        for t in monly:
            print(f"{t} | WATCH | safe=N | why: manual-only ticker present, but not in SAFE report")

    if args.full_details:
        print_grouped("SAFE ONLY", sonly, "Y", show_rejects=args.show_rejects)
    else:
        print_focus_details("SAFE ONLY / TOP DETAIL", sonly, "Y", args.detail_top)

    print("\nLegend:")
    print("- QUICK PICKS = 위 요약")
    print("- TOP DETAIL = 각 풀에서 상위 N개만 상세")
    print("- wider room is ranked higher; target-passed setups are pushed down")
    print("- --detail-top 3 처럼 늘리면 상세를 3개까지 볼 수 있음")
    print("- --full-details 붙이면 예전처럼 전체 상세를 다 봄")


if __name__ == "__main__":
    main()
