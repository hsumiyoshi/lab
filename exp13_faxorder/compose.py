#!/usr/bin/env python3
"""「前後」の1枚絵を作る。左: 受け取ったFAX注文書、右: 確認画面（品番つき）。

提案文に添付する画像。確認画面は review.html を Chromium で撮る。

使い方: python3 compose.py out/fax1.png out/fax1/review.html out/fax1/before_after.png [--title "..."]
"""
from __future__ import annotations

import argparse
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

FONT = "/usr/share/fonts/opentype/ipafont-gothic/ipagp.ttf"


def screenshot(html_path: Path, png: Path, width: int = 1400):
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        b = p.chromium.launch(executable_path="/opt/pw-browsers/chromium-1194/chrome-linux/chrome")
        pg = b.new_page(viewport={"width": width, "height": 900})
        pg.goto(html_path.resolve().as_uri())
        pg.wait_for_timeout(500)
        pg.screenshot(path=str(png), full_page=True)
        b.close()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("fax")
    ap.add_argument("review_html")
    ap.add_argument("out")
    ap.add_argument("--title", default="FAX注文書 → 品番つきの確認画面")
    a = ap.parse_args()
    out = Path(a.out)
    shot = out.with_name("review.png")
    screenshot(Path(a.review_html), shot)
    left = Image.open(a.fax).convert("RGB")
    right = Image.open(shot).convert("RGB")
    H = 1100
    left = left.resize((int(left.width * H / left.height), H))
    right = right.resize((int(right.width * H / right.height), H))
    gap, top = 40, 90
    W = left.width + gap + right.width + 80
    canvas = Image.new("RGB", (W, H + top + 40), (246, 246, 242))
    d = ImageDraw.Draw(canvas)
    f = ImageFont.truetype(FONT, 30)
    fs = ImageFont.truetype(FONT, 22)
    d.text((40, 24), a.title, font=f, fill=(29, 36, 51))
    d.text((40, 64), "左: 受け取ったFAX注文書（架空）　→　右: 読み取り結果を品番に直し、人が確認する画面。確認後にCSVを書き出す", font=fs, fill=(91, 100, 119))
    canvas.paste(left, (40, top))
    canvas.paste(right, (40 + left.width + gap, top))
    d.rectangle([40, top, 40 + left.width, top + H], outline=(217, 220, 227), width=2)
    d.rectangle([40 + left.width + gap, top, 40 + left.width + gap + right.width, top + H], outline=(217, 220, 227), width=2)
    canvas.save(out)
    print(out)


if __name__ == "__main__":
    main()
