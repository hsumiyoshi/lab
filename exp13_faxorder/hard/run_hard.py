#!/usr/bin/env python3
"""意地悪な注文書50行で、pipeline.py の精度を測る。

条件
- A_print: 画像（記入欄も活字）→ Tesseract（pipeline.read_lines）
- A_hand : 画像（記入欄は手書き風フォント）→ Tesseract（pipeline.read_lines）
- B      : 完全な読み手。シートに書かれた字そのまま（略記・誤字は残す。取消線の字は除く）を
           「品名 | 数量 | 納期」の行で渡す（pipeline.py --lines と同じ）。品番への変換と要確認の判定だけを測る

どの条件も pipeline.main() と同じ順で pipeline の関数を呼ぶ（read_lines → parse_row → Matcher.match）。
main() を通さないのは、行の位置（y）を残して正解行と対応させるため。review.html は書かない。

数え方
- 自動確定 = 確度0.9以上（確認画面の「一致」）。要確認 = 0.9未満
- 自信を持って誤った行 = 自動確定のうち、品番が違う行と、期待=要確認（規格が書かれていない）なのに決めた行
- 読み落とし = 出力のどの行にも対応しない正解行（確認画面に出てこない）
- 出力行と正解行の対応: 同じ行の位置（±25px）にあるか品名が近い（0.5以上）組のうち、
  位置・品名・数量の合計が最大になるように順序を保って取る（DP）
- 余計な行 = どの正解行にも対応しない出力行（見出しの誤読など）

使い方: python3 hard/run_hard.py [--conds A_print,A_hand,B]
出力: hard/results_<条件>.csv（行ごと）, hard/summary.csv（難しさごと）。標準出力に表
"""
from __future__ import annotations

import argparse
import csv
import difflib
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
EXP = HERE.parent
sys.path.insert(0, str(EXP))
import pipeline as P  # noqa: E402

CATALOG = {"架空": "catalog.csv", "富士パン粉": "catalog_fujipanko.csv",
           "オーサワ": "catalog_ohsawa.csv", "マイン": "catalog_mine.csv"}
KINDS = ["略記", "誤字・書き損じ", "同名で規格違い", "かすれ・ノイズ", "傾き・ずれ"]
CONDS = ["A_print", "A_hand", "B"]
ROW0, ROW_H = 336, 64  # make_hard.py の1行目の上端と行の高さ


def visible(s: str) -> str:
    return re.sub(r"~[^~]*~", "", s)


def grid_status(img: str) -> str:
    """read_lines() と同じ手順で罫線を探した結果。あり（横12本）/ 一部（n本）/ なし（ページ全体を行で読む）"""
    import cv2
    grid = P._table_grid(P.deskew(cv2.imread(img, cv2.IMREAD_GRAYSCALE)))
    if not grid:
        return "なし"
    return "あり" if len(grid[0]) >= 12 else f"一部（横{len(grid[0])}本）"


def run_pipeline(cond: str, rows: list[dict], sheet: str) -> tuple[list[dict], list[dict], str]:
    """pipeline.main() と同じ処理。戻り: (注文行, 読み取った全行, 罫線の状態)"""
    catalog = list(csv.DictReader(open(EXP / "sample" / CATALOG[rows[0]["商品一覧"]], newline="", encoding="utf-8")))
    m = P.Matcher(catalog, {})
    if cond == "B":
        lines = [dict(text=f"{visible(r['シート上の品名'])} | {visible(r['シート上の数量'])} | {r['シート上の納期']}",
                      conf=99.0, y=ROW0 + ROW_H * i) for i, r in enumerate(rows)]
        grid = ""
    else:
        img = str(HERE / "img" / ("hand" if cond == "A_hand" else "print") / f"{sheet}.png")
        lines = P.read_lines(img)
        grid = grid_status(img)
    out = []
    for ln in lines:
        p = P.parse_row(ln["text"])
        if not p:
            continue
        p.update(m.match(p["品名_読み取り"]))
        p["y"], p["読み取り行"] = ln["y"], ln["text"]
        out.append(p)
    return out, lines, grid


def pair_score(i: int, t: dict, o: dict) -> float:
    s = difflib.SequenceMatcher(None, P.norm(visible(t["シート上の品名"])), P.norm(o["品名_読み取り"])).ratio()
    q = 0.3 if str(o["数量"]) == t["数量"] else 0.0
    pos = max(0.0, 1 - abs(o["y"] - (ROW0 + ROW_H * i)) / ROW_H)
    # 同じ行の位置（±25px）にあれば品名が崩れていても対応させる（確認画面には出ている）。位置がずれていれば品名の近さで
    return s + q + pos * 0.5 if pos >= 0.6 or s >= 0.5 else 0.0


def align(truth: list[dict], out: list[dict]) -> dict[int, int]:
    """順序を保つ対応（DP）。戻り: {正解の添字: 出力の添字}"""
    n, m = len(truth), len(out)
    best = [[0.0] * (m + 1) for _ in range(n + 1)]
    for i in range(n - 1, -1, -1):
        for j in range(m - 1, -1, -1):
            s = pair_score(i, truth[i], out[j])
            best[i][j] = max(best[i + 1][j], best[i][j + 1], (s + best[i + 1][j + 1]) if s > 0 else 0)
    res, i, j = {}, 0, 0
    while i < n and j < m:
        s = pair_score(i, truth[i], out[j])
        if s > 0 and abs(best[i][j] - (s + best[i + 1][j + 1])) < 1e-9:
            res[i] = j
            i, j = i + 1, j + 1
        elif best[i][j] == best[i + 1][j]:
            i += 1
        else:
            j += 1
    return res


def evaluate(cond: str, truth: list[dict], names: dict) -> tuple[list[dict], list[dict]]:
    sheets: dict[str, list] = {}
    for r in truth:
        sheets.setdefault(r["用紙"], []).append(r)
    results, extras = [], []
    for sheet, rows in sheets.items():
        out, lines, grid = run_pipeline(cond, rows, sheet)
        amap = align(rows, out)
        used = set(amap.values())
        for j, o in enumerate(out):
            if j not in used:
                extras.append(dict(条件=cond, 用紙=sheet, 読み取り行=o["読み取り行"], 品番=o["品番"], 確度=o["確度"],
                                   自動確定=o["確度"] >= 0.9))
        for i, t in enumerate(rows):
            r = dict(id=t["id"], 難しさ=t["難しさ"], 商品一覧=t["商品一覧"], 用紙=sheet, 罫線=grid,
                     シート上の品名=t["シート上の品名"], 正解品番=t["正解品番"], 期待=t["期待"])
            if i not in amap:
                y = ROW0 + ROW_H * i
                near = [ln["text"] for ln in lines if abs(ln["y"] - y) < ROW_H * 0.6]
                r.update(判定="読み落とし", 読み取り行=" / ".join(near) if near else "（この位置に読めた行なし）",
                         品名_読み取り="", 品番="", 確度="", 候補="", 品番正誤="", 候補に正解="",
                         数量="", 数量正誤="", 納期="", 納期正誤="", 誤確定="", 理由="")
                results.append(r)
                continue
            o = out[amap[i]]
            auto = o["確度"] >= 0.9
            wrong = auto and (t["期待"] == "要確認" or o["品番"] != t["正解品番"])
            reason = ""
            if wrong and t["期待"] == "要確認":
                hit = "（たまたま正解と同じ）" if o["品番"] == t["正解品番"] else ""
                reason = f"規格が書かれていないのに {o['品番']} {names[o['品番']]} に決めた{hit}"
            elif wrong:
                reason = f"「{o['品名_読み取り']}」→ {o['品番']} {names.get(o['品番'], '')}（正解 {t['正解品番']} {names[t['正解品番']]}）"
            r.update(判定="自動確定" if auto else "要確認", 読み取り行=o["読み取り行"], 品名_読み取り=o["品名_読み取り"],
                     品番=o["品番"], 確度=o["確度"], 候補=" ".join(o["候補"]), 品番正誤=o["品番"] == t["正解品番"],
                     候補に正解=t["正解品番"] in o["候補"] or o["品番"] == t["正解品番"],
                     数量=o["数量"], 数量正誤=str(o["数量"]) == t["数量"], 納期=o["納期"], 納期正誤=o["納期"] == t["納期"],
                     誤確定=wrong, 理由=reason)
            results.append(r)
    return results, extras


def summarize(cond: str, results: list[dict], extras: list[dict]) -> list[dict]:
    rows = []
    for kind in KINDS + ["計"]:
        rs = [r for r in results if kind == "計" or r["難しさ"] == kind]
        found = [r for r in rs if r["判定"] != "読み落とし"]
        auto = [r for r in rs if r["判定"] == "自動確定"]
        ok = [r for r in auto if not r["誤確定"]]
        rows.append(dict(
            条件=cond, 難しさ=kind, 行数=len(rs),
            自動確定=len(auto), 自動確定の正解=len(ok),
            自動確定の正解率=f"{len(ok)}/{len(auto)}" if auto else "-",
            要確認=sum(r["判定"] == "要確認" for r in rs), 読み落とし=sum(r["判定"] == "読み落とし" for r in rs),
            自信を持って誤った行=sum(bool(r["誤確定"]) for r in rs),
            要確認のうち候補に正解=sum(r["判定"] == "要確認" and r["候補に正解"] is True for r in rs),
            数量正解=f"{sum(r['数量正誤'] is True for r in found)}/{len(found)}",
            納期正解=f"{sum(r['納期正誤'] is True for r in found)}/{len(found)}",
            品番は正しく自動確定したが数量か納期が違う=sum(not r["誤確定"] and not (r["数量正誤"] and r["納期正誤"]) for r in auto),
            余計な行=len(extras) if kind == "計" else "",
            余計な行の自動確定=sum(e["自動確定"] for e in extras) if kind == "計" else "",
        ))
    return rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--conds", default=",".join(CONDS))
    a = ap.parse_args()
    truth = list(csv.DictReader(open(HERE / "truth.csv", newline="", encoding="utf-8")))
    names = {}
    for f in CATALOG.values():
        names.update({r["品番"]: r["品名"] for r in csv.DictReader(open(EXP / "sample" / f, newline="", encoding="utf-8"))})
    summary = []
    for cond in a.conds.split(","):
        results, extras = evaluate(cond, truth, names)
        with open(HERE / f"results_{cond}.csv", "w", newline="", encoding="utf-8") as fh:
            w = csv.DictWriter(fh, fieldnames=list(results[0].keys()))
            w.writeheader()
            w.writerows(results)
        s = summarize(cond, results, extras)
        summary += s
        print(f"\n## {cond}")
        print("| 難しさ | 自動確定の正解率 | 確認に回った | 読み落とし | 自信を持って誤った | 数量 | 納期 | 品番は正しいが数量か納期が違う自動確定 |")
        print("|---|---|---|---|---|---|---|---|")
        for x in s:
            print(f"| {x['難しさ']} | {x['自動確定の正解率']} | {x['要確認']}/{x['行数']} | {x['読み落とし']}/{x['行数']} "
                  f"| {x['自信を持って誤った行']} | {x['数量正解']} | {x['納期正解']} | {x['品番は正しく自動確定したが数量か納期が違う']} |")
        print(f"余計な行 {len(extras)}（うち自動確定 {sum(e['自動確定'] for e in extras)}）")
        for e in extras:
            print("  余計な行:", e)
        for r in results:
            if r["誤確定"]:
                print(f"  誤確定 {r['id']} {r['難しさ']}: {r['理由']}")
    if a.conds == ",".join(CONDS):
        with open(HERE / "summary.csv", "w", newline="", encoding="utf-8") as fh:
            w = csv.DictWriter(fh, fieldnames=list(summary[0].keys()))
            w.writeheader()
            w.writerows(summary)


if __name__ == "__main__":
    main()
