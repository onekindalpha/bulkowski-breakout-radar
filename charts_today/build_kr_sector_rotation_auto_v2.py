#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
build_kr_sector_rotation_auto_v2.py

B = 업종/섹터 로테이션 참고용 overlay
Patched:
- use yf.Ticker(sym).history()
- if yahoo sector is missing, fallback to industry, else UNKNOWN
- writes debug csv
"""
from __future__ import annotations
import argparse
import re
from pathlib import Path

import pandas as pd
import yfinance as yf

SEED_FILES = [
    "kr_manual_conviction.txt",
    "kr_tactical_leverage.txt",
    "finviz_manual_korea.txt",
    "tickers_core_korea.txt",
    "tickers_leverage2x_korea.txt",
]

TICKER_RE = re.compile(r"^\d{6}\.(KS|KQ)$")


def read_seed_tickers(paths: list[str]) -> list[str]:
    out, seen = [], set()
    for path in paths:
        p = Path(path)
        if not p.exists():
            continue
        for line in p.read_text(encoding="utf-8", errors="ignore").splitlines():
            s = line.strip()
            if not s or s.startswith("#"):
                continue
            if "#" in s:
                s = s.split("#", 1)[0].strip()
            for tok in re.split(r"[\s,;]+", s):
                t = tok.strip().upper()
                if not t:
                    continue
                if re.fullmatch(r"\d{6}", t):
                    t = t + ".KS"
                if TICKER_RE.match(t) and t not in seen:
                    seen.add(t)
                    out.append(t)
    return out


def rank_desc(series: pd.Series) -> pd.Series:
    s = pd.to_numeric(series, errors="coerce")
    n = max(len(s.dropna()), 1)
    return (1.0 - s.rank(method="average", ascending=False, pct=True) + (1.0 / n)).fillna(0.0)


def fetch_sector_info(sym: str) -> tuple[str, str]:
    try:
        tk = yf.Ticker(sym)
        info = getattr(tk, "info", {}) or {}
        sector = str(info.get("sector", "")).strip()
        industry = str(info.get("industry", "")).strip()
        return sector, industry
    except Exception:
        return "", ""


def fetch_rows(tickers: list[str]) -> tuple[pd.DataFrame, pd.DataFrame]:
    rows = []
    debug = []
    total = len(tickers)

    for i, sym in enumerate(tickers, 1):
        try:
            tk = yf.Ticker(sym)
            df = tk.history(period="3mo", interval="1d", auto_adjust=False)
            if df is None or df.empty or "Close" not in df.columns:
                debug.append({"ticker": sym, "status": "no_data_or_no_close", "rows": 0})
                continue

            s = pd.to_numeric(df["Close"], errors="coerce").dropna()
            if len(s) < 25:
                debug.append({"ticker": sym, "status": "too_short", "rows": int(len(s))})
                continue

            chg = float((s.iloc[-1] / s.iloc[-2] - 1.0) * 100.0) if len(s) >= 2 else None
            w = float((s.iloc[-1] / s.iloc[-6] - 1.0) * 100.0) if len(s) >= 6 else None
            m = float((s.iloc[-1] / s.iloc[-22] - 1.0) * 100.0) if len(s) >= 22 else None
            sector, industry = fetch_sector_info(sym)
            sector = sector or industry or "UNKNOWN"

            rows.append({
                "ticker": sym,
                "sector": sector,
                "industry": industry,
                "change": chg,
                "perf_week": w,
                "perf_month": m,
            })
            debug.append({"ticker": sym, "status": "ok", "rows": int(len(s)), "sector": sector, "industry": industry})
        except Exception as e:
            debug.append({"ticker": sym, "status": f"error:{type(e).__name__}", "rows": 0})
        if i in (20, 40, total):
            print(f"... sector rotation {i}/{total}")

    return pd.DataFrame(rows), pd.DataFrame(debug)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--top-sectors", type=int, default=5)
    ap.add_argument("--leaders-per-sector", type=int, default=4)
    ap.add_argument("--wt-change", type=float, default=0.5)
    ap.add_argument("--wt-perf-week", type=float, default=0.3)
    ap.add_argument("--wt-perf-month", type=float, default=0.2)
    args = ap.parse_args()

    tickers = read_seed_tickers(SEED_FILES)
    if not tickers:
        Path("kr_sector_rotation_auto.txt").write_text("", encoding="utf-8")
        pd.DataFrame(columns=["sector","score","change","perf_week","perf_month"]).to_csv("kr_sector_rotation_groups.csv", index=False)
        pd.DataFrame(columns=["sector","ticker","industry","change","perf_week","perf_month","leader_score"]).to_csv("kr_sector_rotation_members.csv", index=False)
        raise SystemExit("No Korea seed tickers found for sector-rotation overlay.")

    df, dbg = fetch_rows(tickers)
    dbg.to_csv("kr_sector_rotation_debug.csv", index=False)

    if df.empty:
        Path("kr_sector_rotation_auto.txt").write_text("", encoding="utf-8")
        pd.DataFrame(columns=["sector","score","change","perf_week","perf_month"]).to_csv("kr_sector_rotation_groups.csv", index=False)
        pd.DataFrame(columns=["sector","ticker","industry","change","perf_week","perf_month","leader_score"]).to_csv("kr_sector_rotation_members.csv", index=False)
        raise SystemExit("Could not fetch any yfinance rows for Korea sector-rotation overlay. See kr_sector_rotation_debug.csv")

    df["rank_change"] = rank_desc(df["change"])
    df["rank_perf_week"] = rank_desc(df["perf_week"])
    df["rank_perf_month"] = rank_desc(df["perf_month"])
    tot = args.wt_change + args.wt_perf_week + args.wt_perf_month
    df["leader_score"] = (
        args.wt_change * df["rank_change"]
        + args.wt_perf_week * df["rank_perf_week"]
        + args.wt_perf_month * df["rank_perf_month"]
    ) / tot

    grp_rows = []
    for sector, g in df.groupby("sector", dropna=False):
        g2 = g.sort_values("leader_score", ascending=False).head(3)
        grp_rows.append({
            "sector": sector,
            "score": float(g2["leader_score"].mean()),
            "change": float(pd.to_numeric(g2["change"], errors="coerce").mean()),
            "perf_week": float(pd.to_numeric(g2["perf_week"], errors="coerce").mean()),
            "perf_month": float(pd.to_numeric(g2["perf_month"], errors="coerce").mean()),
            "member_count": int(len(g))
        })

    groups = pd.DataFrame(grp_rows).sort_values(
        ["score","change","perf_week","perf_month","sector"],
        ascending=[False,False,False,False,True]
    ).reset_index(drop=True)

    selected_groups = groups.head(args.top_sectors).copy()
    selected_sector_set = set(selected_groups["sector"].tolist())

    members = df[df["sector"].isin(selected_sector_set)].copy()
    members = members.sort_values(["sector","leader_score","change","perf_week","perf_month"],
                                  ascending=[True,False,False,False,False])

    picked = []
    picked_rows = []
    seen = set()
    for sector in selected_groups["sector"].tolist():
        sub = members[members["sector"] == sector].head(args.leaders_per_sector)
        for _, r in sub.iterrows():
            t = r["ticker"]
            picked_rows.append({
                "sector": sector,
                "ticker": t,
                "industry": r.get("industry",""),
                "change": r["change"],
                "perf_week": r["perf_week"],
                "perf_month": r["perf_month"],
                "leader_score": r["leader_score"],
            })
            if t not in seen:
                seen.add(t)
                picked.append(t)

    Path("kr_sector_rotation_auto.txt").write_text(
        "\n".join(picked) + ("\n" if picked else ""),
        encoding="utf-8"
    )
    selected_groups.to_csv("kr_sector_rotation_groups.csv", index=False)
    pd.DataFrame(picked_rows).to_csv("kr_sector_rotation_members.csv", index=False)

    print(f"Saved: kr_sector_rotation_auto.txt ({len(picked)} tickers)")
    print(f"Saved: kr_sector_rotation_groups.csv ({len(selected_groups)} sectors)")
    print(f"Saved: kr_sector_rotation_members.csv ({len(picked_rows)} rows)")
    print("Top sectors:")
    for i, r in selected_groups.iterrows():
        print(f"[B] #{i+1} {r['sector']}  score={r['score']:.4f}  chg={r['change']:.2f}%  w={r['perf_week']:.2f}%  m={r['perf_month']:.2f}%")

if __name__ == "__main__":
    main()
