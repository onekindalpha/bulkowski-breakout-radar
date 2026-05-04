import argparse
from pathlib import Path
import logging

import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import Rectangle

logging.getLogger("matplotlib.font_manager").setLevel(logging.ERROR)
plt.rcParams["font.family"] = "DejaVu Sans"
plt.rcParams["axes.unicode_minus"] = False


def parse_dates(s: pd.Series) -> pd.Series:
    return pd.to_datetime(s.astype(str).str.replace(r"\.0$", "", regex=True), format="%Y%m%d", errors="coerce")


def calc_status(price_off, ad_off, lag_gap, price_at_high, ad_at_high,
                near_high_pct, warn_gap_pct, danger_gap_pct):
    if pd.isna(price_off) or pd.isna(ad_off):
        return "No signal", "Not enough history"
    if price_at_high and ad_at_high:
        return "Confirmed breakout", "Price and A/D both confirm highs"
    if price_at_high and (not ad_at_high) and lag_gap >= danger_gap_pct:
        return "Serious A/D non-confirmation", "Price is at/new high, A/D clearly lags"
    if price_at_high and (not ad_at_high) and lag_gap >= warn_gap_pct:
        return "Early A/D warning", "Price is at/new high, A/D is not confirming"
    if price_off <= near_high_pct and lag_gap >= danger_gap_pct:
        return "Narrow advance warning", "Price recovered faster than breadth"
    if price_off <= near_high_pct and lag_gap >= warn_gap_pct:
        return "Breadth lag candidate", "Breadth lags a near-high price move"
    if lag_gap <= -warn_gap_pct and ad_off < price_off:
        return "Breadth-led recovery", "A/D recovered faster than price"
    if ad_off <= near_high_pct and price_off > near_high_pct:
        return "Breadth stronger than price", "Internal participation improved ahead of price"
    return "Neutral", "Price and breadth are roughly in sync"


def draw_candles(ax, df):
    width = 0.6
    for i, row in enumerate(df.itertuples(index=False)):
        o, h, l, c = row.open, row.high, row.low, row.close
        color = "green" if c >= o else "red"
        ax.vlines(i, l, h, linewidth=0.6, color=color, alpha=0.8)
        body_low = min(o, c)
        body_h = max(abs(c - o), 0.001)
        ax.add_patch(Rectangle((i - width / 2, body_low), width, body_h,
                               facecolor=color, edgecolor=color, linewidth=0.6, alpha=0.9))
    ax.set_xlim(-1, len(df))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--breadth", required=True)
    ap.add_argument("--index", required=True)
    ap.add_argument("--lookback", type=int, default=126)
    ap.add_argument("--near-high", type=float, default=2.0)
    ap.add_argument("--warn-gap", type=float, default=1.0)
    ap.add_argument("--danger-gap", type=float, default=2.0)
    ap.add_argument("--new-high-tol", type=float, default=0.2)
    ap.add_argument("--months", type=int, default=6)
    ap.add_argument("--png", required=True)
    args = ap.parse_args()

    b = pd.read_csv(args.breadth)
    x = pd.read_csv(args.index)

    for col in ["date", "ad_line"]:
        if col not in b.columns:
            raise ValueError(f"breadth missing column: {col}")
    for col in ["date", "open", "high", "low", "close"]:
        if col not in x.columns:
            raise ValueError(f"index missing column: {col}")

    b["date"] = parse_dates(b["date"])
    x["date"] = parse_dates(x["date"])
    b["ad_line"] = pd.to_numeric(b["ad_line"], errors="coerce")
    for c in ["open", "high", "low", "close"]:
        x[c] = pd.to_numeric(x[c], errors="coerce")

    df = pd.merge(x, b[["date", "ad_line"]], on="date", how="inner")
    df = df.dropna(subset=["date", "open", "high", "low", "close", "ad_line"]).sort_values("date").reset_index(drop=True)
    if len(df) < args.lookback + 5:
        raise ValueError(f"Too few merged rows: {len(df)}")

    tail_days = max(args.months * 21, args.lookback + 5)
    plot_df = df.tail(tail_days).copy().reset_index(drop=True)

    recent = df.tail(args.lookback).copy()
    price_high = recent["close"].max()
    ad_high = recent["ad_line"].max()
    latest = df.iloc[-1]

    price_off = (price_high - latest["close"]) / price_high * 100.0 if price_high > 0 else float("nan")
    ad_off = (ad_high - latest["ad_line"]) / ad_high * 100.0 if ad_high > 0 else float("nan")
    lag_gap = ad_off - price_off

    price_at_high = price_off <= args.new_high_tol
    ad_at_high = ad_off <= args.new_high_tol

    status, note = calc_status(
        price_off, ad_off, lag_gap, price_at_high, ad_at_high,
        args.near_high, args.warn_gap, args.danger_gap
    )

    fig, (ax1, ax2) = plt.subplots(
        2, 1, figsize=(16, 8),
        gridspec_kw={"height_ratios": [2.2, 1]},
        sharex=True
    )

    draw_candles(ax1, plot_df)
    ax1.set_title(f"KOSPI + Advance-Decline Line v4 Unified Sensitive ({args.months}M)")
    ax1.set_ylabel("KOSPI")
    ax1.grid(True, alpha=0.25)

    ax2.plot(range(len(plot_df)), plot_df["ad_line"].values, linewidth=2)
    ax2.set_ylabel("A/D Line")
    ax2.grid(True, alpha=0.25)

    txt = (
        f"{latest['date'].date()}\n"
        f"Status: {status}\n"
        f"Note: {note}\n"
        f"Price off high: {price_off:.2f}%\n"
        f"A/D off high: {ad_off:.2f}%\n"
        f"A/D lag gap: {lag_gap:.2f}%"
    )
    ax1.text(
        0.01, 0.02, txt, transform=ax1.transAxes, va="bottom", ha="left",
        bbox=dict(boxstyle="round", facecolor="white", alpha=0.9)
    )

    color = "green" if "Confirmed" in status else ("red" if ("Serious" in status or "Narrow" in status) else ("orange" if ("warning" in status.lower() or "candidate" in status.lower()) else "teal"))
    ax2.text(
        0.98, 0.90,
        status + "\n" + note,
        transform=ax2.transAxes,
        va="top", ha="right",
        bbox=dict(boxstyle="round", facecolor=color, alpha=0.85)
    )

    ticks = list(range(0, len(plot_df), max(1, len(plot_df) // 8)))
    labels = [plot_df.loc[i, "date"].strftime("%Y-%m") for i in ticks]
    ax2.set_xticks(ticks)
    ax2.set_xticklabels(labels, rotation=30)

    Path(args.png).parent.mkdir(parents=True, exist_ok=True)
    plt.tight_layout()
    plt.savefig(args.png, dpi=160, bbox_inches="tight")
    plt.close(fig)

    print("=" * 80)
    print(f"latest date: {latest['date'].date()}")
    print(f"status: {status}")
    print(f"note: {note}")
    print(f"price_off_high: {price_off:.4f}%")
    print(f"ad_off_high: {ad_off:.4f}%")
    print(f"lag_gap: {lag_gap:.4f}%")
    print(f"saved: {args.png}")
    print("=" * 80)


if __name__ == "__main__":
    main()
