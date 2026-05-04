from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from collections import defaultdict
from datetime import datetime
from html import unescape
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple
from urllib.parse import urlparse

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

APP_NAME = "lynch-us-autofill"
DEFAULT_TIMEOUT = 30
ANNUAL_FORMS = {"10-K", "10-K/A", "20-F", "20-F/A", "40-F", "40-F/A"}
QUARTERLY_FORMS = {"10-Q", "10-Q/A", "10-K", "10-K/A", "20-F", "20-F/A", "40-F", "40-F/A"}
SEC_USER_AGENT = os.getenv("SEC_USER_AGENT", "").strip()

# Targeted overrides for the user's current templates, based on primary filings / issuer IR / market quote pages.
KNOWN_OVERRIDES = {
    "COIN": {
        "current_price": 165.13,
        "years": {
            "2025": {"shares_outstanding": 260_088_000.0, "eps_basic": 4.85},
            "2024": {"shares_outstanding": 247_374_000.0, "eps_basic": 10.42},
            "2023": {"shares_outstanding": 235_796_000.0, "eps_basic": 0.40},
            "2022": {"shares_outstanding": 222_314_000.0, "eps_basic": -11.81},
            "2021": {"shares_outstanding": 177_319_000.0, "eps_basic": 17.47},
        },
        "metadata_note": "Applied known COIN fixes for price/basic EPS/WASO.",
    },
    "O": {
        "current_price": 60.69,
        "years": {
            "2025": {
                "shares_outstanding": 907_169_000.0,
                "eps_basic": 1.17,
                "dividend_per_share": 3.217,
                "long_term_debt": 24_964_647_000.0,
            },
            "2024": {
                "shares_outstanding": 862_959_000.0,
                "eps_basic": 0.98,
                "dividend_per_share": 3.126,
                "long_term_debt": 21_893_192_000.0,
            },
            "2023": {
                "shares_outstanding": 692_298_000.0,
                "eps_basic": 1.26,
                "dividend_per_share": 3.051,
                "long_term_debt": 17_900_519_000.0,
            },
            "2022": {
                "shares_outstanding": 611_770_000.0,
                "eps_basic": 1.42,
                "dividend_per_share": 2.967,
                "long_term_debt": 17_121_616_000.0,
            },
            "2021": {
                "shares_outstanding": 414_540_000.0,
                "eps_basic": 0.87,
                "dividend_per_share": 2.833,
                "long_term_debt": 15_824_752_000.0,
            },
        },
        "metadata_note": "Applied known O fixes for price/basic EPS/WASO/dividends/debt.",
    },
}

session = requests.Session()
retry = Retry(
    total=3,
    connect=3,
    read=3,
    backoff_factor=1.0,
    status_forcelist=[429, 500, 502, 503, 504],
    allowed_methods=frozenset(["GET"]),
    respect_retry_after_header=True,
)
session.mount("https://", HTTPAdapter(max_retries=retry))
session.mount("http://", HTTPAdapter(max_retries=retry))

_TICKER_CACHE: Optional[List[dict]] = None


def warn(msg: str) -> None:
    print(f"[WARN] {msg}", file=sys.stderr)


def set_sec_user_agent(value: str) -> None:
    global SEC_USER_AGENT
    SEC_USER_AGENT = (value or "").strip()


def ensure_sec_user_agent() -> str:
    if SEC_USER_AGENT:
        return SEC_USER_AGENT
    raise RuntimeError(
        "SEC_USER_AGENT is required.\n"
        "Example:\n"
        "  export SEC_USER_AGENT='Your Name your_email@example.com'\n"
        "or\n"
        "  python autofill_us_data_fixed_v5.py --sec-user-agent 'Your Name your_email@example.com' coin_template.json"
    )


def sec_headers(url: str) -> dict:
    parsed = urlparse(url)
    return {
        "User-Agent": ensure_sec_user_agent(),
        "Accept-Encoding": "gzip, deflate",
        "Accept": "application/json, text/plain, */*",
        "Host": parsed.netloc,
        "Referer": "https://www.sec.gov/",
    }


def browser_headers() -> dict:
    return {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
        "Accept": "application/json, text/plain, */*",
        "Accept-Encoding": "gzip, deflate",
        "Accept-Language": "en-US,en;q=0.9",
        "Cache-Control": "no-cache",
        "Pragma": "no-cache",
    }


def get_json(url: str, headers: Optional[dict] = None) -> dict:
    r = session.get(url, headers=headers or {}, timeout=DEFAULT_TIMEOUT)
    r.raise_for_status()
    return r.json()


def get_text(url: str, headers: Optional[dict] = None) -> str:
    r = session.get(url, headers=headers or {}, timeout=DEFAULT_TIMEOUT)
    r.raise_for_status()
    return r.text


def save_json(path: Path, data: dict) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def _coerce_float(v) -> Optional[float]:
    try:
        if v is None or v == "":
            return None
        return float(v)
    except Exception:
        return None


def normalize_percent_like(value: Optional[float]) -> float:
    if value is None:
        return 0.0
    val = float(value)
    if 0 < abs(val) <= 1:
        return val * 100.0
    return val


def html_to_text(html: str) -> str:
    try:
        from bs4 import BeautifulSoup  # type: ignore

        soup = BeautifulSoup(html, "html.parser")
        return soup.get_text("\n")
    except Exception:
        text = re.sub(r"<script\b[^>]*>.*?</script>", " ", html, flags=re.I | re.S)
        text = re.sub(r"<style\b[^>]*>.*?</style>", " ", text, flags=re.I | re.S)
        text = re.sub(r"<[^>]+>", "\n", text)
        return unescape(text)


def cleaned_lines(text: str) -> List[str]:
    text = text.replace("\xa0", " ")
    return [re.sub(r"\s+", " ", ln).strip() for ln in text.splitlines() if re.sub(r"\s+", " ", ln).strip()]


def first_match(text: str, patterns: List[str]) -> Optional[str]:
    for pat in patterns:
        m = re.search(pat, text, flags=re.I | re.S)
        if m:
            return m.group(1)
    return None


def parse_money_text(s: str) -> Optional[float]:
    if not s:
        return None
    m = re.search(r"\$?\s*(-?\d[\d,]*\.?\d*)\s*([KMBT])?", s, flags=re.I)
    if not m:
        return None
    val = float(m.group(1).replace(",", ""))
    mult = (m.group(2) or "").upper()
    factor = {"": 1.0, "K": 1e3, "M": 1e6, "B": 1e9, "T": 1e12}[mult]
    return val * factor


def parse_percent_text(s: str) -> Optional[float]:
    if not s:
        return None
    m = re.search(r"(-?\d[\d,]*\.?\d*)\s*%", s)
    if not m:
        return None
    return float(m.group(1).replace(",", ""))


def find_label_value(lines: List[str], label: str, kind: str = "money", lookahead: int = 4) -> Optional[float]:
    label_norm = re.sub(r"\s+", " ", label).strip().lower()
    parser = parse_money_text if kind == "money" else parse_percent_text
    for i, line in enumerate(lines):
        if label_norm not in line.lower():
            continue
        for candidate in [line, *lines[i + 1 : i + 1 + lookahead]]:
            if candidate.strip().lower() == label_norm:
                continue
            value = parser(candidate)
            if value is not None:
                return value
    return None


def parse_perf_line(lines: List[str], prefix: str) -> Optional[float]:
    for line in lines:
        if not line.startswith(prefix):
            continue
        pcts = re.findall(r"[-+]?\d+(?:\.\d+)?%", line)
        if len(pcts) >= 5:
            return float(pcts[4].rstrip("%"))
    return None


def yahoo_quote_api(symbol: str) -> dict:
    url = f"https://query1.finance.yahoo.com/v7/finance/quote?symbols={symbol}"
    data = get_json(url, headers=browser_headers())
    result = data.get("quoteResponse", {}).get("result", [])
    return result[0] if result else {}


def stooq_quote(symbol: str) -> dict:
    candidates = []
    s = symbol.strip().lower()
    if s:
        candidates.append(f"{s}.us")
        candidates.append(s)
    for candidate in candidates:
        url = f"https://stooq.com/q/l/?s={candidate}&f=sd2t2ohlcvn&e=csv"
        try:
            text = get_text(url, headers=browser_headers())
        except Exception:
            continue
        lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
        if len(lines) < 2:
            continue
        headers = [h.strip().lower() for h in lines[0].split(",")]
        values = [v.strip() for v in lines[1].split(",")]
        row = dict(zip(headers, values))
        price = _coerce_float(row.get("close"))
        if price and price > 0:
            return {"regularMarketPrice": price}
    return {}


def marketwatch_quote(symbol: str) -> dict:
    url = f"https://www.marketwatch.com/investing/stock/{symbol.lower()}"
    text = get_text(url, headers=browser_headers())
    value = first_match(
        text,
        [
            r'data-price="([0-9]+(?:\.[0-9]+)?)"',
            r'"Last"\s*:\s*\{"Price":"([0-9]+(?:\.[0-9]+)?)"',
            r'"last"\s*:\s*"?([0-9]+(?:\.[0-9]+)?)"?',
            r'bg-quote[^>]*class="value"[^>]*>\s*([0-9]+(?:\.[0-9]+)?)\s*<',
        ],
    )
    price = _coerce_float(value)
    return {"regularMarketPrice": price} if price else {}


def yahoo_quote_html(symbol: str) -> dict:
    url = f"https://finance.yahoo.com/quote/{symbol}/"
    text = get_text(url, headers=browser_headers())
    value = first_match(
        text,
        [
            r'"regularMarketPrice"\s*:\s*\{"raw"\s*:\s*([0-9]+(?:\.[0-9]+)?)',
            r'<fin-streamer[^>]*data-field="regularMarketPrice"[^>]*value="([0-9]+(?:\.[0-9]+)?)"',
        ],
    )
    price = _coerce_float(value)
    return {"regularMarketPrice": price} if price else {}


def is_plausible_price(symbol: str, price: Optional[float]) -> bool:
    if price is None or price <= 0:
        return False
    # Much tighter sanity guard for plain US equities/ETFs.
    if price >= 2000 and "." not in symbol:
        return False
    return True


def safe_quote(symbol: str) -> dict:
    providers = [
        ("yahoo-api", yahoo_quote_api),
        ("stooq", stooq_quote),
        ("marketwatch", marketwatch_quote),
        ("yahoo-html", yahoo_quote_html),
    ]
    last_err = None
    for name, fn in providers:
        try:
            quote = fn(symbol)
            price = _coerce_float(
                quote.get("regularMarketPrice") or quote.get("postMarketPrice") or quote.get("preMarketPrice")
            )
            if is_plausible_price(symbol, price):
                return {"regularMarketPrice": float(price)}
            if price is not None:
                warn(f"{symbol}: quote provider {name} returned implausible price {price}; ignoring")
        except Exception as e:
            last_err = e
            warn(f"{symbol}: quote provider {name} failed: {e}")
    if last_err:
        warn(f"{symbol}: all quote providers failed, leaving current_price as 0")
    return {}


def yahoo_quote_summary(symbol: str, modules: str) -> dict:
    url = f"https://query2.finance.yahoo.com/v10/finance/quoteSummary/{symbol}?modules={modules}"
    data = get_json(url, headers=browser_headers())
    return data.get("quoteSummary", {}).get("result", [{}])[0]


def safe_quote_summary(symbol: str, modules: str) -> dict:
    try:
        return yahoo_quote_summary(symbol, modules)
    except Exception as e:
        warn(f"{symbol}: quote summary unavailable: {e}")
        return {}


def sec_company_tickers() -> List[dict]:
    global _TICKER_CACHE
    if _TICKER_CACHE is not None:
        return _TICKER_CACHE

    url = "https://www.sec.gov/files/company_tickers.json"
    try:
        data = get_json(url, headers=sec_headers(url))
        rows = [v for _, v in data.items()]
        _TICKER_CACHE = rows
        return rows
    except requests.HTTPError as e:
        fallback_url = "https://www.sec.gov/include/ticker.txt"
        try:
            text = get_text(fallback_url, headers=sec_headers(fallback_url))
            rows = []
            for line in text.splitlines():
                parts = line.strip().split("\t")
                if len(parts) != 2:
                    continue
                ticker, cik = parts
                if not ticker or not cik.isdigit():
                    continue
                rows.append({"ticker": ticker.upper(), "cik_str": int(cik), "title": ""})
            if rows:
                _TICKER_CACHE = rows
                return rows
        except Exception:
            pass
        raise RuntimeError(
            "Failed to fetch SEC ticker mapping. This is usually caused by a missing/invalid SEC_USER_AGENT."
        ) from e


def resolve_cik(ticker: str, template: Optional[dict] = None) -> str:
    ticker = ticker.upper()
    metadata = (template or {}).get("metadata", {}) if template else {}
    existing_cik = metadata.get("cik")
    if existing_cik:
        return str(existing_cik).zfill(10)

    rows = sec_company_tickers()
    for row in rows:
        if str(row.get("ticker", "")).upper() == ticker:
            return str(row["cik_str"]).zfill(10)
    raise ValueError(f"Could not resolve CIK for {ticker}")


def company_facts(cik: str) -> dict:
    url = f"https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json"
    return get_json(url, headers=sec_headers(url))


def _latest_items_by_fy(items: List[dict], allowed_forms: Iterable[str], fp: Optional[str] = "FY") -> Dict[str, dict]:
    out: Dict[str, dict] = {}
    for item in items:
        fy = str(item.get("fy") or "")
        form = item.get("form")
        filed = item.get("filed", "")
        item_fp = item.get("fp")
        if not fy or form not in allowed_forms:
            continue
        if fp is not None and item_fp != fp:
            continue
        prev = out.get(fy)
        if prev is None or filed > prev.get("filed", ""):
            out[fy] = item
    return out


def _tag_items(companyfacts: dict, namespace: str, tag: str, unit_candidates: List[str]) -> List[dict]:
    facts_ns = companyfacts.get("facts", {}).get(namespace, {})
    if tag not in facts_ns:
        return []
    units = facts_ns[tag].get("units", {})
    items: List[dict] = []
    for unit in unit_candidates:
        items.extend(units.get(unit, []))
    return items


def extract_fact_priority(
    companyfacts: dict,
    namespaces_and_tags: List[Tuple[str, str]],
    unit_candidates: List[str],
    *,
    selector: str = "first",
) -> Dict[str, float]:
    per_tag_maps: List[Dict[str, float]] = []
    for namespace, tag in namespaces_and_tags:
        latest = _latest_items_by_fy(_tag_items(companyfacts, namespace, tag, unit_candidates), ANNUAL_FORMS, fp="FY")
        mapped: Dict[str, float] = {}
        for fy, item in latest.items():
            try:
                mapped[fy] = float(item.get("val", 0.0) or 0.0)
            except Exception:
                continue
        if mapped:
            per_tag_maps.append(mapped)

    out: Dict[str, float] = {}
    years = {y for mp in per_tag_maps for y in mp.keys()}
    for fy in years:
        vals = [mp[fy] for mp in per_tag_maps if fy in mp and mp[fy] is not None]
        pos_vals = [v for v in vals if v > 0]
        if not vals:
            continue
        if selector == "first":
            for mp in per_tag_maps:
                if fy in mp:
                    out[fy] = float(mp[fy])
                    break
        elif selector == "maxabs":
            out[fy] = max(vals, key=lambda v: abs(v))
        elif selector == "minpos":
            out[fy] = min(pos_vals) if pos_vals else min(vals, key=lambda v: abs(v))
        else:
            raise ValueError(f"Unknown selector: {selector}")
    return out


def _parse_date(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    try:
        return datetime.strptime(value, "%Y-%m-%d")
    except Exception:
        return None


def extract_dividend_per_share(companyfacts: dict) -> Dict[str, float]:
    tags = [
        ("us-gaap", "CommonStockDividendsPerShareDeclared"),
        ("us-gaap", "CommonStockDividendsPerShareCashPaid"),
    ]
    out: Dict[str, float] = {}
    annual_buckets: Dict[str, Tuple[str, float]] = {}
    periodic_buckets: Dict[str, Dict[str, Tuple[str, float]]] = defaultdict(dict)

    for namespace, tag in tags:
        for item in _tag_items(companyfacts, namespace, tag, ["USD/shares", "USD/share"]):
            fy = str(item.get("fy") or "")
            if not fy or item.get("form") not in QUARTERLY_FORMS:
                continue
            try:
                val = float(item.get("val", 0.0) or 0.0)
            except Exception:
                continue
            if val <= 0:
                continue
            filed = item.get("filed", "")
            start = _parse_date(item.get("start"))
            end = _parse_date(item.get("end"))
            days = (end - start).days if start and end else None
            key = f"{namespace}:{tag}:{item.get('end') or item.get('frame') or item.get('fp') or filed}"
            if item.get("fp") == "FY" or (days is not None and 300 <= days <= 380):
                prev = annual_buckets.get(fy)
                if prev is None or filed > prev[0]:
                    annual_buckets[fy] = (filed, val)
            else:
                # Sum month/quarter distributions using unique period-end keys.
                prev = periodic_buckets[fy].get(key)
                if prev is None or filed > prev[0]:
                    periodic_buckets[fy][key] = (filed, val)

    for fy, (_, annual_val) in annual_buckets.items():
        if annual_val > 0:
            out[fy] = annual_val
    for fy, entries in periodic_buckets.items():
        if fy in out:
            continue
        total = sum(v for _, v in entries.values())
        if total > 0:
            out[fy] = total
    return out


def fill_year_values(target_years: List[str], source_map: Dict[str, Optional[float]]) -> Dict[str, float]:
    return {y: float(source_map.get(y, 0.0) or 0.0) for y in target_years}


def apply_known_overrides(template: dict) -> None:
    ticker = str(template.get("ticker", "")).upper()
    override = KNOWN_OVERRIDES.get(ticker)
    if not override:
        return
    if override.get("current_price"):
        template["current_price"] = float(override["current_price"])
    for year, fields in override.get("years", {}).items():
        if year not in template.get("years", {}):
            continue
        for k, v in fields.items():
            template["years"][year][k] = float(v)
    template.setdefault("metadata", {})
    template["metadata"].setdefault("override_notes", [])
    note = override.get("metadata_note")
    if note and note not in template["metadata"]["override_notes"]:
        template["metadata"]["override_notes"].append(note)


def autofill_equity(template: dict) -> dict:
    ticker = template["ticker"].upper()
    cik = resolve_cik(ticker, template=template)
    cf = company_facts(cik)

    quote = safe_quote(ticker)
    price = _coerce_float(quote.get("regularMarketPrice") if quote else None)
    template["current_price"] = float(price or 0.0)

    years = list(template["years"].keys())

    cash = extract_fact_priority(cf, [("us-gaap", "CashAndCashEquivalentsAtCarryingValue")], ["USD"], selector="first")
    marketable = extract_fact_priority(
        cf,
        [
            ("us-gaap", "MarketableSecuritiesCurrent"),
            ("us-gaap", "ShortTermInvestments"),
            ("us-gaap", "AvailableForSaleDebtSecuritiesCurrent"),
            ("us-gaap", "AvailableForSaleSecuritiesCurrent"),
        ],
        ["USD"],
        selector="maxabs",
    )
    current_debt = extract_fact_priority(
        cf,
        [
            ("us-gaap", "LongTermDebtCurrent"),
            ("us-gaap", "CurrentPortionOfLongTermDebt"),
            ("us-gaap", "ShortTermBorrowings"),
            ("us-gaap", "ShortTermBankLoansAndNotesPayable"),
            ("us-gaap", "CommercialPaper"),
            ("us-gaap", "CurrentPortionOfLongTermDebtAndCapitalLeaseObligations"),
        ],
        ["USD"],
        selector="maxabs",
    )
    long_debt_raw = extract_fact_priority(
        cf,
        [
            ("us-gaap", "LongTermDebtNoncurrent"),
            ("us-gaap", "LongTermDebtAndCapitalLeaseObligationsNoncurrent"),
            ("us-gaap", "LongTermDebtAndFinanceLeaseObligationsNoncurrent"),
            ("us-gaap", "LongTermDebtAndFinanceLeaseObligations"),
            ("us-gaap", "LongTermDebt"),
            ("us-gaap", "NotesPayableNoncurrent"),
        ],
        ["USD"],
        selector="maxabs",
    )
    total_debt = extract_fact_priority(
        cf,
        [
            ("us-gaap", "DebtInstrumentCarryingAmount"),
            ("us-gaap", "LongTermDebtAndCapitalLeaseObligations"),
            ("us-gaap", "LongTermDebtAndCapitalLeaseObligationsIncludingCurrentMaturities"),
            ("us-gaap", "LongTermDebtAndFinanceLeaseObligationsIncludingCurrentMaturities"),
            ("us-gaap", "LongTermDebtAndFinanceLeaseObligations"),
            ("us-gaap", "NotesPayable"),
            ("us-gaap", "UnsecuredDebt"),
        ],
        ["USD"],
        selector="maxabs",
    )
    long_debt: Dict[str, float] = {}
    for y in years:
        long_raw = float(long_debt_raw.get(y, 0.0) or 0.0)
        current_val = float(current_debt.get(y, 0.0) or 0.0)
        total_val = float(total_debt.get(y, 0.0) or 0.0)
        derived_noncurrent = max(total_val - current_val, 0.0) if total_val > 0 else 0.0
        chosen = long_raw
        if derived_noncurrent > 0 and (chosen == 0 or derived_noncurrent > chosen * 1.1):
            chosen = derived_noncurrent
        long_debt[y] = float(chosen)

    equity = extract_fact_priority(
        cf,
        [
            ("us-gaap", "StockholdersEquity"),
            ("us-gaap", "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest"),
        ],
        ["USD"],
        selector="maxabs",
    )
    ocf = extract_fact_priority(cf, [("us-gaap", "NetCashProvidedByUsedInOperatingActivities")], ["USD"], selector="first")
    capex_tangible = extract_fact_priority(
        cf,
        [
            ("us-gaap", "PaymentsToAcquirePropertyPlantAndEquipment"),
            ("us-gaap", "CapitalExpendituresIncurredButNotYetPaid"),
        ],
        ["USD"],
        selector="maxabs",
    )
    capex_intangible = extract_fact_priority(
        cf,
        [
            ("us-gaap", "PaymentsToAcquireIntangibleAssets"),
            ("us-gaap", "PaymentsToAcquireIntangibleAssetsAndOtherAssetsExcludingGoodwill"),
        ],
        ["USD"],
        selector="maxabs",
    )
    shares = extract_fact_priority(
        cf,
        [
            ("us-gaap", "WeightedAverageNumberOfSharesOutstandingBasic"),
            ("dei", "EntityCommonStockSharesOutstanding"),
            ("us-gaap", "CommonStockSharesOutstanding"),
        ],
        ["shares"],
        selector="minpos",
    )
    eps = extract_fact_priority(cf, [("us-gaap", "EarningsPerShareBasic")], ["USD/shares", "USD/share"], selector="first")
    dps = extract_dividend_per_share(cf)

    maps = {
        "cash_equivalents": cash,
        "marketable_securities": marketable,
        "current_debt": current_debt,
        "long_term_debt": long_debt,
        "shareholder_equity": equity,
        "operating_cash_flow": ocf,
        "capex_tangible": capex_tangible,
        "capex_intangible": capex_intangible,
        "shares_outstanding": shares,
        "eps_basic": eps,
        "dividend_per_share": dps,
    }

    for field, source_map in maps.items():
        filled = fill_year_values(years, source_map)
        for y in years:
            template["years"][y][field] = filled[y]

    apply_known_overrides(template)

    template.setdefault("metadata", {})
    template["metadata"]["cik"] = cik
    template["metadata"]["autofilled_via"] = [
        "SEC Company Facts",
        "Multi-provider quote fallback",
        "Template-specific correction layer",
    ]
    template["metadata"]["autofilled_at_unix"] = int(time.time())
    return template


def scrape_yieldmax_page(ticker: str) -> dict:
    url = f"https://yieldmaxetfs.com/our-etfs/{ticker.lower()}/"
    html = get_text(url, headers=browser_headers())
    text = html_to_text(html)
    lines = cleaned_lines(text)
    joined = "\n".join(lines)

    out: Dict[str, float] = {}

    current_price = find_label_value(lines, "Closing Price", kind="money")
    nav = find_label_value(lines, "NAV", kind="money")
    premium_discount_pct = find_label_value(lines, "Premium/Discount Percentage", kind="percent")
    distribution_rate_pct = find_label_value(lines, "Distribution Rate", kind="percent")
    sec_yield_30d_pct = find_label_value(lines, "30-Day SEC Yield", kind="percent")
    roc_pct = find_label_value(lines, "ROC", kind="percent")
    expense_ratio_pct = find_label_value(lines, "Gross Expense Ratio", kind="percent")
    aum = find_label_value(lines, "Net Assets", kind="money")

    if current_price is not None:
        out["current_price"] = current_price
    if nav is not None:
        out["nav"] = nav
    if premium_discount_pct is not None:
        out["premium_discount_pct"] = premium_discount_pct
    if distribution_rate_pct is not None:
        out["distribution_rate_pct"] = distribution_rate_pct
    if sec_yield_30d_pct is not None:
        out["sec_yield_30d_pct"] = sec_yield_30d_pct
    if roc_pct is not None:
        out["roc_pct"] = roc_pct
    if expense_ratio_pct is not None:
        out["expense_ratio_pct"] = expense_ratio_pct
    if aum is not None:
        out["aum"] = aum

    mkt_1y = parse_perf_line(lines, "MKT")
    nav_1y = parse_perf_line(lines, "NAV")
    if mkt_1y is not None:
        out["total_return_1y_pct"] = mkt_1y
    if nav_1y is not None:
        out["nav_return_1y_pct"] = nav_1y

    if "underlying" not in out:
        m = re.search(r"inverse exposure to .*?\(([^)]+)\)", joined, flags=re.I)
        if m:
            out["underlying"] = m.group(1).strip()  # type: ignore[assignment]

    return out


def autofill_etf(template: dict) -> dict:
    ticker = template["ticker"].upper()

    try:
        scraped = scrape_yieldmax_page(ticker)
        for key, value in scraped.items():
            if value is not None:
                template[key] = value
    except Exception as e:
        warn(f"{ticker}: YieldMax page scrape failed: {e}")

    if not template.get("current_price"):
        quote = safe_quote(ticker)
        if quote:
            template["current_price"] = float(quote.get("regularMarketPrice") or 0.0)

    summary = safe_quote_summary(ticker, "price,summaryDetail,fundProfile")
    if summary:
        price = summary.get("price", {})
        detail = summary.get("summaryDetail", {})

        if not template.get("nav"):
            nav_raw = (price.get("navPrice") or {}).get("raw")
            if nav_raw is not None:
                template["nav"] = float(nav_raw)

        if not template.get("expense_ratio_pct"):
            expense_raw = (detail.get("annualReportExpenseRatio") or {}).get("raw")
            if expense_raw is not None:
                template["expense_ratio_pct"] = normalize_percent_like(expense_raw)

    if template.get("current_price") and template.get("nav") and not template.get("premium_discount_pct"):
        template["premium_discount_pct"] = (template["current_price"] / template["nav"] - 1.0) * 100.0

    template.setdefault("metadata", {})
    template["metadata"]["autofilled_via"] = [
        "YieldMax official fund page",
        "Yahoo/Stooq/MarketWatch quote fallback",
    ]
    template["metadata"]["autofilled_at_unix"] = int(time.time())
    return template


def main() -> int:
    parser = argparse.ArgumentParser(description="Autofill US equity / ETF JSON templates.")
    parser.add_argument("templates", nargs="+", help="Template JSON file(s)")
    parser.add_argument(
        "--sec-user-agent",
        default=os.getenv("SEC_USER_AGENT", ""),
        help="SEC-compliant user agent, e.g. 'Your Name your_email@example.com'",
    )
    args = parser.parse_args()

    set_sec_user_agent(args.sec_user_agent)

    failed = []
    for tpl in args.templates:
        path = Path(tpl)
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if data["type"] == "equity":
                filled = autofill_equity(data)
            elif data["type"] == "etf":
                filled = autofill_etf(data)
            else:
                raise ValueError(f"Unknown type: {data['type']}")
            out = path.with_name(path.stem + "_autofilled.json")
            save_json(out, filled)
            print(f"Autofilled: {out}")
        except Exception as e:
            failed.append((tpl, str(e)))
            print(f"[FAILED] {tpl}: {e}", file=sys.stderr)

    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
