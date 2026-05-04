import argparse
import pandas as pd


def is_true(x):
    return str(x).strip().lower() in ("true", "1", "y", "yes")


def to_num(df, cols):
    for c in cols:
        if c in df.columns:
            df[c] = pd.to_numeric(df[c], errors="coerce")
    return df


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--file", default="report_v2.csv")
    ap.add_argument("--top-buy", type=int, default=3)
    ap.add_argument("--top-near", type=int, default=5)
    args = ap.parse_args()

    df = pd.read_csv(args.file)
    df = to_num(df, [
        "price", "gap_pct", "rsi14", "room_to_weekly_r1_pct",
        "weekly_r1", "daily_break_level", "px_vs_sma50", "px_vs_sma200", "score"
    ])

    # 매크로 / 인버스 / 금리계열 제외
    hard_exclude = {
        "^TNX", "^VIX", "TMV", "TMF", "TBT", "TLT",
        "QID", "SQQQ", "SOXS", "FAZ", "SCO", "KOLD",
        "BITI", "GLL", "BOIL", "DIG", "ERX", "GUSH",
        "WTI", "OIL", "GOLD"
    }
    df = df[~df["ticker"].astype(str).str.upper().isin(hard_exclude)].copy()

    # 공통 계산
    df = df[df["price"].notna() & df["daily_break_level"].notna()].copy()
    df["ext_pct"] = (df["price"] / df["daily_break_level"] - 1.0) * 100.0
    df["dist_to_break_pct"] = (df["daily_break_level"] / df["price"] - 1.0) * 100.0
    df["daily_breakout_bool"] = df["daily_breakout"].map(is_true)
    df["daily_retest_bool"] = df["daily_retest"].map(is_true)

    # ---------- 1) 돌파 확정 후보 ----------
    confirmed = df.copy()
    confirmed = confirmed[confirmed["daily_breakout_bool"]]
    confirmed = confirmed[confirmed["ext_pct"].between(0, 5)]
    confirmed = confirmed[confirmed["px_vs_sma50"] > 0]
    confirmed = confirmed[confirmed["px_vs_sma200"] > 0]
    confirmed = confirmed[confirmed["rsi14"].between(45, 75)]

    # 레거시식 점수: retest 우대 + break 바로 위 우대 + 추세 우대 + gap 과열 패널티
    confirmed["legacy_buy_score"] = 0.0
    confirmed["legacy_buy_score"] += confirmed["daily_retest_bool"].astype(int) * 4.0
    confirmed["legacy_buy_score"] += confirmed["score"].fillna(0) * 0.5
    confirmed["legacy_buy_score"] += (confirmed["px_vs_sma50"] > 0).astype(int) * 1.0
    confirmed["legacy_buy_score"] += (confirmed["px_vs_sma200"] > 0).astype(int) * 1.0
    confirmed["legacy_buy_score"] += confirmed["room_to_weekly_r1_pct"].fillna(-999).clip(lower=-5, upper=10) * 0.15
    confirmed["legacy_buy_score"] -= confirmed["ext_pct"].clip(lower=0) * 0.7
    confirmed["legacy_buy_score"] -= (confirmed["rsi14"] - 65).clip(lower=0) * 0.15

    confirmed = confirmed.sort_values(
        ["legacy_buy_score", "daily_retest_bool", "ext_pct"],
        ascending=[False, False, True]
    )

    # ---------- 2) 돌파 직전 대기 후보 ----------
    near = df.copy()
    near = near[~near["daily_breakout_bool"]]
    near = near[near["dist_to_break_pct"].between(0, 3)]
    near = near[near["px_vs_sma50"] > 0]
    near = near[near["px_vs_sma200"] > 0]
    near = near[near["rsi14"].between(45, 70)]

    near["legacy_near_score"] = 0.0
    near["legacy_near_score"] += near["daily_retest_bool"].astype(int) * 4.0
    near["legacy_near_score"] += near["score"].fillna(0) * 0.5
    near["legacy_near_score"] += (near["px_vs_sma50"] > 0).astype(int) * 1.0
    near["legacy_near_score"] += (near["px_vs_sma200"] > 0).astype(int) * 1.0
    near["legacy_near_score"] += near["room_to_weekly_r1_pct"].fillna(-999).clip(lower=0, upper=10) * 0.2
    near["legacy_near_score"] -= near["dist_to_break_pct"].clip(lower=0) * 0.8
    near["legacy_near_score"] -= (near["rsi14"] - 67).clip(lower=0) * 0.12

    near = near.sort_values(
        ["legacy_near_score", "daily_retest_bool", "dist_to_break_pct"],
        ascending=[False, False, True]
    )

    top_buy = confirmed.head(args.top_buy)
    top_near = near.head(args.top_near)

    print("=== LEGACY BUY CANDIDATES ===")
    if top_buy.empty:
        print("(none)")
    else:
        for i, (_, r) in enumerate(top_buy.iterrows(), 1):
            print(
                f"{i}. {r['ticker']}: "
                f"{r['price']:.2f} > {r['daily_break_level']:.2f} "
                f"(+{r['ext_pct']:.2f}%) | "
                f"retest={bool(r['daily_retest_bool'])} | "
                f"rsi={r['rsi14']:.2f} | "
                f"score={r['score']}"
            )

    print("\n=== LEGACY NEAR-BREAKOUT WATCH ===")
    if top_near.empty:
        print("(none)")
    else:
        for i, (_, r) in enumerate(top_near.iterrows(), 1):
            print(
                f"{i}. {r['ticker']}: "
                f"{r['price']:.2f} vs {r['daily_break_level']:.2f} "
                f"({r['dist_to_break_pct']:.2f}% below break) | "
                f"retest={bool(r['daily_retest_bool'])} | "
                f"rsi={r['rsi14']:.2f} | "
                f"score={r['score']}"
            )


if __name__ == "__main__":
    main()