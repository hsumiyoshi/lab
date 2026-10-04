#!/usr/bin/env python3
"""変換後のDXFを機械で検品し、人が見るべき所を列挙する。

見るもの:
- 線分の数・全長・外形の大きさ（縮尺の妥当性: 住宅なら外形は 5〜20m）
- 短すぎる線分（ゴミの可能性）
- 端が他の線に届いていない線（途切れ・開口の候補。人が「開口」か「途切れ」かを決める）

使い方: python3 check.py out/plan.dxf
"""
from __future__ import annotations

import math
import sys

import ezdxf


def main(path: str):
    doc = ezdxf.readfile(path)
    lines = [(e.dxf.start.x, e.dxf.start.y, e.dxf.end.x, e.dxf.end.y) for e in doc.modelspace().query("LINE")]
    if not lines:
        print("LINE が1本も無い。変換に失敗している")
        sys.exit(1)
    xs = [c for l in lines for c in (l[0], l[2])]
    ys = [c for l in lines for c in (l[1], l[3])]
    w, h = max(xs) - min(xs), max(ys) - min(ys)
    lengths = [math.dist((l[0], l[1]), (l[2], l[3])) for l in lines]
    short = [l for l, ln in zip(lines, lengths) if ln < 150]  # 15cm未満
    ends = [(l[0], l[1]) for l in lines] + [(l[2], l[3]) for l in lines]

    def touched(p, tol=60.0):
        for l in lines:
            if (abs(p[0] - l[0]) < 1e-6 and abs(p[1] - l[1]) < 1e-6) or (abs(p[0] - l[2]) < 1e-6 and abs(p[1] - l[3]) < 1e-6):
                continue
            # 点と線分の距離
            x1, y1, x2, y2 = l
            dx, dy = x2 - x1, y2 - y1
            L2 = dx * dx + dy * dy
            t = 0 if L2 == 0 else max(0, min(1, ((p[0] - x1) * dx + (p[1] - y1) * dy) / L2))
            if math.dist(p, (x1 + t * dx, y1 + t * dy)) <= tol:
                return True
        return False

    dangling = [p for p in ends if not touched(p)]
    print(f"線分 {len(lines)}本 / 全長 {sum(lengths)/1000:.1f} m / 外形 {w/1000:.1f} x {h/1000:.1f} m")
    ok_size = 4000 <= w <= 25000 and 4000 <= h <= 25000
    print(f"縮尺: {'妥当' if ok_size else '要確認（住宅の外形は4〜25mのはず）'}")
    print(f"短い線分（15cm未満）: {len(short)}本 → ゴミか建具の一部。人が見る")
    print(f"端が浮いている点: {len(dangling)}個 → 開口（窓・戸）か途切れ。人が決める")
    for p in dangling[:20]:
        print(f"  ({p[0]/1000:.2f}, {p[1]/1000:.2f}) m")
    sys.exit(0 if ok_size else 2)


if __name__ == "__main__":
    main(sys.argv[1])
