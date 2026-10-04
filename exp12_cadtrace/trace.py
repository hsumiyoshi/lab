#!/usr/bin/env python3
"""間取り図の画像から壁の線を取り出し、DXFにする（試作）。

方針: 既存のAI変換サービス（DARE等）は規約上、代行への利用に許可が要り、APIも無い。
自分の仕組みとして回すため、線の抽出を自分で持つ。

手順:
1. 二値化 → 太い線（壁）だけをモルフォロジーで残す（細い文字・建具を落とす）
2. 水平・垂直の線分を取り出し、近い線分をつなぐ
3. ezdxf で LINE として書き出す。文字は OCR せず、位置だけ TEXT の目印にする（検品で人が入れる）
4. 元図に重ねた確認用PNGを出す

使い方: python3 trace.py sample/plan.png out/plan.dxf [--scale mm_per_px]
"""
from __future__ import annotations

import argparse
from pathlib import Path

import cv2
import ezdxf
import numpy as np


def extract_walls(gray: np.ndarray, min_thickness: int = 4) -> np.ndarray:
    _, binary = cv2.threshold(gray, 160, 255, cv2.THRESH_BINARY_INV)
    k = np.ones((min_thickness, min_thickness), np.uint8)
    walls = cv2.morphologyEx(binary, cv2.MORPH_OPEN, k)  # 細い線と文字が落ちる
    return walls


def segments_along(walls: np.ndarray, horizontal: bool, min_len: int = 30) -> list[tuple[int, int, int, int]]:
    """壁の画素から、水平か垂直の線分を取り出す（中心線）。"""
    ker = cv2.getStructuringElement(cv2.MORPH_RECT, (min_len, 1) if horizontal else (1, min_len))
    lines = cv2.morphologyEx(walls, cv2.MORPH_OPEN, ker)
    n, labels, stats, _ = cv2.connectedComponentsWithStats(lines, connectivity=8)
    segs = []
    for i in range(1, n):
        x, y, w, h, area = stats[i]
        if horizontal and w >= min_len:
            cy = y + h // 2
            segs.append((x, cy, x + w, cy))
        elif not horizontal and h >= min_len:
            cx = x + w // 2
            segs.append((cx, y, cx, y + h))
    return segs


def merge_collinear(segs: list[tuple[int, int, int, int]], horizontal: bool, gap: int = 12, tol: int = 6):
    """同じ行（列）にあり、端が gap 以下で離れた線分をつなぐ。"""
    key = (lambda s: s[1]) if horizontal else (lambda s: s[0])
    span = (lambda s: (s[0], s[2])) if horizontal else (lambda s: (s[1], s[3]))
    segs = sorted(segs, key=lambda s: (key(s), span(s)[0]))
    out: list[list[int]] = []
    for s in segs:
        if out:
            p = out[-1]
            if abs(key(s) - key(tuple(p))) <= tol and span(s)[0] - span(tuple(p))[1] <= gap:
                if horizontal:
                    p[2] = max(p[2], s[2])
                else:
                    p[3] = max(p[3], s[3])
                continue
        out.append(list(s))
    return [tuple(p) for p in out]


def write_dxf(segs: list[tuple[int, int, int, int]], height: int, scale: float, path: Path):
    doc = ezdxf.new("R2010")
    doc.layers.add("WALL", color=7)
    msp = doc.modelspace()
    for x1, y1, x2, y2 in segs:
        # 画像座標（y下向き）→ CAD座標（y上向き）。単位は mm
        msp.add_line((x1 * scale, (height - y1) * scale), (x2 * scale, (height - y2) * scale),
                     dxfattribs={"layer": "WALL"})
    doc.saveas(path)


def overlay(gray: np.ndarray, segs, path: Path):
    color = cv2.cvtColor(gray, cv2.COLOR_GRAY2BGR)
    for x1, y1, x2, y2 in segs:
        cv2.line(color, (x1, y1), (x2, y2), (0, 0, 255), 2)
    cv2.imwrite(str(path), color)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("image")
    ap.add_argument("dxf")
    ap.add_argument("--scale", type=float, default=10.0, help="1画素あたりの mm（既定10。縮尺が分かれば差し替える）")
    a = ap.parse_args()
    gray = cv2.imread(a.image, cv2.IMREAD_GRAYSCALE)
    walls = extract_walls(gray)
    h = merge_collinear(segments_along(walls, True), True)
    v = merge_collinear(segments_along(walls, False), False)
    segs = h + v
    out = Path(a.dxf)
    out.parent.mkdir(parents=True, exist_ok=True)
    write_dxf(segs, gray.shape[0], a.scale, out)
    overlay(gray, segs, out.with_suffix(".overlay.png"))
    print(f"線分 {len(segs)}本（水平{len(h)}・垂直{len(v)}）→ {out} / 確認用 {out.with_suffix('.overlay.png')}")


if __name__ == "__main__":
    main()
