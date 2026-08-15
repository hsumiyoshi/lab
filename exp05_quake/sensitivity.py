#!/usr/bin/env python3
"""issue #6: 大森則予測の頑健性——感度分析の再現スクリプト。

出力: reports/sensitivity.md（数表）と reports/sensitivity.png（3パネル図）
①完全性マグニチュードMc ②M閾値別の日次1歩先予測誤差（大森則vs横ばい）③c感度
"""

from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

HERE = Path(__file__).parent
MAIN = pd.Timestamp("2026-07-28 16:27:00")
BLUE, ORANGE, GREEN = "#2a78d6", "#eb6834", "#1baf7a"
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


def omori_fit(t, n):
    ok = (t > 0) & (n > 0)
    t, n = t[ok], n[ok]
    if len(t) < 5:
        return None
    best = None
    for c in (0.01, 0.05, 0.1, 0.2, 0.5, 1.0, 2.0):
        x = np.log(t + c)
        sl, ic = np.polyfit(x, np.log(n), 1)
        sse = ((np.log(n) - (ic + sl * x)) ** 2).sum()
        if best is None or sse < best[0]:
            best = (sse, np.exp(ic), c, -sl)
    return best[1:]


def one_step(k, m):
    """日次1歩先予測: (日付, 実績, 大森則予測, 前日横ばい) の表"""
    daily = k[k["mag"] >= m].set_index("time").resample("D").size()
    days = (daily.index - MAIN.normalize()).days.values.astype(float) + 0.5
    rows = []
    for i in range(6, len(daily) - 1):
        fit = omori_fit(days[:i + 1], daily.values[:i + 1].astype(float))
        if fit is None:
            continue
        K, c, p = fit
        rows.append({"date": daily.index[i + 1].date(),
                     "actual": int(daily.values[i + 1]),
                     "omori": round(K / (days[i + 1] + c) ** p, 1),
                     "flat": int(daily.values[i])})
    df = pd.DataFrame(rows)
    df["err_omori"] = (np.log(df["omori"] + 1) - np.log(df["actual"] + 1)).abs().round(3)
    df["err_flat"] = (np.log(df["flat"] + 1) - np.log(df["actual"] + 1)).abs().round(3)
    return df


if __name__ == "__main__":
    df = pd.read_csv(HERE / "archive" / "quakes.csv", parse_dates=["time"])
    k = df[df["name"].astype(str).str.contains("熊本")]
    k = k[k["time"] >= MAIN]
    out = ["# issue #6 感度分析 — 大森則予測の頑健性", "",
           f"データ: 熊本余震 {len(k)}件（{MAIN:%Y-%m-%d %H:%M} 本震以降、取得 {k['time'].max():%m-%d %H:%M}）", ""]

    # ① Mc
    mags = k["mag"].dropna().round(1)
    hist = mags.value_counts().sort_index()
    mc = hist.idxmax()
    out += [f"## ① 完全性マグニチュード Mc ≈ {mc}", "",
            "| M | " + " | ".join(f"{m:.1f}" for m in hist.index if 1.5 <= m <= 4.5) + " |",
            "|---|" + "---|" * len([m for m in hist.index if 1.5 <= m <= 4.5]),
            "| 件数 | " + " | ".join(str(hist[m]) for m in hist.index if 1.5 <= m <= 4.5) + " |", ""]

    # ② 1歩先予測（M閾値別）
    tables = {}
    out.append("## ② 日次1歩先予測（大森則 vs 前日横ばい）\n")
    for m in (0, 2.5, 3.0):
        t = one_step(k, m)
        tables[m] = t
        label = f"M≥{m}" if m else "全有感"
        out += [f"### {label}（対数誤差平均: 大森則 {t['err_omori'].mean():.3f} vs 横ばい {t['err_flat'].mean():.3f}）", "",
                "| 日付 | 実績 | 大森則予測 | 横ばい予測 | 誤差(大森) | 誤差(横ばい) |", "|---|---|---|---|---|---|"]
        out += [f"| {r.date} | {r.actual} | {r.omori} | {r.flat} | {r.err_omori} | {r.err_flat} |"
                for r in t.itertuples()]
        out.append("")

    # ③ c感度
    daily = k.set_index("time").resample("D").size()
    days = (daily.index - MAIN.normalize()).days.values.astype(float) + 0.5
    n_all = daily.values.astype(float)
    ok = (days > 0) & (n_all > 0)
    t_next = np.arange(days[-1] + 1, days[-1] + 8)
    c_grid, preds = (0.01, 0.1, 0.5, 1.0, 2.0), []
    for c in c_grid:
        x = np.log(days[ok] + c)
        sl, ic = np.polyfit(x, np.log(n_all[ok]), 1)
        preds.append(sum(np.exp(ic) / (tt + c) ** (-sl) for tt in t_next))
    out += ["## ③ cパラメータ感度（来週7日合計・全有感）", "",
            "| c | " + " | ".join(str(c) for c in c_grid) + " |",
            "|---|" + "---|" * len(c_grid),
            "| 予測件数 | " + " | ".join(f"{p:.0f}" for p in preds) + " |", ""]
    (HERE / "reports" / "sensitivity.md").write_text("\n".join(out), encoding="utf-8")

    # 図: 3パネル
    fig, axes = plt.subplots(1, 3, figsize=(13, 4))
    fig.patch.set_facecolor(SURFACE)
    a = axes[0]
    sel = hist[(hist.index >= 1.5) & (hist.index <= 5.0)]
    a.bar(sel.index, sel.values, width=0.08, color=BLUE)
    a.axvline(mc, color=ORANGE, lw=1.8, ls="--")
    a.annotate(f"Mc = {mc}\n(undercount below)", (mc + 0.1, sel.max() * 0.9),
               color=ORANGE, fontsize=8.5)
    a.set_title("Magnitude histogram", color=INK, fontsize=10, loc="left")
    a.set_xlabel("M", color=INK2, fontsize=9)

    a = axes[1]
    labels = ["all felt", "M>=2.5", "M>=3.0"]
    xs = np.arange(3)
    eo = [tables[m]["err_omori"].mean() for m in (0, 2.5, 3.0)]
    ef = [tables[m]["err_flat"].mean() for m in (0, 2.5, 3.0)]
    a.bar(xs - 0.17, eo, width=0.3, color=ORANGE, label="Omori")
    a.bar(xs + 0.17, ef, width=0.3, color="#eda100", label="flat (naive)")
    a.set_xticks(xs, labels)
    a.set_title("1-step-ahead log error (lower = better)", color=INK, fontsize=10, loc="left")
    a.legend(frameon=False, labelcolor=INK2, fontsize=8.5)

    a = axes[2]
    a.plot(c_grid, preds, color=BLUE, lw=2, marker="o", ms=5, mec=SURFACE)
    a.set_xscale("log")
    a.set_title("Next-week total vs c parameter", color=INK, fontsize=10, loc="left")
    a.set_xlabel("c (log scale)", color=INK2, fontsize=9)
    a.set_ylabel("predicted events / week", color=INK2, fontsize=9)
    for ax in axes:
        style(ax)
    fig.tight_layout()
    fig.savefig(HERE / "reports" / "sensitivity.png", dpi=130, facecolor=SURFACE)
    print("wrote reports/sensitivity.md, sensitivity.png")
