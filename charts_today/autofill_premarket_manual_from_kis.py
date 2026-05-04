#!/usr/bin/env python3
"""
Autofill premarket_manual_korea.csv from Korea Investment Securities (KIS) Open API.

Purpose
-------
Automate filling/updating `premarket_manual_korea.csv` with latest quote values,
so you do not have to type shortlist prices by hand every morning.

What this script does well
--------------------------
- Reads your shortlist from a CSV (`symbol`/`ticker` column).
- Fetches quotes from KIS REST current-price API.
- Writes price, previous close, fetched time, and a simple freshness flag.
- Can run once or continuously on an interval.

What this script does NOT promise
---------------------------------
- It does not guarantee NXT premarket true realtime by itself.
- KIS official docs explicitly recommend WebSocket for realtime quotes, and provide
  realtime WebSocket/NXT endpoints. This script uses REST first because it is much
  easier to stand up safely. If the API response is stale during premarket, the
  script marks the row with a warning flag so you can notice it.

Recommended usage
-----------------
1) Keep your existing evening pipeline.
2) In the morning, run this script before merge/scan/review.
3) If premarket values still look stale, upgrade the provider to KIS WebSocket
   or continue to hand-check only the final 5~15 names.

Environment variables
---------------------
export KIS_APP_KEY='...'
export KIS_APP_SECRET='...'
# optional:
export KIS_BASE_URL='https://openapi.koreainvestment.com:9443'

Example
-------
python autofill_premarket_manual_from_kis.py \
  --input premarket_manual_korea.csv \
  --output premarket_manual_korea.csv \
  --once

python autofill_premarket_manual_from_kis.py \
  --input premarket_manual_korea.csv \
  --output premarket_manual_korea.csv \
  --watch --interval 3 --until 08:48:20
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import sys
import time
from dataclasses import dataclass
from datetime import datetime, time as dtime
from typing import Any, Dict, Iterable, List, Optional, Tuple
from zoneinfo import ZoneInfo

import requests

KST = ZoneInfo("Asia/Seoul")
DEFAULT_BASE_URL = os.getenv("KIS_BASE_URL", "https://openapi.koreainvestment.com:9443")

# Common KIS REST current-price endpoint/tr_id used in many official/public examples.
# If your account/documentation differs, adjust these in ONE place.
REST_PRICE_PATH = "/uapi/domestic-stock/v1/quotations/inquire-price"
REST_PRICE_TR_ID = "FHKST01010100"
REST_MARKET_DIV_CODE = "J"

SYMBOL_CANDIDATES = ["symbol", "ticker", "종목코드", "code"]
PRICE_CANDIDATES = ["price", "current_price", "현재가"]


@dataclass
class QuoteResult:
    symbol: str
    numeric_code: str
    price: Optional[float]
    prev_close: Optional[float]
    fetched_at: str
    status: str
    raw_time: Optional[str]
    source: str = "KIS_REST"
    note: str = ""


class KISClient:
    def __init__(self, app_key: str, app_secret: str, base_url: str = DEFAULT_BASE_URL, session: Optional[requests.Session] = None):
        self.app_key = app_key
        self.app_secret = app_secret
        self.base_url = base_url.rstrip("/")
        self.session = session or requests.Session()
        self._access_token: Optional[str] = None
        self._access_token_expires_at: Optional[float] = None

    def _post(self, path: str, *, json_body: Dict[str, Any], headers: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
        url = f"{self.base_url}{path}"
        resp = self.session.post(url, json=json_body, headers=headers or {}, timeout=15)
        resp.raise_for_status()
        return resp.json()

    def _get(self, path: str, *, params: Dict[str, Any], headers: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
        url = f"{self.base_url}{path}"
        resp = self.session.get(url, params=params, headers=headers or {}, timeout=15)
        resp.raise_for_status()
        return resp.json()

    def ensure_access_token(self) -> str:
        now = time.time()
        if self._access_token and self._access_token_expires_at and now < self._access_token_expires_at - 60:
            return self._access_token

        data = self._post(
            "/oauth2/tokenP",
            json_body={
                "grant_type": "client_credentials",
                "appkey": self.app_key,
                "appsecret": self.app_secret,
            },
            headers={"content-type": "application/json; charset=UTF-8"},
        )
        token = data.get("access_token")
        if not token:
            raise RuntimeError(f"KIS token response missing access_token: {data}")
        expires_in = int(data.get("expires_in", 86400))
        self._access_token = token
        self._access_token_expires_at = now + expires_in
        return token

    def inquire_price(self, symbol: str) -> QuoteResult:
        access_token = self.ensure_access_token()
        code = normalize_symbol_to_numeric(symbol)
        fetched_at = datetime.now(KST).strftime("%Y-%m-%d %H:%M:%S %Z")
        data = self._get(
            REST_PRICE_PATH,
            params={
                "FID_COND_MRKT_DIV_CODE": REST_MARKET_DIV_CODE,
                "FID_INPUT_ISCD": code,
            },
            headers={
                "content-type": "application/json; charset=UTF-8",
                "authorization": f"Bearer {access_token}",
                "appkey": self.app_key,
                "appsecret": self.app_secret,
                "tr_id": REST_PRICE_TR_ID,
            },
        )

        output = data.get("output") or {}
        # Common KIS fields. If your response schema differs, adjust mapping here.
        price = to_float(output.get("stck_prpr"))
        prev_close = to_float(output.get("stck_sdpr"))
        raw_time = str(output.get("stck_cntg_hour") or "").strip() or None

        status = "OK"
        note = ""
        now_kst = datetime.now(KST)
        if price is None:
            status = "NO_PRICE"
            note = f"No stck_prpr in response. rt_cd={data.get('rt_cd')} msg={data.get('msg1')}"
        elif now_kst.time() < dtime(9, 0) and prev_close is not None and price == prev_close:
            # We cannot know for sure whether this is stale, but this is the exact problem you saw.
            status = "MAYBE_PREV_CLOSE"
            note = "Price equals previous close before 09:00; may be stale for premarket."

        return QuoteResult(
            symbol=symbol,
            numeric_code=code,
            price=price,
            prev_close=prev_close,
            fetched_at=fetched_at,
            status=status,
            raw_time=raw_time,
            note=note,
        )


def normalize_symbol_to_numeric(symbol: str) -> str:
    s = str(symbol).strip().upper()
    if "." in s:
        s = s.split(".", 1)[0]
    return s.zfill(6)


def to_float(value: Any) -> Optional[float]:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    s = str(value).strip().replace(",", "")
    if s == "":
        return None
    try:
        return float(s)
    except ValueError:
        return None


def detect_column(columns: Iterable[str], candidates: List[str]) -> Optional[str]:
    lower_map = {c.lower(): c for c in columns}
    for cand in candidates:
        if cand.lower() in lower_map:
            return lower_map[cand.lower()]
    return None


def read_rows(path: str) -> Tuple[List[Dict[str, Any]], List[str], str]:
    with open(path, "r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        rows = list(reader)
        if reader.fieldnames is None:
            raise RuntimeError(f"No header found in {path}")
        columns = list(reader.fieldnames)
    symbol_col = detect_column(columns, SYMBOL_CANDIDATES)
    if not symbol_col:
        raise RuntimeError(
            f"Could not detect symbol/ticker column in {path}. Expected one of {SYMBOL_CANDIDATES}, found {columns}"
        )
    return rows, columns, symbol_col


def write_rows(path: str, rows: List[Dict[str, Any]], columns: List[str]) -> None:
    with open(path, "w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=columns)
        writer.writeheader()
        writer.writerows(rows)


def update_rows_in_memory(rows: List[Dict[str, Any]], columns: List[str], symbol_col: str, client: KISClient, only_symbols: Optional[set[str]] = None) -> Tuple[List[Dict[str, Any]], List[QuoteResult]]:
    price_col = detect_column(columns, PRICE_CANDIDATES) or "price"
    extra_cols = ["prev_close", "fetched_at", "fetch_status", "fetch_note", "source"]
    for c in [price_col, *extra_cols]:
        if c not in columns:
            columns.append(c)

    results: List[QuoteResult] = []
    for row in rows:
        symbol = str(row.get(symbol_col, "")).strip()
        if not symbol:
            continue
        if only_symbols and normalize_symbol_to_numeric(symbol) not in only_symbols and symbol.upper() not in only_symbols:
            continue
        result = client.inquire_price(symbol)
        results.append(result)
        if result.price is not None:
            row[price_col] = f"{int(result.price)}" if float(result.price).is_integer() else f"{result.price:.4f}"
        row["prev_close"] = "" if result.prev_close is None else (f"{int(result.prev_close)}" if float(result.prev_close).is_integer() else f"{result.prev_close:.4f}")
        row["fetched_at"] = result.fetched_at
        row["fetch_status"] = result.status
        row["fetch_note"] = result.note
        row["source"] = result.source
    return rows, results


def parse_only_symbols(values: List[str]) -> set[str]:
    out = set()
    for v in values:
        for part in str(v).split(","):
            p = part.strip()
            if not p:
                continue
            out.add(p.upper())
            out.add(normalize_symbol_to_numeric(p))
    return out


def parse_until(value: Optional[str]) -> Optional[dtime]:
    if not value:
        return None
    hh, mm, ss = value.split(":")
    return dtime(int(hh), int(mm), int(ss))


def should_continue(until_time: Optional[dtime]) -> bool:
    if until_time is None:
        return True
    now_t = datetime.now(KST).time()
    return now_t <= until_time


def print_summary(results: List[QuoteResult]) -> None:
    if not results:
        print("No rows updated.")
        return
    print("\n=== KIS autofill summary ===")
    for r in results:
        price_text = "" if r.price is None else (f"{int(r.price)}" if float(r.price).is_integer() else f"{r.price:.4f}")
        prev_text = "" if r.prev_close is None else (f"{int(r.prev_close)}" if float(r.prev_close).is_integer() else f"{r.prev_close:.4f}")
        print(f"{r.symbol:12s} price={price_text:>10s} prev_close={prev_text:>10s} status={r.status:16s} fetched_at={r.fetched_at}")
        if r.note:
            print(f"    note: {r.note}")


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Autofill premarket_manual_korea.csv using KIS Open API")
    p.add_argument("--input", required=True, help="Input CSV path, e.g. premarket_manual_korea.csv")
    p.add_argument("--output", help="Output CSV path. Default: overwrite input")
    mode = p.add_mutually_exclusive_group(required=False)
    mode.add_argument("--once", action="store_true", help="Run once and exit")
    mode.add_argument("--watch", action="store_true", help="Poll repeatedly")
    p.add_argument("--interval", type=float, default=3.0, help="Polling interval seconds for --watch")
    p.add_argument("--until", help="Stop watching at HH:MM:SS KST, e.g. 08:48:20")
    p.add_argument("--only-symbols", nargs="*", default=[], help="Optional subset of symbols, e.g. 005930.KS 000660.KS")
    p.add_argument("--dry-run", action="store_true", help="Do not write output, only print summary")
    return p


def main() -> int:
    args = build_parser().parse_args()
    app_key = os.getenv("KIS_APP_KEY")
    app_secret = os.getenv("KIS_APP_SECRET")
    if not app_key or not app_secret:
        print("ERROR: Set KIS_APP_KEY and KIS_APP_SECRET environment variables.", file=sys.stderr)
        return 2

    output_path = args.output or args.input
    rows, columns, symbol_col = read_rows(args.input)
    client = KISClient(app_key=app_key, app_secret=app_secret)
    only_symbols = parse_only_symbols(args.only_symbols)
    only_symbols = only_symbols or None
    until_time = parse_until(args.until)

    if args.watch:
        while True:
            rows_copy = [dict(r) for r in rows]
            cols_copy = list(columns)
            rows_updated, results = update_rows_in_memory(rows_copy, cols_copy, symbol_col, client, only_symbols=only_symbols)
            print_summary(results)
            if not args.dry_run:
                write_rows(output_path, rows_updated, cols_copy)
                print(f"Wrote {output_path} ({len(rows_updated)} rows)")
            if not should_continue(until_time):
                break
            time.sleep(args.interval)
        return 0

    # Default to once if neither explicitly selected.
    rows_updated, results = update_rows_in_memory(rows, columns, symbol_col, client, only_symbols=only_symbols)
    print_summary(results)
    if not args.dry_run:
        write_rows(output_path, rows_updated, columns)
        print(f"Wrote {output_path} ({len(rows_updated)} rows)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
