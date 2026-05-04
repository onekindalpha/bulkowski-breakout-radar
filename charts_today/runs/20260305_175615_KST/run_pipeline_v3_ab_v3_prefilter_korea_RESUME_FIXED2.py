#!/usr/bin/env python3
"""
run_pipeline_v3_ab_v3_prefilter_korea_RESUME_FIXED.py

KRX runner with resume support.
- Uses existing *_korea.py scripts.
- Allows resuming after manual entry (merge/scan2/report), matching the US pipeline UX.

Usage examples:
  python run_pipeline_v3_ab_v3_prefilter_korea_RESUME_FIXED.py --intraday --hold-bars 3 --top 10 --max-2x 5 --pdf
  python run_pipeline_v3_ab_v3_prefilter_korea_RESUME_FIXED.py --from-step merge --break-mode b --intraday --hold-bars 3 --pdf
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path
from datetime import datetime
from zoneinfo import ZoneInfo
import shutil
import glob

KST = ZoneInfo("Asia/Seoul")

def now_kst_stamp() -> str:
    return datetime.now(KST).strftime("%Y%m%d_%H%M%S_KST")

def pick_first(candidates: list[str]) -> str:
    for c in candidates:
        if Path(c).exists():
            return c
    raise FileNotFoundError(f"None of these files exist: {candidates}")

def run(cmd: list[str]) -> None:
    print("\n$ " + " ".join(cmd))
    subprocess.check_call(cmd)

def prompt_break_mode(default: str = "b") -> str:
    ans = input("Select break-mode (A=60d high, B=pattern neckline/triangle) [B]: ").strip().lower()
    if not ans:
        ans = default
    if ans not in ("a", "b"):
        print("Invalid break-mode. Use 'a' or 'b'. Defaulting to 'b'.")
        ans = "b"
    return ans

def archive_outputs(patterns: list[str], run_dir: Path) -> None:
    run_dir.mkdir(parents=True, exist_ok=True)
    copied = 0
    for pat in patterns:
        for f in glob.glob(pat):
            p = Path(f)
            if p.is_file():
                shutil.copy2(p, run_dir / p.name)
                copied += 1
    (Path("runs") / "LATEST_RUN.txt").write_text(str(run_dir) + "\n", encoding="utf-8")
    (Path("runs") / "LATEST_RUN_KOREA.txt").write_text(str(run_dir) + "\n", encoding="utf-8")
    print(f"\n[archive] Copied {copied} files to {run_dir}")

def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--break-mode", choices=["a", "b"], default=None, help="a=60d high, b=pattern neckline/triangle")
    ap.add_argument("--intraday", action="store_true")
    ap.add_argument("--hold-bars", type=int, default=3)
    ap.add_argument("--top", type=int, default=10)
    ap.add_argument("--max-2x", type=int, default=5)
    ap.add_argument("--pdf", action="store_true")
    ap.add_argument("--xlsx", action="store_true", default=True)
    ap.add_argument("--no-prefilter", action="store_true")
    ap.add_argument("--no-audit", action="store_true")
    ap.add_argument("--from-step", choices=["start", "manual", "merge", "scan2", "report"], default="start",
                    help="Resume pipeline: start=all, manual=manual+..., merge=merge+..., scan2=scan2+report, report=report only.")
    args = ap.parse_args()

    py = sys.executable or "python"

    break_mode = args.break_mode or prompt_break_mode(default="b")
    print(f"[mode] break-mode={'A' if break_mode=='a' else 'B'}")

    # Flags (simple step ordering)
    do_start = args.from_step == "start"
    do_manual = args.from_step in ("start", "manual")
    do_merge = args.from_step in ("start", "manual", "merge")
    do_scan2 = args.from_step in ("start", "manual", "merge", "scan2")
    do_report = args.from_step in ("start", "manual", "merge", "scan2", "report")

    # 0) start block: sync -> prefilter -> update -> scan1
    if do_start:
        if Path("sync_tickers_txt_korea.py").exists():
            run([py, "sync_tickers_txt_korea.py"])

        universe_file = Path("tickers_korea.txt")
        if not args.no_prefilter and Path("prefilter_universe_yf_korea.py").exists():
            run([py, "prefilter_universe_yf_korea.py",
                 "--universe", str(universe_file),
                 "--out", "universe_filtered_korea.txt",
                 "--report", "prefilter_report_korea.csv"])
            if Path("universe_filtered_korea.txt").exists():
                universe_file = Path("universe_filtered_korea.txt")

        update_py = pick_first([
            "update_premarket_yf_auto_fast_v3_korea.py",
            "update_premarket_yf_auto_fast_v2_korea.py",
            "update_premarket_yf_auto_fast_v3.py",
            "update_premarket_yf_auto_fast_v2.py",
        ])
        cmd = [py, update_py]
        if universe_file.exists():
            cmd += ["--universe-file", str(universe_file)]
        run(cmd)

        # 1) scan1 (top candidates)
        if break_mode == "a":
            scan1 = pick_first([
                "bulkowski_scan_from_debugcsv_strict_v4_korea.py",
                "bulkowski_scan_from_debugcsv_strict_v4.py",
                "bulkowski_scan_from_debugcsv_strict_v3.py",
            ])
        else:
            scan1 = pick_first([
                "bulkowski_scan_from_debugcsv_pattern_v3_korea.py",
                "bulkowski_scan_from_debugcsv_pattern_v3.py",
                "bulkowski_scan_from_debugcsv_pattern_v2.py",
            ])

        cmd = [py, scan1,
               "--top", str(args.top),
               "--out", "candidates_korea.txt",
               "--max-2x", str(args.max_2x),
               "--break-mode", break_mode,
               "--groups", "tickers_core,tickers_leverage2x,finviz_manual,macro_watch_yahoo"]
        run(cmd)

    # 2) manual entry
    if do_manual:
        run([py, "make_premarket_manual_5_korea.py"])

    # 3) merge (manual 우선)
    if do_merge:
        merge_py = pick_first([
            "merge_premarkets_v2_korea.py",
            "merge_premarkets_v2.py",
            "merge_premarkets.py",
        ])
        run([py, merge_py])

    # 4) scan2 (strict)
    if do_scan2:
        scan2 = pick_first([
            "scan_candidates_v2_safe_v7_strict_v2d_korea.py",
            "scan_candidates_v2_safe_v7_strict_v2d.py",
            "scan_candidates_v2_safe_v7_strict_v2.py",
        ])
        cmd = [py, scan2,
               "--break-mode", break_mode,
               "--premarket", "premarket_korea.csv"]
        if args.intraday:
            cmd += ["--intraday", "--hold-bars", str(args.hold_bars)]
        run(cmd)

    # 5) buy report
    if do_report:
        buy_py = pick_first([
            "make_buy_report_v9_korea.py",
            "make_buy_report_v9.py",
            "make_buy_report_v8.py",
        ])
        cmd = [py, buy_py, "--top", "20"]
        if args.xlsx:
            cmd += ["--xlsx"]
        if args.pdf:
            cmd += ["--pdf"]
        run(cmd)

        # audit
        if (not args.no_audit) and Path("ticker_audit_korea.py").exists():
            run([py, "ticker_audit_korea.py"])

        # archive
        ts = now_kst_stamp()
        run_dir = Path("runs") / f"{ts}_korea"
        patterns = [
            "universe_filtered_korea.txt",
            "prefilter_report_korea.csv",
            "premarket_auto*_korea.csv",
            "premarket_manual*_korea.csv",
            "premarket_korea.csv",
            "candidates*_korea.*",
            "candidates_meta*_korea.csv",
            "report_v2_korea*.csv",
            "buy_report_korea_*_KST.*",
            "buy_report_latest_korea.txt",
            "*.log",
        ]
        archive_outputs(patterns, run_dir)

if __name__ == "__main__":
    main()
