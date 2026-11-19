#!/usr/bin/env python3
"""ベジ探（alic）から東京都の青果日別卸売データを取得する。

データ: 卸売市場別入荷量・価格（日別）— 東京都（豊洲・大田・豊島・淀橋の計）
出典: https://vegetan.alic.go.jp/ （出所: 東京都中央卸売市場データ）
取得可能期間: 2024年〜（ベジ探の日別フォームの提供範囲）

礼儀: リクエスト間に0.4秒スリープ。取得済み月はスキップ（冪等）。
"""

import re
import time
import urllib.parse
import urllib.request
from datetime import date
from pathlib import Path

import pathlib as _pl, sys
sys.path.insert(0, str(_pl.Path(__file__).resolve().parent.parent / "collector"))

import pandas as pd
from runtime import CollectError

DATA = Path(__file__).parent / "data"
URL = "https://vegetan.alic.go.jp/vegetan/sch7.do"
CITY_TOKYO = "101"

ITEMS = {
    "34100": "きゅうり",
    "34400": "トマト",
    "31700": "キャベツ",
    "33400": "レタス",
}


def fetch_month(item: str, year: int, month: int) -> pd.DataFrame:
    body = urllib.parse.urlencode({
        "CMD": "search", "searchFlg": "1", "outPutKbn": "4",
        "baseYear": str(year), "baseMonthFr": str(month),
        "city": CITY_TOKYO, "hinmokuRuibetu": "-1", "hinmokuCode": item,
    }).encode()
    # 2026-08-24: 共通ランタイム経由（POST）。Haruki方針「基盤に寄せる安心感を優先」により、
    # 1ソースのためでもランタイム側にPOSTを足して統一した。礼儀・バックオフ・429対応が付く
    from runtime import fetch_bytes
    html = fetch_bytes(URL, data=body, interval=2.0, retries=4, timeout=60).decode("euc-jp", errors="replace")
    tables = re.findall(r"<table[^>]*>(.*?)</table>", html, re.S)
    if len(tables) < 2:
        return pd.DataFrame()

    def parse(table_html: str) -> dict:
        """{(origin, day): value} を返す。月内の前半/後半ブロックに対応"""
        out = {}
        days = []
        for row in re.findall(r"<tr[^>]*>(.*?)</tr>", table_html, re.S):
            cells = [re.sub(r"\s|,", "", re.sub(r"<[^>]+>", "", c))
                     for c in re.findall(r"<t[hd][^>]*>(.*?)</t[hd]>", row, re.S)]
            if not cells:
                continue
            if any(c.endswith("日") for c in cells[1:]):
                days = [int(c[:-1]) if c.endswith("日") else None for c in cells[1:]]
                continue
            origin = cells[0]
            for d, v in zip(days, cells[1:]):
                if d and v and v.isdigit():
                    out[(origin, d)] = int(v)
        return out

    qty, price = parse(tables[0]), parse(tables[1])
    rows = []
    for (origin, d), p in price.items():
        rows.append({"date": date(year, month, d), "item": ITEMS[item],
                     "origin": origin, "price": p, "qty": qty.get((origin, d))})
    return pd.DataFrame(rows)


if __name__ == "__main__":
    DATA.mkdir(exist_ok=True)
    today = date.today()
    months = [(y, m) for y in (2024, 2025, 2026) for m in range(1, 13)
              if (y, m) <= (today.year, today.month)]
    for code, name in ITEMS.items():
        dest = DATA / f"veg_{code}.csv"
        have = set()
        if dest.exists():
            old = pd.read_csv(dest, parse_dates=["date"])
            # **当月と前月は再取得する**（2026-09-01）。当月だけだと、月をまたいだ
            # 瞬間に「その月は取得済み」と見なされ、月末数日が永久に欠ける。
            # 実際に2026-08-10で4ファイルとも止まっていた（ベジ探は2〜3日遅れ、
            # CIは水曜なので、当月扱いのうちに月末まで埋まりきらない）。
            # 前月まで見れば、月が閉じた後に一度だけ取り直す機会がある。
            prev = (today.year, today.month - 1) if today.month > 1 else (today.year - 1, 12)
            have = {(d.year, d.month) for d in old["date"]
                    if (d.year, d.month) < prev}
        else:
            old = pd.DataFrame()
        frames = [old[old["date"].dt.strftime("%Y-%m").isin(
            [f"{y}-{m:02d}" for (y, m) in have])] if not old.empty else old]
        for y, m in months:
            if (y, m) in have:
                continue
            try:
                df = fetch_month(code, y, m)
            except CollectError as e:
                # 2026-11-19: ベジ探の検索が、ブラウザで開いても 404 になった（サイト側の故障）。
                # 取れない月は、持っている行を残して続ける。落とすと週の採点ごと止まり、
                # 再取得の対象の当月・前月の行まで消える。止まった事は警告の注記で残す
                kept = old[old["date"].dt.strftime("%Y-%m") == f"{y}-{m:02d}"] if not old.empty else old
                print(f"!! {name} {y}-{m:02d} の取得に失敗: {e} → 持っている {len(kept)}行で続行")
                print(f"::warning title=ベジ探の取得に失敗::{name} {y}-{m:02d}: {e}")
                frames.append(kept)
                continue
            print(f"{name} {y}-{m:02d}: {len(df)}行")
            frames.append(df)
            time.sleep(0.4)
        frames = [f for f in frames if len(f)]
        if not frames:
            print(f"  -> {dest} は書かない（取れた行も、持っている行も無い）")
            continue
        alldf = pd.concat(frames, ignore_index=True)
        alldf["date"] = pd.to_datetime(alldf["date"])
        alldf = alldf.drop_duplicates(["date", "item", "origin"]).sort_values(["date", "origin"])
        alldf.to_csv(dest, index=False)
        print(f"  -> {dest} 計{len(alldf)}行")
