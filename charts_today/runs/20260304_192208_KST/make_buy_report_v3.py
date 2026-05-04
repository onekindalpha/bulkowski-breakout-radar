#!/usr/bin/env python3
"""
make_buy_report_v3.py

Adds BUY_NOW (Y/N) based on a strict, explicit rule so you don't have to interpret columns mentally.

Rule (default):
BUY_NOW = all true:
  1) breakout_confirmed_close == True
  2) breakout_volume_confirmed == True
  3) If breakout_confirmed_intraday column exists: breakout_confirmed_intraday == True
  4) price >= daily_break_level
  5) price <= daily_break_level * (1 + buy_chase_pct/100)   # don't chase too far above break line
  6) room_to_weekly_r1_pct >= buy_min_room_pct              # avoid buying into immediate resistance
  7) buy_rsi_min <= rsi14 <= buy_rsi_max                    # avoid too weak/too overbought

You can relax/tighten via CLI flags.

Outputs:
  - buy_report_<stamp>_KST.csv with:
      BUY_NOW, BUY_NOW_REASON, reason (summary), and key scan columns
  - Optional PDF (if reportlab installed)

Usage:
  python make_buy_report_v3.py --top 15
  python make_buy_report_v3.py --top 15 --pdf
  python make_buy_report_v3.py --tickers XLE,XOP,UYM,MPC,IYE
"""

from __future__ import annotations

import argparse
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd

KST = ZoneInfo("Asia/Seoul")


def newest_report_path() -> Path:
    stamped = sorted(Path(".").glob("report_v2_*_KST.csv"), key=lambda p: p.stat().st_mtime, reverse=True)
    if stamped:
        return stamped[0]
    p = Path("report_v2.csv")
    if not p.exists():
        raise FileNotFoundError("No report found: report_v2.csv or report_v2_*_KST.csv")
    return p


def _fmt_bool(x) -> str:
    try:
        return "Y" if bool(x) else "N"
    except Exception:
        return "N"


def _to_float(x):
    try:
        return float(x)
    except Exception:
        return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--top", type=int, default=15)
    ap.add_argument("--tickers", default="")
    ap.add_argument("--pdf", action="store_true")
    # BUY_NOW knobs
    ap.add_argument("--buy-chase-pct", type=float, default=2.0, help="max % above break_level allowed for BUY_NOW (default 2.0)")
    ap.add_argument("--buy-min-room-pct", type=float, default=1.0, help="min room_to_weekly_r1_pct for BUY_NOW (default 1.0)")
    ap.add_argument("--buy-rsi-min", type=float, default=45.0, help="min RSI14 for BUY_NOW (default 45)")
    ap.add_argument("--buy-rsi-max", type=float, default=75.0, help="max RSI14 for BUY_NOW (default 75)")
    ap.add_argument("--require-intraday", action="store_true", help="force requiring breakout_confirmed_intraday even if column missing")
    ap.add_argument("--no-intraday", action="store_true", help="do NOT require breakout_confirmed_intraday even if present")
    args = ap.parse_args()

    rp = newest_report_path()
    df = pd.read_csv(rp, comment="#")
    df.columns = [c.strip() for c in df.columns]

    want_cols = [
        "ticker","grade","score","price","gap_pct","rsi14",
        "daily_break_level","daily_breakout","daily_retest",
        "breakout_confirmed_close","breakout_volume_confirmed","breakout_confirmed_intraday",
        "weekly_r1","room_to_weekly_r1_pct","px_vs_sma50","px_vs_sma200",
    ]
    cols = [c for c in want_cols if c in df.columns]
    out = df[cols].copy()

    if args.tickers.strip():
        keep = {t.strip().upper() for t in args.tickers.split(",") if t.strip()}
        out = out[out["ticker"].astype(str).str.upper().isin(keep)].copy()
    else:
        out = out.head(max(1, args.top)).copy()

    # Human readable reason (summary of key flags/metrics)
    def build_reason(r):
        parts = []
        if "grade" in out.columns and pd.notna(r.get("grade")):
            parts.append(f"Grade={r.get('grade')}")
        if "score" in out.columns and pd.notna(r.get("score")):
            parts.append(f"Score={r.get('score')}")
        if "breakout_confirmed_intraday" in out.columns:
            parts.append(f"IntradayHold={_fmt_bool(r.get('breakout_confirmed_intraday'))}")
        if "breakout_confirmed_close" in out.columns:
            parts.append(f"CloseBreak={_fmt_bool(r.get('breakout_confirmed_close'))}")
        if "breakout_volume_confirmed" in out.columns:
            parts.append(f"VolConfirm={_fmt_bool(r.get('breakout_volume_confirmed'))}")
        if "daily_retest" in out.columns:
            parts.append(f"Retest={_fmt_bool(r.get('daily_retest'))}")
        if "rsi14" in out.columns and pd.notna(r.get("rsi14")):
            parts.append(f"RSI={float(r.get('rsi14')):.1f}")
        if "room_to_weekly_r1_pct" in out.columns and pd.notna(r.get("room_to_weekly_r1_pct")):
            parts.append(f"RoomR1={float(r.get('room_to_weekly_r1_pct')):.1f}%")
        if "gap_pct" in out.columns and pd.notna(r.get("gap_pct")):
            parts.append(f"Gap={float(r.get('gap_pct')):.2f}%")
        if "daily_break_level" in out.columns and pd.notna(r.get("daily_break_level")):
            parts.append(f"BreakLvl={float(r.get('daily_break_level')):.2f}")
        return " | ".join(parts)

    out["reason"] = out.apply(build_reason, axis=1)

    # BUY_NOW decision
    has_intraday_col = "breakout_confirmed_intraday" in out.columns
    require_intraday = False
    if args.no_intraday:
        require_intraday = False
    elif args.require_intraday:
        require_intraday = True
    else:
        # default: require intraday if column exists (you usually run --intraday --hold-bars 3)
        require_intraday = has_intraday_col

    def buy_eval(r):
        fail = []
        # 1) close confirm
        if "breakout_confirmed_close" in out.columns:
            if not bool(r.get("breakout_confirmed_close")):
                fail.append("CloseBreak=N")
        else:
            fail.append("CloseBreak=missing")

        # 2) volume confirm
        if "breakout_volume_confirmed" in out.columns:
            if not bool(r.get("breakout_volume_confirmed")):
                fail.append("VolConfirm=N")
        else:
            fail.append("VolConfirm=missing")

        # 3) intraday hold confirm (optional)
        if require_intraday:
            if not has_intraday_col:
                fail.append("IntradayHold=missing")
            else:
                if not bool(r.get("breakout_confirmed_intraday")):
                    fail.append("IntradayHold=N")

        # numbers
        price = _to_float(r.get("price"))
        lvl = _to_float(r.get("daily_break_level"))
        rsi = _to_float(r.get("rsi14"))
        room = _to_float(r.get("room_to_weekly_r1_pct"))

        # 4) price >= level
        if price is None or lvl is None:
            fail.append("Price/Level=missing")
        else:
            if price < lvl:
                fail.append("BelowBreakLvl")
            # 5) don't chase too far
            if price > lvl * (1.0 + args.buy_chase_pct/100.0):
                fail.append(f"Chase>{args.buy_chase_pct:.1f}%")

        # 6) room to weekly R1
        if room is None:
            fail.append("RoomR1=missing")
        else:
            if room < args.buy_min_room_pct:
                fail.append(f"RoomR1<{args.buy_min_room_pct:.1f}%")

        # 7) RSI band
        if rsi is None:
            fail.append("RSI=missing")
        else:
            if rsi < args.buy_rsi_min:
                fail.append(f"RSI<{args.buy_rsi_min:.0f}")
            if rsi > args.buy_rsi_max:
                fail.append(f"RSI>{args.buy_rsi_max:.0f}")

        buy_now = (len(fail) == 0)
        return ("Y" if buy_now else "N"), ("OK" if buy_now else ";".join(fail))

    out[["BUY_NOW","BUY_NOW_REASON"]] = out.apply(lambda r: pd.Series(buy_eval(r)), axis=1)

    ts = datetime.now(KST).strftime("%Y%m%d_%H%M%S")
    csv_name = f"buy_report_{ts}_KST.csv"
    out.to_csv(csv_name, index=False)

    buy_cnt = (out["BUY_NOW"] == "Y").sum()
    print(f"Source: {rp.name}")
    print(f"Saved: {csv_name} (rows={len(out)} | BUY_NOW=Y: {buy_cnt})")
    print(f"BUY_NOW rule: CloseBreak=Y & VolConfirm=Y"
          f"{' & IntradayHold=Y' if require_intraday else ''}"
          f" & price in [BreakLvl, BreakLvl*(1+{args.buy_chase_pct:.1f}%)]"
          f" & RoomR1>={args.buy_min_room_pct:.1f}%"
          f" & RSI in [{args.buy_rsi_min:.0f},{args.buy_rsi_max:.0f}]")

    if args.pdf:
        try:
            from reportlab.lib.pagesizes import letter, landscape
            from reportlab.pdfgen import canvas

            pdf_name = f"buy_report_{ts}_KST.pdf"
            c = canvas.Canvas(pdf_name, pagesize=landscape(letter))
            c.setFont("Helvetica", 10)
            c.drawString(30, 560, f"Buy Report (KST {datetime.now(KST).strftime('%Y-%m-%d %H:%M:%S')})  Source={rp.name}")
            c.setFont("Helvetica", 8)

            lines = []
            header = " | ".join([f"{col}" for col in list(out.columns)])
            lines.append(header)
            lines.append("-" * min(220, len(header)))
            for _, rr in out.iterrows():
                row = " | ".join([str(rr.get(col, "")) for col in list(out.columns)])
                lines.append(row)

            y = 540
            for line in lines:
                c.drawString(30, y, line[:200])
                y -= 10
                if y < 30:
                    c.showPage()
                    c.setFont("Helvetica", 8)
                    y = 560

            c.save()
            print(f"Saved: {pdf_name}")
        except Exception as e:
            print(f"[warn] PDF not created: {type(e).__name__}: {e}")


if __name__ == "__main__":
    main()
