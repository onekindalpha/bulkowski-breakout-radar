#!/usr/bin/env python3
"""
run_pipeline_v3_ab_v2.py

Adds per-run archiving so your folder doesn't get messy:
- Creates <out-root>/<YYYYMMDD_HHMMSS_KST>/ per run
- Copies all artifacts (csv/xlsx/pdf/txt/log + watchlists + scripts snapshot)
- Writes:
  - manifest.txt
  - LATEST_RUN.txt

Usage:
  python run_pipeline_v3_ab_v2.py --intraday --hold-bars 3 --top 10 --max-2x 5 --pdf
"""
import argparse
import subprocess
import sys
from pathlib import Path
from datetime import datetime
from zoneinfo import ZoneInfo
import shutil
import glob

KST = ZoneInfo("Asia/Seoul")

def run(cmd):
    print("\\n$ " + " ".join(cmd))
    p = subprocess.run(cmd)
    if p.returncode != 0:
        raise SystemExit(p.returncode)

def pick_first(names):
    for n in names:
        if Path(n).exists():
            return n
    return None

def kst_stamp():
    return datetime.now(KST).strftime("%Y%m%d_%H%M%S_KST")

def ensure_dir(p: Path):
    p.mkdir(parents=True, exist_ok=True)

def copy_patterns(dst: Path, patterns: list[str]):
    copied = []
    for pat in patterns:
        for f in glob.glob(pat):
            src = Path(f)
            if src.is_file():
                try:
                    shutil.copy2(src, dst / src.name)
                    copied.append(src.name)
                except Exception:
                    pass
    return sorted(set(copied))

def archive_run(out_root: Path, run_id: str, args_text: str):
    run_dir = out_root / run_id
    ensure_dir(run_dir)

    patterns = [
        "premarket_auto*.csv",
        "premarket_manual*.csv",
        "premarket.csv",
        "premarket_*.csv",
        "candidates*.txt",
        "candidates_meta*.csv",
        "report_v2*.csv",
        "buy_report_*_KST.*",
        "buy_report_latest.txt",
        "scan_skipped.log",
        "signals_*_KST.csv",
        "positions*.csv",
        "tickers.txt",
        "macro_watch_yahoo.txt",
        "tickers_core.txt",
        "tickers_leverage2x.txt",
        "finviz_manual.txt",
        "finviz_manul.txt",
        "*.py",
    ]
    copied = copy_patterns(run_dir, patterns)

    (run_dir / "manifest.txt").write_text(
        f"run_id={run_id}\\n"
        f"created_kst={datetime.now(KST).strftime('%Y-%m-%d %H:%M:%S KST')}\\n"
        f"cwd={Path.cwd()}\\n"
        f"python={sys.executable}\\n\\n"
        f"args:\\n{args_text}\\n\\n"
        f"copied_files={len(copied)}\\n" + "\\n".join(copied) + "\\n",
        encoding="utf-8"
    )
    (out_root / "LATEST_RUN.txt").write_text(str(run_dir.resolve()) + "\\n", encoding="utf-8")
    return run_dir, copied

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--break-mode", choices=["a","b"], default=None,
                    help="A=60d high, B=pattern neckline/triangle (fallback 60d). If omitted, prompts.")
    ap.add_argument("--refresh-bad", action="store_true")
    ap.add_argument("--top", type=int, default=10)
    ap.add_argument("--max-2x", type=int, default=5)
    ap.add_argument("--intraday", action="store_true")
    ap.add_argument("--hold-bars", type=int, default=3)
    ap.add_argument("--no-audit", action="store_true")
    ap.add_argument("--buy-top", type=int, default=20)
    ap.add_argument("--pdf", action="store_true")
    ap.add_argument("--out-root", default="runs", help="Base folder to store per-run outputs")
    ap.add_argument("--no-archive", action="store_true", help="Disable per-run archiving")
    args = ap.parse_args()

    if args.break_mode is None:
        ans = input("Select break-mode (A=60d high, B=pattern neckline/triangle) [B]: ").strip().lower()
        args.break_mode = "a" if ans == "a" else "b"
        print(f"[mode] break-mode={args.break_mode.upper()}\\n")

    py = sys.executable or "python"

    if Path("sync_tickers_txt.py").exists():
        run([py, "sync_tickers_txt.py"])

    cmd = [py, "update_premarket_yf_auto_fast_v2.py"]
    if args.refresh_bad:
        cmd.append("--refresh-bad")
    run(cmd)

    if args.break_mode == "a":
        scan1 = pick_first([
            "bulkowski_scan_from_debugcsv_strict_v4.py",
            "bulkowski_scan_from_debugcsv_strict_v3.py",
            "bulkowski_scan_from_debugcsv_strict_v2.py",
            "bulkowski_scan_from_debugcsv_strict.py",
        ])
        if scan1 is None:
            scan1 = pick_first(["bulkowski_scan_from_debugcsv_pattern_v3.py",
                               "bulkowski_scan_from_debugcsv_pattern_v2.py"])
    else:
        scan1 = pick_first(["bulkowski_scan_from_debugcsv_pattern_v3.py",
                           "bulkowski_scan_from_debugcsv_pattern_v2.py"])

    if scan1 is None:
        raise SystemExit("No 1차 후보 스캐너를 찾지 못했습니다 (bulkowski_scan_*.py).")

    cmd = [py, scan1,
           "--top", str(args.top),
           "--out", "candidates.txt",
           "--max-2x", str(args.max_2x)]
    if ("pattern" in scan1) or ("strict_v4" in scan1):
        cmd += ["--break-mode", args.break_mode]
    cmd += ["--groups", "tickers_core,tickers_leverage2x,finviz_manual,macro_watch_yahoo"]
    run(cmd)

    run([py, "make_premarket_manual_5.py"])

    merge_py = pick_first(["merge_premarkets_v2.py", "merge_premarkets.py"])
    if merge_py is None:
        raise SystemExit("merge_premarkets*.py not found")
    run([py, merge_py])

    scan2 = pick_first([
        "scan_candidates_v2_safe_v7_strict_v2d.py",
        "scan_candidates_v2_safe_v7_strict_v2c.py",
        "scan_candidates_v2_safe_v7_strict_v2.py",
    ])
    if scan2 is None:
        raise SystemExit("scan_candidates*.py not found")

    cmd = [py, scan2]
    if "v2d" in scan2:
        cmd += ["--break-mode", args.break_mode]
    if args.intraday:
        cmd += ["--intraday", "--hold-bars", str(args.hold_bars)]
    run(cmd)

    buy_py = pick_first(["make_buy_report_v9.py", "make_buy_report_v8.py", "make_buy_report_v7.py"])
    if buy_py is None:
        raise SystemExit("make_buy_report_v7/v8/v9.py not found")

    cmd = [py, buy_py, "--top", str(args.buy_top), "--xlsx"]
    if args.pdf:
        cmd.append("--pdf")
    run(cmd)

    if (not args.no_audit) and Path("ticker_audit.py").exists():
        run([py, "ticker_audit.py"])

    lf = Path("buy_report_latest.txt")
    if lf.exists():
        print("\\n" + ("="*80))
        print("FINAL BUY OPINION (LAST BLOCK)".center(80))
        print(("="*80) + "\\n")
        print(lf.read_text(encoding="utf-8").rstrip())
        print("\\n" + ("="*80))
    else:
        print("\\n[warn] buy_report_latest.txt not found (buy report may have failed).")

    if not args.no_archive:
        out_root = Path(args.out_root)
        ensure_dir(out_root)
        run_id = kst_stamp()
        args_text = " ".join(sys.argv[1:])
        run_dir, copied = archive_run(out_root, run_id, args_text)
        print(f"\\nArchived outputs -> {run_dir}  (files={len(copied)})")
        print(f"Latest pointer -> {out_root/'LATEST_RUN.txt'}")

if __name__ == "__main__":
    main()
