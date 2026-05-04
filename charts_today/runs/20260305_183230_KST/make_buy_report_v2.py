#!/usr/bin/env python3
"""
make_buy_report_v2.py

- Reads newest report_v2_*_KST.csv if exists, else report_v2.csv
- Writes a buy report CSV with an extra human-readable 'reason' column
- Optional PDF output via reportlab (if installed). If not installed, it prints a warning but still succeeds.

Usage:
  python make_buy_report_v2.py
  python make_buy_report_v2.py --top 15
  python make_buy_report_v2.py --tickers XLE,XOP,UYM,MPC,IYE
  python make_buy_report_v2.py --top 15 --pdf
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


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--top", type=int, default=15, help="top N rows to include (default 15)")
    ap.add_argument("--tickers", default="", help="comma-separated tickers to include (overrides --top)")
    ap.add_argument("--pdf", action="store_true", help="also write a simple PDF table (requires reportlab)")
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

    ts = datetime.now(KST).strftime("%Y%m%d_%H%M%S")
    csv_name = f"buy_report_{ts}_KST.csv"
    out.to_csv(csv_name, index=False)

    print(f"Source: {rp.name}")
    print(f"Saved: {csv_name} (rows={len(out)})")

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
