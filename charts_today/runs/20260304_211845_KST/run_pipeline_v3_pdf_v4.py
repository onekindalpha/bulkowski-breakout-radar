#!/usr/bin/env python3
"""
run_pipeline_v3_pdf_v3.py
- B-mode pattern break levels (neckline/triangle resistance) via bulkowski_scan_from_debugcsv_pattern_v2.py
- candidates-only final scan (no old manual leftovers) via scan_candidates_v2_safe_v7_strict_v2c.py
- buy report w/ terminal summary + CSV/XLSX/PDF via make_buy_report_v9.py or v8 if you copy it
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

    scan_pick = "bulkowski_scan_from_debugcsv_pattern_v2.py" if Path("bulkowski_scan_from_debugcsv_pattern_v2.py").exists() else "bulkowski_scan_from_debugcsv_pattern_v1.py"
    run([py, scan_pick,
         "--top", str(args.top),
         "--out", "candidates.txt",
         "--max-2x", str(args.max_2x),
         "--groups", "tickers_core,tickers_leverage2x,finviz_manual,macro_watch_yahoo"])

    run([py, "make_premarket_manual_5.py"])

    merge_py = "merge_premarkets_v2.py" if Path("merge_premarkets_v2.py").exists() else "merge_premarkets.py"
    run([py, merge_py])

    scan_py = "scan_candidates_v2_safe_v7_strict_v2c.py" if Path("scan_candidates_v2_safe_v7_strict_v2c.py").exists() else "scan_candidates_v2_safe_v7_strict_v2.py"
    cmd = [py, scan_py]
    if args.intraday:
        cmd += ["--intraday", "--hold-bars", str(args.hold_bars)]
    run(cmd)

    buy_py = "make_buy_report_v9.py" if Path("make_buy_report_v9.py").exists() else ("make_buy_report_v9.py" if Path("make_buy_report_v9.py").exists() else None)
    if buy_py:
        cmd = [py, buy_py, "--top", str(args.buy_top), "--xlsx"]
        if args.pdf:
            cmd.append("--pdf")
        run(cmd)
    else:
        print("\n[warn] buy report script not found.\n")

    if (not args.no_audit) and Path("ticker_audit.py").exists():
        run([py, "ticker_audit.py"])

    lf = Path("buy_report_latest.txt")
    if lf.exists():
        print("\n=== OUTPUT FILES (latest) ===")
        print(lf.read_text(encoding="utf-8").strip())

    print("\n✅ Done.\n")

if __name__ == "__main__":
    main()
