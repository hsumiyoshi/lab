#!/usr/bin/env python3
"""不動産取引価格の基本観察プロット。

1. quarterly_counts.png — 四半期別の取引件数（公表ラグで直近四半期は不完全）
2. unit_price.png       — 単価中央値の推移（宅地(土地と建物) / 中古マンション等）

東京=対照群、熊本=2026-07-28 M7.1のイベントスタディ対象（地震前ベースライン）。
"""

from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import pandas as pd

HERE = Path(__file__).parent
OUT = HERE / "output"
BLUE, ORANGE = "#2a78d6", "#eb6834"
INK, INK2, MUTED = "#0b0b0b", "#52514e", "#898781"
GRID, BASE, SURFACE = "#e1e0d9", "#c3c2b7", "#fcfcfb"

AREAS = {"tokyo": "Tokyo", "kumamoto": "Kumamoto"}
TYPES = {"宅地(土地と建物)": ("Land w/ building", BLUE),
         "中古マンション等": ("Pre-owned condo", ORANGE)}


def style(ax):
    ax.set_facecolor(SURFACE)
    ax.grid(color=GRID, lw=0.7)
    for s in ("top", "right"):
        ax.spines[s].set_visible(False)
    for s in ("left", "bottom"):
        ax.spines[s].set_color(BASE)
    ax.tick_params(colors=INK2, labelsize=8.5)


def load(name: str) -> pd.DataFrame:
    df = pd.read_csv(HERE / "data" / f"trades_{name}.csv", low_memory=False)
    df["q"] = pd.PeriodIndex(df["_year"].astype(str) + "Q" + df["_quarter"].astype(str), freq="Q")
    df["TradePrice"] = pd.to_numeric(df["TradePrice"], errors="coerce")
    df["Area"] = pd.to_numeric(df["Area"], errors="coerce")
    df["unit_man_m2"] = df["TradePrice"] / df["Area"] / 1e4  # 万円/m2
    return df


def plot_counts(dfs: dict[str, pd.DataFrame]) -> None:
    fig, axes = plt.subplots(len(dfs), 1, figsize=(10, 6), sharex=True)
    fig.patch.set_facecolor(SURFACE)
    for ax, (name, df) in zip(axes, dfs.items()):
        counts = df.groupby("q").size()
        ax.bar(counts.index.astype(str), counts.values, color=BLUE, width=0.62)
        ax.set_title(f"{AREAS[name]}: transactions per quarter", color=INK,
                     fontsize=10, loc="left")
        style(ax)
    axes[-1].tick_params(axis="x", rotation=60)
    fig.suptitle("Real-estate transactions (last quarters incomplete: publication lag)",
                 color=INK2, fontsize=9, y=0.99)
    fig.tight_layout()
    fig.savefig(OUT / "quarterly_counts.png", dpi=130, facecolor=SURFACE)


def plot_unit_price(dfs: dict[str, pd.DataFrame]) -> None:
    fig, axes = plt.subplots(len(dfs), 1, figsize=(10, 6.5), sharex=True)
    fig.patch.set_facecolor(SURFACE)
    for ax, (name, df) in zip(axes, dfs.items()):
        for jtype, (label, color) in TYPES.items():
            med = (df[df["Type"] == jtype].groupby("q")["unit_man_m2"]
                   .median().dropna())
            ax.plot(med.index.astype(str), med.values, color=color, lw=2,
                    marker="o", ms=4, mec=SURFACE, mew=0.8, label=label)
        ax.set_title(f"{AREAS[name]}: median price (10k JPY per m2)", color=INK,
                     fontsize=10, loc="left")
        ax.legend(frameon=False, labelcolor=INK2, fontsize=8.5)
        style(ax)
    axes[-1].tick_params(axis="x", rotation=60)
    fig.tight_layout()
    fig.savefig(OUT / "unit_price.png", dpi=130, facecolor=SURFACE)


if __name__ == "__main__":
    OUT.mkdir(exist_ok=True)
    dfs = {name: load(name) for name in AREAS}
    for name, df in dfs.items():
        last = df["q"].max()
        print(f"{AREAS[name]}: {len(df)}件 / {df['q'].min()}〜{last} "
              f"(直近四半期 {last} は {(df['q'] == last).sum()}件=不完全の可能性)")
        for jtype, (label, _) in TYPES.items():
            sub = df[df["Type"] == jtype]["unit_man_m2"].dropna()
            print(f"  {label}: n={len(sub)}, 単価中央値 {sub.median():.1f}万円/m2")
    plot_counts(dfs)
    plot_unit_price(dfs)
    print("wrote", OUT)
