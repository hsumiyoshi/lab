#!/usr/bin/env python3
"""JEPXスポット市場の約定結果CSVを取得する。

データソース: https://www.jepx.jp/electricpower/market-data/spot/
ファイルは年度単位（4月始まり）。翌日分の価格は前日夕方には確定して載る。

使い方:
    python3 fetch.py            # 今年度分を取得
    python3 fetch.py 2024 2025  # 指定年度分を取得
"""

import sys
import pathlib
import sys
import urllib.request

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "collector"))
from datetime import date
from pathlib import Path

DATA_DIR = Path(__file__).parent / "data"
URL = "https://www.jepx.jp/market/excel/spot_{fy}.csv"


def fiscal_year(d: date) -> int:
    return d.year if d.month >= 4 else d.year - 1


def fetch(fy: int) -> Path:
    DATA_DIR.mkdir(exist_ok=True)
    dest = DATA_DIR / f"spot_{fy}.csv"
    url = URL.format(fy=fy)
    print(f"fetch {url}")
    # 2026-08-24: HTTPは共通ランタイム経由（礼儀・バックオフ・429対応を1箇所に集約）。
    # **bytes版を使う**——cp932の往復デコードでCSVの中身が変わる事故を避けるため
    from runtime import fetch_bytes
    _payload = fetch_bytes(url, interval=2.0, retries=4)
    class _R:
        def read(self):
            return _payload
    with __import__("contextlib").nullcontext(_R()) as res:
        raw = res.read()
    if len(raw) < 1000:
        raise RuntimeError(f"{url}: 応答が小さすぎる（{len(raw)}bytes）。年度が存在しない可能性")
    dest.write_text(raw.decode("shift-jis"), encoding="utf-8")
    print(f"  -> {dest} ({len(raw):,} bytes)")
    return dest


if __name__ == "__main__":
    years = [int(a) for a in sys.argv[1:]] or [fiscal_year(date.today())]
    for fy in years:
        fetch(fy)
