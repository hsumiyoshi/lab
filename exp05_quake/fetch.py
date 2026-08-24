#!/usr/bin/env python3
"""P2P地震情報APIから地震履歴（気象庁発表の中継）を取得する。

API: https://api.p2pquake.net/v2/history?codes=551&limit=100&offset=N
code 551 = 地震情報。1リクエスト100件、オフセットで遡る。
"""

import json
import time
import pathlib
import sys
import urllib.request
from pathlib import Path

import pandas as pd

import pathlib as _pl, sys
sys.path.insert(0, str(_pl.Path(__file__).resolve().parent.parent / "collector"))  # 共通ランタイム

DATA = Path(__file__).parent / "data"
URL = "https://api.p2pquake.net/v2/history?codes=551&limit=100&offset={off}"
PAGES = 20  # 100件×20 = 直近2,000イベント


def _rt():
    """共通ランタイムのfetch（礼儀・バックオフ・429対応）を借りる。"""
    sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "collector"))
    from runtime import fetch as rt_fetch
    return rt_fetch


def fetch() -> pd.DataFrame:
    rows = []
    for p in range(PAGES):
        url = URL.format(off=p * 100)
        req = urllib.request.Request(url, headers={"User-Agent": "lab-exp05/0.1 (personal research)"})
        for attempt in range(4):
            try:
                from runtime import fetch as _rt
                _txt = _rt(url, interval=1.0, retries=4)
                import io as _io, contextlib as _ctx
                with _ctx.nullcontext(_io.StringIO(_txt)) as res:
                    batch = json.load(res)
                break
            except Exception:
                if attempt == 3:
                    raise
                time.sleep(2 ** (attempt + 1))
        for ev in batch:
            eq = ev.get("earthquake") or {}
            hypo = eq.get("hypocenter") or {}
            rows.append({
                "time": eq.get("time"),
                "name": hypo.get("name"),
                "lat": hypo.get("latitude"),
                "lon": hypo.get("longitude"),
                "depth_km": hypo.get("depth"),
                "mag": hypo.get("magnitude"),
                "max_scale": eq.get("maxScale"),  # 10=震度1, 45=5弱, 70=震度7
            })
        print(f"page {p+1}/{PAGES}: 累計{len(rows)}件")
        time.sleep(0.3)
    df = pd.DataFrame(rows)
    df["time"] = pd.to_datetime(df["time"], format="%Y/%m/%d %H:%M:%S", errors="coerce")
    df = df.dropna(subset=["time"]).drop_duplicates(subset=["time", "name", "mag"])
    return df.sort_values("time")


if __name__ == "__main__":
    DATA.mkdir(exist_ok=True)
    df = fetch()
    # APIは直近約2,000件しか遡れないため、コミット対象のarchive/へ累積マージする
    archive = Path(__file__).parent / "archive" / "quakes.csv"
    archive.parent.mkdir(exist_ok=True)
    if archive.exists():
        old = pd.read_csv(archive, parse_dates=["time"])
        df = (pd.concat([old, df], ignore_index=True)
              .drop_duplicates(subset=["time", "name", "mag"]).sort_values("time"))
    df.to_csv(archive, index=False)
    (DATA / "quakes.csv").write_text(archive.read_text())  # 既存プロットの互換用
    print(f"-> {archive} {len(df)}件 ({df['time'].min()} 〜 {df['time'].max()})")