#!/usr/bin/env python3
"""
pipeline_config.py

Single source of truth for your pipeline file names + ticker parsing + count reporting.

Default ticker sources (all should be considered in all steps):
  - macro_watch_yahoo.txt
  - tickers_core.txt
  - tickers_leverage2x.txt
  - finviz_manual.txt   (also accepts finviz_manul.txt as a common typo)
"""

from __future__ import annotations
import re
from dataclasses import dataclass
from pathlib import Path
from datetime import datetime
from zoneinfo import ZoneInfo

TICKER_RE = re.compile(r"^[A-Z0-9\^\=\.\-\/]{1,20}$")

DEFAULT_GROUP_FILES = [
    ("macro_watch_yahoo", "macro_watch_yahoo.txt"),
    ("tickers_core", "tickers_core.txt"),
    ("tickers_leverage2x", "tickers_leverage2x.txt"),
    ("finviz_manual", "finviz_manual.txt"),
    # common typo fallback:
    ("finviz_manual", "finviz_manul.txt"),
]


@dataclass
class GroupTickers:
    group: str
    path: str
    tickers: list[str]


def now_kst_str(fmt: str = "%Y-%m-%d %H:%M:%S KST") -> str:
    return datetime.now(ZoneInfo("Asia/Seoul")).strftime(fmt)


def read_tickers_from_file(path: str) -> list[str]:
    """
    Line-based parsing:
      - Skip full-line comments starting with '#'
      - Remove inline comments after '#'
      - Split on whitespace / comma / semicolon
      - Keep tokens matching TICKER_RE
    """
    p = Path(path)
    if not p.exists():
        return []
    out: list[str] = []
    seen: set[str] = set()
    for line in p.read_text(encoding="utf-8", errors="ignore").splitlines():
        raw = line.strip()
        if not raw:
            continue
        if raw.startswith("#"):
            continue
        if "#" in raw:
            raw = raw.split("#", 1)[0].strip()
        if not raw:
            continue
        for tok in re.split(r"[\s,;]+", raw):
            t = tok.strip().upper()
            if not t:
                continue
            if not TICKER_RE.match(t):
                continue
            if t not in seen:
                seen.add(t)
                out.append(t)
    return out


def load_default_groups() -> list[GroupTickers]:
    """Load default ticker groups from local txt files.

    Note on finviz_manual:
      We accept both 'finviz_manual.txt' and the common typo 'finviz_manul.txt'.
      If both exist, we merge them into a *single* group named 'finviz_manual'
      to avoid duplicate printing while still keeping the union correct.
    """
    file_specs: list[tuple[str, str]] = [
        ("macro_watch_yahoo", "macro_watch_yahoo.txt"),
        ("tickers_core", "tickers_core.txt"),
        ("tickers_leverage2x", "tickers_leverage2x.txt"),
        ("finviz_manual", "finviz_manual.txt"),
        ("finviz_manual", "finviz_manul.txt"),  # typo support
    ]

    merged: dict[str, list[str]] = {}
    paths_by_group: dict[str, list[str]] = {}

    for group, path in file_specs:
        p = Path(path)
        if not p.exists():
            continue
        tickers = read_ticker_file(path)
        if group not in merged:
            merged[group] = []
            paths_by_group[group] = []
        merged[group] = merge_unique_ordered(merged[group], tickers)
        paths_by_group[group].append(path)

    groups: list[GroupTickers] = []
    for group, tickers in merged.items():
        # Keep a readable path string for printing
        paths = paths_by_group.get(group, [])
        path_repr = paths[0] if len(paths) == 1 else (paths[0] + " (+" + ",".join(paths[1:]) + ")")
        groups.append(GroupTickers(group=group, path=path_repr, tickers=tickers))

    return groups


def union_ordered(groups: list[GroupTickers]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for g in groups:
        for t in g.tickers:
            if t not in seen:
                seen.add(t)
                out.append(t)
    return out


def counts_dict(groups: list[GroupTickers]) -> dict[str, int]:
    return {g.group: len(g.tickers) for g in groups}


def write_header_lines(path: str, header_lines: list[str], csv_body: str) -> None:
    """
    Writes:
      # key,value
      # ...
      <csv>
    """
    p = Path(path)
    with p.open("w", encoding="utf-8") as f:
        for line in header_lines:
            if not line.startswith("#"):
                line = "# " + line
            f.write(line.rstrip() + "\n")
        f.write(csv_body)


def print_group_counts(groups: list[GroupTickers], title: str = "TICKER COUNTS") -> None:
    union = union_ordered(groups)
    print(f"\n=== {title} ===")
    for g in groups:
        print(f"{g.group:20s} {len(g.tickers):4d}  ({g.path})")
    print(f"{'UNION_TOTAL':20s} {len(union):4d}\n")
