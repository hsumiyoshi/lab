#!/usr/bin/env python3
"""意地悪な架空のFAX注文書を描く（truth.csv の50行 → 20枚）。

make_fax.py と同じ用紙の型（ただし罫線つき10行の欄を先に印刷し、上から埋める）に、
難しさの種類ごとの崩し方を足す。
- 略記・誤字・同名で規格違い: make_fax.py と同じ既定の崩し（傾き±0.8度・ぼかし・ごま塩0.2%・2値）
- かすれ・ノイズ: 濃淡むら＋黒点の欠け＋解像度落ち（横0.6・縦0.45倍で2値化して戻す）＋ごま塩0.8%＋縦すじ1本
- 傾き・ずれ: 用紙ごとに 2.0 / -2.8 / 3.5 / -4.5 度。字を罫線にかける（下・上・左・数量のはみ出し）

シート上の品名・数量の「~...~」は取消線で消した字（書き損じ）。

--style print: 記入欄も活字（IPA Pゴシック）
--style hand : 記入欄を手書き風フォント（Klee One）で、1字ずつ傾き・大きさ・位置を揺らす。
               本物の手書きではない（同じ字は同じ形になる）

使い方: python3 hard/make_hard.py hard/truth.csv hard/img/print --style print
"""
from __future__ import annotations

import argparse
import csv
import random
import re
import sys
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from make_fax import FONT, fit  # noqa: E402

HAND_FONT = "/usr/share/fonts/truetype/klee/KleeOne-SemiBold.ttf"  # apt: fonts-klee
SKEW = {"K_fict": 2.0, "K_fuji": -2.8, "K_ohsawa": 3.5, "K_mine": -4.5}
SENDER = {"架空": "ホテル架空荘", "富士パン粉": "架空食堂みなと", "オーサワ": "自然食品の店 架空堂", "マイン": "架空ビジネスホテル"}
W, H = 1240, 1754  # A4 150dpi 相当（make_fax.py と同じ）
TOP, LEFT, RIGHT = 280, 80, 1160
COLS = [LEFT, 680, 860, 1160]
ROW_H = 64
FORM_ROWS = 10  # 印刷済みの欄の行数


def visible(s: str) -> str:
    """取消線の字を除いた、読み手が読むべき字"""
    return re.sub(r"~[^~]*~", "", s)


def segments(s: str):
    """「~消した字~」を (字, 取消線か) に分ける"""
    parts = s.split("~")
    return [(p, i % 2 == 1) for i, p in enumerate(parts) if p]


class Pen:
    def __init__(self, img, style: str, rng: random.Random):
        self.img, self.d, self.style, self.rng = img, ImageDraw.Draw(img), style, rng

    def font(self, size: int):
        return ImageFont.truetype(HAND_FONT if self.style == "hand" else FONT, size)

    def width(self, text: str, f) -> float:
        return self.d.textlength(text, font=f) * (1.04 if self.style == "hand" else 1.0)

    def text(self, x: float, y: float, text: str, f) -> float:
        """字を書いて、書き終わりの x を返す"""
        if self.style != "hand":
            self.d.text((x, y), text, font=f, fill=0)
            return x + self.d.textlength(text, font=f)
        rng, sz = self.rng, f.size
        drift = rng.uniform(-0.02, 0.02)  # 書いているうちに行が少し上下する
        x0 = x
        for ch in text:
            adv = self.d.textlength(ch, font=f)
            if ch == " ":
                x += adv * rng.uniform(0.8, 1.3)
                continue
            pad = int(sz * 0.5)
            tile = Image.new("L", (int(adv) + 2 * pad, sz + 2 * pad), 0)
            ImageDraw.Draw(tile).text((pad, pad), ch, font=f, fill=255)
            s = rng.uniform(0.9, 1.12)
            tile = tile.resize((max(1, int(tile.width * s)), max(1, int(tile.height * s))), Image.BICUBIC)
            tile = tile.rotate(rng.uniform(-7, 7), resample=Image.BICUBIC)
            dy = rng.uniform(-3, 3) + (x - x0) * drift
            self.img.paste(0, (int(x - pad * s), int(y + dy - pad * s)), tile)
            x += adv * rng.uniform(0.95, 1.12)
        return x

    def marked(self, x: float, y: float, s: str, f):
        """取消線つきで書く（~...~ の部分に線を2本引く）"""
        for t, struck in segments(s):
            x1 = self.text(x, y, t, f)
            if struck:
                ym = y + f.size * 0.55
                for k in (-3, 3):
                    j = self.rng.uniform(-2, 2) if self.style == "hand" else 0
                    self.d.line([x - 3, ym + k + j, x1 + 3, ym + k - j], fill=0, width=2)
            x = x1 + (6 if struck else 0)


def degrade(img: Image.Image, kind: str, sheet: str, seed: int) -> np.ndarray:
    """崩し方は字の書き方と別の乱数で決める（活字と手書き風で同じ傾き・同じノイズになる）"""
    nrng = np.random.default_rng(seed)
    angle = SKEW.get(sheet, random.Random(seed).uniform(-0.8, 0.8))
    img = img.rotate(angle, resample=Image.BICUBIC, fillcolor=255)
    if kind != "かすれ・ノイズ":
        img = img.filter(ImageFilter.GaussianBlur(0.6))
        arr = np.array(img)
        arr[nrng.random(arr.shape) < 0.002] = 0
        return np.where(arr < 128, 0, 255).astype(np.uint8)
    img = img.filter(ImageFilter.GaussianBlur(0.8))
    arr = np.array(img).astype(np.float32)
    # 濃淡むら: 粗い乱数の面を引き伸ばし、インクの濃さに掛ける（薄い所は2値化で字が欠ける）
    field = Image.fromarray((nrng.random((14, 10)) * 255).astype(np.uint8)).resize((W, H), Image.BICUBIC)
    field = np.array(field).astype(np.float32) / 255
    arr = 255 - (255 - arr) * (0.6 + 0.4 * field)
    arr = np.where(arr < 170, 0, 255).astype(np.uint8)
    arr[(arr == 0) & (nrng.random(arr.shape) < 0.12)] = 255  # 黒点の欠け
    # 解像度落ち: 小さくして2値化し、受け側で引き伸ばす
    small = Image.fromarray(arr).resize((int(W * 0.6), int(H * 0.45)), Image.BOX)
    small = np.where(np.array(small) < 170, 0, 255).astype(np.uint8)
    arr = np.array(Image.fromarray(small).resize((W, H), Image.NEAREST))
    arr[nrng.random(arr.shape) < 0.008] = 0  # ごま塩
    x = int(nrng.integers(120, W - 120))  # 送り側の読み取り面の汚れ＝縦すじ
    arr[:, x:x + 2] = 0
    return arr


def draw_sheet(rows: list[dict], sheet: str, style: str, seed: int) -> tuple[np.ndarray, str]:
    rng = random.Random(seed)
    img = Image.new("L", (W, H), 255)
    pen = Pen(img, style, rng)
    d = pen.d
    f_big, f, f_s = (ImageFont.truetype(FONT, s) for s in (44, 30, 24))
    kind, cat = rows[0]["難しさ"], rows[0]["商品一覧"]
    d.text((80, 70), "注 文 書", font=f_big, fill=0)
    d.text((800, 80), "（試験用・架空）", font=f_s, fill=0)
    d.text((80, 150), "株式会社サンプル商店 御中", font=f, fill=0)
    d.text((800, 150), "発注日 2026/10/13", font=f_s, fill=0)
    d.text((800, 190), f"{SENDER[cat]}", font=f_s, fill=0)
    d.text((80, 200), "下記のとおり注文します。", font=f_s, fill=0)
    d.rectangle([LEFT, TOP, RIGHT, TOP + 56], outline=0, width=2)
    for i, h in enumerate(["品名", "数量", "納期"]):
        d.text((COLS[i] + 16, TOP + 12), h, font=f, fill=0)
    # 用紙は罫線つきの10行の欄（印刷済み）。上から埋め、残りは空欄のまま
    bottom = TOP + 56 + FORM_ROWS * ROW_H
    for k in range(FORM_ROWS):
        d.rectangle([LEFT, TOP + 56 + k * ROW_H, RIGHT, TOP + 56 + (k + 1) * ROW_H], outline=0, width=1)
    for x in COLS[1:3]:
        d.line([x, TOP, x, bottom], fill=0, width=1)
    y = TOP + 56
    fw = pen.font(30)
    for r in rows:
        shift = r["ずれ"]
        dx = -30 if shift == "左" else 0
        dy = 24 if shift == "下" else -20 if shift == "上" else 0
        name = r["シート上の品名"]
        if "~" in name:  # 書き損じは短い品名だけ。1行で書く
            pen.marked(COLS[0] + 16 + dx, y + 14 + dy, name, fw)
        else:
            lines, fn = fit(d, name, int((COLS[1] - COLS[0] - 32) / (1.04 if style == "hand" else 1)),
                            font=HAND_FONT if style == "hand" else FONT)
            if len(lines) == 1:
                pen.text(COLS[0] + 16 + dx, y + 14 + dy, lines[0], fn)
            else:
                for k, t in enumerate(lines):
                    pen.text(COLS[0] + 16 + dx, y + 4 + k * 28 + dy, t, fn)
        qty = r["シート上の数量"]
        qx = COLS[1] + 16
        if shift == "数量はみ出し":  # 数量が納期の列にまたがる
            qx = COLS[2] - pen.width(visible(qty), fw) * 0.6
        pen.marked(qx, y + 14 + (dy if shift in ("下", "上") else 0), qty, fw)
        if r["シート上の納期"]:
            pen.text(COLS[2] + 16, y + 14 + (dy if shift in ("下", "上") else 0), r["シート上の納期"], fw)
        y += ROW_H
    d.text((80, bottom + 40), "※ いつもの通りでお願いします。", font=f_s, fill=0)
    return degrade(img, kind, sheet, seed), kind


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("truth")
    ap.add_argument("out_dir")
    ap.add_argument("--style", choices=["print", "hand"], default="print")
    ap.add_argument("--seed", type=int, default=13)
    a = ap.parse_args()
    if a.style == "hand" and not Path(HAND_FONT).exists():
        sys.exit(f"手書き風フォントがありません: {HAND_FONT}（apt install fonts-klee）")
    rows = list(csv.DictReader(open(a.truth, newline="", encoding="utf-8")))
    sheets: dict[str, list] = {}
    for r in rows:
        sheets.setdefault(r["用紙"], []).append(r)
    out = Path(a.out_dir)
    out.mkdir(parents=True, exist_ok=True)
    for i, (sheet, rs) in enumerate(sheets.items()):
        arr, _ = draw_sheet(rs, sheet, a.style, a.seed * 100 + i)
        Image.fromarray(arr).save(out / f"{sheet}.png", optimize=True)
    print(f"{out}: {len(sheets)}枚・{len(rows)}行（{a.style}）")


if __name__ == "__main__":
    main()
