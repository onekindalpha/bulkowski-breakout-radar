# kr_afterhours_signal.py
# Purpose: KRX 시간외 단일가(체결내역 CSV)로 1차 신호(PASS/FAIL) 판단
# Default ticker: 488080 (TIGER 반도체TOP10레버리지)

import argparse
import pandas as pd
import numpy as np
from pathlib import Path

def _read_csv_robust(path: str) -> pd.DataFrame:
    # 삼성/HTS CSV는 cp949/euc-kr가 흔함
    for enc in ("cp949", "euc-kr", "utf-8-sig", "utf-8"):
        try:
            return pd.read_csv(path, encoding=enc)
        except Exception:
            pass
    # 마지막 시도: 엔진 변경
    return pd.read_csv(path, engine="python")

def _find_col(df: pd.DataFrame, candidates) -> str | None:
    cols = {c.strip(): c for c in df.columns}
    lower_map = {c.lower(): c for c in df.columns}
    for cand in candidates:
        # exact
        if cand in cols:
            return cols[cand]
        # lower contains
        for lc, orig in lower_map.items():
            if cand.lower() == lc:
                return orig
            if cand.lower() in lc:
                return orig
    return None

def parse_trades_csv(path: str) -> pd.DataFrame:
    df = _read_csv_robust(path)

    # 흔한 컬럼 후보들
    time_col = _find_col(df, ["체결시각", "체결시간", "시간", "time", "datetime"])
    price_col = _find_col(df, ["체결가", "가격", "price", "last"])
    vol_col   = _find_col(df, ["체결량", "거래량", "수량", "volume", "qty"])

    if price_col is None or vol_col is None:
        raise ValueError(
            f"CSV 컬럼 인식 실패. columns={df.columns.tolist()}\n"
            "필요: price(체결가) + volume(체결량)."
        )

    out = pd.DataFrame()
    out["price"] = pd.to_numeric(df[price_col].astype(str).str.replace(",", ""), errors="coerce")
    out["volume"] = pd.to_numeric(df[vol_col].astype(str).str.replace(",", ""), errors="coerce").fillna(0)

    if time_col is not None:
        out["time_raw"] = df[time_col].astype(str)
    else:
        out["time_raw"] = ""

    out = out.dropna(subset=["price"])
    return out

def eval_afterhours_signal(
    trades: pd.DataFrame,
    regular_vol: float,
    min_abs_vol: int = 50_000,
    min_share_of_regular: float = 0.002,   # 0.2% of regular session volume
    near_high_pos: float = 0.75,           # close in top quartile of AH range
    dump_close_pos: float = 0.25,          # close in bottom quartile
    dump_drop_pct: float = 0.01            # high->close drop >= 1%
):
    high = float(trades["price"].max())
    low = float(trades["price"].min())
    close = float(trades["price"].iloc[-1])
    after_vol = float(trades["volume"].sum())

    rng = max(high - low, 1e-9)
    close_pos = (close - low) / rng
    drop_pct = (high - close) / max(high, 1e-9)
    vol_share = after_vol / max(regular_vol, 1e-9)

    # 1) close near high?
    close_near_high = close_pos >= near_high_pos

    # 2) volume meaningful?
    vol_ok = (after_vol >= min_abs_vol) and (vol_share >= min_share_of_regular)

    # 3) pump&dump-ish?
    pump_dump = (close_pos <= dump_close_pos) and (drop_pct >= dump_drop_pct)

    passed = close_near_high and vol_ok and (not pump_dump)

    reasons = []
    if not close_near_high:
        reasons.append(f"close_not_near_high(pos={close_pos:.2f} < {near_high_pos})")
    if not vol_ok:
        reasons.append(f"vol_weak(after={after_vol:.0f}, share={vol_share:.4f} < {min_share_of_regular})")
    if pump_dump:
        reasons.append(f"pump_dump(close_pos={close_pos:.2f}, drop={drop_pct*100:.2f}%)")

    return {
        "PASS": passed,
        "high": high,
        "low": low,
        "close": close,
        "after_vol": after_vol,
        "regular_vol": regular_vol,
        "vol_share": vol_share,
        "close_pos": close_pos,
        "drop_pct": drop_pct,
        "reasons": ";".join(reasons) if reasons else "OK"
    }

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--code", default="488080", help="KRX code (default: 488080 TIGER 반도체TOP10레버리지)")
    ap.add_argument("--trades-csv", required=True, help="삼성/HTS에서 저장한 '시간외 단일가 체결내역' CSV")
    ap.add_argument("--regular-vol", type=float, required=True, help="당일 정규장 거래량(주). (삼성/네이버/HTS에서 확인)")
    ap.add_argument("--min-abs-vol", type=int, default=50000)
    ap.add_argument("--min-share", type=float, default=0.002)
    ap.add_argument("--near-high-pos", type=float, default=0.75)
    ap.add_argument("--dump-close-pos", type=float, default=0.25)
    ap.add_argument("--dump-drop-pct", type=float, default=0.01)
    args = ap.parse_args()

    trades = parse_trades_csv(args.trades_csv)
    r = eval_afterhours_signal(
        trades,
        regular_vol=args.regular_vol,
        min_abs_vol=args.min_abs_vol,
        min_share_of_regular=args.min_share,
        near_high_pos=args.near_high_pos,
        dump_close_pos=args.dump_close_pos,
        dump_drop_pct=args.dump_drop_pct,
    )

    print(f"\n=== AFTER-HOURS AUCTION SIGNAL ({args.code}) ===")
    print(f"PASS={r['PASS']} | reasons={r['reasons']}")
    print(f"high={r['high']:.2f} low={r['low']:.2f} close={r['close']:.2f}")
    print(f"after_vol={r['after_vol']:.0f} regular_vol={r['regular_vol']:.0f} share={r['vol_share']:.4f}")
    print(f"close_pos_in_range={r['close_pos']:.2f} high_to_close_drop={r['drop_pct']*100:.2f}%\n")

if __name__ == "__main__":
    main()