#!/usr/bin/env python3
"""熊本余震の週次件数フォワード予測（大森則 vs 脳死ベンチ）。

対象: 気象庁発表の地震情報（P2P API中継）のうち震源名に「熊本」を含む週次件数。
本震: 2026-07-28 16:27 M7.1（熊本県熊本地方・最大震度7）

戦略（凍結 2026-08-13。以後ルールは変更しない）:
- omori    : 改良大森則 n(t)=K/(t+c)^p。週開始前のデータだけでK,c,pを最小二乗
             フィットし、翌週7日分の合計を予測（因果性维持・パラメータ再推定は
             ルールの一部として毎週行う）
- flat     : 前週の実績件数をそのまま予測（脳死ベンチ）
- oracle   : 実績そのもの（上限=誤差ゼロ）

採点: 対数誤差 |log(pred+1) - log(actual+1)|。台帳はステートレス（全再計算）。
ラウンド: FORWARD_START以降のISO週（月〜日）。データが週末まで揃った週のみ採点。
"""

from datetime import date, timedelta
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

HERE = Path(__file__).parent
REPORTS = HERE / "reports"
MAINSHOCK = pd.Timestamp("2026-07-28 16:27:00")
FORWARD_START = date(2026, 8, 17)  # 初ラウンドの週の月曜
FREEZE_NOTE = "戦略凍結 2026-08-13（omori/flat、採点=対数誤差、週=ISO月〜日）"

BLUE, ORANGE = "#2a78d6", "#eb6834"
INK, INK2, MUTED = "#0b0b0b", "#52514e", "#898781"
GRID, BASE, SURFACE = "#e1e0d9", "#c3c2b7", "#fcfcfb"


def load_daily() -> pd.Series:
    df = pd.read_csv(HERE / "archive" / "quakes.csv", parse_dates=["time"])
    k = df[df["name"].astype(str).str.contains("熊本")]
    daily = k.set_index("time").resample("D").size()
    return daily[daily.index >= MAINSHOCK.normalize()]


def fit_omori(daily: pd.Series, upto: date) -> tuple[float, float, float] | None:
    """upto（週開始日）より前のデータで n(t)=K/(t+c)^p をフィット。"""
    hist = daily[daily.index.date < upto]
    t = (hist.index - MAINSHOCK).days.values.astype(float) + 0.5  # 日の中央
    n = hist.values.astype(float)
    ok = (t > 0) & (n > 0)
    t, n = t[ok], n[ok]
    if len(t) < 5:
        return None
    best = None
    for c in (0.01, 0.05, 0.1, 0.2, 0.5, 1.0, 2.0):
        x = np.log(t + c)
        slope, intercept = np.polyfit(x, np.log(n), 1)
        sse = float(((np.log(n) - (intercept + slope * x)) ** 2).sum())
        if best is None or sse < best[0]:
            best = (sse, np.exp(intercept), c, -slope)
    _, K, c, p = best
    return K, c, p


def predict_omori(params, week_start: date) -> float:
    K, c, p = params
    days = [(pd.Timestamp(week_start) + pd.Timedelta(days=i) - MAINSHOCK).days + 0.5
            for i in range(7)]
    return float(sum(K / (t + c) ** p for t in days))


def build_ledger(daily: pd.Series) -> pd.DataFrame:
    rows = []
    last_data = daily.index.date.max()
    ws = FORWARD_START
    while ws + timedelta(days=6) <= last_data:
        we = ws + timedelta(days=6)
        actual = int(daily[(daily.index.date >= ws) & (daily.index.date <= we)].sum())
        prev = int(daily[(daily.index.date >= ws - timedelta(days=7))
                         & (daily.index.date < ws)].sum())
        params = fit_omori(daily, ws)
        row = {"week": f"{ws.isocalendar()[0]}-W{ws.isocalendar()[1]:02d}",
               "week_start": ws, "actual": actual, "flat": prev}
        if params:
            row["omori"] = round(predict_omori(params, ws), 1)
            row["omori_params"] = f"K={params[0]:.0f},c={params[1]},p={params[2]:.2f}"
        rows.append(row)
        ws += timedelta(days=7)
    df = pd.DataFrame(rows)
    if len(df):
        for name in ("omori", "flat"):
            df[f"err_{name}"] = (np.log(df[name] + 1) - np.log(df["actual"] + 1)).abs().round(3)
    return df


def plot(daily: pd.Series, ledger: pd.DataFrame) -> None:
    fig, ax = plt.subplots(figsize=(10, 4.5))
    fig.patch.set_facecolor(SURFACE)
    ax.bar(daily.index, daily.values, color=BLUE, width=0.7, label="daily count")
    params = fit_omori(daily, daily.index.date.max() + timedelta(days=1))
    if params:
        K, c, p = params
        t = np.arange(0.5, (daily.index.max() - MAINSHOCK).days + 8, 0.5)
        ax.plot(MAINSHOCK + pd.to_timedelta(t, unit="D"), K / (t + c) ** p,
                color=ORANGE, lw=2, label=f"Omori fit (p={p:.2f})")
    for _, r in ledger.iterrows():
        ax.axvline(pd.Timestamp(r["week_start"]), color=BASE, lw=0.8, ls="--")
    ax.set_title("Kumamoto aftershocks: daily counts and Omori forecast basis",
                 color=INK, fontsize=11, loc="left")
    ax.set_ylabel("events/day", color=INK2, fontsize=9)
    ax.set_facecolor(SURFACE)
    ax.grid(color=GRID, lw=0.7)
    for s in ("top", "right"):
        ax.spines[s].set_visible(False)
    for s in ("left", "bottom"):
        ax.spines[s].set_color(BASE)
    ax.tick_params(colors=INK2, labelsize=8.5)
    ax.legend(frameon=False, labelcolor=INK2, fontsize=8.5)
    fig.tight_layout()
    fig.savefig(REPORTS / "quake_forward.png", dpi=130, facecolor=SURFACE)


def report(daily: pd.Series, ledger: pd.DataFrame) -> str:
    lines = ["# 熊本余震・週次件数フォワード予測", "",
             f"{FREEZE_NOTE} / 生成: {pd.Timestamp.now():%Y-%m-%d %H:%M}", "",
             "![daily](quake_forward.png)", ""]
    if len(ledger) == 0:
        lines.append(f"初ラウンドは {FORWARD_START} の週。データが週末まで揃い次第ここに採点が載る。")
    else:
        lines.append("| 週 | 実績 | omori予測 | flat予測 | 誤差omori | 誤差flat |")
        lines.append("|---|---|---|---|---|---|")
        for _, r in ledger.iterrows():
            lines.append(f"| {r['week']} | {r['actual']} | {r.get('omori','—')} "
                         f"| {r['flat']} | {r.get('err_omori','—')} | {r['err_flat']} |")
        lines.append("")
        lines.append(f"累計対数誤差: omori {ledger['err_omori'].sum():.3f} vs "
                     f"flat {ledger['err_flat'].sum():.3f}（小さいほど良い）")
    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    REPORTS.mkdir(exist_ok=True)
    daily = load_daily()
    ledger = build_ledger(daily)
    ledger.to_csv(REPORTS / "quake_ledger.csv", index=False)
    plot(daily, ledger)
    (REPORTS / "quake_forward.md").write_text(report(daily, ledger))
    print(report(daily, ledger))
