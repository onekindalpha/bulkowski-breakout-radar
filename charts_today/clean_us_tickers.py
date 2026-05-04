#!/usr/bin/env python3
from pathlib import Path
import argparse
import re

BAD_TOKENS = {
    # comment / description words accidentally parsed as tickers
    "GLOBAL", "CLEAN", "ENERGY", "GRANITESHARES", "LONG",
    "CRUDE", "BRENT", "GASOLINE", "RBOB", "NASDAQ", "VIX", "SP", "S&P",
    "DIREXION", "DAILY", "SEMICONDUCTOR", "SEMICONDUCTORS",
    "PROSHARES", "ULTRA", "BENCHMARK", "MICROSECTORS",
    "ROBOTICS", "BIG", "DATA", "HEALTHCARE", "INACTIVE", "WILL",
    "AUTO-SKIP", "NO", "BIOTECH", "PHARMACEUTICAL", "MEDICAL",
    "CONSUMER", "DISCRETIONARY", "INDUSTRIALS", "AEROSPACE",
    "DEFENSE", "TRANSPORTATION", "ELECTRIC", "AUTONOMOUS", "VEHICLES",
    "SOURCE",
    # wrong company-name token, actual ticker is VSAT
    "VIASAT",
    # leverage descriptors
    "1X", "2X", "3X", "-3X",
}

ALIASES = {
    "CRUDE": "CL=F",
    "BRENT": "BZ=F",
    "GASOLINE": "RB=F",
    "RBOB": "RB=F",
    "VIX": "^VIX",
    "NASDAQ": "^IXIC",
}

VALID_RE = re.compile(r"^[A-Z][A-Z0-9.\-=^]{0,14}$|^\^[A-Z0-9]+$|^[A-Z]{1,4}=F$")

def clean_line(line: str) -> list[str]:
    # Remove inline comment first
    line = line.split("#", 1)[0].strip()
    if not line:
        return []

    out = []
    for raw in re.split(r"[\s,;]+", line):
        t = raw.strip().upper()
        if not t:
            continue
        t = t.lstrip("$")

        if t in ALIASES:
            t = ALIASES[t]

        if t in BAD_TOKENS:
            continue

        if not VALID_RE.match(t):
            continue

        # Ignore bare numbers and obvious descriptors
        if t.isdigit():
            continue

        out.append(t)
    return out

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--path", default="tickers.txt")
    args = ap.parse_args()

    p = Path(args.path)
    if not p.exists():
        raise SystemExit(f"missing: {p}")

    seen = set()
    cleaned = []
    removed = []

    for line in p.read_text(encoding="utf-8", errors="ignore").splitlines():
        before = re.split(r"[\s,;]+", line.split("#", 1)[0].strip()) if line.strip() else []
        after = clean_line(line)

        for tok in before:
            tok = tok.strip().upper()
            if tok and tok not in after and tok in BAD_TOKENS:
                removed.append(tok)

        for t in after:
            if t not in seen:
                seen.add(t)
                cleaned.append(t)

    p.write_text("\n".join(cleaned) + "\n", encoding="utf-8")
    print(f"cleaned {p}: {len(cleaned)} tickers")
    if removed:
        print(f"removed bad tokens: {len(set(removed))} unique -> {sorted(set(removed))[:40]}")

if __name__ == "__main__":
    main()
