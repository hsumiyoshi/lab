#!/usr/bin/env python3
"""Sentinel-2でキャベツ産地のNDVI（植生指数）時系列を取得する。

対象: 嬬恋村の高原キャベツ地帯（東京市場の夏キャベツの主産地）。
狙い: 畑の緑度を数日おきに測り、出荷量・市況（exp02）の先読みに使えるか。

データ: AWS Open Data の Sentinel-2 L2A COG（キー不要）
  STAC検索: https://earth-search.aws.element84.com/v1 (sentinel-2-l2a)
  NDVI = (B08近赤外 - B04赤) / (B08 + B04)、雲量フィルタ＋SCLで雲画素除外
"""

import json
import time
import urllib.request
from pathlib import Path

import numpy as np
import pandas as pd
import rasterio
from rasterio.warp import transform_bounds
from rasterio.windows import from_bounds

HERE = Path(__file__).parent
DATA = HERE / "data"
STAC = "https://earth-search.aws.element84.com/v1/search"

# 嬬恋村・田代〜大前の高原キャベツ地帯（約6km×5km）
BBOX = [138.44, 36.48, 138.51, 36.53]  # lon/lat
YEARS = (2024, 2025, 2026)
MONTHS = (4, 11)  # 4月〜10月（作付け〜出荷期）
MAX_CLOUD = 40  # シーン全体の雲量%（画素単位はSCLで再フィルタ）


def stac_search(year: int) -> list[dict]:
    body = {
        "collections": ["sentinel-2-l2a"],
        "bbox": BBOX,
        "datetime": f"{year}-{MONTHS[0]:02d}-01T00:00:00Z/{year}-{MONTHS[1]:02d}-01T00:00:00Z",
        "query": {"eo:cloud_cover": {"lt": MAX_CLOUD}},
        "limit": 200,
    }
    req = urllib.request.Request(
        STAC, data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=60) as res:
        return json.load(res)["features"]


def scene_ndvi(item: dict) -> float | None:
    """対象bboxの雲なし画素の平均NDVI。有効画素が2割未満ならNone。"""
    assets = item["assets"]
    try:
        with rasterio.open(assets["red"]["href"]) as red_ds:
            bounds = transform_bounds("EPSG:4326", red_ds.crs, *BBOX)
            win = from_bounds(*bounds, red_ds.transform)
            red = red_ds.read(1, window=win).astype("float32")
        with rasterio.open(assets["nir"]["href"]) as nir_ds:
            win = from_bounds(*transform_bounds("EPSG:4326", nir_ds.crs, *BBOX),
                              nir_ds.transform)
            nir = nir_ds.read(1, window=win).astype("float32")
        with rasterio.open(assets["scl"]["href"]) as scl_ds:
            win = from_bounds(*transform_bounds("EPSG:4326", scl_ds.crs, *BBOX),
                              scl_ds.transform)
            scl = scl_ds.read(1, window=win)
    except Exception as e:
        print(f"  skip {item['id']}: {e}")
        return None
    scl = np.repeat(np.repeat(scl, 2, axis=0), 2, axis=1)  # 20m→10mに揃える
    h = min(red.shape[0], nir.shape[0], scl.shape[0])
    w = min(red.shape[1], nir.shape[1], scl.shape[1])
    red, nir, scl = red[:h, :w], nir[:h, :w], scl[:h, :w]
    ok = np.isin(scl, (4, 5, 6)) & (red > 0) & (nir > 0)  # 植生/裸地/水のみ
    if ok.mean() < 0.2:
        return None
    ndvi = (nir[ok] - red[ok]) / (nir[ok] + red[ok] + 1e-9)
    return float(ndvi.mean())


if __name__ == "__main__":
    DATA.mkdir(exist_ok=True)
    rows = []
    for year in YEARS:
        items = stac_search(year)
        print(f"{year}: 候補シーン{len(items)}枚（雲量<{MAX_CLOUD}%）")
        for item in items:
            date = item["properties"]["datetime"][:10]
            v = scene_ndvi(item)
            if v is not None:
                rows.append({"date": date, "ndvi": v,
                             "cloud": item["properties"]["eo:cloud_cover"]})
                print(f"  {date}: NDVI {v:.3f}")
            time.sleep(0.2)
    df = (pd.DataFrame(rows).groupby("date", as_index=False)
          .agg(ndvi=("ndvi", "mean"), cloud=("cloud", "min")))
    dest = DATA / "tsumagoi_ndvi.csv"
    df.to_csv(dest, index=False)
    print(f"-> {dest} {len(df)}日分")
