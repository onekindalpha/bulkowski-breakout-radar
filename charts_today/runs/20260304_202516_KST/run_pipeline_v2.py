#!/usr/bin/env python3
"""
run_pipeline_v2.py (UPDATED)

What changed:
- Candidate sieve step now uses Bulkowski "pattern-aware" sieve:
    bulkowski_scan_from_debugcsv_pattern_v1.py
  (instead of breakout-only strict v2)
- Optional auto buy-report generation at the end:
    make_buy_report.py --top N --pdf

Default flow:
  1) Update Yahoo snapshot (premarket_auto*.csv)
  2) Build candidates.txt via pattern-aware sieve (top N, with 2x cap)
  3) Manual Samsung prices input (premarket_manual.csv)
  4) Merge (premarket.csv)
  5) Final strict scan (report_v2*.csv) with optional intraday hold confirmation
  6) Optional: buy report CSV/PDF
  7) Optional: ticker_audit.py (stage counts + missing tickers)
  8) Optional: exit/stop signals if positions.csv exists

Usage:
  python run_pipeline_v2.py
  python run_pipeline_v2.py --intraday --hold-bars 3
  python run_pipeline_v2.py --refresh-bad
  python run_pipeline_v2.py --top 10 --max-2x 3
  python run_pipeline_v2.py --no-buy-report
  python run_pipeline_v2.py --buy-top 15
  python run_pipeline_v2.py --no-audit

Assumes these scripts exist in the same folder:
  - update_premarket_yf_auto_fast_v2.py
  - bulkowski_scan_from_debugcsv_pattern_v1.py
  - make_premarket_manual_5.py
  - merge_premarkets.py
  - scan_candidates_v2_safe_v7_strict_v2.py
  - make_buy_report.py (optional)
  - ticker_audit.py (optional)
  - bulkowski_entry_exit_signals_v2_autofill.py (optional)
"""

import argparse
import subprocess
import sys
from pathlib import Path


def run(cmd: list[str]) -> None:
    print("\n$ " + " ".join(cmd))
    p = subprocess.run(cmd)
    if p.returncode != 0:
        raise SystemExit(p.returncode)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--refresh-bad", action="store_true", help="re-test skiplist in update step (slower)")
    ap.add_argument("--top", type=int, default=10, help="top N candidates to write to candidates.txt (default 10)")
    ap.add_argument("--max-2x", type=int, default=3, help="cap how many 2x tickers can appear in candidates.txt (default 3)")
    ap.add_argument("--intraday", action="store_true", help="enable intraday hold confirmation in final scan")
    ap.add_argument("--hold-bars", type=int, default=3, help="intraday consecutive 5m closes above level (default 3)")
    ap.add_argument("--no-audit", action="store_true", help="do not run ticker_audit.py at the end")
    ap.add_argument("--no-buy-report", action="store_true", help="do not run make_buy_report.py at the end")
    ap.add_argument("--buy-top", type=int, default=15, help="top N rows for buy report (default 15)")
    args = ap.parse_args()

    py = sys.executable or "python"

    # 1) update snapshot
    update_cmd = [py, "update_premarket_yf_auto_fast_v2.py"]
    if args.refresh_bad:
        update_cmd.append("--refresh-bad")
    run(update_cmd)

    # 2) pattern-aware candidates sieve
    if not Path("bulkowski_scan_from_debugcsv_pattern_v1.py").exists():
        raise SystemExit("Missing: bulkowski_scan_from_debugcsv_pattern_v1.py")
    run([py, "bulkowski_scan_from_debugcsv_pattern_v1.py", "--top", str(args.top), "--out", "candidates.txt", "--max-2x", str(args.max_2x), "--groups", "tickers_core,tickers_leverage2x,finviz_manual,macro_watch_yahoo"]) 

    # 3) manual Samsung input
    run([py, "make_premarket_manual_5.py"])

    # 4) merge
    run([py, "merge_premarkets.py"])

    # 5) final strict scan (auto-uses candidates.txt)
    scan_cmd = [py, "scan_candidates_v2_safe_v7_strict_v2.py"]
    if args.intraday:
        scan_cmd += ["--intraday", "--hold-bars", str(args.hold_bars)]
    run(scan_cmd)

    # 6) buy report (optional)
    if (not args.no_buy_report) and Path("make_buy_report.py").exists():
        run([py, "make_buy_report.py", "--top", str(args.buy_top), "--pdf"])
    elif not args.no_buy_report:
        print("\n[info] make_buy_report.py not found -> skip buy-report step.")

    # 7) exit/stop signals (optional)
    if Path("positions.csv").exists():
        if Path("bulkowski_entry_exit_signals_v2_autofill.py").exists():
            run([py, "bulkowski_entry_exit_signals_v2_autofill.py", "--positions", "positions.csv", "--warn-dd", "2", "--stop-dd", "3"])
        elif Path("bulkowski_entry_exit_signals.py").exists():
            run([py, "bulkowski_entry_exit_signals.py", "--positions", "positions.csv", "--premarket", "premarket.csv"])
        else:
            print("\n[info] no entry/exit signals script found -> skip signals step.")
    else:
        print("\n[info] positions.csv not found -> skip signals step.")

    # 8) audit counts + missing tickers (default ON)
    if (not args.no_audit) and Path("ticker_audit.py").exists():
        run([py, "ticker_audit.py"])
    elif not args.no_audit:
        print("\n[info] ticker_audit.py not found -> skip audit step.")

    print("\n✅ Done. Check: report_v2.csv / report_v2_*_KST.csv (and buy_report_*_KST.* if enabled)\n")


if __name__ == "__main__":
    main()
