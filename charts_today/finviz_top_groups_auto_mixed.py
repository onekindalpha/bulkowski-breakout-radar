#!/usr/bin/env python3
from __future__ import annotations

import argparse
import re
import sys
import time
from dataclasses import dataclass, asdict
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
    url: str
    score: float
    change: float | None
    perf_week: float | None
    perf_month: float | None
    rel_volume: float | None
    stocks: int | None = None
    rank_change: float | None = None
    rank_perf_week: float | None = None
    rank_perf_month: float | None = None
    rank_rel_volume: float | None = None


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
    for i, row in enumerate(rows[:10]):
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
            if set(vals) == header_set or required_headers.issubset(set(vals)):
                continue
            if len(vals) < n:
                vals = vals + [""] * (n - len(vals))
            elif len(vals) > n:
                vals = vals[:n]
            data.append(vals)

        df = pd.DataFrame(data, columns=headers)
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


def percentile_score_desc(series: pd.Series) -> pd.Series:
    s = pd.to_numeric(series, errors="coerce")
    ranks = s.rank(method="average", ascending=False, pct=True)
    return 1.0 - ranks + (1.0 / len(s.dropna()) if len(s.dropna()) else 0.0)


def fetch_group_rankings_mixed(
    client: FinvizSession,
    kind: str,
    top_n: int,
    wt_change: float,
    wt_perf_week: float,
    wt_perf_month: float,
    wt_rel_volume: float = 0.0,
    min_change: float | None = None,
    min_perf_week: float | None = None,
    min_perf_month: float | None = None,
) -> list[GroupRow]:
    if kind not in {"industry", "sector"}:
        raise ValueError(f"Unsupported group kind: {kind}")

    # Performance view exposes Perf Week / Perf Month / Rel Volume / Change together.
    url = f"https://finviz.com/groups.ashx?g={kind}&v=142&st=d1"
    html = client.get(url)
    required = {"Name", "Perf Week", "Perf Month", "Rel Volume", "Change"}
    table = parse_table_with_headers(html, required)
    links = extract_group_links(html)

    table.columns = [str(c).strip() for c in table.columns]
    table = table[table["Name"].notna()].copy()
    table["change_num"] = table["Change"].map(parse_pct_or_num)
    table["perf_week_num"] = table["Perf Week"].map(parse_pct_or_num)
    table["perf_month_num"] = table["Perf Month"].map(parse_pct_or_num)
    table["rel_volume_num"] = table["Rel Volume"].map(parse_pct_or_num)
    if "Stocks" in table.columns:
        table["stocks_num"] = table["Stocks"].map(parse_pct_or_num)
    else:
        table["stocks_num"] = None

    if min_change is not None:
        table = table[table["change_num"].fillna(-10**9) >= float(min_change)]
    if min_perf_week is not None:
        table = table[table["perf_week_num"].fillna(-10**9) >= float(min_perf_week)]
    if min_perf_month is not None:
        table = table[table["perf_month_num"].fillna(-10**9) >= float(min_perf_month)]

    if table.empty:
        return []

    total_w = wt_change + wt_perf_week + wt_perf_month + wt_rel_volume
    if total_w <= 0:
        raise ValueError("At least one weight must be > 0")

    table["rank_change"] = percentile_score_desc(table["change_num"]).fillna(0.0)
    table["rank_perf_week"] = percentile_score_desc(table["perf_week_num"]).fillna(0.0)
    table["rank_perf_month"] = percentile_score_desc(table["perf_month_num"]).fillna(0.0)
    table["rank_rel_volume"] = percentile_score_desc(table["rel_volume_num"]).fillna(0.0)

    table["leader_score"] = (
        wt_change * table["rank_change"]
        + wt_perf_week * table["rank_perf_week"]
        + wt_perf_month * table["rank_perf_month"]
        + wt_rel_volume * table["rank_rel_volume"]
    ) / total_w

    table = table.sort_values(
        ["leader_score", "change_num", "perf_week_num", "perf_month_num", "Name"],
        ascending=[False, False, False, False, True],
        na_position="last",
    )

    out: list[GroupRow] = []
    for _, r in table.iterrows():
        name = str(r["Name"]).strip()
        link = links.get(name)
        if not link:
            continue
        out.append(
            GroupRow(
                kind=kind,
                rank=len(out) + 1,
                name=name,
                url=link,
                score=float(r["leader_score"]),
                change=r["change_num"],
                perf_week=r["perf_week_num"],
                perf_month=r["perf_month_num"],
                rel_volume=r["rel_volume_num"],
                stocks=None if pd.isna(r["stocks_num"]) else int(float(r["stocks_num"])),
                rank_change=float(r["rank_change"]),
                rank_perf_week=float(r["rank_perf_week"]),
                rank_perf_month=float(r["rank_perf_month"]),
                rank_rel_volume=float(r["rank_rel_volume"]),
            )
        )
        if len(out) >= top_n:
            break
    return out


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
        use["group_score"] = group.score
        frames.append(use)

        for next_url in extract_pagination_links(html):
            if next_url not in seen_pages and next_url not in pages:
                pages.append(next_url)

        if max_per_group is not None and sum(len(x) for x in frames) >= max_per_group:
            break

    if not frames:
        return pd.DataFrame(columns=[
            "Ticker", "Company", "Sector", "Industry", "Price", "Change",
            "group_kind", "group_name", "group_rank", "group_score"
        ])

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
    lines.append("# source=finviz_groups_mixed")
    lines.append(f"# groups={len(groups)}")
    lines.append(f"# tickers={len(tickers)}")
    if existing_count is not None:
        lines.append(f"# existing_universe_count={existing_count}")
    for g in groups:
        lines.append(
            f"# {g.kind}:{g.rank}:{g.name} | score={g.score:.4f} | "
            f"change={'' if g.change is None else f'{g.change:.2f}%'} | "
            f"perf_week={'' if g.perf_week is None else f'{g.perf_week:.2f}%'} | "
            f"perf_month={'' if g.perf_month is None else f'{g.perf_month:.2f}%'} | "
            f"rel_volume={'' if g.rel_volume is None else f'{g.rel_volume:.2f}'} | url={g.url}"
        )
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
        description="Scrape top Finviz industries/sectors using mixed leadership score and write constituent tickers to txt."
    )
    ap.add_argument("--top-industries", type=int, default=5, help="number of top industries to include (default: 5)")
    ap.add_argument("--top-sectors", type=int, default=0, help="number of top sectors to include (default: 0)")
    ap.add_argument("--wt-change", type=float, default=0.5, help="weight for current Change rank (default: 0.5)")
    ap.add_argument("--wt-perf-week", type=float, default=0.3, help="weight for Perf Week rank (default: 0.3)")
    ap.add_argument("--wt-perf-month", type=float, default=0.2, help="weight for Perf Month rank (default: 0.2)")
    ap.add_argument("--wt-rel-volume", type=float, default=0.0, help="optional weight for Rel Volume rank (default: 0.0)")
    ap.add_argument("--min-change", type=float, default=None, help="drop groups below this current-day %% change")
    ap.add_argument("--min-perf-week", type=float, default=None, help="drop groups below this Perf Week %%")
    ap.add_argument("--min-perf-month", type=float, default=None, help="drop groups below this Perf Month %%")
    ap.add_argument("--max-per-group", type=int, default=20, help="cap number of tickers per group (default: 20)")
    ap.add_argument("--max-pages", type=int, default=20, help="max screener pages to follow per group (default: 20)")
    ap.add_argument("--delay", type=float, default=0.4, help="delay between HTTP requests in seconds (default: 0.4)")
    ap.add_argument("--out", type=str, default="finviz_top_groups_auto.txt", help="output txt path")
    ap.add_argument("--groups-csv", type=str, default="finviz_top_groups_groups.csv", help="save selected groups here")
    ap.add_argument("--members-csv", type=str, default="finviz_top_groups_members.csv", help="save constituent rows here")
    ap.add_argument("--exclude-existing", nargs="*", default=[], help="txt files whose tickers should be excluded from output")
    ap.add_argument("--append", action="store_true", help="append to output instead of overwrite")
    args = ap.parse_args()

    client = FinvizSession(delay_sec=args.delay)

    selected_groups: list[GroupRow] = []
    if args.top_industries > 0:
        selected_groups.extend(
            fetch_group_rankings_mixed(
                client,
                "industry",
                top_n=args.top_industries,
                wt_change=args.wt_change,
                wt_perf_week=args.wt_perf_week,
                wt_perf_month=args.wt_perf_month,
                wt_rel_volume=args.wt_rel_volume,
                min_change=args.min_change,
                min_perf_week=args.min_perf_week,
                min_perf_month=args.min_perf_month,
            )
        )
    if args.top_sectors > 0:
        selected_groups.extend(
            fetch_group_rankings_mixed(
                client,
                "sector",
                top_n=args.top_sectors,
                wt_change=args.wt_change,
                wt_perf_week=args.wt_perf_week,
                wt_perf_month=args.wt_perf_month,
                wt_rel_volume=args.wt_rel_volume,
                min_change=args.min_change,
                min_perf_week=args.min_perf_week,
                min_perf_month=args.min_perf_month,
            )
        )

    if not selected_groups:
        print("No groups selected. Try lowering filters or increasing top counts.", file=sys.stderr)
        return 2

    group_df = pd.DataFrame([asdict(g) for g in selected_groups])

    member_frames: list[pd.DataFrame] = []
    for g in selected_groups:
        print(
            f"[group] {g.kind} #{g.rank} {g.name}  score={g.score:.4f}  "
            f"chg={'' if g.change is None else f'{g.change:.2f}%'}  "
            f"w={'' if g.perf_week is None else f'{g.perf_week:.2f}%'}  "
            f"m={'' if g.perf_month is None else f'{g.perf_month:.2f}%'}",
            file=sys.stderr,
        )
        member_frames.append(fetch_group_constituents(client, g, max_per_group=args.max_per_group, max_pages=args.max_pages))

    members = pd.concat(member_frames, ignore_index=True) if member_frames else pd.DataFrame()
    if members.empty:
        print("No constituent tickers were scraped from Finviz.", file=sys.stderr)
        return 3

    members["Price_num"] = members["Price"].map(parse_pct_or_num)
    members["Change_num"] = members["Change"].map(parse_pct_or_num)
    members = members.sort_values(
        by=["group_score", "group_rank", "Change_num", "Ticker"],
        ascending=[False, True, False, True],
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
