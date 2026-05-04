# auto_loop.py
import time
import subprocess
from datetime import datetime

INTERVAL_SEC = 60  # 1분

while True:
    ts = datetime.now().strftime("%H:%M:%S")
    print(f"\n[{ts}] Updating auto prices + scanning...")
    subprocess.run(["python", "update_premarket_yf_auto.py"])
    subprocess.run(["python", "merge_premarkets.py"])
    subprocess.run(["python", "scan_candidates_v2_safe.py"])
    time.sleep(INTERVAL_SEC)