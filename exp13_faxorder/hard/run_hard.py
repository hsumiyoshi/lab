#!/usr/bin/env python3
"""意地悪な注文書50行で、pipeline.py の精度を測る。

条件
- A_print: 画像（記入欄も活字）→ Tesseract（pipeline.read_lines）
- A_hand : 画像（記入欄は手書き風フォント）→ Tesseract（pipeline.read_lines）
- B      : 完全な読み手。シートに書かれた字そのまま（略記・誤字は残す。取消線の字は除く）を
           「品名 | 数量 | 納期」の行で渡す（pipeline.py --lines と同じ）。品番への変換と要確認の判定だけを測る

どの条件も pipeline.main() と同じ順で pipeline の関数を呼ぶ（read_sheet → build_rows（parse_line → Matcher.match → 判定））。
main() を通さないのは、行の位置（y）を残して正解行と対応させるため。review.html は書かない。

数え方
- 自動確定 = 判定が「一致」（確認画面でそのまま書き出せる行）。要確認 = 「要確認」と「不明」
- 自信を持って誤った行 = 品番が入った状態で出た行のうち、品番が違う行と、期待=要確認（規格が書かれていない）なのに
  品番を入れた行。品番が入った状態で出るのは「一致」の行だけ（要確認の行は品番を空で始める）。余計な行も数える
- 一致なのに数量か納期が違う = 「一致」の行のうち、数量か納期が正解と違う行（黙って通る数量・納期の誤り）
- 数量か納期の誤りに印なし = 確認画面に出た行のうち、数量か納期が正解と違うのに、そのマスに要確認の印が無い行
  （品番だけを直して通すと、誤った数量・納期が残る）
- 読み落とし = 出力のどの行にも対応しない正解行。用紙ごと要確認（確認画面の上に理由が出る）の用紙なら「表に出た」、
  そうでなければ「黙って消えた」
- 出力行と正解行の対応: 同じ行の位置（±25px）にあるか品名が近い（0.5以上）組のうち、
  位置・品名・数量の合計が最大になるように順序を保って取る（DP）
- 余計な行 = どの正解行にも対応しない出力行（見出しの誤読・空欄のごみなど）

使い方: python3 hard/run_hard.py [--conds A_print,A_hand,B]
出力: hard/results_<条件>.csv（行ごと）, hard/summary.csv（難しさごと）。標準出力に表
"""
from __future__ import annotations

import argparse
import csv
import datetime as dt
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


REF = dt.date(2026, 10, 13)  # 用紙の発注日（make_hard.py）＝受信日


def run_pipeline(cond: str, rows: list[dict], sheet: str) -> tuple[list[dict], list[dict], list[str]]:
    """pipeline.main() と同じ処理。戻り: (確認画面の行, 読み取った全行, 用紙の問題)"""
    catalog = list(csv.DictReader(open(EXP / "sample" / CATALOG[rows[0]["商品一覧"]], newline="", encoding="utf-8")))
    m = P.Matcher(catalog, {})
    if cond == "B":  # 字だけの入力（--lines と同じ）。読み取りの確度は無い
        lines = []
        for i, r in enumerate(rows):
            cells = [visible(r["シート上の品名"]), visible(r["シート上の数量"]), r["シート上の納期"]]
            lines.append(dict(cells=cells, confs=[None] * 3, ink=[bool(c) for c in cells], text=" | ".join(cells),
                              conf=None, y=ROW0 + ROW_H * i, flags=[]))
        issues: list[str] = []
    else:
        img = str(HERE / "img" / ("hand" if cond == "A_hand" else "print") / f"{sheet}.png")
        lines, issues = P.read_sheet(img)
    out = P.build_rows(lines, m, REF)
    for o in out:
        o["読み取り行"] = next((ln["text"] for ln in lines if ln.get("y") == o["y"]), "")
    return out, lines, issues


def qty_s(o: dict) -> str:
    return "" if o["数量"] is None else str(o["数量"])


def pair_score(i: int, t: dict, o: dict) -> float:
    s = difflib.SequenceMatcher(None, P.norm(visible(t["シート上の品名"])), P.norm(o["品名_読み取り"])).ratio()
    q = 0.3 if qty_s(o) == t["数量"] else 0.0
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


def evaluate(cond: str, truth: list[dict], names: dict) -> tuple[list[dict], list[dict], dict]:
    sheets: dict[str, list] = {}
    for r in truth:
        sheets.setdefault(r["用紙"], []).append(r)
    results, extras, sheet_issues = [], [], {}
    for sheet, rows in sheets.items():
        out, lines, issues = run_pipeline(cond, rows, sheet)
        sheet_issues[sheet] = issues
        amap = align(rows, out)
        used = set(amap.values())
        for j, o in enumerate(out):
            if j not in used:
                extras.append(dict(条件=cond, 用紙=sheet, 読み取り行=o["読み取り行"], 品名_読み取り=o["品名_読み取り"],
                                   品番=o["品番"], 判定=o["判定"], 自動確定=o["判定"] == "一致", 品番入り=bool(o["品番"])))
        for i, t in enumerate(rows):
            r = dict(id=t["id"], 難しさ=t["難しさ"], 商品一覧=t["商品一覧"], 用紙=sheet,
                     用紙ごと要確認=" / ".join(issues), シート上の品名=t["シート上の品名"], 正解品番=t["正解品番"], 期待=t["期待"])
            if i not in amap:
                y = ROW0 + ROW_H * i
                near = [ln["text"] for ln in lines if abs(ln.get("y", -999) - y) < ROW_H * 0.6]
                r.update(判定="読み落とし", 読み落としの扱い="用紙ごと要確認で表に出た" if issues else "黙って消えた",
                         読み取り行=" / ".join(near) if near else "（この位置に読めた行なし）",
                         品名_読み取り="", 品番="", 確度="", 根拠="", 候補="", 品番正誤="", 候補に正解="",
                         数量="", 数量正誤="", 納期="", 納期正誤="", 数量確度="", 納期確度="", 誤確定="", 一致で数量か納期が違う="",
                         数量か納期の誤りに印なし="", 理由="")
                results.append(r)
                continue
            o = out[amap[i]]
            auto = o["判定"] == "一致"
            filled = bool(o["品番"])
            wrong = filled and (t["期待"] == "要確認" or o["品番"] != t["正解品番"])
            q_ok, d_ok = qty_s(o) == t["数量"], o["納期"] == t["納期"]
            reason = " / ".join(f"{k}: {m}" for k, m in o["理由"])
            kinds = {k for k, _ in o["理由"]}
            unmarked = (not q_ok and "数量" not in kinds) or (not d_ok and "納期" not in kinds and "数量・納期も" not in reason)
            if wrong and t["期待"] == "要確認":
                hit = "（たまたま正解と同じ）" if o["品番"] == t["正解品番"] else ""
                reason = f"規格が書かれていないのに {o['品番']} {names[o['品番']]} に決めた{hit}"
            elif wrong:
                reason = f"「{o['品名_読み取り']}」→ {o['品番']} {names.get(o['品番'], '')}（正解 {t['正解品番']} {names[t['正解品番']]}）"
            r.update(判定="自動確定" if auto else "要確認", 読み落としの扱い="", 読み取り行=o["読み取り行"],
                     品名_読み取り=o["品名_読み取り"], 品番=o["品番"], 確度=o["確度"], 根拠=o["根拠"], 候補=" ".join(o["候補"]),
                     品番正誤=o["品番"] == t["正解品番"], 候補に正解=t["正解品番"] in o["候補"] or o["品番"] == t["正解品番"],
                     数量=qty_s(o), 数量正誤=q_ok, 納期=o["納期"], 納期正誤=d_ok,
                     数量確度=o["数量確度"] if o["数量確度"] is not None else "", 納期確度=o["納期確度"] if o["納期確度"] is not None else "",
                     誤確定=wrong,
                     一致で数量か納期が違う=auto and not (q_ok and d_ok), 数量か納期の誤りに印なし=unmarked, 理由=reason)
            results.append(r)
    return results, extras, sheet_issues


def summarize(cond: str, results: list[dict], extras: list[dict]) -> list[dict]:
    rows = []
    for kind in KINDS + ["計"]:
        rs = [r for r in results if kind == "計" or r["難しさ"] == kind]
        found = [r for r in rs if r["判定"] != "読み落とし"]
        auto = [r for r in rs if r["判定"] == "自動確定"]
        ok = [r for r in auto if not r["誤確定"]]
        miss = [r for r in rs if r["判定"] == "読み落とし"]
        rows.append(dict(
            条件=cond, 難しさ=kind, 行数=len(rs),
            自動確定=len(auto), 自動確定の正解=len(ok),
            自動確定の正解率=f"{len(ok)}/{len(auto)}" if auto else "-",
            要確認=sum(r["判定"] == "要確認" for r in rs), 読み落とし=len(miss),
            読み落とし_黙って消えた=sum(r["読み落としの扱い"] == "黙って消えた" for r in miss),
            自信を持って誤った行=sum(bool(r["誤確定"]) for r in rs),
            一致で数量か納期が違う=sum(bool(r["一致で数量か納期が違う"]) for r in rs),
            数量か納期の誤りに印なし=sum(r["数量か納期の誤りに印なし"] is True for r in rs),
            要確認のうち候補に正解=sum(r["判定"] == "要確認" and r["候補に正解"] is True for r in rs),
            数量正解=f"{sum(r['数量正誤'] is True for r in found)}/{len(found)}",
            納期正解=f"{sum(r['納期正誤'] is True for r in found)}/{len(found)}",
            余計な行=len(extras) if kind == "計" else "",
            余計な行の品番入り=sum(e["品番入り"] for e in extras) if kind == "計" else "",
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
        results, extras, issues = evaluate(cond, truth, names)
        with open(HERE / f"results_{cond}.csv", "w", newline="", encoding="utf-8") as fh:
            w = csv.DictWriter(fh, fieldnames=list(results[0].keys()))
            w.writeheader()
            w.writerows(results)
        s = summarize(cond, results, extras)
        summary += s
        print(f"\n## {cond}")
        print("| 難しさ | 自動確定の正解率 | 確認に回った | 読み落とし（うち黙って消えた） | 自信を持って誤った | 一致なのに数量か納期が違う | 数量か納期の誤りに印なし | 数量 | 納期 |")
        print("|---|---|---|---|---|---|---|---|---|")
        for x in s:
            print(f"| {x['難しさ']} | {x['自動確定の正解率']} | {x['要確認']}/{x['行数']} | {x['読み落とし']}/{x['行数']}（{x['読み落とし_黙って消えた']}） "
                  f"| {x['自信を持って誤った行']} | {x['一致で数量か納期が違う']} | {x['数量か納期の誤りに印なし']} | {x['数量正解']} | {x['納期正解']} |")
        print(f"余計な行 {len(extras)}（うち品番入り {sum(e['品番入り'] for e in extras)}）")
        for e in extras:
            print("  余計な行:", e)
        for sheet, iss in issues.items():
            if iss:
                print(f"  用紙ごと要確認 {sheet}: {' / '.join(iss)}")
        for r in results:
            if r["誤確定"]:
                print(f"  誤確定 {r['id']} {r['難しさ']}: {r['理由']}")
            if r["一致で数量か納期が違う"]:
                print(f"  一致で数量か納期が違う {r['id']}: 数量 {r['数量']}（正解 {truth[int(r['id'][1:]) - 1]['数量']}）"
                      f" 納期 {r['納期']}（正解 {truth[int(r['id'][1:]) - 1]['納期']}）")
            if r["判定"] == "読み落とし":
                print(f"  読み落とし {r['id']}（{r['読み落としの扱い']}）: {r['読み取り行']}")
    if a.conds == ",".join(CONDS):
        with open(HERE / "summary.csv", "w", newline="", encoding="utf-8") as fh:
            w = csv.DictWriter(fh, fieldnames=list(summary[0].keys()))
            w.writeheader()
            w.writerows(summary)


if __name__ == "__main__":
    main()
