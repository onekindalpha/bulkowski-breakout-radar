#!/usr/bin/env python3
"""
run_pipeline_v3_pdf_v5.py

Guarantees:
- BUY OPINION blocks are printed as the LAST terminal output (no scrolling).
- Uses:
  - sync_tickers_txt.py (optional)
  - update_premarket_yf_auto_fast_v2.py
  - bulkowski_scan_from_debugcsv_pattern_v2.py (pattern B-levels)
  - make_premarket_manual_5.py
  - merge_premarkets_v2.py
  - scan_candidates_v2_safe_v7_strict_v2c.py (candidates-only + pattern break_level override)
  - make_buy_report_v9.py (writes buy_report_latest.txt)
  - ticker_audit.py (optional) BEFORE final BUY opinion print
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
    ap.add_argument("--break-mode", choices=["a","b"], default=None,
                    help="A=prior-60d-high (option A); B=pattern neckline/resistance (option B). If omitted, you'll be prompted.")
    ap.add_argument("--intraday", action="store_true")
    ap.add_argument("--hold-bars", type=int, default=3)
    ap.add_argument("--no-audit", action="store_true")
    ap.add_argument("--buy-top", type=int, default=20)
    ap.add_argument("--pdf", action="store_true")
    args = ap.parse_args()

    if args.break_mode is None:
        ans = input("Select break-mode (A=60d high, B=pattern neckline/triangle) [B]: ").strip().lower()
        args.break_mode = 'a' if ans == 'a' else 'b'
        print(f"[mode] break-mode={args.break_mode.upper()}\n")

    py = sys.executable or "python"

    if Path("sync_tickers_txt.py").exists():
        run([py, "sync_tickers_txt.py"])

    cmd = [py, "update_premarket_yf_auto_fast_v2.py"]
    if args.refresh_bad:
        cmd.append("--refresh-bad")
    run(cmd)

    scan_pick = None
    if args.break_mode == 'a':
        for c in ['bulkowski_scan_from_debugcsv_strict_v3.py','bulkowski_scan_from_debugcsv_strict_v2.py','bulkowski_scan_from_debugcsv_strict.py']:
            if Path(c).exists():
                scan_pick = c
                break
        if scan_pick is None:
            scan_pick = 'bulkowski_scan_from_debugcsv_pattern_v3.py' if Path('bulkowski_scan_from_debugcsv_pattern_v3.py').exists() else 'bulkowski_scan_from_debugcsv_pattern_v2.py'
    else:
        scan_pick = 'bulkowski_scan_from_debugcsv_pattern_v3.py' if Path('bulkowski_scan_from_debugcsv_pattern_v3.py').exists() else 'bulkowski_scan_from_debugcsv_pattern_v2.py'

    run([py, scan_pick,
         "--top", str(args.top),
         "--out", "candidates.txt",
         "--max-2x", str(args.max_2x),
         "--break-mode", args.break_mode,
         "--groups", "tickers_core,tickers_leverage2x,finviz_manual,macro_watch_yahoo"])

    run([py, "make_premarket_manual_5.py"])

    merge_py = "merge_premarkets_v2.py" if Path("merge_premarkets_v2.py").exists() else "merge_premarkets.py"
    run([py, merge_py])

    scan_py = "scan_candidates_v2_safe_v7_strict_v2d.py" if Path("scan_candidates_v2_safe_v7_strict_v2d.py").exists() else ("scan_candidates_v2_safe_v7_strict_v2c.py" if Path("scan_candidates_v2_safe_v7_strict_v2c.py").exists() else "scan_candidates_v2_safe_v7_strict_v2.py")
    cmd = [py, scan_py, '--break-mode', args.break_mode]
    if args.intraday:
        cmd += ["--intraday", "--hold-bars", str(args.hold_bars)]
    run(cmd)

    buy_py = "make_buy_report_v9.py" if Path("make_buy_report_v9.py").exists() else ("make_buy_report_v8.py" if Path("make_buy_report_v8.py").exists() else None)
    if buy_py:
        cmd = [py, buy_py, "--top", str(args.buy_top), "--xlsx"]
        if args.pdf:
            cmd.append("--pdf")
        run(cmd)
    else:
        print("\n[warn] buy report script not found.\n")

    # Audit BEFORE final BUY OPINION print (so audit doesn't push it up)
    if (not args.no_audit) and Path("ticker_audit.py").exists():
        run([py, "ticker_audit.py"])

    # OUTPUT FILES (latest)
    lf = Path("buy_report_latest.txt")
    if lf.exists():
        # Print *as last output* with clear separators and some padding.
        print("\n" + ("="*80))
        print("FINAL BUY OPINION (THIS IS THE LAST BLOCK)".center(80))
        print(("="*80) + "\n")
        print(lf.read_text(encoding="utf-8").rstrip())
        print("\n" + ("="*80))
        print("END".center(80))
        print(("="*80) + "\n")
    else:
        print("\n[warn] buy_report_latest.txt not found (buy report may have failed).\n")

if __name__ == "__main__":
    main()
