#!/usr/bin/env python3
"""地震履歴の基本観察プロット。

1. gutenberg_richter.png — マグニチュード別累積頻度（片対数）とb値
2. daily_counts.png      — 日別の地震回数（クラスタリングの観察）
"""

from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

HERE = Path(__file__).parent
OUT = HERE / "output"
BLUE, ORANGE = "#2a78d6", "#eb6834"
INK, INK2, MUTED = "#0b0b0b", "#52514e", "#898781"
GRID, BASE, SURFACE = "#e1e0d9", "#c3c2b7", "#fcfcfb"


def style(ax):
    ax.set_facecolor(SURFACE)
    ax.grid(color=GRID, lw=0.7)
    for s in ("top", "right"):
        ax.spines[s].set_visible(False)
    for s in ("left", "bottom"):
        ax.spines[s].set_color(BASE)
    ax.tick_params(colors=INK2, labelsize=8.5)


def plot_gr(df: pd.DataFrame) -> float:
    mags = df[df["mag"] >= 0]["mag"]
    grid = np.arange(1.0, mags.max() + 0.1, 0.1)
    counts = [(mags >= m).sum() for m in grid]
    fig, ax = plt.subplots(figsize=(8, 5))
    fig.patch.set_facecolor(SURFACE)
    ax.semilogy(grid, counts, "o", color=BLUE, ms=5, mec=SURFACE, mew=0.8)
    # b値: 完全性マグニチュード(頻度最大のM)以上でフィット
    mc = round(float(mags.round(1).mode().iloc[0]), 1)
    fit_m = grid[(grid >= mc) & (np.array(counts) >= 3)]
    fit_c = np.log10([c for m, c in zip(grid, counts) if m >= mc and c >= 3])
    b, a = np.polyfit(fit_m, fit_c, 1)
    ax.semilogy(fit_m, 10 ** (a + b * fit_m), color=ORANGE, lw=2,
                label=f"fit: b = {-b:.2f} (M >= {mc})")
    ax.set_xlabel("Magnitude M", color=INK2)
    ax.set_ylabel("N (events >= M)", color=INK2)
    ax.set_title(f"Gutenberg-Richter, Japan, {df['time'].min().date()} - {df['time'].max().date()}",
                 color=INK, fontsize=11)
    style(ax)
    ax.legend(frameon=False, labelcolor=INK2)
    fig.tight_layout()
    fig.savefig(OUT / "gutenberg_richter.png", dpi=130, facecolor=SURFACE)
    print(f"b値 = {-b:.2f}（教科書値は約1.0）/ 完全性M >= {mc}")
    return -b


def plot_daily(df: pd.DataFrame) -> None:
    daily = df.set_index("time").resample("D").size()
    big = df[df["mag"] >= 5.0]
    fig, ax = plt.subplots(figsize=(10, 4.5))
    fig.patch.set_facecolor(SURFACE)
    ax.bar(daily.index, daily.values, color=BLUE, width=0.7)
    for t in big["time"]:
        ax.axvline(t, color=ORANGE, lw=1.2, alpha=0.7)
    ax.set_title("Earthquakes per day (orange line = M5+)", color=INK, fontsize=11)
    ax.set_ylabel("events/day", color=INK2, fontsize=9)
    style(ax)
    fig.tight_layout()
    fig.savefig(OUT / "daily_counts.png", dpi=130, facecolor=SURFACE)


if __name__ == "__main__":
    OUT.mkdir(exist_ok=True)
    df = pd.read_csv(HERE / "data" / "quakes.csv", parse_dates=["time"])
    days = (df["time"].max() - df["time"].min()).days or 1
    print(f"{len(df)}件 / {days}日 = {len(df)/days:.1f}回/日")
    print("最多震源トップ5:", df["name"].value_counts().head(5).to_dict())
    print(f"M5以上: {(df['mag']>=5).sum()}件 / 最大 M{df['mag'].max()} ({df.loc[df['mag'].idxmax(),'name']})")
    plot_gr(df)
    plot_daily(df)
    print("wrote", OUT)
