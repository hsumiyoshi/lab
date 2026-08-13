#!/usr/bin/env python3
"""JEPXスポット価格の基本観察プロットを output/ に出す。

1. daily_price.png   — 日次のシステムプライス平均と日内スプレッド（min-max帯）
2. intraday_shape.png — 直近60日の平均日内カーブ（48コマ）＋直近日
"""

from datetime import timedelta
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import pandas as pd

HERE = Path(__file__).parent
OUT = HERE / "output"

COLS = ["date", "koma", "sell_kwh", "buy_kwh", "volume_kwh", "system_price"]


def load() -> pd.DataFrame:
    files = sorted((HERE / "data").glob("spot_*.csv"))
    if not files:
        raise SystemExit("data/ にCSVがない。先に fetch.py を実行")
    frames = []
    for f in files:
        df = pd.read_csv(f, usecols=range(6), header=0, names=COLS)
        frames.append(df)
    df = pd.concat(frames, ignore_index=True)
    df["date"] = pd.to_datetime(df["date"])
    # コマ1 = 00:00-00:30
    df["ts"] = df["date"] + pd.to_timedelta((df["koma"] - 1) * 30, unit="m")
    return df


def plot_daily(df: pd.DataFrame) -> None:
    d = df.groupby("date")["system_price"].agg(["mean", "min", "max"])
    fig, ax = plt.subplots(figsize=(12, 5))
    ax.fill_between(d.index, d["min"], d["max"], alpha=0.25, label="daily min-max")
    ax.plot(d.index, d["mean"], lw=1.2, label="daily mean")
    ax.set_title("JEPX spot system price")
    ax.set_ylabel("JPY/kWh")
    ax.legend()
    fig.tight_layout()
    fig.savefig(OUT / "daily_price.png", dpi=130)
    print(f"wrote {OUT/'daily_price.png'}")


def plot_intraday(df: pd.DataFrame) -> None:
    last_day = df["date"].max()
    recent = df[df["date"] >= last_day - timedelta(days=60)]
    shape = recent.groupby("koma")["system_price"].mean()
    latest = df[df["date"] == last_day].set_index("koma")["system_price"]
    hours = [(k - 1) / 2 for k in shape.index]
    fig, ax = plt.subplots(figsize=(10, 5))
    ax.plot(hours, shape.values, label="mean (last 60d)", lw=2)
    ax.plot([(k - 1) / 2 for k in latest.index], latest.values,
            label=f"{last_day.date()}", lw=1, alpha=0.8)
    ax.set_title("JEPX intraday price shape (system price)")
    ax.set_xlabel("hour of day")
    ax.set_ylabel("JPY/kWh")
    ax.set_xticks(range(0, 25, 3))
    ax.legend()
    fig.tight_layout()
    fig.savefig(OUT / "intraday_shape.png", dpi=130)
    print(f"wrote {OUT/'intraday_shape.png'}")


if __name__ == "__main__":
    OUT.mkdir(exist_ok=True)
    df = load()
    print(f"{df['date'].min().date()} 〜 {df['date'].max().date()}  {len(df):,} rows")
    plot_daily(df)
    plot_intraday(df)
