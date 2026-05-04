# 실행: python3 check_report_dates.py
import pandas as pd
df = pd.read_csv('report_v2.csv', parse_dates=True, infer_datetime_format=True, low_memory=False)
# 날짜가 있는 컬럼을 자동으로 찾아 최소/최대 출력
date_cols = [c for c in df.columns if 'date' in c.lower() or 'time' in c.lower()]
if not date_cols:
    print("report_v2.csv에 명시적 날짜 컬럼이 없습니다. 파일 헤더:", df.columns.tolist())
else:
    for c in date_cols:
        s = pd.to_datetime(df[c], errors='coerce')
        print(c, s.min(), s.max())