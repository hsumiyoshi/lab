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

import pandas as pd

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
    for attempt in range(4):
        try:
            with urllib.request.urlopen(urllib.request.Request(URL, data=body), timeout=60) as res:
                html = res.read().decode("euc-jp", errors="replace")
            break
        except Exception:
            if attempt == 3:
                raise
            time.sleep(2 ** (attempt + 1))
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
            have = {(d.year, d.month) for d in old["date"]
                    if (d.year, d.month) < (today.year, today.month)}  # 当月は再取得
        else:
            old = pd.DataFrame()
        frames = [old[old["date"].dt.strftime("%Y-%m").isin(
            [f"{y}-{m:02d}" for (y, m) in have])] if not old.empty else old]
        for y, m in months:
            if (y, m) in have:
                continue
            df = fetch_month(code, y, m)
            print(f"{name} {y}-{m:02d}: {len(df)}行")
            frames.append(df)
            time.sleep(0.4)
        alldf = pd.concat([f for f in frames if len(f)], ignore_index=True)
        alldf["date"] = pd.to_datetime(alldf["date"])
        alldf = alldf.drop_duplicates(["date", "item", "origin"]).sort_values(["date", "origin"])
        alldf.to_csv(dest, index=False)
        print(f"  -> {dest} 計{len(alldf)}行")
