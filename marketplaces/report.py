#!/usr/bin/env python3
"""台帳（ledger/ledger.tsv）から report.md を作る。手で書かない。

- 売り場ごとに、最新の日付の値を表にする
- 2時点以上ある項目は、前回との差分を「直近の動き」として横に出す
"""
from __future__ import annotations

import csv
from collections import defaultdict
from pathlib import Path

HERE = Path(__file__).resolve().parent
LEDGER = HERE / "ledger" / "ledger.tsv"
OUT = HERE / "report.md"


def load():
    with LEDGER.open(newline="") as f:
        return list(csv.DictReader(f, delimiter="\t"))


def fmt(v):
    try:
        x = float(v)
    except (TypeError, ValueError):
        return str(v)
    return f"{int(x):,}" if x == int(x) else f"{x:,.1f}"


def main():
    rows = load()
    dates = sorted({r["date"] for r in rows})
    latest, prev = dates[-1], (dates[-2] if len(dates) > 1 else None)
    by = defaultdict(dict)  # (source,key,metric) -> {date: value}
    names = {}
    for r in rows:
        by[(r["source"], r["key"], r["metric"])][r["date"]] = r["value"]
        names[(r["source"], r["key"])] = (r["name"], r["url"])

    out = [f"# 売り場の注文の種類（台帳から生成・最新 {latest}）\n",
           "手で編集しない。元は ledger/ledger.tsv。数字はすべて売り場の公開ページにある値。\n"]

    def table(source, metrics, title, sort_metric):
        keys = sorted({k for (s, k, m) in by if s == source},
                      key=lambda k: -float(by.get((source, k, sort_metric), {}).get(latest, 0) or 0))
        if not keys:
            return
        out.append(f"\n## {title}\n")
        head = "| 名前 | " + " | ".join(metrics) + (" | 前回比(" + sort_metric + ") |" if prev else " |")
        out.append(head)
        out.append("|" + "---|" * (len(metrics) + 1 + (1 if prev else 0)))
        for k in keys:
            name, url = names[(source, k)]
            cells = [fmt(by.get((source, k, m), {}).get(latest, "—")) for m in metrics]
            diff = ""
            if prev:
                a, b = by.get((source, k, sort_metric), {}).get(latest), by.get((source, k, sort_metric), {}).get(prev)
                diff = f" {float(a) - float(b):+,.0f} |" if a and b else " — |"
            out.append(f"| [{name}]({url}) | " + " | ".join(cells) + " |" + diff)

    table("coconala", ["track_record", "reviews_sum", "reviews_max", "price_median", "full_capacity"],
          "ココナラ（累計実績・1ページ目の評価件数の合計と最大・価格中央値・満枠の数）", "track_record")
    table("shopify", ["reviews", "rating"], "Shopify App Store（レビュー件数＝利用店舗数の代理）", "reviews")
    table("gumroad", ["items", "price_median_usd"], "Gumroad（カテゴリの商品点数・上位の価格中央値 USD）", "items")
    table("booth", ["items"], "BOOTH（カテゴリの商品点数）", "items")
    OUT.write_text("\n".join(out) + "\n")
    print(f"report.md を更新（{latest}、前回 {prev}）")


if __name__ == "__main__":
    main()
