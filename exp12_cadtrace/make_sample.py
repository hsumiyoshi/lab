#!/usr/bin/env python3
"""試験用の架空の間取り図を描く（権利の問題を避けるため、実在の図面は使わない）。

出力: sample/plan.png（1200x900、黒線の壁・建具・部屋名）
"""
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

OUT = Path(__file__).resolve().parent / "sample"


def main():
    OUT.mkdir(exist_ok=True)
    img = Image.new("L", (1200, 900), 255)
    d = ImageDraw.Draw(img)
    W = 10  # 外壁の太さ
    # 外壁
    d.rectangle([100, 100, 1100, 800], outline=0, width=W)
    # 内壁（太さ6）
    for x1, y1, x2, y2 in [(500, 100, 500, 800), (100, 450, 500, 450), (500, 350, 1100, 350),
                           (800, 350, 800, 800)]:
        d.line([x1, y1, x2, y2], fill=0, width=6)
    # 開口（壁を白で消す）と建具の弧
    d.line([500, 520, 500, 620], fill=255, width=8)
    d.arc([400, 520, 600, 720], start=270, end=360, fill=0, width=2)
    d.line([650, 350, 750, 350], fill=255, width=8)
    d.arc([650, 250, 850, 450], start=90, end=180, fill=0, width=2)
    d.line([880, 800, 1000, 800], fill=255, width=12)  # 玄関
    # 窓（二重線）
    d.line([200, 100, 400, 100], fill=255, width=12)
    d.line([200, 96, 400, 96], fill=0, width=2)
    d.line([200, 104, 400, 104], fill=0, width=2)
    # 部屋名
    try:
        font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 28)
    except OSError:
        font = ImageFont.load_default()
    for text, xy in [("LDK 16.5", (230, 250)), ("Bedroom 6.0", (230, 600)), ("WIC", (600, 180)),
                     ("Bath", (900, 180)), ("Entrance", (880, 600)), ("Hall", (600, 600))]:
        d.text(xy, text, fill=0, font=font)
    img.save(OUT / "plan.png")
    print(OUT / "plan.png")


if __name__ == "__main__":
    main()
