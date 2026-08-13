#!/usr/bin/env python3
"""青果日別価格の基本観察プロット。

1. daily_price.png  — 品目別の日次卸売単価（東京都・総計）2x2小multiples
2. weekday_shape.png — 曜日別の平均価格プロファイル（品目平均=100に正規化）
   ※JEPXのweekshapeの青果版。休市日はデータ無しとして自然に欠落
"""

from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import pandas as pd

HERE = Path(__file__).parent
OUT = HERE / "output"

# dataviz検証済みパレット（固定順）
COLORS = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100"]
INK, INK2, MUTED = "#0b0b0b", "#52514e", "#898781"
GRID, BASE, SURFACE = "#e1e0d9", "#c3c2b7", "#fcfcfb"
DOW = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
EN = {"きゅうり": "Cucumber", "トマト": "Tomato", "キャベツ": "Cabbage", "レタス": "Lettuce"}


def load() -> pd.DataFrame:
    frames = [pd.read_csv(f, parse_dates=["date"]) for f in sorted((HERE / "data").glob("veg_*.csv"))]
    df = pd.concat(frames, ignore_index=True)
    return df[df["origin"] == "総計"].copy()


def style(ax):
    ax.set_facecolor(SURFACE)
    ax.grid(color=GRID, lw=0.7)
    for s in ("top", "right"):
        ax.spines[s].set_visible(False)
    for s in ("left", "bottom"):
        ax.spines[s].set_color(BASE)
    ax.tick_params(colors=INK2, labelsize=8)


def plot_daily(df: pd.DataFrame) -> None:
    items = sorted(df["item"].unique())
    fig, axes = plt.subplots(2, 2, figsize=(12, 7), sharex=True)
    fig.patch.set_facecolor(SURFACE)
    for ax, item, c in zip(axes.flat, items, COLORS):
        d = df[df["item"] == item].set_index("date").sort_index()
        ax.plot(d.index, d["price"], color=c, lw=1.2)
        ax.set_title(EN.get(item, item), color=INK, fontsize=10)
        ax.set_ylabel("JPY/kg", color=INK2, fontsize=8)
        style(ax)
    fig.suptitle("Tokyo wholesale daily price (all origins)", color=INK, fontsize=12)
    fig.tight_layout()
    fig.savefig(OUT / "daily_price.png", dpi=130, facecolor=SURFACE)
    print(f"wrote {OUT/'daily_price.png'}")


def plot_weekday(df: pd.DataFrame) -> None:
    fig, ax = plt.subplots(figsize=(9, 5))
    fig.patch.set_facecolor(SURFACE)
    for item, c in zip(sorted(df["item"].unique()), COLORS):
        d = df[df["item"] == item].copy()
        d["rel"] = d["price"] / d["price"].mean() * 100
        prof = d.groupby(d["date"].dt.dayofweek)["rel"].mean()
        ax.plot(prof.index, prof.values, "-o", color=c, lw=2, ms=6,
                mec=SURFACE, mew=1, label=EN.get(item, item))
        print(f"{item} 曜日profile:", {DOW[k]: round(v,1) for k,v in prof.items()})
    ax.set_xticks(range(7), DOW)
    ax.axhline(100, color=BASE, lw=1)
    ax.set_title("Weekday price profile (item mean = 100)", color=INK, fontsize=11)
    ax.set_ylabel("relative price", color=INK2, fontsize=9)
    style(ax)
    ax.legend(fontsize=9, frameon=False, labelcolor=INK2)
    fig.tight_layout()
    fig.savefig(OUT / "weekday_shape.png", dpi=130, facecolor=SURFACE)
    print(f"wrote {OUT/'weekday_shape.png'}")


if __name__ == "__main__":
    OUT.mkdir(exist_ok=True)
    df = load()
    print(f"{df['date'].min().date()}〜{df['date'].max().date()} 総計{len(df)}日分")
    for item, g in df.groupby("item"):
        p = g["price"]
        print(f"{item}: 平均{p.mean():.0f}円/kg  変動係数{p.std()/p.mean():.2f}  "
              f"最高/最安 {p.max()}/{p.min()}円 ({p.max()/p.min():.1f}倍)")
    plot_daily(df)
    plot_weekday(df)
