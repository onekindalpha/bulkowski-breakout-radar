#!/usr/bin/env python3
"""
run_pipeline_v3_pdf.py  (PDF-enabled runner)

Run:
  python run_pipeline_v3_pdf.py --intraday --hold-bars 3 --top 10 --max-2x 5 --pdf

Outputs:
  report_v2_..._KST.csv (+ report_v2.csv)
  buy_report_..._KST.csv
  buy_report_..._KST.xlsx (colored)
  buy_report_..._KST.pdf  (when --pdf)
"""
import argparse, subprocess, sys
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
    ap.add_argument("--buy-top", type=int, default=20)
    ap.add_argument("--pdf", action="store_true")
    args = ap.parse_args()

    py = sys.executable or "python"

    if Path("sync_tickers_txt.py").exists():
        run([py, "sync_tickers_txt.py"])

    cmd = [py, "update_premarket_yf_auto_fast_v2.py"]
    if args.refresh_bad:
        cmd.append("--refresh-bad")
    run(cmd)

    run([py, "bulkowski_scan_from_debugcsv_pattern_v1.py",
         "--top", str(args.top),
         "--out", "candidates.txt",
         "--max-2x", str(args.max_2x),
         "--groups", "tickers_core,tickers_leverage2x,finviz_manual,macro_watch_yahoo"])

    run([py, "make_premarket_manual_5.py"])

    merge_py = "merge_premarkets_v2.py" if Path("merge_premarkets_v2.py").exists() else "merge_premarkets.py"
    run([py, merge_py])

    scan_py = "scan_candidates_v2_safe_v7_strict_v2b.py" if Path("scan_candidates_v2_safe_v7_strict_v2b.py").exists() else "scan_candidates_v2_safe_v7_strict_v2.py"
    cmd = [py, scan_py]
    if args.intraday:
        cmd += ["--intraday", "--hold-bars", str(args.hold_bars)]
    run(cmd)

    if Path("make_buy_report_v6.py").exists():
        cmd = [py, "make_buy_report_v6.py", "--top", str(args.buy_top), "--xlsx"]
        if args.pdf:
            cmd.append("--pdf")
        run(cmd)
    else:
        print("\n[warn] make_buy_report_v6.py not found.\n")

    if (not args.no_audit) and Path("ticker_audit.py").exists():
        run([py, "ticker_audit.py"])

    print("\n✅ Done.\n")

if __name__ == "__main__":
    main()
