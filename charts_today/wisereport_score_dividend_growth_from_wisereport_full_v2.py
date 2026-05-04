#!/usr/bin/env python3
# -*- coding: utf-8 -*-
from __future__ import annotations

import argparse
import pandas as pd
import numpy as np


def num_col(df: pd.DataFrame, col: str) -> pd.Series:
    if col not in df.columns:
        return pd.Series(np.nan, index=df.index, dtype="float64")
    return pd.to_numeric(df[col], errors="coerce")


def yn(mask: pd.Series, missing: pd.Series | None = None) -> pd.Series:
    out = pd.Series("N", index=mask.index, dtype="object")
    if missing is not None:
        out[missing] = "데이터없음"
    out[mask.fillna(False)] = "Y"
    return out


def cagr(start: pd.Series, end: pd.Series, years: int) -> pd.Series:
    out = ((end / start) ** (1 / years) - 1.0) * 100.0
    out[(start <= 0) | (end <= 0)] = np.nan
    return out


def payout_bucket(v: float) -> str:
    if pd.isna(v):
        return "데이터없음"
    if v >= 100:
        return "제외(100%이상)"
    if v >= 80:
        return "경고(80%이상)"
    if v > 50:
        return "주의(50%초과)"
    if v <= 40:
        return "적정(40%이하)"
    return "허용(50%이하)"


def roe_label(v: float) -> str:
    if pd.isna(v):
        return "데이터없음"
    if v >= 30:
        return "매우좋음"
    if v >= 25:
        return "좋음"
    if v >= 15:
        return "보통"
    return "낮음"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--in", dest="input_path", required=True)
    ap.add_argument("--out", dest="output_path", default="wisereport_dividend_scored_v2.tsv")
    args = ap.parse_args()

    df = pd.read_csv(args.input_path, sep="\t", dtype=object)

    sales_g23, sales_g24, sales_g25 = [num_col(df, c) for c in ["매출액증가율_2023", "매출액증가율_2024", "매출액증가율_2025"]]
    op_g23, op_g24, op_g25 = [num_col(df, c) for c in ["영업이익증가율_2023", "영업이익증가율_2024", "영업이익증가율_2025"]]
    ni_g23, ni_g24, ni_g25 = [num_col(df, c) for c in ["순이익증가율_2023", "순이익증가율_2024", "순이익증가율_2025"]]

    opm23, opm24, opm25 = [num_col(df, c) for c in ["영업이익률_2023", "영업이익률_2024", "영업이익률_2025"]]
    roe23, roe24, roe25 = [num_col(df, c) for c in ["ROE_2023", "ROE_2024", "ROE_2025"]]
    debt25 = num_col(df, "부채비율_2025")
    current25 = num_col(df, "유동비율_2025")

    eps21, eps25 = [num_col(df, c) for c in ["EPS_2021", "EPS_2025"]]
    per23, per24, per25 = [num_col(df, c) for c in ["PER_2023", "PER_2024", "PER_2025"]]
    dps21, dps22, dps23, dps24, dps25 = [num_col(df, c) for c in ["DPS_2021", "DPS_2022", "DPS_2023", "DPS_2024", "DPS_2025"]]
    divy25 = num_col(df, "현금배당수익률_2025")
    payout25 = num_col(df, "현금배당성향_2025")
    price = num_col(df, "현재가")

    cfo23, cfo24, cfo25 = [num_col(df, c) for c in ["영업활동현금_2023", "영업활동현금_2024", "영업활동현금_2025"]]
    cfi23, cfi24, cfi25 = [num_col(df, c) for c in ["투자활동현금_2023", "투자활동현금_2024", "투자활동현금_2025"]]
    cff23, cff24, cff25 = [num_col(df, c) for c in ["재무활동현금_2023", "재무활동현금_2024", "재무활동현금_2025"]]

    rev_up = sales_g23.gt(0) & sales_g24.gt(0) & sales_g25.gt(0)
    op_up = op_g23.gt(0) & op_g24.gt(0) & op_g25.gt(0)
    ni_up = ni_g23.gt(0) & ni_g24.gt(0) & ni_g25.gt(0)

    df["매출액_3년연속증가(증가율대용)"] = yn(rev_up, sales_g23.isna() | sales_g24.isna() | sales_g25.isna())
    df["영업이익_3년연속증가(증가율대용)"] = yn(op_up, op_g23.isna() | op_g24.isna() | op_g25.isna())
    df["순이익_3년연속증가(증가율대용)"] = yn(ni_up, ni_g23.isna() | ni_g24.isna() | ni_g25.isna())

    opm_ok = opm23.ge(30) & opm24.ge(30) & opm25.ge(30) & (opm23 < opm24) & (opm24 < opm25)
    df["영업이익률_3년30이상_및_증가"] = yn(opm_ok, opm23.isna() | opm24.isna() | opm25.isna())

    roe_avg = pd.concat([roe23, roe24, roe25], axis=1).mean(axis=1)
    roe_min_ok = roe23.ge(15) & roe24.ge(15) & roe25.ge(15)
    roe_avg_ok = roe_avg.ge(25)
    roe_up = (roe23 < roe24) & (roe24 < roe25)

    df["ROE평균_2023_2025"] = roe_avg
    df["ROE평균판정"] = roe_avg.map(roe_label)
    df["ROE_최소15이상_3년"] = yn(roe_min_ok, roe23.isna() | roe24.isna() | roe25.isna())
    df["ROE_평균25이상"] = yn(roe_avg_ok, roe_avg.isna())
    df["ROE_상승추세"] = yn(roe_up, roe23.isna() | roe24.isna() | roe25.isna())

    eps_cagr = cagr(eps21, eps25, 4)
    eps_ok = eps_cagr.gt(20)
    df["EPS_CAGR_2021_2025"] = eps_cagr
    df["EPS_5년CAGR_20이상"] = yn(eps_ok, eps_cagr.isna())

    div_cut = (dps24 < dps23) | (dps25 < dps24)
    div_stop = (dps23 <= 0) | (dps24 <= 0) | (dps25 <= 0)
    div_record = ((dps23 > 0).astype(int) + (dps24 > 0).astype(int) + (dps25 > 0).astype(int))
    div_inc4 = (dps22 > dps21) & (dps23 > dps22) & (dps24 > dps23) & (dps25 > dps24)

    df["배당기록_최근3년"] = div_record
    df["배당삭감여부_최근3년"] = yn(div_cut, dps23.isna() | dps24.isna() | dps25.isna())
    df["배당중지여부_최근3년"] = yn(div_stop, dps23.isna() | dps24.isna() | dps25.isna())
    df["배당삭감_최근3년_제외"] = yn(~div_cut, dps23.isna() | dps24.isna() | dps25.isna())
    df["배당중지_최근3년_제외"] = yn(~div_stop, dps23.isna() | dps24.isna() | dps25.isna())
    df["배당기록3년이상_충족"] = yn(div_record >= 3, dps23.isna() | dps24.isna() | dps25.isna())
    df["4년연속배당증가"] = yn(div_inc4, dps21.isna() | dps22.isna() | dps23.isna() | dps24.isna() | dps25.isna())

    df["배당성향버킷_2025"] = payout25.map(payout_bucket)
    df["배당성향_40이하"] = yn(payout25.le(40), payout25.isna())
    df["배당성향_50이하"] = yn(payout25.le(50), payout25.isna())
    df["배당성향_80이상_경고"] = yn(payout25.ge(80), payout25.isna())
    df["배당성향100미만"] = yn(payout25.lt(100), payout25.isna())

    earn_yield = (eps25 / price) * 100.0
    earn_yield[(price <= 0) | eps25.isna()] = np.nan
    df["이익수익률_2025"] = earn_yield
    df["배당수익률_2이상"] = yn(divy25.ge(2), divy25.isna())
    df["이익수익률_배당수익률이상"] = yn(earn_yield >= divy25, earn_yield.isna() | divy25.isna())
    df["배당수익률_이익수익률초과_경고"] = yn(divy25 > earn_yield, earn_yield.isna() | divy25.isna())

    df["부채비율_100이하"] = yn(debt25.le(100), debt25.isna())
    df["유동비율_150이상"] = yn(current25.ge(150), current25.isna())

    per_down = (per23 > per24) & (per24 > per25)
    df["PER하향추세_2023_2025"] = yn(per_down, per23.isna() | per24.isna() | per25.isna())

    cfo_good = (cfo23 > 0) & (cfo24 > 0) & (cfo25 > 0)
    cfi_good = (cfi23 < 0) & (cfi24 < 0) & (cfi25 < 0)
    cff_good = (cff23 < 0) & (cff24 < 0) & (cff25 < 0)
    df["영업현금흐름_3년모두플러스"] = yn(cfo_good, cfo23.isna() | cfo24.isna() | cfo25.isna())
    df["투자현금흐름_3년모두마이너스"] = yn(cfi_good, cfi23.isna() | cfi24.isna() | cfi25.isna())
    df["재무현금흐름_3년모두마이너스"] = yn(cff_good, cff23.isna() | cff24.isna() | cff25.isna())

    required = (
        rev_up & op_up & ni_up & opm_ok &
        debt25.le(100) & roe_min_ok & eps_ok &
        (~div_cut) & (~div_stop) & (div_record >= 3) & payout25.lt(100)
    )
    required_missing = (
        sales_g23.isna() | sales_g24.isna() | sales_g25.isna() |
        op_g23.isna() | op_g24.isna() | op_g25.isna() |
        ni_g23.isna() | ni_g24.isna() | ni_g25.isna() |
        opm23.isna() | opm24.isna() | opm25.isna() |
        debt25.isna() | roe23.isna() | roe24.isna() | roe25.isna() |
        eps_cagr.isna() | dps23.isna() | dps24.isna() | dps25.isna() | payout25.isna()
    )
    df["필수통과"] = yn(required, required_missing)

    score = pd.Series(0, index=df.index, dtype="int64")
    score += required.fillna(False).astype(int) * 8
    score += roe_avg_ok.fillna(False).astype(int) * 2
    score += roe_up.fillna(False).astype(int)
    score += divy25.ge(2).fillna(False).astype(int)
    score += payout25.le(40).fillna(False).astype(int) * 2
    score += ((payout25 > 40) & (payout25 <= 50)).fillna(False).astype(int)
    score += div_inc4.fillna(False).astype(int) * 2
    score += per_down.fillna(False).astype(int)
    score += cfo_good.fillna(False).astype(int)
    score += cfi_good.fillna(False).astype(int)
    score += cff_good.fillna(False).astype(int)
    score -= payout25.ge(80).fillna(False).astype(int) * 2
    score -= (divy25 > earn_yield).fillna(False).astype(int)
    score -= ((ni_up) & (~cfo_good)).fillna(False).astype(int)
    df["총점"] = score

    grade = pd.Series("제외", index=df.index, dtype="object")
    grade[required.fillna(False) & (score >= 13)] = "A"
    grade[required.fillna(False) & (score >= 10) & (score < 13)] = "B"
    grade[required.fillna(False) & (score < 10)] = "C"
    df["최종등급"] = grade

    reasons = []
    for _, r in df.iterrows():
        items = []
        if r["필수통과"] == "데이터없음":
            items.append("필수데이터부족")
        else:
            if r["매출액_3년연속증가(증가율대용)"] != "Y": items.append("매출 3년증가 미흡")
            if r["영업이익_3년연속증가(증가율대용)"] != "Y": items.append("영업이익 3년증가 미흡")
            if r["순이익_3년연속증가(증가율대용)"] != "Y": items.append("순이익 3년증가 미흡")
            if r["영업이익률_3년30이상_및_증가"] != "Y": items.append("영업이익률 조건 미흡")
            if r["부채비율_100이하"] != "Y": items.append("부채비율 100 초과")
            if r["ROE_최소15이상_3년"] != "Y": items.append("ROE 최소 15 미달")
            if r["EPS_5년CAGR_20이상"] != "Y": items.append("EPS 5년 CAGR 미흡")
            if r["배당삭감_최근3년_제외"] != "Y": items.append("최근3년 배당삭감")
            if r["배당중지_최근3년_제외"] != "Y": items.append("최근3년 배당중지")
            if r["배당기록3년이상_충족"] != "Y": items.append("배당기록 3년 미만")
            if r["배당성향100미만"] != "Y": items.append("배당성향 100% 이상")
        if r["배당수익률_2이상"] == "N": items.append("배당수익률 2% 미만")
        if r["배당성향_80이상_경고"] == "Y": items.append("배당성향 과열")
        if r["배당수익률_이익수익률초과_경고"] == "Y": items.append("배당수익률>이익수익률")
        if r["영업현금흐름_3년모두플러스"] == "N": items.append("영업현금흐름 약함")
        reasons.append("; ".join(dict.fromkeys(items)))
    df["판정사유"] = reasons

    preferred = [
        "종목코드","종목명","현재가","필수통과","총점","최종등급","판정사유",
        "매출액증가율_2023","매출액증가율_2024","매출액증가율_2025","매출액_3년연속증가(증가율대용)",
        "영업이익증가율_2023","영업이익증가율_2024","영업이익증가율_2025","영업이익_3년연속증가(증가율대용)",
        "순이익증가율_2023","순이익증가율_2024","순이익증가율_2025","순이익_3년연속증가(증가율대용)",
        "영업이익률_2023","영업이익률_2024","영업이익률_2025","영업이익률_3년30이상_및_증가",
        "ROE_2023","ROE_2024","ROE_2025","ROE평균_2023_2025","ROE평균판정","ROE_최소15이상_3년","ROE_평균25이상","ROE_상승추세",
        "EPS_2021","EPS_2025","EPS_CAGR_2021_2025","EPS_5년CAGR_20이상",
        "DPS_2021","DPS_2022","DPS_2023","DPS_2024","DPS_2025",
        "배당기록_최근3년","배당삭감여부_최근3년","배당중지여부_최근3년","배당삭감_최근3년_제외","배당중지_최근3년_제외","배당기록3년이상_충족","4년연속배당증가",
        "현금배당수익률_2025","배당수익률_2이상",
        "현금배당성향_2025","배당성향버킷_2025","배당성향_40이하","배당성향_50이하","배당성향_80이상_경고","배당성향100미만",
        "이익수익률_2025","이익수익률_배당수익률이상","배당수익률_이익수익률초과_경고",
        "부채비율_2025","부채비율_100이하","유동비율_2025","유동비율_150이상",
        "PER_2023","PER_2024","PER_2025","PER하향추세_2023_2025",
        "영업현금흐름_3년모두플러스","투자현금흐름_3년모두마이너스","재무현금흐름_3년모두마이너스"
    ]
    existing = [c for c in preferred if c in df.columns]
    df = df[existing + [c for c in df.columns if c not in existing]]
    df.to_csv(args.output_path, sep="\t", index=False, encoding="utf-8-sig")
    print(f"saved: {args.output_path} ({len(df)} rows)", flush=True)


if __name__ == "__main__":
    main()
