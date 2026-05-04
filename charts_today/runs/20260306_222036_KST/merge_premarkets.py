# merge_premarkets.py
from pathlib import Path
import pandas as pd

def read_prices(path: str):
    p = Path(path)
    if not p.exists():
        return {}
    try:
        df = pd.read_csv(p)
    except Exception:
        return {}
    if "ticker" not in df.columns or "premarket" not in df.columns:
        return {}
    df = df.dropna()
    out = {}
    for _, r in df.iterrows():
        t = str(r["ticker"]).strip().upper()
        try:
            out[t] = float(r["premarket"])
        except Exception:
            continue
    return out

def main():
    auto = read_prices("premarket_auto.csv")
    manual = read_prices("premarket_manual.csv")

    merged = dict(auto)
    overridden = []
    for t, p in manual.items():
        if t in merged and merged[t] != p:
            overridden.append((t, merged[t], p))
        merged[t] = p

    out = pd.DataFrame([{"ticker": t, "premarket": round(p, 2)} for t, p in sorted(merged.items())])
    out.to_csv("premarket.csv", index=False)

    print(f"Saved premarket.csv ({len(out)} tickers).")
    if overridden:
        print("\nOverridden by manual (ticker: auto -> manual):")
        for t, a, m in overridden[:20]:
            print(f"  {t}: {a} -> {m}")
        if len(overridden) > 20:
            print(f"  ... +{len(overridden)-20} more")

if __name__ == "__main__":
    main()