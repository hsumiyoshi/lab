#!/usr/bin/env python3
"""仮想蓄電池のペーパー運用: 複数戦略の並列バックテストとランキング。

電池モデル: 容量10kWh / 出力5kW（=2.5kWh/コマ） / 充放電効率 各95%
毎日SoC=0から開始（日をまたぐ持ち越しなしの単純化・1日1サイクル）。

設計: 全戦略が「予測価格カーブ」を出し、共通の最適化（時間順序を守る
分割探索）でコマを選ぶ。したがってランキングの差 = 純粋に予測の質の差。
oracle（当日実価格を知る神様）との差分が「予測の市場価値」になる。

現実の市場では前日朝の入札締切までにコマを決める必要があり、
約定価格は後から判明する——oracleはその意味で到達不能な上限。
"""

import itertools
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

HERE = Path(__file__).parent
CAP = 10.0       # kWh
POWER = 2.5      # kWh/コマ (5kW)
EFF = 0.95       # 充電・放電それぞれ
N_KOMA = 4       # 充電・放電に使うコマ数


def load() -> pd.DataFrame:
    cols = ["date", "koma", "sell", "buy", "vol", "sp"]
    df = pd.concat(
        [pd.read_csv(f, usecols=range(6), header=0, names=cols)
         for f in sorted((HERE / "data").glob("spot_*.csv"))],
        ignore_index=True,
    )
    df["date"] = pd.to_datetime(df["date"])
    return df


# ---------------- 共通部品 ----------------

def run_day(prices: pd.Series, charge: set, discharge: set) -> float:
    """コマを時系列に歩き、SoC制約下で約定させて当日の損益(円)を返す"""
    soc = 0.0
    pnl = 0.0
    for koma in sorted(prices.index):
        p = prices[koma]
        if koma in charge and soc < CAP:
            buy = min(POWER, (CAP - soc) / EFF)
            soc += buy * EFF
            pnl -= buy * p
        elif koma in discharge and soc > 0:
            sell = min(POWER, soc)
            soc -= sell
            pnl += sell * EFF * p
    return pnl


def choose_split(pred: pd.Series):
    """予測カーブから「充電コマ全部→放電コマ全部」の順序を守る最良の組を選ぶ。

    境界tを走査し、t以前の最安N_KOMAで充電（4コマ充電では容量制約が
    効かないため最安選択が厳密）。放電はt以降の価格上位8候補の組合せを
    実ディスパッチ（run_day）で評価して最良を採る。
    旧実装は放電4コマを等量とみなす近似（est=Σ価格）だったが、SoC端数で
    時間順最後の放電コマだけ約2.0kWhに減量されるため、端数が高値コマに
    落ちる日には真の上限を下回った（issue #13、2026-08-18受渡で顕在化）。
    """
    best, best_pair = -1e18, None
    komas = sorted(pred.index)
    for t in komas[N_KOMA:-N_KOMA + len(komas) and len(komas) - N_KOMA + 1]:
        before = pred[pred.index < t]
        after = pred[pred.index >= t]
        if len(before) < N_KOMA or len(after) < N_KOMA:
            continue
        c = set(before.nsmallest(N_KOMA).index)
        cand = after.nlargest(min(8, len(after))).index
        for combo in itertools.combinations(cand, N_KOMA):
            v = run_day(pred, c, set(combo))
            if v > best:
                best, best_pair = v, (c, set(combo))
    return best_pair or (set(), set())


# ---------------- 戦略 = 予測カーブの出し方 ----------------

def pred_oracle(hist, today, day):
    """神様: 当日の実価格そのもの（到達不能な上限）"""
    return today


def pred_yesterday(hist, today, day):
    """昨日の形が今日も続くと予測"""
    if hist.empty:
        return None
    return hist[hist["date"] == hist["date"].max()].set_index("koma")["sp"]


def pred_weekshape(hist, today, day, weeks=4):
    """直近4週の同曜日の平均カーブを予測とする"""
    same = hist[hist["date"].dt.dayofweek == day.dayofweek]
    days = same["date"].drop_duplicates().nlargest(weeks)
    sub = same[same["date"].isin(days)]
    if sub.empty:
        return None
    return sub.groupby("koma")["sp"].mean()


WEATHER_A = None  # 実測（学習用）
WEATHER_F = None  # 予報アーカイブ（判断入力用）


def load_weather():
    global WEATHER_A, WEATHER_F
    a = HERE / "data" / "weather_actual.csv"
    f = HERE / "data" / "weather_forecast.csv"
    if a.exists() and f.exists():
        WEATHER_A = pd.read_csv(a, parse_dates=["date"]).set_index("date")
        WEATHER_F = pd.read_csv(f, parse_dates=["date"]).set_index("date")


# 予測ロジックの追加参加は「予測提出型」で行う（picks/ にJSONを提出）。
# ベースライン以外の戦略コードはこのリポジトリには置かない。
PRED_STRATS = {
    "oracle": pred_oracle,
    "yesterday": pred_yesterday,
    "weekshape": pred_weekshape,
}


def strat_clock():
    """固定時刻: 昼11-13時に充電、夕方17:30-19:30に放電（データ不使用の基準線）"""
    return {23, 24, 25, 26}, {36, 37, 38, 39}


# ---------------- バックテスト ----------------

def backtest(df: pd.DataFrame) -> pd.DataFrame:
    days = df["date"].drop_duplicates().sort_values().iloc[28:]  # 4週分の履歴を確保
    rows = []
    for day in days:
        today = df[df["date"] == day].set_index("koma")["sp"]
        hist = df[df["date"] < day]
        row = {"date": day, "clock": run_day(today, *strat_clock())}
        for name, fn in PRED_STRATS.items():
            pred = fn(hist, today, day)
            row[name] = run_day(today, *choose_split(pred)) if pred is not None else 0.0
        rows.append(row)
    return pd.DataFrame(rows).set_index("date")


if __name__ == "__main__":
    df = load()
    load_weather()
    res = backtest(df)
    out = HERE / "output"
    out.mkdir(exist_ok=True)
    res.to_csv(out / "sim_daily.csv")

    total = res.sum().sort_values(ascending=False)
    days = len(res)
    print(f"=== 仮想蓄電池リーダーボード（{res.index.min().date()}〜{res.index.max().date()}, {days}日, 10kWh/5kW/往復90%）===")
    print(f"{'戦略':<12}{'累計損益':>12}{'円/日':>8}{'対オラクル':>9}")
    for name, v in total.items():
        print(f"{name:<12}{v:>11,.0f}円{v/days:>7.1f}{v/total['oracle']*100:>8.1f}%")
    gap = total["oracle"] - total.drop("oracle").max()
    print(f"\n予測の価値（オラクル−最良ヒューリスティック）: {gap:,.0f}円 / {days}日 = {gap/days:.1f}円/日/10kWh")

    cum = res.cumsum()
    fig, ax = plt.subplots(figsize=(11, 5))
    for name in total.index:
        ax.plot(cum.index, cum[name], label=name, lw=1.6)
    ax.set_title("Virtual 10kWh battery: cumulative P&L by strategy")
    ax.set_ylabel("JPY")
    ax.legend()
    fig.tight_layout()
    fig.savefig(out / "sim_leaderboard.png", dpi=130)
    print(f"wrote {out/'sim_daily.csv'} / {out/'sim_leaderboard.png'}")
