# live_runner.py
import subprocess
import threading
import time

INTERVAL_SEC = 60  # 오토 갱신 주기(너무 잦으면 야후 제한 걸릴 수 있음: 60~120 추천)
ASK_MANUAL_ON_START = True  # 실행하자마자 수동입력 창 띄우고 싶으면 True

lock = threading.Lock()
stop_event = threading.Event()

def run(cmd):
    # cmd: ["python", "script.py"]
    try:
        subprocess.run(cmd, check=False)
    except Exception as e:
        print(f"[ERR] {' '.join(cmd)} -> {e}")

def merge_and_scan():
    # 파일 충돌 방지: merge+scan은 항상 lock으로 보호
    with lock:
        run(["python", "merge_premarkets.py"])
        run(["python", "scan_candidates_v2_safe.py"])

def auto_loop():
    while not stop_event.is_set():
        # 오토 가격만 갱신
        run(["python", "update_premarket_yf_auto.py"])
        # merge+scan 갱신
        merge_and_scan()
        # 대기
        for _ in range(INTERVAL_SEC):
            if stop_event.is_set():
                break
            time.sleep(1)

def main():
    print("\n[AUTO] update_premarket_yf_auto.py 주기 실행 시작")
    print("[MANUAL] 언제든지 'm' + Enter 치면 5개 수동입력 창 뜸")
    print("[QUIT] 종료는 'q' + Enter\n")

    t = threading.Thread(target=auto_loop, daemon=True)
    t.start()

    if ASK_MANUAL_ON_START:
        with lock:
            run(["python", "make_premarket_manual_5.py"])
        merge_and_scan()

    while True:
        cmd = input("> ").strip().lower()
        if cmd == "m":
            with lock:
                run(["python", "make_premarket_manual_5.py"])
            merge_and_scan()
        elif cmd == "q":
            stop_event.set()
            print("Stopping...")
            break
        else:
            print("m=manual 입력창 / q=종료")

if __name__ == "__main__":
    main()