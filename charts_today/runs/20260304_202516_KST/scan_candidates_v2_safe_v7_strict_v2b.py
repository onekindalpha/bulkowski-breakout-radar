#!/usr/bin/env python3
"""
scan_candidates_v2_safe_v7_strict_v2b.py (STABLE WRAPPER)

Why:
- Your previous v2b got an indentation error.
- This wrapper simply runs the known-good script:
    scan_candidates_v2_safe_v7_strict_v2.py
  passing all CLI args through.

Usage:
  python scan_candidates_v2_safe_v7_strict_v2b.py --intraday --hold-bars 3
"""

import os
import subprocess
import sys

def main():
    py = sys.executable or "python"
    target = "scan_candidates_v2_safe_v7_strict_v2.py"
    cmd = [py, target] + sys.argv[1:]
    print("\n$ " + " ".join(cmd))
    raise SystemExit(subprocess.call(cmd))

if __name__ == "__main__":
    main()
