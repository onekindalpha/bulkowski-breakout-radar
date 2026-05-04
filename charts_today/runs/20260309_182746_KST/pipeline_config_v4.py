#!/usr/bin/env python3
"""
pipeline_config.py (v4, stable)

- Parses tickers from your txt files
- Merges duplicates safely (finviz_manual + typo finviz_manul)
- Prints each group ONCE (no duplicate finviz_manual line)
- Provides helpers used across scripts:
    load_default_groups, union_ordered, print_group_counts, now_kst_str, write_header_lines
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from datetime import datetime
from zoneinfo import ZoneInfo

TICKER_RE = re.compile(r"^[A-Z0-9\^\=\.\-\/]{1,20}$")

DEFAULT_GROUP_FILES: list[tuple[str, str]] = [
    ("macro_watch_yahoo", "macro_watch_yahoo.txt"),
    ("tickers_core", "tickers_core.txt"),
    ("tickers_leverage2x", "tickers_leverage2x.txt"),
    ("finviz_manual", "finviz_manual.txt"),
    # common typo fallback:
    ("finviz_manual", "finviz_manul.txt"),
]

KST = ZoneInfo("Asia/Seoul")


@dataclass
class GroupTickers:
    group: str
    path: str
    tickers: list[str]


def now_kst_str(fmt: str = "%Y-%m-%d %H:%M:%S KST") -> str:
    return datetime.now(KST).strftime(fmt)


def merge_unique_ordered(existing: list[str], incoming: list[str]) -> list[str]:
    """Merge two lists preserving order, removing duplicates."""
    out = list(existing)
    seen = set(existing)
    for t in incoming:
        if t not in seen:
            seen.add(t)
            out.append(t)
    return out


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
    """
    Loads groups in DEFAULT_GROUP_FILES order.
    If both finviz_manual.txt and finviz_manul.txt exist, both are read and merged under the same group name.
    Returns each group ONCE (no duplicate printing).
    """
    merged: dict[str, list[str]] = {}
    paths_used: dict[str, list[str]] = {}

    for group, path in DEFAULT_GROUP_FILES:
        ticks = read_tickers_from_file(path)
        if not ticks:
            continue
        if group not in merged:
            merged[group] = []
            paths_used[group] = []
        merged[group] = merge_unique_ordered(merged[group], ticks)
        paths_used[group].append(path)

    groups: list[GroupTickers] = []
    seen_groups: set[str] = set()
    for group, _ in DEFAULT_GROUP_FILES:
        if group in seen_groups:
            continue
        if group not in merged:
            continue
        seen_groups.add(group)
        rep_path = ",".join(paths_used.get(group, []))
        groups.append(GroupTickers(group=group, path=rep_path, tickers=merged[group]))

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


def write_header_lines(path: str, header_lines: list[str], csv_body: str) -> None:
    """
    Writes:
      # key,value
      # ...
      <csv body text>
    """
    p = Path(path)
    with p.open("w", encoding="utf-8") as f:
        for line in header_lines:
            if not line.startswith("#"):
                line = "# " + line
            f.write(line.rstrip() + "\n")
        f.write(csv_body)


def print_group_counts(groups: list[GroupTickers], title: str = "INPUT TXT COUNTS (ALL GROUPS)") -> None:
    union = union_ordered(groups)
    print(f"\n=== {title} ===")
    for g in groups:
        print(f"{g.group:20s} {len(g.tickers):4d}  ({g.path})")
    print(f"{'UNION_TOTAL':20s} {len(union):4d}\n")
