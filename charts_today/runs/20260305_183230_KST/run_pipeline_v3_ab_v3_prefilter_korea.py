#!/usr/bin/env python3
"""
run_pipeline_v3_ab_v3_prefilter_korea.py

Adds baseline prefiltering to your A/B pipeline:
- sync tickers -> tickers.txt
- prefilter_universe_yf.py -> universe_filtered.txt
- update_premarket_yf_auto_fast_v3.py --universe-file universe_filtered.txt
- 1차 후보 scanner uses same filtered universe by passing --universe-file when supported
- Keeps A/B interactive prompt
- Keeps per-run archiving (runs/<KSTSTAMP>/)

Usage:
  python run_pipeline_v3_ab_v3_prefilter_korea.py --intraday --hold-bars 3 --top 10 --max-2x 5 --pdf
"""
import argparse, subprocess, sys, shutil, glob
from pathlib import Path
from datetime import datetime
from zoneinfo import ZoneInfo

KST = ZoneInfo("Asia/Seoul")

def run(cmd):
    print("\n$ " + " ".join(cmd))
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
        "universe_filtered_korea.txt",
        "prefilter_report_korea.csv",
        "premarket_auto*_korea.csv",
        "premarket_manual*_korea.csv",
        "premarket_korea.csv",
        "candidates*_korea.txt",
        "candidates_meta*_korea.csv",
        "report_v2_korea*.csv",
        "buy_report_korea_*_KST.*",
        "buy_report_latest_korea.txt",
        "scan_skipped.log",
        "signals_*_KST.csv",
        "positions*.csv",
        "tickers_korea.txt",
        "macro_watch_yahoo_korea.txt",
        "tickers_core_korea.txt",
        "tickers_leverage2x_korea.txt",
        "finviz_manual_korea.txt",
        "*.py",
    ]
    copied = copy_patterns(run_dir, patterns)
    (run_dir / "manifest.txt").write_text(
        f"run_id={run_id}\n"
        f"created_kst={datetime.now(KST).strftime('%Y-%m-%d %H:%M:%S KST')}\n"
        f"cwd={Path.cwd()}\n"
        f"python={sys.executable}\n\n"
        f"args:\n{args_text}\n\n"
        f"copied_files={len(copied)}\n" + "\n".join(copied) + "\n",
        encoding="utf-8"
    )
    (out_root / "LATEST_RUN.txt").write_text(str(run_dir.resolve()) + "\n", encoding="utf-8")
    return run_dir, copied

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--break-mode", choices=["a","b"], default=None,
                    help="A=60d high, B=pattern neckline/triangle. If omitted, prompts.")
    ap.add_argument("--refresh-bad", action="store_true")
    ap.add_argument("--top", type=int, default=10)
    ap.add_argument("--max-2x", type=int, default=5)
    ap.add_argument("--intraday", action="store_true")
    ap.add_argument("--hold-bars", type=int, default=3)
    ap.add_argument("--no-audit", action="store_true")
    ap.add_argument("--buy-top", type=int, default=20)
    ap.add_argument("--pdf", action="store_true")
    ap.add_argument("--out-root", default="runs")
    ap.add_argument("--no-archive", action="store_true")
    ap.add_argument("--no-prefilter", action="store_true", help="Disable prefilter step (use full tickers.txt)")
    args = ap.parse_args()

    if args.break_mode is None:
        ans = input("Select break-mode (A=60d high, B=pattern neckline/triangle) [B]: ").strip().lower()
        args.break_mode = "a" if ans == "a" else "b"
        print(f"[mode] break-mode={args.break_mode.upper()}\n")

    py = sys.executable or "python"

    # 0) sync tickers
    if Path("sync_tickers_txt_korea.py").exists():
        run([py, "sync_tickers_txt_korea.py"])

    universe_file = None
    if not args.no_prefilter:
        if Path("prefilter_universe_yf_korea.py").exists():
            run([py, "prefilter_universe_yf_korea.py", "--universe", "tickers_korea.txt", "--out", "universe_filtered_korea.txt", "--report", "prefilter_report_korea.csv"])
            # If filter result is empty, relax relvol (KRX often has relvol < 1.0 on quiet days)
            try:
                if Path("universe_filtered_korea.txt").exists() and Path("universe_filtered_korea.txt").read_text(encoding="utf-8").strip()=="" :
                    print("[warn] Prefilter returned 0 tickers; retrying with --no-relvol")
                    run([py, "prefilter_universe_yf_korea.py", "--universe", "tickers_korea.txt", "--out", "universe_filtered_korea.txt", "--report", "prefilter_report_korea.csv", "--no-relvol"])
            except Exception:
                pass
            universe_file = "universe_filtered_korea.txt"
        else:
            print("[warn] prefilter_universe_yf.py missing; skipping prefilter.")
    else:
        print("[mode] prefilter disabled.")

    # 1) update snapshot (use v3 if available)
    upd = pick_first(["update_premarket_yf_auto_fast_v3_korea.py", "update_premarket_yf_auto_fast_v2_korea.py", "update_premarket_yf_auto_fast_v3.py", "update_premarket_yf_auto_fast_v2.py"])
    cmd = [py, upd]
    if args.refresh_bad:
        cmd.append("--refresh-bad")
    if universe_file and ("v3" in Path(upd).stem):
        cmd += ["--universe-file", universe_file]
    run(cmd)

    # 2) 1차 후보
    if args.break_mode == "a":
        scan1 = pick_first(["bulkowski_scan_from_debugcsv_strict_v4_korea.py", "bulkowski_scan_from_debugcsv_strict_v4.py",
                            "bulkowski_scan_from_debugcsv_strict_v3.py",
                            "bulkowski_scan_from_debugcsv_strict_v2.py",
                            "bulkowski_scan_from_debugcsv_strict.py"])
        if scan1 is None:
            scan1 = pick_first(["bulkowski_scan_from_debugcsv_pattern_v3_korea.py", "bulkowski_scan_from_debugcsv_pattern_v3.py", "bulkowski_scan_from_debugcsv_pattern_v2.py"])
    else:
        scan1 = pick_first(["bulkowski_scan_from_debugcsv_pattern_v3_korea.py", "bulkowski_scan_from_debugcsv_pattern_v3.py", "bulkowski_scan_from_debugcsv_pattern_v2.py"])

    if scan1 is None:
        raise SystemExit("No 1차 후보 스캐너를 찾지 못했습니다 (bulkowski_scan_*.py).")

    cmd = [py, scan1, "--top", str(args.top), "--out", "candidates_korea.txt", "--max-2x", str(args.max_2x)]
    # pass break-mode if supported
    if ("pattern" in scan1) or ("strict_v4" in scan1):
        cmd += ["--break-mode", args.break_mode]
    # If scanner supports universe-file, pass it (optional)
    if universe_file and "--universe-file" in Path(scan1).read_text(encoding="utf-8", errors="ignore"):
        cmd += ["--universe-file", universe_file]
    cmd += ["--groups", "tickers_core,tickers_leverage2x,finviz_manual,macro_watch_yahoo"]
    run(cmd)

    # 3) manual
    run([py, "make_premarket_manual_5_korea.py"])

    # 4) merge
    merge_py = pick_first(["merge_premarkets_v2_korea.py", "merge_premarkets_v2.py", "merge_premarkets.py"])
    run([py, merge_py])

    # 5) 2차 scan
    scan2 = pick_first(["scan_candidates_v2_safe_v7_strict_v2d_korea.py", "scan_candidates_v2_safe_v7_strict_v2d.py",
                        "scan_candidates_v2_safe_v7_strict_v2c.py",
                        "scan_candidates_v2_safe_v7_strict_v2.py"])
    cmd = [py, scan2, "--premarket", "premarket_korea.csv"]
    if "v2d" in scan2:
        cmd += ["--break-mode", args.break_mode]
    if args.intraday:
        cmd += ["--intraday", "--hold-bars", str(args.hold_bars)]
    run(cmd)

    # 6) buy report
    buy_py = pick_first(["make_buy_report_v9_korea.py", "make_buy_report_v9.py", "make_buy_report_v8.py", "make_buy_report_v7.py"])
    cmd = [py, buy_py, "--top", str(args.buy_top), "--xlsx"]
    if args.pdf:
        cmd.append("--pdf")
    run(cmd)

    if (not args.no_audit) and Path("ticker_audit_korea.py").exists():
        run([py, "ticker_audit_korea.py"])

    # final block
    lf = Path("buy_report_latest_korea.txt")
    if lf.exists():
        print("\n" + ("="*80))
        print("FINAL BUY OPINION (LAST BLOCK)".center(80))
        print(("="*80) + "\n")
        print(lf.read_text(encoding="utf-8").rstrip())
        print("\n" + ("="*80))
    else:
        print("\n[warn] buy_report_latest.txt not found.\n")

    # archive
    if not args.no_archive:
        out_root = Path(args.out_root)
        ensure_dir(out_root)
        run_id = kst_stamp()
        run_dir, copied = archive_run(out_root, run_id, " ".join(sys.argv[1:]))
        print(f"\nArchived outputs -> {run_dir}  (files={len(copied)})")
        print(f"Latest pointer -> {out_root/'LATEST_RUN.txt'}")

if __name__ == "__main__":
    main()