from pathlib import Path
from datetime import datetime
try:
    from zoneinfo import ZoneInfo
except ImportError:
    # Python <3.9: 사용자 환경에 따라 backports.zoneinfo 설치 필요
    raise

CSV_PATH = Path(__file__).with_name("premarket_auto.csv")

def rewrite_with_kst(path: Path):
    txt = path.read_text(encoding="utf-8")
    lines = txt.splitlines()
    # 기존에 saved_at_kr 로 시작하는 줄이 있으면 제거
    if lines and lines[0].startswith("saved_at_kr"):
        lines = lines[1:]
    # 현재 한국시간
    now_kst = datetime.now(ZoneInfo("Asia/Seoul")).strftime("%Y-%m-%d %H:%M:%S KST")
    new_first = f"saved_at_kr,{now_kst}"
    new_txt = "\n".join([new_first] + lines) + "\n"
    path.write_text(new_txt, encoding="utf-8")

if __name__ == "__main__":
    rewrite_with_kst(CSV_PATH)
    # 사용 예: python save_with_kst.py
