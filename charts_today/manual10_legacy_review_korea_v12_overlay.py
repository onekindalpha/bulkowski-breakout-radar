#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Optional
import pandas as pd

TRUE_SET = {"true", "1", "y", "yes", "t", "on"}

GREEN = "\033[1;92m"
YELLOW = "\033[1;93m"
CYAN = "\033[1;96m"
MAGENTA = "\033[1;95m"
RED = "\033[1;91m"
BLUE = "\033[1;94m"
RESET = "\033[0m"

ETF_TICKERS = {
    "069500.KS", "390390.KS", "471760.KS", "471990.KS", "475310.KS",
}
ETF_NAME_KEYS = ["KODEX", "TIGER", "SOL", "ACE", "KBSTAR", "ARIRANG", "HANARO", "KOSEF"]
NAME_COL_CANDIDATES = [
    "name", "name_kr", "company", "company_name", "stock_name", "종목명", "한글명", "name_kor"
]
OVERLAY_TEMPLATE_COLUMNS = [
    "ticker",
    "theme_strength",      # 0~2
    "cycle_strength",      # 0~2
    "news_strength",       # 0~2
    "flow_strength",       # 0~2
    "company_quality",     # 0~2
    "prebreak_ok",         # true/false
    "must_wait_breakout",  # true/false
    "avoid_new",           # true/false
    "notes",
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


def clamp_num(v, lo=0.0, hi=2.0):
    x = to_float(v, 0.0)
    if x is None:
        x = 0.0
    return max(lo, min(hi, x))


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


def color_entry_state(state: str) -> str:
    if state == "PREBREAK OK":
        return f"{BLUE}{state}{RESET}"
    if state == "ENTRY OK":
        return f"{GREEN}{state}{RESET}"
    if state == "SMALL SIZE":
        return f"{CYAN}{state}{RESET}"
    if state == "HOLD ONLY":
        return f"{MAGENTA}{state}{RESET}"
    if state == "AVOID NEW":
        return f"{RED}{state}{RESET}"
    if state == "WATCH":
        return f"{YELLOW}{state}{RESET}"
    return state


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


def load_overlay(path: Optional[str]) -> pd.DataFrame:
    if not path:
        return pd.DataFrame(columns=OVERLAY_TEMPLATE_COLUMNS)
    p = Path(path)
    if not p.exists():
        raise SystemExit(f"overlay file not found: {path}")
    df = robust_read_csv(path)
    df = df.dropna(axis=1, how="all").dropna(axis=0, how="all")
    if df.empty:
        return pd.DataFrame(columns=OVERLAY_TEMPLATE_COLUMNS)
    tcol = find_col(df, ["ticker", "symbol"])
    if tcol is None:
        raise SystemExit("overlay file missing ticker/symbol column")
    df[tcol] = df[tcol].astype(str).str.strip().str.upper()
    return df.rename(columns={tcol: "ticker"})


def write_overlay_template(path: str) -> None:
    sample = pd.DataFrame([
        {
            "ticker": "009150.KS",
            "theme_strength": 2,
            "cycle_strength": 2,
            "news_strength": 2,
            "flow_strength": 2,
            "company_quality": 2,
            "prebreak_ok": True,
            "must_wait_breakout": False,
            "avoid_new": False,
            "notes": "AI server MLCC/FC-BGA, strong story",
        },
        {
            "ticker": "267260.KS",
            "theme_strength": 2,
            "cycle_strength": 2,
            "news_strength": 1,
            "flow_strength": 2,
            "company_quality": 2,
            "prebreak_ok": True,
            "must_wait_breakout": False,
            "avoid_new": False,
            "notes": "power infra / grid capex",
        },
        {
            "ticker": "278470.KS",
            "theme_strength": 2,
            "cycle_strength": 1,
            "news_strength": 1,
            "flow_strength": 1,
            "company_quality": 1,
            "prebreak_ok": False,
            "must_wait_breakout": True,
            "avoid_new": False,
            "notes": "strong price but late feel",
        },
    ], columns=OVERLAY_TEMPLATE_COLUMNS)
    sample.to_csv(path, index=False)


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


def thesis_state_rank(state: str) -> int:
    return {
        "INSTITUTIONAL STORY": 0,
        "STRONG STORY": 1,
        "OK STORY": 2,
        "NO OVERLAY": 3,
        "AVOID": 4,
    }.get(state, 9)


def entry_state_rank(state: str) -> int:
    return {
        "PREBREAK OK": 0,
        "ENTRY OK": 1,
        "SMALL SIZE": 2,
        "HOLD ONLY": 3,
        "AVOID NEW": 4,
        "WATCH": 5,
        "REJECT": 6,
    }.get(state, 9)


def label_rank(label: str) -> int:
    return {
        "BUY NOW": 0,
        "NEAR BREAKOUT": 1,
        "WATCH": 2,
        "WATCH / CHASE": 3,
        "REJECT": 4,
    }.get(label, 9)


def overlay_info_for_ticker(overlay_by_ticker: dict, ticker: str) -> dict:
    row = overlay_by_ticker.get(ticker)
    if row is None:
        return {
            "theme_strength": 0.0,
            "cycle_strength": 0.0,
            "news_strength": 0.0,
            "flow_strength": 0.0,
            "company_quality": 0.0,
            "prebreak_ok": False,
            "must_wait_breakout": False,
            "avoid_new": False,
            "notes": "",
            "overlay_score": 0.0,
            "thesis_state": "NO OVERLAY",
        }
    theme = clamp_num(row.get("theme_strength"))
    cycle = clamp_num(row.get("cycle_strength"))
    news = clamp_num(row.get("news_strength"))
    flow = clamp_num(row.get("flow_strength"))
    company = clamp_num(row.get("company_quality"))
    prebreak_ok = is_true(row.get("prebreak_ok"))
    must_wait_breakout = is_true(row.get("must_wait_breakout"))
    avoid_new = is_true(row.get("avoid_new"))
    notes = str(row.get("notes", "")).strip()
    overlay_score = theme + cycle + news + flow + company
    if avoid_new:
        thesis_state = "AVOID"
    elif overlay_score >= 8:
        thesis_state = "INSTITUTIONAL STORY"
    elif overlay_score >= 6:
        thesis_state = "STRONG STORY"
    elif overlay_score >= 3:
        thesis_state = "OK STORY"
    else:
        thesis_state = "NO OVERLAY"
    return {
        "theme_strength": theme,
        "cycle_strength": cycle,
        "news_strength": news,
        "flow_strength": flow,
        "company_quality": company,
        "prebreak_ok": prebreak_ok,
        "must_wait_breakout": must_wait_breakout,
        "avoid_new": avoid_new,
        "notes": notes,
        "overlay_score": overlay_score,
        "thesis_state": thesis_state,
    }


def classify_row(r: pd.Series, overlay: dict, near_max_dist: float, chase_ext_max: float):
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
    score_total = to_float(r.get("score_total"), None)
    score_raw = to_float(r.get("score"), None)
    score = score_total if score_total is not None else score_raw

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
    overbought = (rsi is not None and rsi >= 70)
    low_score = (score is not None and score <= 0)
    late_entry = target_passed

    if label in {"BUY NOW", "NEAR BREAKOUT"}:
        if overlay["avoid_new"] or overbought or low_score:
            entry_state = "AVOID NEW"
            if overlay["avoid_new"]:
                manage = "overlay says avoid -> do not open new position"
            elif overbought and low_score:
                manage = "too hot + weak score -> avoid new entry, wait for reset"
            elif overbought:
                manage = "too hot -> avoid new entry, wait for reset or tight base"
            else:
                manage = "weak score -> avoid new entry until quality improves"
        elif overlay["must_wait_breakout"] and not breakout:
            entry_state = "HOLD ONLY"
            manage = "story may be good but wait for true breakout confirmation"
        elif overlay["prebreak_ok"] and overlay["overlay_score"] >= 8 and not overbought and not low_score and not target_passed:
            entry_state = "PREBREAK OK"
            manage = "strong company/theme/cycle/news/flow story -> starter allowed before break"
        elif late_entry:
            entry_state = "HOLD ONLY"
            manage = "late / target1 passed -> prefer hold-only or wait for fresh setup"
        elif room_small:
            entry_state = "SMALL SIZE"
            manage = "room is tight -> size smaller or require stronger volume"
        else:
            entry_state = "ENTRY OK"
            manage = "valid setup -> confirm volume and keep break as control line"
    elif label.startswith("WATCH"):
        entry_state = "WATCH"
        manage = "not a priority setup now"
    else:
        entry_state = "REJECT"
        manage = "not a priority setup now"

    return {
        "label": label,
        "entry_state": entry_state,
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
        "overbought": overbought,
        "low_score": low_score,
        "late_entry": late_entry,
        "manage": manage,
        **overlay,
    }


def sort_key(ticker: str, row: pd.Series, info: dict):
    score_val = to_float(info["score"], -999)
    dist_abs = 999 if info["dist"] is None else abs(info["dist"])
    ext_abs = 999 if info["ext"] is None else abs(info["ext"])
    rsi_penalty = 999 if info["rsi"] is None else abs(info["rsi"] - 55)
    etf_penalty = 1 if is_etf_ticker(ticker, row) else 0
    return (
        entry_state_rank(info["entry_state"]),
        label_rank(info["label"]),
        0 if info["retest"] else 1,
        etf_penalty,
        thesis_state_rank(info["thesis_state"]),
        -to_float(info["overlay_score"], 0.0),
        grade_rank(info["grade"]),
        room_rank(info["room_pct"]),
        dist_abs,
        ext_abs,
        rsi_penalty,
        -score_val,
        ticker,
    )


def category_name(ticker: str, row: pd.Series, info: dict) -> str:
    kind = "ETF" if is_etf_ticker(ticker, row) else "STOCK"
    return f"{info['entry_state']} / {kind}"


def ranked_items(tickers, report_by_ticker, overlay_by_ticker, near_max_dist, chase_ext_max):
    items = []
    for t in sorted(tickers):
        row = report_by_ticker[t]
        overlay = overlay_info_for_ticker(overlay_by_ticker, t)
        info = classify_row(row, overlay, near_max_dist, chase_ext_max)
        items.append((t, row, info))
    items.sort(key=lambda x: sort_key(x[0], x[1], x[2]))
    return items


def print_row(ticker: str, row: pd.Series, info: dict, safe_text: str):
    room_text = fmt_pct(info["room_pct"])
    room_left_text = "" if info["room_left"] is None else f"{info['room_left']:.2f}"
    weekly_r1_text = fmt_num(info["weekly_r1"])
    label_text = color_label(info["label"])
    entry_text = color_entry_state(info["entry_state"])
    name = get_name_from_row(row)
    name_text = f" | name={name}" if name else ""
    etf_text = " | asset=ETF" if is_etf_ticker(ticker, row) else " | asset=STOCK"
    overlay_text = (
        f" | thesis={info['thesis_state']}({info['overlay_score']:.1f})"
        f" T/C/N/F/Q={info['theme_strength']:.0f}/{info['cycle_strength']:.0f}/{info['news_strength']:.0f}/{info['flow_strength']:.0f}/{info['company_quality']:.0f}"
    )
    print(
        f"{ticker}{name_text}{etf_text} | {label_text} | entry={entry_text} | safe={safe_text} | grade={info['grade']} score={info['score']}"
        f"{overlay_text} | breakout={'Y' if info['breakout'] else 'N'} retest={'Y' if info['retest'] else 'N'} | "
        f"price={fmt_num(info['price'])} break={fmt_num(info['break'])} | ext={fmt_pct(info['ext'])} | dist={fmt_pct(info['dist'])} | "
        f"rsi={fmt_num(info['rsi'])} | room_pct={room_text} | weekly_r1={weekly_r1_text} | room_left={room_left_text} | "
        f"sma50={'Y' if info['sma50_ok'] else 'N'} sma200={'Y' if info['sma200_ok'] else 'N'}"
    )
    print(f"    why: {info['why']}")
    print(f"    manage: {info['manage']}")
    if info.get("notes"):
        print(f"    overlay notes: {info['notes']}")
    if info["warnings"]:
        print(f"    safe warning: {info['warnings']}")


def print_summary(title: str, items, allowed_states: set[str], topn: int):
    print(f"\n--- {title} ---")
    picks = [(t, row, info) for t, row, info in items if info["entry_state"] in allowed_states]
    if not picks:
        print("(none)")
        return []
    shown = []
    for i, (t, row, info) in enumerate(picks[:topn], start=1):
        name = get_name_from_row(row)
        asset = "ETF" if is_etf_ticker(t, row) else "STOCK"
        print(
            f"{i:02d}. {t} {name} [{asset}] | entry={info['entry_state']} | thesis={info['thesis_state']}({info['overlay_score']:.1f}) | {info['label']} | "
            f"grade={info['grade']} score={info['score']} | price={fmt_num(info['price'])} break={fmt_num(info['break'])} | "
            f"dist={fmt_pct(info['dist'])} room={fmt_pct(info['room_pct'])} | {info['manage']}"
        )
        shown.append(t)
    return shown


def print_focus_details(title: str, items, safe_text: str, detail_top: int):
    print(f"\n--- {title} ---")
    picks = [(t, row, info) for t, row, info in items if info["entry_state"] in {"PREBREAK OK", "ENTRY OK", "SMALL SIZE", "HOLD ONLY", "AVOID NEW", "WATCH"}]
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
        "PREBREAK OK / STOCK", "PREBREAK OK / ETF",
        "ENTRY OK / STOCK", "ENTRY OK / ETF",
        "SMALL SIZE / STOCK", "SMALL SIZE / ETF",
        "HOLD ONLY / STOCK", "HOLD ONLY / ETF",
        "AVOID NEW / STOCK", "AVOID NEW / ETF",
        "WATCH / STOCK", "WATCH / ETF",
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
    ap.add_argument("--overlay", default=None, help="optional CSV for manual thesis flags")
    ap.add_argument("--write-overlay-template", default=None, help="write sample overlay CSV and exit")
    ap.add_argument("--near-max-dist", type=float, default=2.5)
    ap.add_argument("--chase-ext-max", type=float, default=1.5)
    ap.add_argument("--quick-top", type=int, default=10)
    ap.add_argument("--detail-top", type=int, default=1)
    ap.add_argument("--show-rejects", action="store_true")
    ap.add_argument("--full-details", action="store_true")
    args = ap.parse_args()

    if args.write_overlay_template:
        write_overlay_template(args.write_overlay_template)
        print(f"Saved overlay template: {args.write_overlay_template}")
        return

    manual = load_manual_tickers(args.manual)
    report = load_report(args.report)
    overlay = load_overlay(args.overlay)

    manual_set = set(manual["ticker"].tolist())
    safe_set = set(report["ticker"].astype(str).str.upper().tolist())
    report_by_ticker = {r["ticker"]: r for _, r in report.iterrows()}
    overlay_by_ticker = {r["ticker"]: r for _, r in overlay.iterrows()} if not overlay.empty else {}

    inter = ranked_items(manual_set & safe_set, report_by_ticker, overlay_by_ticker, args.near_max_dist, args.chase_ext_max)
    sonly = ranked_items(safe_set - manual_set, report_by_ticker, overlay_by_ticker, args.near_max_dist, args.chase_ext_max)
    monly = sorted(manual_set - safe_set)

    print("=== V12 OVERLAY REVIEW ===")
    print(f"manual tickers in report: {len(manual_set & safe_set)} | safe top: {len(safe_set)} | overlay rows: {len(overlay_by_ticker)}")
    print("Ranking priority: PREBREAK OK > ENTRY OK > SMALL SIZE > HOLD ONLY > AVOID NEW > WATCH > REJECT")
    print("Within each state: BUY NOW > NEAR BREAKOUT > retest > STOCK > ETF > thesis > overlay_score > grade > room > dist > ext")
    print("Overlay purpose: let you reward strong company/theme/cycle/news/flow stories while watching videos/news.")

    print_summary("NEW ENTRY / MANUAL ∩ SAFE", inter, {"PREBREAK OK", "ENTRY OK", "SMALL SIZE"}, args.quick_top)
    print_summary("NEW ENTRY / SAFE ONLY", sonly, {"PREBREAK OK", "ENTRY OK", "SMALL SIZE"}, args.quick_top)
    print_summary("HOLD ONLY / MANUAL ∩ SAFE", inter, {"HOLD ONLY"}, args.quick_top)
    print_summary("AVOID NEW / MANUAL ∩ SAFE", inter, {"AVOID NEW"}, args.quick_top)
    print_summary("HOLD ONLY / SAFE ONLY", sonly, {"HOLD ONLY"}, args.quick_top)
    print_summary("AVOID NEW / SAFE ONLY", sonly, {"AVOID NEW"}, args.quick_top)

    print("\n(기본은 actionable summary + 각 풀 상위 상세만 보여줌. overlay가 있으면 이야기의 질을 같이 반영함.)")

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
            print(f"{t} | WATCH | entry=WATCH | safe=N | why: manual-only ticker present, but not in SAFE report")

    if args.full_details:
        print_grouped("SAFE ONLY", sonly, "Y", show_rejects=args.show_rejects)
    else:
        print_focus_details("SAFE ONLY / TOP DETAIL", sonly, "Y", args.detail_top)

    print("\nLegend:")
    print("- PREBREAK OK = 회사/테마/사이클/뉴스/수급 스토리가 강해 돌파 전 starter 허용")
    print("- ENTRY OK = 신규매수 후보")
    print("- SMALL SIZE = 신규매수 가능하지만 room이 좁아 비중 축소 필요")
    print("- HOLD ONLY = 이미 들고 있는 사람 관리용, 신규 진입은 비추")
    print("- AVOID NEW = 과열 / 점수 문제 / overlay 회피 플래그 등으로 신규 진입 비추")
    print("- --overlay thesis_overlay.csv 로 수동 확신도 반영 가능")
    print("- --write-overlay-template thesis_overlay_template.csv 로 샘플 생성 가능")


if __name__ == "__main__":
    main()
