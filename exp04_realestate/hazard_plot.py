#!/usr/bin/env python3
"""洪水ハザード×価格の観察プロット（熊本市）。

1枚目: 浸水想定区域の外/内(<3m)/内(3m+)別の単価中央値（種別3系列）
2枚目: 区内で揃えた 区域内−区域外 の差（宅地(土地と建物)）
"""

from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

HERE = Path(__file__).parent
OUT = HERE / "output"
BLUE, ORANGE, GREEN = "#2a78d6", "#eb6834", "#1baf7a"
INK, INK2, MUTED = "#0b0b0b", "#52514e", "#898781"
GRID, BASE, SURFACE = "#e1e0d9", "#c3c2b7", "#fcfcfb"

TYPES = [("宅地(土地と建物)", "Land w/ building", BLUE),
         ("中古マンション等", "Pre-owned condo", ORANGE),
         ("宅地(土地)", "Land only", GREEN)]
BUCKETS = [("Outside zone", lambda r: r == 0),
           ("In zone <3m", lambda r: (r > 0) & (r < 3)),
           ("In zone 3m+", lambda r: r >= 3)]


def style(ax):
    ax.set_facecolor(SURFACE)
    ax.grid(color=GRID, lw=0.7, axis="x")
    ax.grid(color=GRID, lw=0.7, axis="y")
    for s in ("top", "right"):
        ax.spines[s].set_visible(False)
    for s in ("left", "bottom"):
        ax.spines[s].set_color(BASE)
    ax.tick_params(colors=INK2, labelsize=8.5)


def plot_buckets(df: pd.DataFrame) -> None:
    fig, ax = plt.subplots(figsize=(9, 5))
    fig.patch.set_facecolor(SURFACE)
    xs = np.arange(len(BUCKETS))
    w = 0.26
    for i, (jtype, label, color) in enumerate(TYPES):
        sub = df[df["Type"] == jtype].dropna(subset=["unit_man_m2"])
        med = [sub[cond(sub["flood_rank"])]["unit_man_m2"].median()
               for _, cond in BUCKETS]
        ax.bar(xs + (i - 1) * (w + 0.02), med, width=w, color=color, label=label)
    ax.set_xticks(xs, [b for b, _ in BUCKETS])
    ax.set_ylabel("median price (10k JPY per m2)", color=INK2, fontsize=9)
    ax.set_title("Kumamoto City: price vs flood-hazard zone (2020-2026Q1)",
                 color=INK, fontsize=11, loc="left")
    ax.legend(frameon=False, labelcolor=INK2, fontsize=8.5)
    style(ax)
    fig.tight_layout()
    fig.savefig(OUT / "hazard_price.png", dpi=130, facecolor=SURFACE)


def plot_wards(df: pd.DataFrame) -> None:
    sub = df[df["Type"] == "宅地(土地と建物)"].dropna(subset=["unit_man_m2"])
    rows = []
    for ward, g in sub.groupby("Municipality"):
        o = g[g["flood_rank"] == 0]["unit_man_m2"]
        i = g[g["flood_rank"] > 0]["unit_man_m2"]
        if len(o) >= 30 and len(i) >= 30:
            rows.append((ward.replace("熊本市", ""), i.median() - o.median()))
    rows.sort(key=lambda r: r[1])
    names = {"中央区": "Chuo", "東区": "Higashi", "西区": "Nishi",
             "南区": "Minami", "北区": "Kita"}
    fig, ax = plt.subplots(figsize=(8, 3.6))
    fig.patch.set_facecolor(SURFACE)
    ax.barh([names.get(w, w) for w, _ in rows], [d for _, d in rows],
            color=BLUE, height=0.55)
    ax.axvline(0, color=BASE, lw=1)
    ax.set_xlabel("in-zone minus out-of-zone median (10k JPY per m2)",
                  color=INK2, fontsize=9)
    ax.set_title("Same-ward comparison, land w/ building: no flood discount",
                 color=INK, fontsize=11, loc="left")
    style(ax)
    fig.tight_layout()
    fig.savefig(OUT / "hazard_ward_diff.png", dpi=130, facecolor=SURFACE)


if __name__ == "__main__":
    OUT.mkdir(exist_ok=True)
    df = pd.read_csv(HERE / "data" / "kumamoto_city_flood.csv", low_memory=False)
    plot_buckets(df)
    plot_wards(df)
    print("wrote", OUT)
