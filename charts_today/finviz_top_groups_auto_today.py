#!/usr/bin/env python3
from __future__ import annotations

import argparse
import re
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable
from urllib.parse import urljoin

import pandas as pd
import requests
from bs4 import BeautifulSoup


BASE = "https://finviz.com/"
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/123.0.0.0 Safari/537.36"
    ),
    "Referer": "https://finviz.com/",
}


def now_utc_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def parse_pct_or_num(x) -> float | None:
    if pd.isna(x):
        return None
    s = str(x).strip().replace(",", "")
    if not s:
        return None
    if s.endswith("%"):
        s = s[:-1]
    try:
        return float(s)
    except Exception:
        return None


def clean_ticker(t: str) -> str:
    return str(t).strip().upper()


@dataclass
class GroupRow:
    kind: str
    rank: int
    name: str
    change: float | None
    stocks: int | None
    url: str


class FinvizSession:
    def __init__(self, delay_sec: float = 0.4, timeout: int = 30) -> None:
        self.delay_sec = delay_sec
        self.timeout = timeout
        self.s = requests.Session()
        self.s.headers.update(HEADERS)

    def get(self, url: str) -> str:
        resp = self.s.get(url, timeout=self.timeout)
        resp.raise_for_status()
        if self.delay_sec:
            time.sleep(self.delay_sec)
        return resp.text


def _table_to_rows(table) -> list[list[str]]:
    rows: list[list[str]] = []
    for tr in table.find_all("tr"):
        cells = tr.find_all(["th", "td"])
        if not cells:
            continue
        row = [c.get_text(" ", strip=True) for c in cells]
        if any(x.strip() for x in row):
            rows.append(row)
    return rows


def _rows_to_df_by_required_headers(rows: list[list[str]], required_headers: set[str]) -> pd.DataFrame | None:
    # Search the first few rows for a header row that contains all required headers.
    for i, row in enumerate(rows[:8]):
        headers = [str(x).strip() for x in row]
        header_set = set(headers)
        if not required_headers.issubset(header_set):
            continue

        n = len(headers)
        data: list[list[str]] = []
        for raw in rows[i + 1 :]:
            vals = [str(x).strip() for x in raw]
            if not any(vals):
                continue
            # Skip repeated header rows.
            if set(vals) == header_set or required_headers.issubset(set(vals)):
                continue
            if len(vals) < n:
                vals = vals + [""] * (n - len(vals))
            elif len(vals) > n:
                vals = vals[:n]
            data.append(vals)

        df = pd.DataFrame(data, columns=headers)
        # Drop obvious junk rows like repeated navigation rows.
        keep = pd.Series(True, index=df.index)
        for req in required_headers:
            keep &= df[req].astype(str).str.strip().ne("")
        df = df[keep].reset_index(drop=True)
        if not df.empty:
            return df
    return None


def parse_table_with_headers(html: str, required_headers: set[str]) -> pd.DataFrame:
    soup = BeautifulSoup(html, "html.parser")
    for table in soup.find_all("table"):
        rows = _table_to_rows(table)
        if not rows:
            continue
        df = _rows_to_df_by_required_headers(rows, required_headers)
        if df is not None:
            return df
    raise RuntimeError(f"Could not find HTML table with headers: {sorted(required_headers)}")


def extract_group_links(html: str) -> dict[str, str]:
    soup = BeautifulSoup(html, "html.parser")
    out: dict[str, str] = {}
    for a in soup.find_all("a", href=True):
        txt = a.get_text(" ", strip=True)
        href = a["href"]
        if not txt:
            continue
        if "screener.ashx?f=" not in href:
            continue
        full = urljoin(BASE, href)
        out.setdefault(txt, full)
    return out


def fetch_group_rankings(
    client: FinvizSession,
    kind: str,
    top_n: int,
    sort_by: str = "change",
    min_change: float | None = None,
) -> list[GroupRow]:
    if kind not in {"industry", "sector"}:
        raise ValueError(f"Unsupported group kind: {kind}")

    url = f"https://finviz.com/groups.ashx?g={kind}&v=152&o=-change"
    html = client.get(url)
    table = parse_table_with_headers(html, {"Name", "Change"})
    links = extract_group_links(html)

    table.columns = [str(c).strip() for c in table.columns]
    table = table[table["Name"].notna()].copy()
    table["Change_num"] = table["Change"].map(parse_pct_or_num)
    if "Stocks" in table.columns:
        table["Stocks_num"] = table["Stocks"].map(parse_pct_or_num)
    else:
        table["Stocks_num"] = None

    if sort_by == "change":
        table = table.sort_values(["Change_num", "Name"], ascending=[False, True], na_position="last")

    if min_change is not None:
        table = table[table["Change_num"].fillna(-10**9) >= float(min_change)]

    rows: list[GroupRow] = []
    for _, r in table.iterrows():
        name = str(r["Name"]).strip()
        link = links.get(name)
        if not link:
            continue
        rows.append(
            GroupRow(
                kind=kind,
                rank=len(rows) + 1,
                name=name,
                change=r["Change_num"],
                stocks=None if pd.isna(r["Stocks_num"]) else int(float(r["Stocks_num"])),
                url=link,
            )
        )
        if len(rows) >= top_n:
            break
    return rows


def extract_pagination_links(html: str) -> list[str]:
    soup = BeautifulSoup(html, "html.parser")
    urls: list[str] = []
    seen: set[str] = set()
    for a in soup.find_all("a", href=True):
        href = a["href"]
        if "screener.ashx?f=" not in href:
            continue
        if "r=" not in href:
            continue
        full = urljoin(BASE, href)
        if full not in seen:
            seen.add(full)
            urls.append(full)
    return urls


def fetch_group_constituents(
    client: FinvizSession,
    group: GroupRow,
    max_per_group: int | None = None,
    max_pages: int = 20,
) -> pd.DataFrame:
    pages: list[str] = [group.url]
    seen_pages: set[str] = set()
    frames: list[pd.DataFrame] = []

    required = {"Ticker", "Company", "Sector", "Industry", "Price", "Change"}

    while pages:
        url = pages.pop(0)
        if url in seen_pages:
            continue
        seen_pages.add(url)
        if len(seen_pages) > max_pages:
            break

        html = client.get(url)
        df = parse_table_with_headers(html, required)
        df.columns = [str(c).strip() for c in df.columns]
        use = df[["Ticker", "Company", "Sector", "Industry", "Price", "Change"]].copy()
        use["Ticker"] = use["Ticker"].map(clean_ticker)
        use = use[use["Ticker"].str.len() > 0]
        use = use[~use["Ticker"].str.contains(r"[^A-Z0-9.=\-^]", regex=True)]
        use["group_kind"] = group.kind
        use["group_name"] = group.name
        use["group_rank"] = group.rank
        frames.append(use)

        for next_url in extract_pagination_links(html):
            if next_url not in seen_pages and next_url not in pages:
                pages.append(next_url)

        if max_per_group is not None and sum(len(x) for x in frames) >= max_per_group:
            break

    if not frames:
        return pd.DataFrame(columns=["Ticker", "Company", "Sector", "Industry", "Price", "Change", "group_kind", "group_name", "group_rank"])

    out = pd.concat(frames, ignore_index=True)
    out = out.drop_duplicates(subset=["Ticker"], keep="first")
    if max_per_group is not None:
        out = out.head(max_per_group).copy()
    return out


def read_existing_tickers(paths: Iterable[Path]) -> set[str]:
    out: set[str] = set()
    for p in paths:
        if not p.exists():
            continue
        for raw in p.read_text(encoding="utf-8", errors="ignore").splitlines():
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            tok = re.split(r"[\s,]+", line)[0].strip().upper()
            if tok:
                out.add(tok)
    return out


def write_txt(
    path: Path,
    tickers: list[str],
    groups: list[GroupRow],
    existing_count: int | None,
    append_mode: bool = False,
) -> None:
    lines: list[str] = []
    lines.append(f"# generated_at_utc={now_utc_iso()}")
    lines.append("# source=finviz_groups")
    lines.append(f"# groups={len(groups)}")
    lines.append(f"# tickers={len(tickers)}")
    if existing_count is not None:
        lines.append(f"# existing_universe_count={existing_count}")
    for g in groups:
        ch = "" if g.change is None else f"{g.change:.2f}%"
        stocks = "" if g.stocks is None else str(g.stocks)
        lines.append(f"# {g.kind}:{g.rank}:{g.name} | change={ch} | stocks={stocks} | url={g.url}")
    body = "\n".join(lines + [""] + tickers) + "\n"

    if append_mode and path.exists():
        prev = path.read_text(encoding="utf-8", errors="ignore")
        if prev and not prev.endswith("\n"):
            prev += "\n"
        path.write_text(prev + body, encoding="utf-8")
    else:
        path.write_text(body, encoding="utf-8")


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Scrape top Finviz industries/sectors and write a txt of constituent tickers."
    )
    ap.add_argument("--top-industries", type=int, default=5, help="number of top industries to include (default: 5)")
    ap.add_argument("--top-sectors", type=int, default=0, help="number of top sectors to include (default: 0)")
    ap.add_argument("--min-change", type=float, default=None, help="drop groups below this current-day %% change")
    ap.add_argument("--max-per-group", type=int, default=20, help="cap number of tickers per group (default: 20)")
    ap.add_argument("--max-pages", type=int, default=20, help="max screener pages to follow per group (default: 20)")
    ap.add_argument("--delay", type=float, default=0.4, help="delay between HTTP requests in seconds (default: 0.4)")
    ap.add_argument("--out", type=str, default="finviz_top_groups_auto_today.txt", help="output txt path")
    ap.add_argument("--groups-csv", type=str, default="finviz_top_groups_today_groups.csv", help="save selected groups here")
    ap.add_argument("--members-csv", type=str, default="finviz_top_groups_today_members.csv", help="save constituent rows here")
    ap.add_argument("--exclude-existing", nargs="*", default=[], help="txt files whose tickers should be excluded from output")
    ap.add_argument("--append", action="store_true", help="append to output instead of overwrite")
    args = ap.parse_args()

    client = FinvizSession(delay_sec=args.delay)

    selected_groups: list[GroupRow] = []
    if args.top_industries > 0:
        selected_groups.extend(
            fetch_group_rankings(client, "industry", top_n=args.top_industries, min_change=args.min_change)
        )
    if args.top_sectors > 0:
        selected_groups.extend(
            fetch_group_rankings(client, "sector", top_n=args.top_sectors, min_change=args.min_change)
        )

    if not selected_groups:
        print("No groups selected. Try increasing --top-industries / --top-sectors or lowering --min-change.", file=sys.stderr)
        return 2

    group_df = pd.DataFrame(
        [
            {
                "kind": g.kind,
                "rank": g.rank,
                "name": g.name,
                "change_pct": g.change,
                "stocks": g.stocks,
                "url": g.url,
            }
            for g in selected_groups
        ]
    )

    member_frames: list[pd.DataFrame] = []
    for g in selected_groups:
        ch = "" if g.change is None else f"{g.change:.2f}%"
        print(f"[group] {g.kind} #{g.rank} {g.name}  change={ch}", file=sys.stderr)
        member_frames.append(fetch_group_constituents(client, g, max_per_group=args.max_per_group, max_pages=args.max_pages))

    members = pd.concat(member_frames, ignore_index=True) if member_frames else pd.DataFrame()
    if members.empty:
        print("No constituent tickers were scraped from Finviz.", file=sys.stderr)
        return 3

    members["Price_num"] = members["Price"].map(parse_pct_or_num)
    members["Change_num"] = members["Change"].map(parse_pct_or_num)
    members = members.sort_values(
        by=["group_rank", "Change_num", "Ticker"],
        ascending=[True, False, True],
        na_position="last",
    ).copy()

    existing_paths = [Path(x) for x in args.exclude_existing]
    existing_tickers = read_existing_tickers(existing_paths) if existing_paths else set()

    tickers: list[str] = []
    seen: set[str] = set()
    for t in members["Ticker"].astype(str):
        t = clean_ticker(t)
        if not t or t in seen:
            continue
        if t in existing_tickers:
            continue
        seen.add(t)
        tickers.append(t)

    out_path = Path(args.out)
    write_txt(out_path, tickers, selected_groups, len(existing_tickers) if existing_paths else None, append_mode=args.append)
    group_df.to_csv(args.groups_csv, index=False)
    members.to_csv(args.members_csv, index=False)

    print(f"Saved: {out_path} ({len(tickers)} tickers)")
    print(f"Saved: {args.groups_csv} ({len(group_df)} groups)")
    print(f"Saved: {args.members_csv} ({len(members)} rows before final dedupe)")
    if existing_paths:
        print(f"Excluded existing universe tickers from {len(existing_paths)} txt file(s): {len(existing_tickers)} unique tickers")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
