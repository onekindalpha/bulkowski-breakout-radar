#!/usr/bin/env python3
"""
run_pipeline_v3.py

Urgent-friendly "one command" runner.

- sync tickers.txt (union of your txt lists)
- update Yahoo snapshot
- candidates via bulkowski_scan_from_debugcsv_pattern_v1.py
- manual prices: make_premarket_manual_5.py (candidate-price-only recommended)
- merge
- final scan: scan_candidates_v2_safe_v7_strict_v2b.py if exists (prevents universe expansion when candidates exist)
- buy report: make_buy_report_v5.py (CSV always; PDF optional with --pdf)

Exit/stop signals are OFF by default (enable with --signals)
because they can be confusing intraday; use monitor_positions_onecmd_v3 for live watching.

Usage:
  python run_pipeline_v3.py --intraday --hold-bars 3 --top 10 --max-2x 5
  python run_pipeline_v3.py --intraday --hold-bars 3 --pdf
  python run_pipeline_v3.py --signals
"""

import argparse
import subprocess
import sys
from pathlib import Path


def run(cmd):
    print("\n$ " + " ".join(cmd))
    p = subprocess.run(cmd)
    if p.returncode != 0:
        raise SystemExit(p.returncode)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--refresh-bad", action="store_true")
    ap.add_argument("--top", type=int, default=10)
    ap.add_argument("--max-2x", type=int, default=3)
    ap.add_argument("--intraday", action="store_true")
    ap.add_argument("--hold-bars", type=int, default=3)
    ap.add_argument("--no-audit", action="store_true")
    ap.add_argument("--buy-top", type=int, default=15)
    ap.add_argument("--pdf", action="store_true", help="try to create buy report PDF (needs reportlab)")
    ap.add_argument("--signals", action="store_true", help="run entry/exit signals (armed) at end (off by default)")
    args = ap.parse_args()

    py = sys.executable or "python"

    # 0) sync tickers.txt
    if Path("sync_tickers_txt.py").exists():
        run([py, "sync_tickers_txt.py"])

    # 1) update
    cmd = [py, "update_premarket_yf_auto_fast_v2.py"]
    if args.refresh_bad:
        cmd.append("--refresh-bad")
    run(cmd)

    # 2) candidates
    run([py, "bulkowski_scan_from_debugcsv_pattern_v1.py",
         "--top", str(args.top),
         "--out", "candidates.txt",
         "--max-2x", str(args.max_2x),
         "--groups", "tickers_core,tickers_leverage2x,finviz_manual,macro_watch_yahoo"])

    # 3) manual prices
    run([py, "make_premarket_manual_5.py"])

    # 4) merge
    merge_py = "merge_premarkets_v2.py" if Path("merge_premarkets_v2.py").exists() else "merge_premarkets.py"
    run([py, merge_py])

    # 5) final scan
    scan_py = "scan_candidates_v2_safe_v7_strict_v2b.py" if Path("scan_candidates_v2_safe_v7_strict_v2b.py").exists() else "scan_candidates_v2_safe_v7_strict_v2.py"
    cmd = [py, scan_py]
    if args.intraday:
        cmd += ["--intraday", "--hold-bars", str(args.hold_bars)]
    run(cmd)

    # 6) buy report (v2)
    if Path("make_buy_report_v5.py").exists():
        cmd = [py, "make_buy_report_v5.py", "--top", str(args.buy_top), "--xlsx"]
        if args.pdf:
            cmd.append("--pdf")
        run(cmd)

    # 7) optional signals (armed)
    if args.signals and Path("positions.csv").exists() and Path("bulkowski_entry_exit_signals_v3_armed.py").exists():
        run([py, "bulkowski_entry_exit_signals_v3_armed.py", "--positions", "positions.csv", "--warn-dd", "2", "--stop-dd", "3"])

    # 8) audit
    if (not args.no_audit) and Path("ticker_audit.py").exists():
        run([py, "ticker_audit.py"])

    print("\n✅ Done. (report_v2*.csv + buy_report_*.csv created)\n")


if __name__ == "__main__":
    main()
