#!/usr/bin/env python3
"""FIRMS熱異常検知の取り込み（実験10の第2系統・要 FIRMS_MAP_KEY）。

GDACSが「イベント（人が定義した事象）」を数えるのに対し、FIRMSは
**衛星が観測した熱異常のピクセル数**という物理量を返す。同じ山火事という現象を
①人の判断が入った集計 ②機械の観測 の2系統で持てるのが狙い——
両者のズレ自体が観察対象になる（イベント化されない火災、逆に大きく報じられる小火災）。

鍵が無い環境では**何もせず正常終了**する（CIを落とさない）。

環境変数:
  FIRMS_MAP_KEY  https://firms.modaps.eosdis.nasa.gov/api/map_key/ で無料発行
"""

import csv
import io
import json
import os
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

UTC = timezone.utc
HERE = Path(__file__).resolve().parent
UA = "Mozilla/5.0 (personal research ledger; https://github.com/hsumiyoshi/lab)"
# 日本周辺（南西諸島〜北海道）と、比較用の全球オーストラリア西部
REGIONS = {"japan": "128,30,146,46"}
SOURCE = "VIIRS_SNPP_NRT"


def fetch(region: str, bbox: str, days: int = 1) -> list:
    key = os.environ.get("FIRMS_MAP_KEY", "").strip()
    if not key:
        print("FIRMS_MAP_KEY 未設定 → スキップ（鍵が入ればこの系統が自動で走り出す）")
        return []
    url = f"https://firms.modaps.eosdis.nasa.gov/api/area/csv/{key}/{SOURCE}/{bbox}/{days}"
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=90) as r:
        text = r.read().decode("utf-8", errors="replace")
    rows = list(csv.DictReader(io.StringIO(text)))
    print(f"FIRMS {region}: {len(rows)}検知")
    return rows


def run():
    out_f = HERE / "data" / "firms_daily.json"
    hist = json.loads(out_f.read_text()) if out_f.exists() else {}
    changed = False
    for region, bbox in REGIONS.items():
        rows = fetch(region, bbox, days=2)
        if not rows:
            continue
        counts = {}
        for r in rows:
            d = r.get("acq_date")
            if d:
                counts[d] = counts.get(d, 0) + 1
        for d, c in counts.items():
            hist.setdefault(region, {})[d] = c
            changed = True
    if changed:
        out_f.parent.mkdir(exist_ok=True)
        out_f.write_text(json.dumps(hist, ensure_ascii=False, indent=1, sort_keys=True), encoding="utf-8")
        print(f"保存: {out_f.name}")


if __name__ == "__main__":
    run()
