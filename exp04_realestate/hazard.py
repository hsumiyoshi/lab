#!/usr/bin/env python3
"""洪水ハザード×取引価格の地区照合（熊本市・概算v1）。

手順:
1. 取引データ（熊本市）のユニーク地区名を国土地理院ジオコーダで代表点に変換
2. 代表点を含むz14タイルの洪水浸水想定区域（XKT026・GeoJSON）を取得
3. 地区代表点が浸水想定ポリゴン内かを判定（浸水深ランクA31a_205の最大値）
4. ハザード内外で単価中央値を比較

注意: 地区の「代表点」1点での判定なので境界を跨ぐ地区は誤差あり（概算）。
浸水深ランク: 1:<0.5m 2:0.5-3m 3:3-5m 4:5-10m 5:10-20m 6:>20m（A31aコード）
"""

import json
import math
import time
import urllib.parse
import urllib.request
from pathlib import Path

import pandas as pd
from shapely.geometry import Point, shape

from fetch import api_key

HERE = Path(__file__).parent
GEO_CACHE = HERE / "data" / "geocode.json"
HAZ_DIR = HERE / "data" / "hazard"
GSI = "https://msearch.gsi.go.jp/address-search/AddressSearch?q={q}"
XKT = ("https://www.reinfolib.mlit.go.jp/ex-api/external/XKT026"
       "?response_format=geojson&z={z}&x={x}&y={y}")
Z = 14


def get(url: str, headers: dict | None = None) -> bytes:
    req = urllib.request.Request(url, headers=headers or {})
    for attempt in range(4):
        try:
            with urllib.request.urlopen(req, timeout=120) as res:
                raw = res.read()
            if raw[:2] == b"\x1f\x8b":
                import gzip
                raw = gzip.decompress(raw)
            return raw
        except Exception:
            if attempt == 3:
                raise
            time.sleep(2 ** (attempt + 1))


def geocode(districts: pd.DataFrame) -> dict[str, tuple[float, float]]:
    cache = json.loads(GEO_CACHE.read_text()) if GEO_CACHE.exists() else {}
    for _, row in districts.iterrows():
        key = f"{row['Municipality']}|{row['DistrictName']}"
        if key in cache:
            continue
        q = urllib.parse.quote(f"熊本県{row['Municipality']}{row['DistrictName']}")
        try:
            hits = json.loads(get(GSI.format(q=q)))
        except Exception:
            hits = []
        cache[key] = hits[0]["geometry"]["coordinates"] if hits else None
        time.sleep(0.25)
        if len(cache) % 50 == 0:
            GEO_CACHE.write_text(json.dumps(cache, ensure_ascii=False))
            print(f"geocode {len(cache)}/{len(districts)}")
    GEO_CACHE.write_text(json.dumps(cache, ensure_ascii=False))
    return cache


def tile_of(lon: float, lat: float, z: int = Z) -> tuple[int, int]:
    n = 2 ** z
    x = int((lon + 180) / 360 * n)
    lat_r = math.radians(lat)
    y = int((1 - math.asinh(math.tan(lat_r)) / math.pi) / 2 * n)
    return x, y


def fetch_hazard(tiles: set[tuple[int, int]], key: str) -> list[dict]:
    HAZ_DIR.mkdir(parents=True, exist_ok=True)
    feats = []
    for i, (x, y) in enumerate(sorted(tiles)):
        dest = HAZ_DIR / f"26_{Z}_{x}_{y}.json"
        if dest.exists():
            gj = json.loads(dest.read_text())
        else:
            raw = get(XKT.format(z=Z, x=x, y=y),
                      {"Ocp-Apim-Subscription-Key": key})
            gj = json.loads(raw) if raw.strip() else {"features": []}
            dest.write_text(json.dumps(gj, ensure_ascii=False))
            time.sleep(0.4)
        feats.extend(gj.get("features") or [])
        if (i + 1) % 25 == 0:
            print(f"hazard tiles {i+1}/{len(tiles)}: 累計{len(feats)}ポリゴン")
    return feats


def flood_rank(lon: float, lat: float, feats: list[dict]) -> int:
    p = Point(lon, lat)
    rank = 0
    for f in feats:
        if f["geometry"] and shape(f["geometry"]).contains(p):
            rank = max(rank, int(f["properties"].get("A31a_205") or 0))
    return rank


if __name__ == "__main__":
    df = pd.read_csv(HERE / "data" / "trades_kumamoto.csv", low_memory=False)
    city = df[df["Municipality"].astype(str).str.startswith("熊本市")].copy()
    districts = city[["Municipality", "DistrictName"]].dropna().drop_duplicates()
    print(f"熊本市 {len(city)}件 / {len(districts)}地区をジオコーディング")
    cache = geocode(districts)
    ok = {k: v for k, v in cache.items() if v}
    print(f"ジオコーディング成功 {len(ok)}/{len(cache)}")

    tiles = {tile_of(lon, lat) for lon, lat in ok.values()}
    print(f"対象タイル {len(tiles)}枚 (z={Z})")
    feats = fetch_hazard(tiles, api_key())
    print(f"浸水想定ポリゴン {len(feats)}件")

    ranks = {k: flood_rank(lon, lat, feats) for k, (lon, lat) in ok.items()}
    city["dkey"] = city["Municipality"] + "|" + city["DistrictName"].fillna("")
    city["flood_rank"] = city["dkey"].map(ranks)
    city = city.dropna(subset=["flood_rank"])
    city["TradePrice"] = pd.to_numeric(city["TradePrice"], errors="coerce")
    city["Area"] = pd.to_numeric(city["Area"], errors="coerce")
    city["unit_man_m2"] = city["TradePrice"] / city["Area"] / 1e4

    n_in = sum(1 for r in ranks.values() if r > 0)
    print(f"\n浸水想定区域内の地区: {n_in}/{len(ranks)} ({n_in/len(ranks):.0%})")
    for jtype in ("宅地(土地と建物)", "宅地(土地)", "中古マンション等"):
        sub = city[city["Type"] == jtype].dropna(subset=["unit_man_m2"])
        out_ = sub[sub["flood_rank"] == 0]["unit_man_m2"]
        in_ = sub[sub["flood_rank"] > 0]["unit_man_m2"]
        deep = sub[sub["flood_rank"] >= 3]["unit_man_m2"]
        print(f"{jtype}: 区域外 {out_.median():.1f}万円/m2 (n={len(out_)}) / "
              f"区域内 {in_.median():.1f} (n={len(in_)}) / "
              f"深い(3m+) {deep.median():.1f} (n={len(deep)})")
    city.to_csv(HERE / "data" / "kumamoto_city_flood.csv", index=False)
    print("-> data/kumamoto_city_flood.csv")
