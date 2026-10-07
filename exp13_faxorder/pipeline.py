#!/usr/bin/env python3
"""FAX注文書 → 読み取り → 品番への変換 → 確認画面 → CSV。

部品: 読み取りは Tesseract（jpn+eng）。将来は画像を読むAI（API）に差し替えられるよう、
read_lines() だけを替えれば済む形にしてある。自分の仕組みは、品番の辞書・あいまい一致・確認画面・CSV。

使い方: python3 pipeline.py fax.png catalog.csv out_dir [--aliases aliases.csv]
出力: out_dir/rows.csv（確認前）, out_dir/review.html（確認画面）, out_dir/order.csv（確認後の取り込み用。確認画面から書き出す）
"""
from __future__ import annotations

import argparse
import os
import csv
import difflib
import html
import json
import os
import re
import subprocess
import unicodedata
from pathlib import Path

HERE = Path(__file__).resolve().parent


# ---------------------------------------------------------------- 1. 読み取り

def _ocr(image_or_path, psm: int = 6) -> list[dict]:
    """Tesseract の TSV を行にまとめる。戻り: [{y, x, text, conf}]"""
    r = subprocess.run(["tesseract", str(image_or_path), "stdout", "-l", "jpn+eng", "--psm", str(psm), "tsv"],
                       capture_output=True, text=True, check=True)
    rows = list(csv.DictReader(r.stdout.splitlines(), delimiter="\t"))
    lines: dict[tuple, list] = {}
    for w in rows:
        if w["level"] != "5" or not w["text"].strip():
            continue
        key = (w["block_num"], w["par_num"], w["line_num"])
        lines.setdefault(key, []).append(w)
    out = []
    for ws in lines.values():
        ws.sort(key=lambda w: int(w["left"]))
        text = "".join(w["text"] for w in ws)  # 日本語は空白で切らない
        conf = [float(w["conf"]) for w in ws if float(w["conf"]) >= 0]
        out.append(dict(y=int(ws[0]["top"]), x=int(ws[0]["left"]), text=text,
                        conf=sum(conf) / len(conf) if conf else 0.0))
    out.sort(key=lambda l: l["y"])
    return out


def deskew(gray):
    """FAXの傾きを罫線の角度から直す（±3度まで）"""
    import cv2
    import numpy as np
    _, b = cv2.threshold(gray, 160, 255, cv2.THRESH_BINARY_INV)
    lines = cv2.HoughLinesP(b, 1, np.pi / 720, 200, minLineLength=gray.shape[1] // 5, maxLineGap=8)
    if lines is None:
        return gray
    angs = []
    for x1, y1, x2, y2 in lines.reshape(-1, 4):
        a = np.degrees(np.arctan2(y2 - y1, x2 - x1))
        if abs(a) < 3:
            angs.append(a)
    if not angs:
        return gray
    ang = float(np.median(angs))
    h, w = gray.shape
    M = cv2.getRotationMatrix2D((w / 2, h / 2), ang, 1.0)
    return cv2.warpAffine(gray, M, (w, h), flags=cv2.INTER_LINEAR, borderValue=255)


def _table_grid(gray):
    """罫線から行と列の境界を見つける。表が無ければ None"""
    import cv2
    import numpy as np
    _, b = cv2.threshold(gray, 160, 255, cv2.THRESH_BINARY_INV)
    h, w = b.shape
    # 細い罫線はFAXで途切れるので、先に短い隙間をつないでから長い線だけを残す
    bh = cv2.morphologyEx(b, cv2.MORPH_CLOSE, cv2.getStructuringElement(cv2.MORPH_RECT, (7, 1)))
    bv = cv2.morphologyEx(b, cv2.MORPH_CLOSE, cv2.getStructuringElement(cv2.MORPH_RECT, (1, 7)))
    hl = cv2.morphologyEx(bh, cv2.MORPH_OPEN, cv2.getStructuringElement(cv2.MORPH_RECT, (w // 16, 1)))
    vl = cv2.morphologyEx(bv, cv2.MORPH_OPEN, cv2.getStructuringElement(cv2.MORPH_RECT, (1, h // 20)))
    ys = [y for y in range(h) if hl[y].sum() > 255 * w // 4]
    xs = [x for x in range(w) if vl[:, x].sum() > 255 * h // 8]

    def squash(v, gap=4):
        out, cur = [], []
        for a in v:
            if cur and a - cur[-1] > gap:
                out.append(sum(cur) // len(cur))
                cur = []
            cur.append(a)
        if cur:
            out.append(sum(cur) // len(cur))
        return out
    ys, xs = squash(ys), squash(xs)
    if len(ys) < 3 or len(xs) < 2:
        return None
    return ys, xs


def read_lines(image: str) -> list[dict]:
    """表があれば罫線でマスに切って1マスずつ読む（psm 7）。無ければページ全体を行で読む。
    1行の text は「品名 | 数量 | 納期」のようにマスを ' | ' でつなぐ"""
    import cv2
    gray = deskew(cv2.imread(image, cv2.IMREAD_GRAYSCALE))
    grid = _table_grid(gray)
    if not grid:
        return _ocr(image, 6)
    ys, xs = grid
    out = []
    for r in range(len(ys) - 1):
        y0, y1 = ys[r] + 3, ys[r + 1] - 3
        if y1 - y0 < 18:
            continue
        cells, confs = [], []
        for c in range(len(xs) - 1):
            x0, x1 = xs[c] + 4, xs[c + 1] - 4
            if x1 - x0 < 20:
                continue
            crop = gray[y0:y1, x0:x1]
            pad = cv2.copyMakeBorder(crop, 12, 12, 12, 12, cv2.BORDER_CONSTANT, value=255)
            tmp = Path("/tmp") / f"_cell_{r}_{c}.png"
            cv2.imwrite(str(tmp), pad)
            got = _ocr(tmp, 7)
            text = "".join(g["text"] for g in got).strip()
            cells.append(text)
            confs += [g["conf"] for g in got]
        if any(cells):
            out.append(dict(y=y0, x=xs[0], text=" | ".join(cells), conf=sum(confs) / len(confs) if confs else 0.0))
    return out


QTY = re.compile(r"(\d+)\s*(個|ケース|箱|本|袋|枚|セット|ｹｰｽ)?")
DUE = re.compile(r"(\d{1,2}/\d{1,2}|至急|来週|今週|月末)")


def parse_row(text: str) -> dict | None:
    """1行から 品名・数量・単位・納期 を切り出す。数量が無い行は注文行ではない"""
    t = unicodedata.normalize("NFKC", text)
    if " | " in t:  # 表のマス: 1列目が品名、残りから数量と納期を探す
        cells = [c.strip() for c in t.split(" | ")]
        name, rest = cells[0], " ".join(cells[1:])
        m = QTY.search(rest)
        if not m or len(name) < 2 or name in ("品名", "商品名"):
            return None
        due = DUE.search(rest)
        return dict(品名_読み取り=name, 数量=int(m.group(1)), 単位=m.group(2) or "", 納期=due.group(1) if due else "")
    t = t.replace("|", " ").replace("｜", " ")
    due = DUE.search(t)
    due_s = due.group(1) if due else ""
    body = t[: due.start()] if due else t
    m = None
    for m in QTY.finditer(body):
        pass  # 最後の数量を採る（品名に数字が入ることがある）
    if not m:
        return None
    name = body[: m.start()].strip(" :：-")
    if len(name) < 2:
        return None
    return dict(品名_読み取り=name, 数量=int(m.group(1)), 単位=m.group(2) or "", 納期=due_s)


# ---------------------------------------------------------------- 2. 品番への変換

def norm(s: str) -> str:
    s = unicodedata.normalize("NFKC", s).lower()
    return re.sub(r"[\s・･\-ー—（）()\[\]【】/,、。]", "", s)


def base(name: str) -> str:
    """括弧の付記を落とした品名（比べる用）"""
    return norm(re.sub(r"[(（][^)）]*[)）]", "", name))


class Matcher:
    def __init__(self, catalog: list[dict], aliases: dict[str, str]):
        self.catalog = catalog
        self.by_code = {c["品番"]: c for c in catalog}
        self.keys = [(norm(c["品名"]), c["品番"]) for c in catalog]
        self.aliases = {norm(k): v for k, v in aliases.items()}

    def match(self, name: str) -> dict:
        n = norm(name)
        if n in self.aliases:
            return dict(品番=self.aliases[n], 確度=1.0, 根拠="辞書", 候補=[self.aliases[n]])
        scored = []
        for k, code in self.keys:
            if not k:
                continue
            if n == k:
                score = 1.0
            elif k.startswith(n) or n.startswith(k):
                score = 0.9
            elif n in k or k in n:
                score = 0.8
            else:
                score = difflib.SequenceMatcher(None, n, k).ratio() * 0.75
            scored.append((score, code))
        scored.sort(reverse=True)
        top = scored[:3]
        best = top[0] if top else (0.0, "")
        # 同じ品名で規格だけ違う候補が並ぶときは、人に選ばせる（650g と 3kg の取り違えを防ぐ）。
        # 「(箱入り)」のような括弧の付記だけが違う品名も同じ扱い——FAXでは括弧ごと省かれる
        if len(top) >= 2 and best[0] >= 0.9 and top[1][0] >= 0.9 \
                and base(self.by_code[top[0][1]]["品名"]) == base(self.by_code[top[1][1]]["品名"]):
            best = (0.85, best[1])
        return dict(品番=best[1] if best[0] >= 0.6 else "", 確度=round(best[0], 2),
                    根拠="一致" if best[0] >= 0.9 else "あいまい" if best[0] >= 0.6 else "不明",
                    候補=[c for _, c in top])


# ---------------------------------------------------------------- 3. 確認画面

def review_html(image_rel: str, rows: list[dict], catalog: list[dict], title: str) -> str:
    cat_json = json.dumps({c["品番"]: c["品名"] for c in catalog}, ensure_ascii=False)
    rows_json = json.dumps(rows, ensure_ascii=False)
    return f"""<!doctype html><html lang="ja"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{html.escape(title)} 確認画面</title>
<style>
:root{{--bg:#f6f6f2;--sheet:#fff;--ink:#1d2433;--sub:#5b6477;--rule:#d9dce3;--ok:#2f6b4f;--ok-soft:#e3efe8;--warn:#b26a00;--warn-soft:#fff3dc;--bad:#b23a2a;--bad-soft:#f7e6e2;--indigo:#2b3f7a}}
body{{margin:0;background:var(--bg);color:var(--ink);font-family:"IPAPGothic","Noto Sans CJK JP",system-ui,sans-serif;font-size:14px}}
header{{padding:12px 20px;border-bottom:2px solid var(--ink);display:flex;justify-content:space-between;align-items:baseline;background:var(--sheet)}}
h1{{font-size:18px;margin:0}} .sub{{color:var(--sub);font-size:12px}}
main{{display:grid;grid-template-columns:1fr 1fr;gap:16px;padding:16px 20px}}
.pane{{background:var(--sheet);border:1px solid var(--rule);border-radius:6px;padding:12px;min-width:0}}
img{{max-width:100%;border:1px solid var(--rule)}}
table{{border-collapse:collapse;width:100%}} th,td{{border-bottom:1px solid var(--rule);padding:6px 8px;text-align:left;vertical-align:top}}
th{{font-size:11.5px;color:var(--sub)}} td.n{{text-align:right;font-variant-numeric:tabular-nums}}
.b{{display:inline-block;padding:1px 8px;border-radius:999px;font-size:11.5px;font-weight:600}}
.ok{{background:var(--ok-soft);color:var(--ok)}} .warn{{background:var(--warn-soft);color:var(--warn)}} .bad{{background:var(--bad-soft);color:var(--bad)}}
select,input{{font:inherit;padding:3px 6px;border:1px solid var(--rule);border-radius:4px;max-width:100%}}
.foot{{display:flex;gap:12px;align-items:center;margin-top:12px}} button{{font:inherit;padding:6px 14px;border-radius:6px;border:1px solid var(--indigo);background:var(--indigo);color:#fff;cursor:pointer}}
.stat{{color:var(--sub);font-size:12.5px}}
td:nth-child(2){{min-width:8em}} td:nth-child(3){{max-width:20em}} td:nth-child(3) select{{width:100%}}
input[type=number]{{width:4.5em}} .b,td:nth-child(5),td:nth-child(6){{white-space:nowrap}}
@media(max-width:900px){{main{{grid-template-columns:1fr}}}}
</style>
<header><h1>{html.escape(title)}</h1><span class="sub">読み取り結果の確認 → 直す → CSVを書き出す（販売管理に取り込む）</span></header>
<main>
<section class="pane"><img src="{image_rel}" alt="受け取ったFAX注文書"></section>
<section class="pane">
<table><thead><tr><th>#</th><th>読み取った品名</th><th>品番（候補から選ぶ）</th><th>数量</th><th>単位</th><th>納期</th><th>判定</th></tr></thead><tbody id="rows"></tbody></table>
<div class="foot"><button id="dl">CSVを書き出す</button><span class="stat" id="stat"></span></div>
</section></main>
<script>
const CAT={cat_json}; const ROWS={rows_json};
const tb=document.getElementById('rows');
function badge(r){{const c=r.確度>=0.9?'ok':r.確度>=0.6?'warn':'bad';const t=r.確度>=0.9?'一致':r.確度>=0.6?'要確認':'不明';return `<span class="b ${{c}}">${{t}} ${{r.確度}}</span>`}}
ROWS.forEach((r,i)=>{{const tr=document.createElement('tr');
 const opts=[...new Set([r.品番,...r.候補].filter(Boolean))].map(c=>`<option value="${{c}}" ${{c===r.品番?'selected':''}}>${{c}} ${{CAT[c]||''}}</option>`).join('')+'<option value="">（該当なし）</option>';
 tr.innerHTML=`<td>${{i+1}}</td><td>${{r.品名_読み取り}}</td><td><select data-i="${{i}}">${{opts}}</select></td><td class="n"><input type="number" value="${{r.数量}}" size="4" data-q="${{i}}"></td><td>${{r.単位}}</td><td>${{r.納期}}</td><td>${{badge(r)}}</td>`;
 tb.appendChild(tr)}});
const need=ROWS.filter(r=>r.確度<0.9).length; document.getElementById('stat').textContent=`${{ROWS.length}}行中 ${{need}}行が要確認`;
document.getElementById('dl').onclick=()=>{{const lines=['品番,品名,数量,単位,納期'];
 document.querySelectorAll('#rows tr').forEach((tr,i)=>{{const code=tr.querySelector('select').value;const q=tr.querySelector('input').value;lines.push([code,(CAT[code]||ROWS[i].品名_読み取り),q,ROWS[i].単位,ROWS[i].納期].map(v=>`"${{String(v).replace(/"/g,'""')}}"`).join(','))}});
 const blob=new Blob(["\\ufeff"+lines.join('\\n')],{{type:'text/csv'}});const a=document.createElement('a');a.href=URL.createObjectURL(blob);a.download='order.csv';a.click()}};
</script></html>"""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("image")
    ap.add_argument("catalog")
    ap.add_argument("out_dir")
    ap.add_argument("--aliases", default=None)
    ap.add_argument("--title", default="FAX注文書")
    a = ap.parse_args()
    out = Path(a.out_dir)
    out.mkdir(parents=True, exist_ok=True)
    catalog = list(csv.DictReader(open(a.catalog, newline="")))
    aliases = {}
    if a.aliases and Path(a.aliases).exists():
        aliases = {r["別名"]: r["品番"] for r in csv.DictReader(open(a.aliases, newline=""))}
    m = Matcher(catalog, aliases)
    rows = []
    for ln in read_lines(a.image):
        p = parse_row(ln["text"])
        if not p:
            continue
        p.update(m.match(p["品名_読み取り"]))
        p["読み取り確度"] = round(ln["conf"], 1)
        rows.append(p)
    with open(out / "rows.csv", "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=["品名_読み取り", "品番", "確度", "根拠", "数量", "単位", "納期", "読み取り確度", "候補"])
        w.writeheader()
        for r in rows:
            w.writerow({**r, "候補": " ".join(r["候補"])})
    img_rel = os.path.relpath(Path(a.image).resolve(), out.resolve())
    (out / "review.html").write_text(review_html(str(img_rel), rows, catalog, a.title), encoding="utf-8")
    need = sum(1 for r in rows if r["確度"] < 0.9)
    print(f"注文行 {len(rows)}行（品番の一致 {len(rows)-need}・要確認 {need}）→ {out/'rows.csv'} / {out/'review.html'}")


if __name__ == "__main__":
    main()
