#!/usr/bin/env python3
"""不動産情報ライブラリAPIから不動産取引価格情報を取得する。

API: https://www.reinfolib.mlit.go.jp/ex-api/external/XIT001
     ?year=YYYY&quarter=N&area=NN（都道府県コード）
認証: ヘッダー Ocp-Apim-Subscription-Key（無料・申請制）
キーの置き場所（コミット禁止）:
  1. 環境変数 REINFOLIB_API_KEY
  2. このディレクトリの .apikey ファイル（.gitignore済み）

初回対象: 熊本（2026-07-28 M7.1の影響を今後の四半期で追うイベントスタディ台帳）
         ＋東京（対照群）。四半期ごとの生JSONをキャッシュし再実行は差分のみ。
"""

import gzip
import json
import os
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

import pandas as pd

HERE = Path(__file__).parent
RAW = HERE / "data" / "raw"
URL = "https://www.reinfolib.mlit.go.jp/ex-api/external/XIT001?{query}"

AREAS = {"43": "kumamoto", "13": "tokyo"}
YEARS = range(2020, 2027)  # 未公表の四半期は空配列が返るだけなので広めに


def api_key() -> str:
    key = os.environ.get("REINFOLIB_API_KEY", "").strip()
    if not key:
        f = HERE / ".apikey"
        if f.exists():
            key = f.read_text().strip()
    if not key:
        sys.exit("APIキーが未設定。環境変数 REINFOLIB_API_KEY か exp04_realestate/.apikey に置く")
    return key


def fetch_quarter(key: str, area: str, year: int, quarter: int) -> list[dict]:
    dest = RAW / f"{area}_{year}Q{quarter}.json"
    if dest.exists():
        return json.loads(dest.read_text())
    q = urllib.parse.urlencode({"year": year, "quarter": quarter, "area": area})
    req = urllib.request.Request(
        URL.format(query=q), headers={"Ocp-Apim-Subscription-Key": key})
    for attempt in range(4):
        try:
            with urllib.request.urlopen(req, timeout=120) as res:
                raw = res.read()
            if raw[:2] == b"\x1f\x8b":  # gzip圧縮で返る
                raw = gzip.decompress(raw)
            body = json.loads(raw)
            break
        except urllib.error.HTTPError as e:
            if e.code == 404:  # 未公表の期は404も空扱い
                body = {"data": []}
                break
            if attempt == 3:
                raise
            time.sleep(2 ** (attempt + 1))
        except Exception:
            if attempt == 3:
                raise
            time.sleep(2 ** (attempt + 1))
    rows = body.get("data") or []
    dest.write_text(json.dumps(rows, ensure_ascii=False))
    print(f"{AREAS.get(area, area)} {year}Q{quarter}: {len(rows)}件")
    time.sleep(0.5)
    return rows


if __name__ == "__main__":
    RAW.mkdir(parents=True, exist_ok=True)
    key = api_key()
    for area, name in AREAS.items():
        rows = []
        for year in YEARS:
            for quarter in (1, 2, 3, 4):
                for r in fetch_quarter(key, area, year, quarter):
                    r["_year"], r["_quarter"] = year, quarter
                    rows.append(r)
        df = pd.DataFrame(rows)
        dest = HERE / "data" / f"trades_{name}.csv"
        df.to_csv(dest, index=False)
        print(f"-> {dest} {len(df)}件")
