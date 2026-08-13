#!/usr/bin/env python3
"""気象データ取得（Open-Meteo、全国8地点の加重指数）。

- archive API   = 実測（ERA5再解析）→ 学習用
- historical-forecast API = 当時のモデル予報のアーカイブ → 判断入力用
  （実運用で前日に手に入るのは予報。実測で判断するとルックアヘッドになる）

rad   = 9-15時平均日射の全国指数（太陽光設備容量の概算比で加重）
tmean = 9-21時平均気温の全国指数（需要規模の概算比で加重）
※加重は概算。厳密には各エリアの導入量統計で更新する。
"""

import json
import time
import urllib.request
from datetime import date, timedelta
from pathlib import Path

import pandas as pd

DATA = Path(__file__).parent / "data"
START = "2025-03-01"

#           緯度     経度    日射weight  気温weight
POINTS = {
    "tokyo":    (35.68, 139.76, 0.25, 0.35),
    "nagoya":   (35.17, 136.91, 0.17, 0.15),
    "kyushu":   (32.80, 130.70, 0.15, 0.10),
    "tohoku":   (38.27, 140.87, 0.13, 0.08),
    "osaka":    (34.69, 135.50, 0.10, 0.17),
    "chugoku":  (34.66, 133.92, 0.10, 0.05),
    "hokkaido": (43.06, 141.35, 0.05, 0.07),
    "shikoku":  (34.34, 134.05, 0.05, 0.03),
}

URLS = {
    "actual": "https://archive-api.open-meteo.com/v1/archive",
    "forecast": "https://historical-forecast-api.open-meteo.com/v1/forecast",
}


def fetch_point(kind: str, lat: float, lon: float) -> pd.DataFrame:
    lag = 5 if kind == "actual" else 0
    end = (date.today() - timedelta(days=lag)).isoformat()
    url = (f"{URLS[kind]}?latitude={lat}&longitude={lon}"
           f"&start_date={START}&end_date={end}"
           f"&hourly=temperature_2m,shortwave_radiation&timezone=Asia%2FTokyo")
    for attempt in range(4):
        try:
            with urllib.request.urlopen(url, timeout=120) as res:
                j = json.load(res)
            break
        except Exception:
            if attempt == 3:
                raise
            time.sleep(2 ** (attempt + 1))
    h = pd.DataFrame(j["hourly"])
    h["time"] = pd.to_datetime(h["time"])
    h["date"] = h["time"].dt.normalize()
    h["hour"] = h["time"].dt.hour
    return h.groupby("date").apply(
        lambda g: pd.Series({
            "rad": g.loc[g["hour"].between(9, 15), "shortwave_radiation"].mean(),
            "tmean": g.loc[g["hour"].between(9, 21), "temperature_2m"].mean(),
        }), include_groups=False).dropna()


def build(kind: str) -> pd.DataFrame:
    rad = None
    tmp = None
    for name, (lat, lon, wr, wt) in POINTS.items():
        d = fetch_point(kind, lat, lon)
        print(f"  {kind}/{name}: {len(d)}日")
        rad = d["rad"] * wr if rad is None else rad.add(d["rad"] * wr, fill_value=0)
        tmp = d["tmean"] * wt if tmp is None else tmp.add(d["tmean"] * wt, fill_value=0)
    return pd.DataFrame({"rad": rad, "tmean": tmp}).dropna()


if __name__ == "__main__":
    DATA.mkdir(exist_ok=True)
    for kind in URLS:
        df = build(kind)
        dest = DATA / f"weather_{kind}.csv"
        df.to_csv(dest)
        print(f"{kind}: {df.index.min().date()}〜{df.index.max().date()} {len(df)}日 -> {dest}")
