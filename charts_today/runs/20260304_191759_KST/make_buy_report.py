#!/usr/bin/env python3
"""
make_buy_report.py

Create a "buy recommendation report" (CSV + optional PDF) from the latest scan output.

Inputs (auto-detected):
  - newest report_v2_*_KST.csv if exists, else report_v2.csv

Outputs:
  - buy_report_<YYYYMMDD_HHMMSS>_KST.csv
  - buy_report_<YYYYMMDD_HHMMSS>_KST.pdf   (if --pdf)

What it contains:
  - ranked top N rows from report_v2
  - key columns for execution + monitoring:
      ticker, grade, score, price, gap_pct, rsi14,
      daily_break_level, daily_breakout, daily_retest,
      weekly_r1, room_to_weekly_r1_pct, px_vs_sma50, px_vs_sma200

Usage:
  python make_buy_report.py
  python make_buy_report.py --top 10 --pdf
  python make_buy_report.py --tickers XLE,XOP,UYM,MPC,IYE --pdf
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
        "weekly_r1","room_to_weekly_r1_pct","px_vs_sma50","px_vs_sma200",
    ]
    cols = [c for c in want_cols if c in df.columns]
    out = df[cols].copy()

    if args.tickers.strip():
        keep = {t.strip().upper() for t in args.tickers.split(",") if t.strip()}
        out = out[out["ticker"].astype(str).str.upper().isin(keep)].copy()
    else:
        out = out.head(max(1, args.top)).copy()

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

            # simple table rendering (monospace-ish)
            c.setFont("Helvetica", 10)
            c.drawString(30, 560, f"Buy Report (KST {datetime.now(KST).strftime('%Y-%m-%d %H:%M:%S')})  Source={rp.name}")
            c.setFont("Helvetica", 8)

            # build text lines
            # fixed-width formatting
            lines = []
            header = " | ".join([f"{col}" for col in cols])
            lines.append(header)
            lines.append("-" * min(200, len(header)))

            for _, r in out.iterrows():
                row = " | ".join([str(r.get(col, "")) for col in cols])
                lines.append(row)

            y = 540
            for line in lines[:55]:  # one page
                c.drawString(30, y, line[:180])
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
