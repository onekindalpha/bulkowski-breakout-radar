from __future__ import annotations

import json
import sys
from pathlib import Path


def cagr(end_value, start_value, periods):
    if periods <= 0 or start_value <= 0 or end_value <= 0:
        return None
    return (end_value / start_value) ** (1 / periods) - 1


def fmt_num(x):
    if x is None:
        return "-"
    if abs(x) >= 1000:
        return f"{x:,.0f}"
    return f"{x:.2f}"


def fmt_pct(x):
    if x is None:
        return "-"
    return f"{x*100:.2f}%"


def fmt_ratio(x):
    if x is None:
        return "-"
    return f"{x:.2f}"


def to_markdown(headers, rows):
    out = []
    out.append("| " + " | ".join(headers) + " |")
    out.append("|" + "|".join(["---"] * len(headers)) + "|")
    for row in rows:
        out.append("| " + " | ".join(str(x) for x in row) + " |")
    return "\n".join(out)


def load_json(path):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def sane_price(x):
    if x is None:
        return None
    try:
        x = float(x)
    except Exception:
        return None
    if x <= 0 or x >= 2000:
        return None
    return x


def safe_sub(a, b):
    if a is None or b is None:
        return None
    return a - b


def safe_div(a, b):
    if a is None or b in (None, 0):
        return None
    return a / b


def generate_equity_tables(data):
    years = sorted(data["years"].keys(), reverse=True)
    per_share_net_cash = {}
    per_share_net_cash_cons = {}
    per_share_fcf = {}
    for y in years:
        d = data["years"][y]
        cash_a = d["cash_equivalents"] + d.get("marketable_securities", 0)
        long_b = d["long_term_debt"]
        current_c = d["current_debt"]
        lynch_net = cash_a - long_b
        cons_net = cash_a - long_b - current_c
        shares = d["shares_outstanding"] if d["shares_outstanding"] else None
        per_share_net_cash[y] = lynch_net / shares if shares else None
        per_share_net_cash_cons[y] = cons_net / shares if shares else None
        fcf = d["operating_cash_flow"] - d["capex_tangible"] - d["capex_intangible"]
        per_share_fcf[y] = fcf / shares if shares else None

    headers = ["항목"] + years
    items = [
        ("현금및현금성자산", "cash_equivalents"),
        ("단기투자자산", "marketable_securities"),
        ("현금성자산 합계 (A)", None),
        ("장기부채 = 비유동 차입금 (B)", "long_term_debt"),
        ("유동 차입금 (C)", "current_debt"),
        ("린치식 순현금 = A - B", None),
        ("보수형 순현금 = A - B - C", None),
        ("유통주식수", "shares_outstanding"),
        ("주당 순현금(린치식)", None),
        ("주당 순현금(보수형)", None),
        ("현금배당금", "dividend_per_share"),
        ("EPS(기본주당이익)", "eps_basic"),
    ]
    rows = []
    for label, key in items:
        row = [label]
        for y in years:
            d = data["years"][y]
            cash_a = d["cash_equivalents"] + d.get("marketable_securities", 0)
            long_b = d["long_term_debt"]
            current_c = d["current_debt"]
            lynch_net = cash_a - long_b
            cons_net = cash_a - long_b - current_c
            if label == "현금성자산 합계 (A)":
                val = cash_a
            elif label == "린치식 순현금 = A - B":
                val = lynch_net
            elif label == "보수형 순현금 = A - B - C":
                val = cons_net
            elif label == "주당 순현금(린치식)":
                val = per_share_net_cash[y]
            elif label == "주당 순현금(보수형)":
                val = per_share_net_cash_cons[y]
            else:
                val = d.get(key)
            row.append(fmt_num(val))
        rows.append(row)

    out = [f"# {data['company']} ({data['ticker']})", "", "## 1) 상단 원자료 표", to_markdown(headers, rows), ""]

    price = sane_price(data.get("current_price"))
    eps_2025 = data["years"]["2025"]["eps_basic"]
    dps_2025 = data["years"]["2025"]["dividend_per_share"]
    adj_price_lynch = safe_sub(price, per_share_net_cash["2025"])
    adj_price_cons = safe_sub(price, per_share_net_cash_cons["2025"])
    per_lynch = safe_div(adj_price_lynch, eps_2025)
    per_cons = safe_div(adj_price_cons, eps_2025)
    general_per = safe_div(price, eps_2025)
    growth_1 = (data["years"]["2025"]["eps_basic"] / data["years"]["2024"]["eps_basic"] - 1
                if data["years"]["2024"]["eps_basic"] > 0 and data["years"]["2025"]["eps_basic"] > 0 else None)
    growth_3 = cagr(data["years"]["2025"]["eps_basic"], data["years"]["2022"]["eps_basic"], 3)
    growth_5 = cagr(data["years"]["2025"]["eps_basic"], data["years"]["2021"]["eps_basic"], 4)
    div_yield = safe_div(dps_2025, price)

    def score_div(g, dy, per):
        if g is None or dy is None or per in (None, 0):
            return None
        return ((g * 100) + (dy * 100)) / per

    rows2 = [
        ["일반 PER", "주가 ÷ EPS", f"{fmt_ratio(general_per)}배" if general_per is not None else "-"],
        ["순현금 차감 주가(린치식)", "주가 - 주당 순현금(린치식)", fmt_num(adj_price_lynch)],
        ["순현금 차감 PER(린치식)", "(주가 - 주당 순현금) ÷ EPS", f"{fmt_ratio(per_lynch)}배" if per_lynch is not None else "-"],
        ["순현금 차감 주가(보수형)", "주가 - 주당 순현금(보수형)", fmt_num(adj_price_cons)],
        ["순현금 차감 PER(보수형)", "(주가 - 주당 순현금) ÷ EPS", f"{fmt_ratio(per_cons)}배" if per_cons is not None else "-"],
        ["연간 이익 증가율(1년)", "(2025 EPS ÷ 2024 EPS - 1) × 100", fmt_pct(growth_1)],
        ["장기성장률(3년 CAGR)", "((2025 EPS ÷ 2022 EPS)^(1/3) - 1) × 100", fmt_pct(growth_3)],
        ["장기성장률(5년 CAGR)", "((2025 EPS ÷ 2021 EPS)^(1/4) - 1) × 100", fmt_pct(growth_5)],
        ["배당수익률", "DPS ÷ 주가 × 100", fmt_pct(div_yield)],
        ["배당까지 감안한 이익성장률(3년)", "(장기성장률(3년) + 배당수익률) ÷ PER", fmt_ratio(score_div(growth_3, div_yield, per_lynch))],
        ["배당까지 감안한 이익성장률(5년)", "(장기성장률(5년) + 배당수익률) ÷ PER", fmt_ratio(score_div(growth_5, div_yield, per_lynch))],
    ]
    out += ["## 2) 평가표", to_markdown(["항목", "계산식", "값"], rows2), ""]

    rows3 = []
    for y in years:
        d = data["years"][y]
        eq = d["shareholder_equity"]
        debt = d["long_term_debt"]
        total = eq + debt
        eq_pct = eq / total if total else None
        debt_pct = debt / total if total else None
        multiple = eq / debt if debt else None
        rows3.append([y, fmt_num(eq), fmt_num(debt), fmt_pct(eq_pct), fmt_pct(debt_pct), fmt_ratio(multiple)])
    out += ["## 3) 주주지분 / 장기부채 표",
            to_markdown(["연도", "주주지분", "장기부채", "주주지분 비중", "장기부채 비중", "주주지분 대 장기부채 배수"], rows3),
            ""]

    rows4 = []
    for y in years:
        d = data["years"][y]
        fcf = d["operating_cash_flow"] - d["capex_tangible"] - d["capex_intangible"]
        rows4.append([y, fmt_num(d["capex_tangible"]), fmt_num(d["capex_intangible"]), fmt_num(fcf), fmt_num(per_share_fcf[y])])
    out += ["## 4) 잉여현금흐름(FCF) 표",
            to_markdown(["연도", "유형자산의 취득", "무형자산의 취득", "잉여현금흐름(FCF)", "주당 잉여현금흐름"], rows4),
            ""]
    return "\n".join(out)


def generate_etf_tables(data):
    rows = [
        ["현재가", fmt_num(data["current_price"])],
        ["NAV", fmt_num(data["nav"])],
        ["프리미엄/디스카운트", f"{data['premium_discount_pct']:.2f}%"],
        ["Distribution Rate", f"{data['distribution_rate_pct']:.2f}%"],
        ["30-Day SEC Yield", f"{data['sec_yield_30d_pct']:.2f}%"],
        ["ROC", f"{data['roc_pct']:.2f}%"],
        ["총보수", f"{data['expense_ratio_pct']:.2f}%"],
        ["AUM", fmt_num(data["aum"])],
        ["기초자산", data["underlying"]],
        ["전략", data["strategy"]],
        ["1년 총수익", f"{data['total_return_1y_pct']:.2f}%"],
        ["1년 NAV 수익률", f"{data['nav_return_1y_pct']:.2f}%"],
    ]
    return "\n".join([
        f"# {data['fund_name']} ({data['ticker']})",
        "",
        "## ETF 요약 표",
        to_markdown(["항목", "값"], rows),
        "",
    ])


def main():
    if len(sys.argv) < 2:
        print("Usage: python generate_lynch_tables_fixed_v3.py <json-file> [<json-file> ...]")
        sys.exit(1)

    failed = []
    for arg in sys.argv[1:]:
        path = Path(arg)
        if not path.exists():
            failed.append((arg, "file not found"))
            print(f"[SKIPPED] {arg}: file not found", file=sys.stderr)
            continue

        try:
            data = load_json(path)
            if data["type"] == "equity":
                md = generate_equity_tables(data)
            elif data["type"] == "etf":
                md = generate_etf_tables(data)
            else:
                raise ValueError(f"Unknown type: {data['type']}")
            out_path = path.with_suffix(".md")
            out_path.write_text(md, encoding="utf-8")
            print(f"Generated: {out_path}")
        except Exception as e:
            failed.append((arg, str(e)))
            print(f"[FAILED] {arg}: {e}", file=sys.stderr)

    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
