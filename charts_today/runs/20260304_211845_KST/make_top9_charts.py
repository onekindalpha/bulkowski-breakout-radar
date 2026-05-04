# pip install pandas matplotlib python-dateutil
import os
import pandas as pd
import matplotlib.pyplot as plt
from matplotlib.backends.backend_pdf import PdfPages
from dateutil.relativedelta import relativedelta
from datetime import datetime

TICKERS = ["psx.us","mpc.us","slb.us","cve.us","fcx.us","scco.us","ccj.us","aem.us","rio.us"]
OUTDIR = "top9_charts"
os.makedirs(OUTDIR, exist_ok=True)

def load_stooq(symbol: str) -> pd.DataFrame:
    url = f"https://stooq.com/q/d/l/?s={symbol}&i=d"
    df = pd.read_csv(url)
    # columns: Date, Open, High, Low, Close, Volume (Stooq 표준)
    df["Date"] = pd.to_datetime(df["Date"])
    df = df.sort_values("Date").set_index("Date")
    return df

def to_weekly_ohlc(df: pd.DataFrame) -> pd.DataFrame:
    o = df["Open"].resample("W-FRI").first()
    h = df["High"].resample("W-FRI").max()
    l = df["Low"].resample("W-FRI").min()
    c = df["Close"].resample("W-FRI").last()
    out = pd.concat([o,h,l,c], axis=1).dropna()
    out.columns = ["Open","High","Low","Close"]
    return out

def plot_ohlc_like(df_ohlc: pd.DataFrame, title: str, path: str):
    # 간단 “캔들 유사” (High-Low + Open/Close tick). 색 지정 안함(기본값).
    fig = plt.figure(figsize=(12,6))
    ax = fig.add_subplot(111)
    x = range(len(df_ohlc))
    for i,(o,h,l,c) in enumerate(df_ohlc[["Open","High","Low","Close"]].itertuples(index=False, name=None)):
        ax.vlines(i, l, h, linewidth=1)
        ax.hlines(o, i-0.2, i, linewidth=2)
        ax.hlines(c, i, i+0.2, linewidth=2)
    ax.set_title(title)
    ax.set_xlim(-1, len(df_ohlc))
    # x축 라벨 최소화
    ticks = list(range(0, len(df_ohlc), max(1, len(df_ohlc)//10)))
    ax.set_xticks(ticks)
    ax.set_xticklabels([df_ohlc.index[t].strftime("%Y-%m") for t in ticks], rotation=0)
    ax.grid(True, linewidth=0.3)
    fig.tight_layout()
    fig.savefig(path, dpi=160)
    plt.close(fig)

def plot_close(df: pd.DataFrame, title: str, path: str):
    fig = plt.figure(figsize=(12,6))
    ax = fig.add_subplot(111)
    ax.plot(df.index, df["Close"])
    ax.set_title(title)
    ax.grid(True, linewidth=0.3)
    fig.tight_layout()
    fig.savefig(path, dpi=160)
    plt.close(fig)

now = datetime.utcnow()
pdf_path = os.path.join(OUTDIR, "TOP9_weekly5y_daily1y.pdf")

with PdfPages(pdf_path) as pdf:
    for sym in TICKERS:
        df = load_stooq(sym)

        # windows
        d_1y = df[df.index >= (now - relativedelta(years=1))]
        w_all = to_weekly_ohlc(df)
        w_5y = w_all[w_all.index >= (now - relativedelta(years=5))]

        base = sym.replace(".us","").upper()

        w_png = os.path.join(OUTDIR, f"{base}_W_5Y.png")
        d_png = os.path.join(OUTDIR, f"{base}_D_1Y.png")

        plot_ohlc_like(w_5y, f"{base} Weekly (Last 5Y)", w_png)
        plot_close(d_1y, f"{base} Daily Close (Last 1Y)", d_png)

        # PDF에도 같은 순서로 추가
        for p in [w_png, d_png]:
            img = plt.imread(p)
            fig = plt.figure(figsize=(12,6))
            ax = fig.add_subplot(111)
            ax.imshow(img)
            ax.axis("off")
            fig.tight_layout()
            pdf.savefig(fig)
            plt.close(fig)

print("DONE:", pdf_path)