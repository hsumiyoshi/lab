#!/usr/bin/env python3
"""嬬恋NDVI×東京市場の群馬産キャベツ入荷量の季節カーブ比較。

上段: Sentinel-2 NDVI（嬬恋の高原キャベツ地帯、雲なし画素平均）
下段: 東京市場の群馬産キャベツ週別入荷量（exp02のベジ探データ）
同じ4〜10月の時間軸に年別の系列を重ね、生育→出荷のリードラグを目視する。
"""

from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import pandas as pd

HERE = Path(__file__).parent
OUT = HERE / "output"
VEG = HERE.parent / "exp02_vegetable" / "data" / "veg_31700.csv"  # キャベツ
BLUE, ORANGE, GREEN = "#2a78d6", "#eb6834", "#1baf7a"
INK, INK2, MUTED = "#0b0b0b", "#52514e", "#898781"
GRID, BASE, SURFACE = "#e1e0d9", "#c3c2b7", "#fcfcfb"
YEAR_COLOR = {2024: BLUE, 2025: ORANGE, 2026: GREEN}
MONTH_TICKS = [(91, "Apr"), (121, "May"), (152, "Jun"), (182, "Jul"),
               (213, "Aug"), (244, "Sep"), (274, "Oct"), (305, "Nov")]


def style(ax):
    ax.set_facecolor(SURFACE)
    ax.grid(color=GRID, lw=0.7)
    for s in ("top", "right"):
        ax.spines[s].set_visible(False)
    for s in ("left", "bottom"):
        ax.spines[s].set_color(BASE)
    ax.tick_params(colors=INK2, labelsize=8.5)


if __name__ == "__main__":
    OUT.mkdir(exist_ok=True)
    ndvi = pd.read_csv(HERE / "data" / "tsumagoi_ndvi.csv", parse_dates=["date"])
    # SCLが拾い損ねた雲・霞の除外: 雲はNDVIを下げるだけなので、
    # 隣接観測より0.25以上低い「局所ディップ」だけを落とす（季節的な漸減は残る）
    nb = pd.concat([ndvi["ndvi"].shift(1), ndvi["ndvi"].shift(-1)], axis=1).max(axis=1)
    bad = ndvi["ndvi"] < nb - 0.25
    if bad.any():
        print("雲アーティファクトとして除外:", ndvi.loc[bad, "date"].dt.date.tolist())
    ndvi = ndvi[~bad]
    veg = pd.read_csv(VEG, parse_dates=["date"])
    gunma = veg[veg["origin"] == "群馬"].copy()

    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(10, 7), sharex=True)
    fig.patch.set_facecolor(SURFACE)
    for year, color in YEAR_COLOR.items():
        n = ndvi[ndvi["date"].dt.year == year]
        ax1.plot(n["date"].dt.dayofyear, n["ndvi"], color=color, lw=1.8,
                 marker="o", ms=4, mec=SURFACE, mew=0.7, label=str(year))
        g = gunma[gunma["date"].dt.year == year]
        wk = g.groupby(g["date"].dt.isocalendar().week.astype(int))\
              .agg(qty=("qty", "sum"), doy=("date", lambda s: s.dt.dayofyear.mean()))
        ax2.plot(wk["doy"], wk["qty"] / 1000, color=color, lw=1.8,
                 marker="o", ms=4, mec=SURFACE, mew=0.7, label=str(year))
    ax1.set_title("Tsumagoi cabbage belt: Sentinel-2 NDVI (cloud-free mean)",
                  color=INK, fontsize=10.5, loc="left")
    ax1.set_ylabel("NDVI", color=INK2, fontsize=9)
    ax1.legend(frameon=False, labelcolor=INK2, fontsize=8.5, title_fontsize=8.5)
    ax2.set_title("Tokyo market: cabbage arrivals from Gunma (weekly)",
                  color=INK, fontsize=10.5, loc="left")
    ax2.set_ylabel("tonnes/week", color=INK2, fontsize=9)
    ax2.legend(frameon=False, labelcolor=INK2, fontsize=8.5)
    ax2.set_xticks([d for d, _ in MONTH_TICKS], [m for _, m in MONTH_TICKS])
    ax2.set_xlim(85, 310)
    for ax in (ax1, ax2):
        style(ax)
    fig.tight_layout()
    fig.savefig(OUT / "ndvi_vs_arrivals.png", dpi=130, facecolor=SURFACE)
    print("wrote", OUT / "ndvi_vs_arrivals.png")
    print(f"NDVI観測日数: {len(ndvi)} / 群馬産キャベツ入荷日数: {gunma['date'].nunique()}")
