#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
manual10_legacy_review_korea_v4.py

Purpose
-------
Review manual tickers against report_v2.csv with STRICTER "BUY NOW" logic.

Key rule changes vs earlier loose behavior
------------------------------------------
1) BUY NOW is allowed ONLY when the ticker is in MANUAL ∩ SAFE
   (i.e. it exists in the final report / safe universe)
2) BUY NOW requires:
   - daily_breakout == True
   - daily_retest == True
   - px_vs_sma50 > 0
   - px_vs_sma200 > 0
   - room_to_weekly_r1_pct >= --room-min
3) If room is too small, it can NEVER be BUY NOW.
   It will be WATCH / NEAR BREAKOUT / REJECT instead.

Works well for Korea, but also fine for US if column names match.
"""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Optional
import pandas as pd


TRUE_SET = {"true", "1", "y", "yes", "t"}
FALSE_SET = {"false", "0", "n", "no", "f"}


def is_true(v) -> bool:
    s = str(v).strip().lower()
    return s in TRUE_SET


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


def load_manual_tickers(path: str) -> pd.DataFrame:
    df = pd.read_csv(path)
    tcol = find_col(df, ["ticker", "symbol"])
    if tcol is None:
        # fallback: first column
        tcol = df.columns[0]
    out = df.copy()
    out[tcol] = out[tcol].astype(str).str.strip().str.upper()
    out = out[out[tcol] != ""].copy()
    out = out.rename(columns={tcol: "ticker"})
    return out[["ticker"]].drop_duplicates().reset_index(drop=True)


def load_report(path: str) -> pd.DataFrame:
    df = pd.read_csv(path)
    # normalize ticker column
    tcol = find_col(df, ["ticker", "symbol"])
    if tcol is None:
        raise SystemExit("report file missing ticker/symbol column")
    df[tcol] = df[tcol].astype(str).str.strip().str.upper()
    df = df.rename(columns={tcol: "ticker"})
    return df


def summarize_row(r: pd.Series, safe_flag: bool, room_min: float, near_max_dist: float, chase_ext_max: float):
    price = to_float(r.get("price"), 0.0)
    brk = to_float(r.get("daily_break_level"), 0.0)
    room = to_float(r.get("room_to_weekly_r1_pct"), None)
    rsi = to_float(r.get("rsi14"), None)
    px50 = to_float(r.get("px_vs_sma50"), None)
    px200 = to_float(r.get("px_vs_sma200"), None)
    ext = to_float(r.get("ext_pct"), None)

    # ext may not exist in report_v2; derive from price/break if possible
    if ext is None and brk:
        ext = (price / brk - 1.0) * 100.0

    breakout = is_true(r.get("daily_breakout"))
    retest = is_true(r.get("daily_retest"))
    sma50_ok = (px50 is not None and px50 > 0)
    sma200_ok = (px200 is not None and px200 > 0)

    dist = None
    if brk:
        dist = (brk / price - 1.0) * 100.0

    grade = str(r.get("grade", ""))
    score = r.get("score", r.get("score_total", ""))

    warnings = []
    why = []

    if room is not None and room < room_min:
        warnings.append(f"weekly room small ({room:.2f}%)")
    if rsi is not None and rsi > 70:
        warnings.append(f"RSI>70 ({rsi:.2f})")
    if rsi is not None and rsi < 40:
        warnings.append(f"RSI low ({rsi:.2f})")
    if not sma50_ok:
        warnings.append("below or near SMA50")
    if not sma200_ok:
        warnings.append("below or near SMA200")

    # STRICT BUY NOW
    if (
        safe_flag
        and breakout
        and retest
        and sma50_ok
        and sma200_ok
        and room is not None
        and room >= room_min
    ):
        label = "BUY NOW"
        why.append("manual+safe confirmed; breakout and retest confirmed; room sufficient")
    else:
        # not BUY NOW -> classify softer
        if breakout and retest and sma50_ok and sma200_ok:
            # breakout but room too small OR not in safe
            if room is not None and room < room_min:
                label = "WATCH"
                why.append("breakout confirmed, but weekly room too small")
            elif not safe_flag:
                label = "WATCH"
                why.append("breakout confirmed, but not in SAFE intersection")
            elif ext is not None and ext > chase_ext_max:
                label = "WATCH / CHASE"
                why.append(f"already extended (+{ext:.2f}%)")
            else:
                label = "WATCH"
                why.append("breakout confirmed, but strict BUY NOW conditions not fully met")
        elif retest and dist is not None and dist <= near_max_dist and sma50_ok and sma200_ok:
            label = "NEAR BREAKOUT"
            why.append("just below break_level; retest/trigger setup looks clean")
        else:
            label = "REJECT"
            if not sma50_ok or not sma200_ok:
                why.append("structure below key moving averages")
            elif dist is not None:
                why.append(f"{dist:.2f}% below break")
            else:
                why.append("not close enough to break / trigger")

    return {
        "label": label,
        "safe_flag": safe_flag,
        "grade": grade,
        "score": score,
        "price": price,
        "break": brk,
        "ext": ext,
        "dist": dist,
        "rsi": rsi,
        "room": room,
        "sma50_ok": sma50_ok,
        "sma200_ok": sma200_ok,
        "why": "; ".join(why),
        "warnings": "; ".join(warnings),
        "breakout": breakout,
        "retest": retest,
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--manual", default="premarket_manual_korea.csv")
    ap.add_argument("--report", default="report_v2.csv")
    ap.add_argument("--room-min", type=float, default=2.0,
                    help="minimum room_to_weekly_r1_pct required for BUY NOW")
    ap.add_argument("--near-max-dist", type=float, default=2.5,
                    help="max distance below break_level to still call NEAR BREAKOUT")
    ap.add_argument("--chase-ext-max", type=float, default=1.5,
                    help="if breakout already happened and ext is above this, classify as WATCH/CHASE")
    args = ap.parse_args()

    manual = load_manual_tickers(args.manual)
    report = load_report(args.report)

    report_by_ticker = {r["ticker"]: r for _, r in report.iterrows()}
    manual_set = set(manual["ticker"].tolist())
    safe_set = set(report["ticker"].astype(str).str.upper().tolist())

    print("=== LEGACY REVIEW WITH STRICT SAFE GATE ===")
    print(f"manual tickers in report: {len(manual_set & safe_set)} | safe top: {len(safe_set)}")
    print(f"BUY_NOW rule: manual ∩ safe + breakout + retest + SMA50/200 + room>={args.room_min:.2f}%")

    # MANUAL ∩ SAFE
    print("\n--- MANUAL ∩ SAFE ---")
    inter = sorted(manual_set & safe_set)
    if not inter:
        print("(none)")
    else:
        for t in inter:
            info = summarize_row(report_by_ticker[t], True, args.room_min, args.near_max_dist, args.chase_ext_max)
            print(
                f"{t} | {info['label']} | safe=Y | grade={info['grade']} score={info['score']} | "
                f"breakout={'Y' if info['breakout'] else 'N'} retest={'Y' if info['retest'] else 'N'} | "
                f"price={info['price']:.2f} break={info['break']:.2f} | "
                f"ext={'' if info['ext'] is None else f'{info['ext']:+.2f}%'} | "
                f"dist={'' if info['dist'] is None else f'{info['dist']:+.2f}%'} | "
                f"rsi={'' if info['rsi'] is None else f'{info['rsi']:.2f}'} | "
                f"room={'' if info['room'] is None else f'{info['room']:.2f}%'} | "
                f"sma50={'Y' if info['sma50_ok'] else 'N'} sma200={'Y' if info['sma200_ok'] else 'N'}"
            )
            print(f"    why: {info['why']}")
            if info["warnings"]:
                print(f"    safe warning: {info['warnings']}")

    # MANUAL ONLY
    print("\n--- MANUAL ONLY ---")
    monly = sorted(manual_set - safe_set)
    if not monly:
        print("(none)")
    else:
        print("(manual-only tickers are not BUY NOW by rule because they fail the SAFE intersection gate)")
        # try to read merged premarket if available for extra context
        premarket = None
        for candidate in ["premarket_korea.csv", "premarket.csv"]:
            p = Path(candidate)
            if p.exists():
                try:
                    temp = pd.read_csv(p, comment="#")
                    tcol = find_col(temp, ["ticker", "symbol"])
                    pcol = find_col(temp, ["premarket", "price", "last", "close"])
                    if tcol is not None and pcol is not None:
                        temp[tcol] = temp[tcol].astype(str).str.strip().str.upper()
                        premarket = temp[[tcol, pcol]].rename(columns={tcol: "ticker", pcol: "price"})
                        break
                except Exception:
                    pass

        for t in monly:
            if premarket is not None and (premarket["ticker"] == t).any():
                px = float(premarket.loc[premarket["ticker"] == t, "price"].iloc[0])
                print(f"{t} | WATCH | safe=N | why: manual-only ticker present, but not in SAFE intersection | price={px:.2f}")
            else:
                print(f"{t} | WATCH | safe=N | why: manual-only ticker present, but not in SAFE intersection")

    # SAFE ONLY
    print("\n--- SAFE ONLY ---")
    sonly = sorted(safe_set - manual_set)
    if not sonly:
        print("(none)")
    else:
        for t in sonly:
            info = summarize_row(report_by_ticker[t], True, args.room_min, args.near_max_dist, args.chase_ext_max)
            print(
                f"{t} | {info['label']} | safe=Y | grade={info['grade']} score={info['score']} | "
                f"breakout={'Y' if info['breakout'] else 'N'} retest={'Y' if info['retest'] else 'N'} | "
                f"price={info['price']:.2f} break={info['break']:.2f} | "
                f"ext={'' if info['ext'] is None else f'{info['ext']:+.2f}%'} | "
                f"dist={'' if info['dist'] is None else f'{info['dist']:+.2f}%'} | "
                f"rsi={'' if info['rsi'] is None else f'{info['rsi']:.2f}'} | "
                f"room={'' if info['room'] is None else f'{info['room']:.2f}%'} | "
                f"sma50={'Y' if info['sma50_ok'] else 'N'} sma200={'Y' if info['sma200_ok'] else 'N'}"
            )
            print(f"    why: {info['why']}")
            if info["warnings"]:
                print(f"    safe warning: {info['warnings']}")

    print("\nLegend:")
    print("- BUY NOW = ONLY manual ∩ safe + breakout + retest + SMA50/200 + room sufficient")
    print("- NEAR BREAKOUT = just below break_level with retest/trigger setup")
    print("- WATCH = structure may be okay, but strict BUY NOW gate failed")
    print("- REJECT = not close / not clean / weak structure")
    print("- room small = likely poor reward/risk for new entry")

if __name__ == "__main__":
    main()
