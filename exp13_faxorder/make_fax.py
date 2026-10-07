#!/usr/bin/env python3
"""試験用の架空のFAX注文書を描く（実在の注文書は使わない）。

- catalog.csv（品番,品名,規格）から行を選び、品名は略した書き方（別名）も混ぜる
- FAXらしく、少しの傾き・ぼかし・ごま塩ノイズを乗せる

使い方: python3 make_fax.py catalog.csv out/fax.png [--rows 8] [--seed 1]
"""
from __future__ import annotations

import argparse
import csv
import random
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont

FONT = "/usr/share/fonts/opentype/ipafont-gothic/ipagp.ttf"


def alias(name: str, rng: random.Random) -> str:
    """事務員がFAXに書く略し方を真似る。半分はそのまま"""
    r = rng.random()
    if r < 0.5:
        return name
    n = name.replace("（", "(").replace("）", ")")
    if r < 0.7:
        return n.split("(")[0].strip()  # 括弧の規格を落とす
    if r < 0.85:
        return n[: max(4, len(n) * 2 // 3)]  # 後ろを省く
    return n.replace(" ", "").replace("・", "")


def fit(d, text: str, width: int, size: int = 30, floor: int = 22):
    """マスに収まる書き方を返す: (行のリスト, フォント)。

    人は長い品名を、字を小さくするか2行に折ってマスに収める。はみ出させない。
    まず1行のまま floor まで小さくし、それでも入らなければ2行に折る
    """
    for sz in range(size, floor - 1, -2):
        f = ImageFont.truetype(FONT, sz)
        if d.textlength(text, font=f) <= width:
            return [text], f
    for sz in range(26, floor - 1, -2):
        f = ImageFont.truetype(FONT, sz)
        cut = len(text) // 2
        sp = text.rfind(" ", 0, cut + 4)
        if sp > cut // 2:
            cut = sp
        a, b = text[:cut].strip(), text[cut:].strip()
        if max(d.textlength(a, font=f), d.textlength(b, font=f)) <= width:
            return [a, b], f
    return [text[: len(text) // 2], text[len(text) // 2 :]], ImageFont.truetype(FONT, floor)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("catalog")
    ap.add_argument("out")
    ap.add_argument("--rows", type=int, default=8)
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--customer", default="株式会社サンプル商店")
    a = ap.parse_args()
    rng = random.Random(a.seed)
    items = list(csv.DictReader(open(a.catalog, newline="")))
    rows = rng.sample(items, min(a.rows, len(items)))

    W, H = 1240, 1754  # A4 150dpi 相当
    img = Image.new("L", (W, H), 255)
    d = ImageDraw.Draw(img)
    f_big = ImageFont.truetype(FONT, 44)
    f = ImageFont.truetype(FONT, 30)
    f_s = ImageFont.truetype(FONT, 24)
    d.text((80, 70), "注 文 書", font=f_big, fill=0)
    d.text((80, 150), f"{a.customer} 御中", font=f, fill=0)
    d.text((800, 150), "発注日 2026/10/06", font=f_s, fill=0)
    d.text((800, 190), "FAX 03-0000-0000", font=f_s, fill=0)
    d.text((80, 200), "下記のとおり注文します。", font=f_s, fill=0)
    # 表
    top, left, right = 280, 80, 1160
    cols = [left, 680, 860, 1160]
    heads = ["品名", "数量", "納期"]
    d.rectangle([left, top, right, top + 56], outline=0, width=2)
    for i, h in enumerate(heads):
        d.text((cols[i] + 16, top + 12), h, font=f, fill=0)
    y = top + 56
    truth = []
    for it in rows:
        qty = rng.choice([1, 2, 3, 5, 6, 10, 12, 20, 24, 30])
        unit = rng.choice(["", "", "個", "ケース", "箱"])
        due = rng.choice(["", "", "10/8", "10/10", "至急", "来週"])
        shown = alias(it["品名"], rng)
        d.rectangle([left, y, right, y + 64], outline=0, width=1)
        lines, fn = fit(d, shown, cols[1] - cols[0] - 32)
        if len(lines) == 1:
            d.text((cols[0] + 16, y + 14), lines[0], font=fn, fill=0)
        else:
            for k, t in enumerate(lines):
                d.text((cols[0] + 16, y + 4 + k * 28), t, font=fn, fill=0)
        d.text((cols[1] + 16, y + 14), f"{qty}{unit}", font=f, fill=0)
        d.text((cols[2] + 16, y + 14), due, font=f, fill=0)
        truth.append(dict(shown=shown, 品番=it["品番"], 品名=it["品名"], 数量=qty, 単位=unit, 納期=due))
        y += 64
    for x in cols[1:3]:
        d.line([x, top, x, y], fill=0, width=1)
    d.text((80, y + 40), "※ いつもの通りでお願いします。担当: 山田", font=f_s, fill=0)

    # FAXらしさ: 傾き・ぼかし・ノイズ
    img = img.rotate(rng.uniform(-0.8, 0.8), resample=Image.BICUBIC, fillcolor=255)
    img = img.filter(ImageFilter.GaussianBlur(0.6))
    arr = np.array(img)
    noise = rng.random()
    mask = np.random.default_rng(a.seed).random(arr.shape) < 0.002
    arr[mask] = 0
    arr = np.where(arr < 128, 0, 255).astype(np.uint8)  # FAXは2値
    out = Path(a.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    Image.fromarray(arr).save(out)
    with open(out.with_suffix(".truth.csv"), "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(truth[0].keys()))
        w.writeheader()
        w.writerows(truth)
    print(f"{out}（{len(rows)}行）と正解 {out.with_suffix('.truth.csv')}")


if __name__ == "__main__":
    main()
