#!/usr/bin/env python3
"""FAX注文書 → 読み取り → 品番への変換 → 確認画面 → CSV。

部品: 読み取りは Tesseract（jpn+eng）。将来は画像を読むAI（API）に差し替えられるよう、
read_sheet() だけを替えれば済む形にしてある。自分の仕組みは、品番の辞書・あいまい一致・確認画面・CSV。

守ること: 自信を持って誤らない。行を黙って落とさない。
- 品番が1つに決まらない行（同点・あいまい・不明）は品番を空のまま「要確認」にする
- 数量・納期が怪しい行も「要確認」にする。要確認の行は、品名が1つに決まっていても品番を空で始める（候補の先頭に置く）
- 罫線を拾えない・行の高さが合わない用紙は「用紙ごと要確認」にし、確認画面の上に理由を出す
- 確認画面は、未確認の行が残るあいだCSVを書き出させない

使い方: python3 pipeline.py fax.png catalog.csv out_dir [--aliases aliases.csv] [--date 2026-10-06]
       [--lines 読み取り済みの行.txt]
出力: out_dir/rows.csv（確認前）, out_dir/review.html（確認画面）, order.csv（確認後。確認画面から書き出す）
"""
from __future__ import annotations

import argparse
import calendar
import csv
import datetime as dt
import difflib
import html
import json
import os
import re
import subprocess
import tempfile
import unicodedata
from pathlib import Path
from statistics import median

HERE = Path(__file__).resolve().parent
_TMP = Path(tempfile.mkdtemp(prefix="exp13_cells_"))


# ---------------------------------------------------------------- 1. 読み取り

def _ocr(image_or_path, psm: int = 6) -> list[dict]:
    """Tesseract の TSV を行にまとめる。戻り: [{y, x, text, conf}]"""
    r = subprocess.run(["tesseract", str(image_or_path), "stdout", "-l", "jpn+eng", "--psm", str(psm), "tsv"],
                       capture_output=True, text=True, check=True)
    # 語が " で始まっても後続の行を飲み込まないよう、引用符を解釈しない
    rows = list(csv.DictReader(r.stdout.splitlines(), delimiter="\t", quoting=csv.QUOTE_NONE))
    lines: dict[tuple, list] = {}
    for w in rows:
        if w.get("level") != "5" or not (w.get("text") or "").strip():
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


DESKEW_MAX = 6.0  # 傾き補正で探す範囲（±度）


def skew_angle(gray, max_deg: float = DESKEW_MAX) -> float:
    """横方向の投影（行ごとの黒の量）が最も尖る角度を探す。罫線と字の行がそろう角度。
    粗く（半分の解像度・0.25度刻み）探してから、その周りを細かく（原寸・0.05度刻み）探す"""
    import cv2
    import numpy as np
    _, b = cv2.threshold(gray, 160, 255, cv2.THRESH_BINARY_INV)
    small = cv2.resize(b, (b.shape[1] // 2, b.shape[0] // 2), interpolation=cv2.INTER_AREA)

    def score(img, a: float) -> float:
        h, w = img.shape
        M = cv2.getRotationMatrix2D((w / 2, h / 2), float(a), 1.0)
        r = cv2.warpAffine(img, M, (w, h), flags=cv2.INTER_LINEAR, borderValue=0)
        p = r.sum(axis=1, dtype=np.float64)
        return float(np.dot(p, p))

    coarse = max(np.arange(-max_deg, max_deg + 1e-9, 0.25), key=lambda a: score(small, a))
    fine = max(np.arange(coarse - 0.3, coarse + 0.3 + 1e-9, 0.05), key=lambda a: score(b, a))
    return round(float(fine), 2)


def deskew(gray, return_angle: bool = False):
    """FAXの傾きを直す（±6度まで）"""
    import cv2
    ang = skew_angle(gray)
    h, w = gray.shape
    M = cv2.getRotationMatrix2D((w / 2, h / 2), ang, 1.0)
    out = cv2.warpAffine(gray, M, (w, h), flags=cv2.INTER_LINEAR, borderValue=255)
    return (out, ang) if return_angle else out


def remove_streaks(gray):
    """用紙を上から下まで貫く縦すじ（送り側の読み取り面の汚れ）を白で消す。
    残すと罫線と見なされてマスが割れ、字にも混ざる。表の縦線は用紙の高さの8割に届かない"""
    import numpy as np
    h = gray.shape[0]
    cols = np.where((gray < 160).sum(axis=0) > h * 0.8)[0]
    if len(cols):
        gray = gray.copy()
        for x in cols:
            gray[:, max(0, x - 1):x + 2] = 255
    return gray, [int(x) for x in cols]


def _squash(v, gap: int = 4) -> list[int]:
    out, cur = [], []
    for a in v:
        if cur and a - cur[-1] > gap:
            out.append(sum(cur) // len(cur))
            cur = []
        cur.append(int(a))
    if cur:
        out.append(sum(cur) // len(cur))
    return out


def _table_grid(gray) -> dict | None:
    """罫線から行と列の境界を見つける。表が無ければ None。
    戻り: {ys: 横線のy, xs: 縦線のx, bottom: 縦線の下端, streaks: 用紙を縦に貫く線（読み取り面の汚れ）のx}"""
    import cv2
    import numpy as np
    _, b = cv2.threshold(gray, 160, 255, cv2.THRESH_BINARY_INV)
    h, w = b.shape
    # 細い罫線はFAXで途切れるので、先に短い隙間をつないでから長い線だけを残す
    bh = cv2.morphologyEx(b, cv2.MORPH_CLOSE, cv2.getStructuringElement(cv2.MORPH_RECT, (7, 1)))
    bv = cv2.morphologyEx(b, cv2.MORPH_CLOSE, cv2.getStructuringElement(cv2.MORPH_RECT, (1, 7)))
    hl = cv2.morphologyEx(bh, cv2.MORPH_OPEN, cv2.getStructuringElement(cv2.MORPH_RECT, (w // 16, 1)))
    vl = cv2.morphologyEx(bv, cv2.MORPH_OPEN, cv2.getStructuringElement(cv2.MORPH_RECT, (1, h // 20)))
    # 補正後に残るわずかな傾きで線が1画素の行に収まらなくても拾えるよう、上下（左右）に3画素ふくらませて数える
    hl0, vl0 = hl, vl
    hl = cv2.dilate(hl, cv2.getStructuringElement(cv2.MORPH_RECT, (1, 3)))
    vl = cv2.dilate(vl, cv2.getStructuringElement(cv2.MORPH_RECT, (3, 1)))
    ys = _squash(np.where((hl > 0).sum(axis=1) > w // 4)[0])
    xs = _squash(np.where((vl > 0).sum(axis=0) > h // 8)[0])
    if len(ys) < 2:
        return None
    keep, streaks, bottoms = [], [], []
    for x in xs:
        on = np.where((vl[:, max(0, x - 2):x + 3] > 0).any(axis=1))[0]  # 開いた後なので短い点は残っていない
        y0, y1 = int(on[0]), int(on[-1])
        # 表の縦線は一番上の横線から始まる。用紙の上の方から下まで貫く線は送り側の読み取り面の汚れ
        if y0 < ys[0] - 80 or (y0 < h * 0.03 and y1 > h * 0.97):  # remove_streaks() の取りこぼし
            streaks.append(x)
        else:
            keep.append(x)
            bottoms.append(y1)
    if len(keep) < 2:
        return None
    # 罫線の画素（字を読む前に消す）。見つけた横線・縦線の近く（±3画素）だけに限る。
    # 字の横画が並んだ所も長い横線に見えるので、そこまで消すと字が欠ける
    rules = np.zeros_like(b)
    for y in ys:
        rules[max(0, y - 3):y + 4] = hl0[max(0, y - 3):y + 4]
    for x in keep:
        rules[:, max(0, x - 3):x + 4] = np.maximum(rules[:, max(0, x - 3):x + 4], vl0[:, max(0, x - 3):x + 4])
    return dict(ys=ys, xs=keep, bottom=int(median(bottoms)), streaks=streaks, rules=rules)


def _has_ink(crop) -> bool:
    """マスに字があるか。ごま塩の点・罫線の切れ端は数えない"""
    import cv2
    _, b = cv2.threshold(crop, 160, 255, cv2.THRESH_BINARY_INV)
    n, _, st, _ = cv2.connectedComponentsWithStats(b, connectivity=8)
    H = crop.shape[0]
    for i in range(1, n):
        x, y, cw, ch, area = st[i]
        if area < 12 or ch <= 3:  # 点・横線の切れ端
            continue
        if cw <= 3 and ch >= H * 0.7:  # 縦線の切れ端
            continue
        return True
    return False


def _read_cell(gray, box: tuple[int, int, int, int], wide: tuple[int, int, int, int], tag: str) -> tuple[str, float | None, bool]:
    """1マスを読む。字の跡はマスの内側（box）で確かめ、読むときは罫線を消した画像を少し広く（wide）切る
    （罫線にかかった字・左の罫線からはみ出した字を切らないため）。戻り: (字, 確度（字が無ければ None）, 字の跡があるか)"""
    import cv2
    y0, y1, x0, x1 = box
    inner = gray[y0:y1, x0:x1]
    if inner.size == 0 or not _has_ink(inner):
        return "", None, False
    y0, y1, x0, x1 = wide
    got = None
    for crop in (gray[max(0, y0):y1, max(0, x0):x1], inner):
        pad = cv2.copyMakeBorder(crop, 12, 12, 12, 12, cv2.BORDER_CONSTANT, value=255)
        tmp = _TMP / f"{os.getpid()}_{tag}.png"  # 並列に動かしても他の処理のマスと混ざらないよう、処理ごとに名前を分ける
        cv2.imwrite(str(tmp), pad)
        try:
            got = _ocr(tmp, 7)
            break
        except subprocess.CalledProcessError:  # Tesseract がまれに落ちる（SIGFPE）。内側だけでもう一度
            continue
    if got is None:
        return "", 0.0, True
    text = "".join(g["text"] for g in got).strip().strip("|｜¦").strip()  # 端の「|」は消し残った罫線
    confs = [g["conf"] for g in got]
    return text, (sum(confs) / len(confs) if confs else None), True


CELL_MARGIN_Y = 6      # 読むときにマスの上下へ広げる画素（罫線にかかった字を切らない）
CELL_LEFT_SPILL = 30   # 品名の列は左の罫線の外まで広げて読む（左にはみ出して書かれた字）


def read_sheet(image: str) -> tuple[list[dict], list[str]]:
    """表があれば罫線でマスに切って1マスずつ読む（psm 7）。無ければページ全体を行で読む。

    戻り: (行, 用紙の問題)。
    行 = {y, x, cells: [品名, 数量, 納期], confs: [各マスの確度], ink: [各マスに字の跡があるか],
          text: マスを ' | ' でつないだもの, conf: 平均の確度, flags: [行の問題]}
    用紙の問題が1つでもあれば、確認画面は「用紙ごと要確認」にする"""
    import cv2
    import numpy as np
    gray0 = cv2.imread(image, cv2.IMREAD_GRAYSCALE)
    gray0, _ = remove_streaks(gray0)
    gray = deskew(gray0)
    grid = _table_grid(gray)
    if not grid:
        tmp = _TMP / f"{os.getpid()}_page.png"
        cv2.imwrite(str(tmp), gray)
        lines = _ocr(tmp, 6)
        for ln in lines:
            ln["flags"] = ["罫線を検出できず、ページ全体を行で読んだ（品名・数量・納期の切り分けが不確か）"]
        return lines, ["表の罫線を検出できませんでした。ページ全体を行で読んだため、行の抜け・品名と数量の取り違えがあり得ます。"
                       "FAXの画像と1行ずつ照らしてください"]
    ys, xs = grid["ys"], grid["xs"]
    issues: list[str] = []
    for x in grid["streaks"]:
        gray[:, max(0, x - 2):x + 3] = 255
    # 読む用の画像: 罫線を消す（字が罫線にかかっていても、罫線の画素だけが抜ける）
    clean = gray.copy()
    clean[cv2.dilate(grid["rules"], np.ones((3, 3), np.uint8)) > 0] = 255
    # 行の帯。横線の間隔の中央値を1行の高さとし、2行分以上の帯は罫線が欠けたものとして高さで分ける
    bands = [(ys[i], ys[i + 1]) for i in range(len(ys) - 1) if ys[i + 1] - ys[i] >= 18]
    if not bands:
        return [], ["表の行を見つけられませんでした。FAXの画像を見て入力してください"]
    unit = median(b1 - b0 for b0, b1 in bands)
    guessed = None
    if grid["bottom"] - ys[-1] > unit * 0.6:  # 縦線が最後の横線より下まで続く＝下の横線が見つからない
        guessed = (ys[-1], grid["bottom"])
        bands.append(guessed)
        issues.append("表の下の方の横線が見つかりませんでした。下の行は縦線の長さから推定して読んでいます")
    rows: list[tuple[int, int, str]] = []
    split = 0
    for b0, b1 in bands:
        k = max(1, round((b1 - b0) / unit))
        if (b0, b1) == guessed:
            step = (b1 - b0) / k
            rows += [(int(b0 + j * step), int(b0 + (j + 1) * step), "表の下の横線が見つからず、縦線の長さから推定した行")
                     for j in range(k)]
            continue
        if k == 1:
            rows.append((b0, b1, "列の罫線が足りず、品名と数量を分けられない" if len(xs) < 3 else ""))
            continue
        split += 1
        step = (b1 - b0) / k
        for j in range(k):
            rows.append((int(b0 + j * step), int(b0 + (j + 1) * step),
                         f"行の罫線が欠けていたため、行の高さで{k}行に分けて読んだ"))
    if split:
        issues.append(f"行の罫線が{split}か所で欠けていました（2行が1マスにつながって見える）。"
                      "高さで分けて読んだので、行の分かれ目をFAXと照らしてください")
    if len(xs) < 3:
        issues.append("列の罫線が足りず、品名と数量のマスを分けられませんでした")
    out = []
    for r, (y0, y1, flag) in enumerate(rows):
        cells, confs, ink = [], [], []
        for c in range(len(xs) - 1):
            x0, x1 = xs[c] + 4, xs[c + 1] - 4
            if x1 - x0 < 20:
                continue
            wx0 = xs[c] - CELL_LEFT_SPILL if c == 0 else x0
            t, cf, has = _read_cell(clean, (y0 + 3, y1 - 3, x0, x1),
                                    (y0 - CELL_MARGIN_Y, y1 + CELL_MARGIN_Y, wx0, x1), f"c{r}_{c}")
            cells.append(t)
            confs.append(cf)
            ink.append(has)
        if not any(ink):
            continue
        known = [cf for cf in confs if cf is not None]
        out.append(dict(y=y0, x=xs[0], cells=cells, confs=confs, ink=ink, text=" | ".join(cells),
                        conf=sum(known) / len(known) if known else 0.0, flags=[flag] if flag else []))
    return out, issues


def read_lines(image: str) -> list[dict]:
    """互換用: 行だけを返す"""
    return read_sheet(image)[0]


def lines_from_text(path: str) -> list[dict]:
    """読み取り済みの行（1行=「品名 | 数量 | 納期」）。画像を読むAIの結果など。
    字だけの入力として扱う（読み取りの確度は無い。品番の判定と数量・納期の検査だけで決める）"""
    out = []
    for i, t in enumerate(open(path, encoding="utf-8")):
        if not t.strip():
            continue
        cells = [c.strip() for c in t.rstrip("\n").split("|")]
        out.append(dict(y=i, x=0, cells=cells, confs=[None] * len(cells), ink=[bool(c) for c in cells],
                        text=" | ".join(cells), conf=None, flags=[]))
    return out


# ---------------------------------------------------------------- 2. 行を切り出す（品名・数量・単位・納期）

def nfkc(s: str) -> str:
    return unicodedata.normalize("NFKC", s or "")


UNITS = ["ケース", "セット", "ロール", "パック", "個", "箱", "本", "袋", "枚", "缶", "組", "足", "包", "冊", "台", "束",
         "kg", "c/s", "cs"]
UNIT_ALIAS = {"c/s": "ケース", "cs": "ケース"}
_UNIT_RE = "|".join(re.escape(u) for u in UNITS)
QTY = re.compile(rf"(\d+)\s*({_UNIT_RE})?", re.I)
QTY_CELL = re.compile(rf"(\d+)({_UNIT_RE})?", re.I)
QTY_MAX_DIGITS = 2   # 3桁以上の数量は要確認。FAXの注文は1〜2桁が普通で、桁が多いのは「単位の字が数字に化けた」（20袋→2088・208）ことが多い
CONF_MIN = 80        # 数量・納期のマスの読み取り確度（Tesseract）がこれ未満なら要確認
DUE_WORDS = {"至急": None, "明日": 1, "明後日": 2, "本日": 0, "今日": 0, "来週": None, "今週": None,
             "月末": "eom", "今月末": "eom"}


def parse_qty(raw: str) -> dict:
    """数量のマス → {数量, 単位, 問題}。数量が読めなければ 数量=None"""
    sp = nfkc(raw).strip().lstrip("x×*")
    t = re.sub(r"\s", "", sp)
    if not t:
        return dict(数量=None, 単位="", 問題=[])
    if len(re.findall(r"\d+", sp)) > 1 and QTY_CELL.fullmatch(t):  # 「5 6」: 書き直しの跡を1つの数に読んだ可能性
        return dict(数量=int(QTY_CELL.fullmatch(t).group(1)), 単位="", 問題=[f"数量のマスに数字が離れて2つ以上ある「{sp}」（書き直しの可能性）"])
    m = QTY_CELL.fullmatch(t)
    issues = []
    if m:
        q, unit = m.group(1), (m.group(2) or "")
    else:
        nums = re.findall(r"\d+", t)
        if not nums:
            return dict(数量=None, 単位="", 問題=[f"数量を読めない「{t}」"])
        q = nums[0]
        mu = re.search(rf"\d+({_UNIT_RE})", t, re.I)
        unit = mu.group(1) if mu else ""
        if len(nums) > 1:
            issues.append(f"数量のマスに数字が{len(nums)}つある「{t}」（書き直しの可能性）")
        else:
            rest = re.sub(rf"\d+({_UNIT_RE})?", "", t, count=1, flags=re.I)
            issues.append(f"単位を読めない「{t}」（字「{rest}」が化けている可能性）")
    unit = UNIT_ALIAS.get(unit.lower(), unit)
    if len(q) > QTY_MAX_DIGITS:
        issues.append(f"数量の桁が多い（{int(q)}）。単位の字が数字に化けた可能性")
    if int(q) == 0:
        issues.append("数量が0")
    return dict(数量=int(q), 単位=unit, 問題=issues)


def parse_due(raw: str, ref: dt.date) -> dict:
    """納期のマス → {納期（表記をそろえたもの）, 納期_日付（ISO・分かるときだけ）, 問題}。
    「至急」は印として残す（日付にしない）。来週・今週は日付を決めずに言葉のまま残す"""
    t = re.sub(r"\s", "", nfkc(raw))
    if not t:
        return dict(納期="", 納期_日付="", 問題=None)
    t = re.sub(r"[(（][月火水木金土日][)）]$", "", t)
    t = re.sub(r"(まで|迄|必着|着|納品|納入|頃|ごろ)$", "", t)
    if t in DUE_WORDS:
        v = DUE_WORDS[t]
        if v == "eom":
            d = ref.replace(day=calendar.monthrange(ref.year, ref.month)[1])
            return dict(納期="月末", 納期_日付=d.isoformat(), 問題=None)
        return dict(納期=t, 納期_日付=(ref + dt.timedelta(days=v)).isoformat() if isinstance(v, int) else "", 問題=None)
    m = re.fullmatch(r"(\d{1,2})[/月.\-](\d{1,2})日?", t)
    if m:
        mo, da = int(m.group(1)), int(m.group(2))
        try:
            d = dt.date(ref.year, mo, da)
        except ValueError:
            return dict(納期="", 納期_日付="", 問題=f"納期の日付がおかしい「{t}」")
        issue = None
        if d < ref:
            if (ref - d).days > 180:
                d = d.replace(year=ref.year + 1)
            else:
                issue = f"納期「{mo}/{da}」が受信日より前"
        return dict(納期=f"{mo}/{da}", 納期_日付=d.isoformat(), 問題=issue)
    return dict(納期="", 納期_日付="", 問題=f"納期を読めない「{t}」")


HEADER_WORDS = {"品名", "商品名", "数量", "納期"}


def parse_line(ln: dict, ref: dt.date) -> dict | None:
    """1行（マスに分かれた行か、ページ全体で読んだ行）→ 品名・数量・単位・納期と問題。
    品名のマスに字があれば、数量が読めなくても行を残す（数量を空にして要確認）"""
    reasons: list[list[str]] = [["行", f] for f in ln.get("flags", [])]
    if ln.get("cells") is not None and len(ln["cells"]) >= 2:
        cells = list(ln["cells"]) + ["", ""]
        confs = list(ln.get("confs") or []) + [None, None, None]
        ink = list(ln.get("ink") or [bool(c) for c in ln["cells"]]) + [False, False, False]
        name = nfkc(cells[0]).strip()
        qcell = nfkc(cells[1]).strip()
        dcell = nfkc(" ".join(c for c in cells[2:] if c)).strip()
        if not any(ink[:len(ln["cells"])]) and not (name or qcell or dcell):
            return None
        if any(re.sub(r"[^\w]", "", norm(c)) in HEADER_WORDS for c in (name, qcell, dcell)):
            return None  # 見出しの行
        qconf, dconf = confs[1], (confs[2] if len(ln["cells"]) > 2 else None)
    else:  # ページ全体を行で読んだ（表が無い）: 最後の数量らしい数字で品名と数量を分ける
        t = nfkc(ln["text"]).replace("|", " ")
        due = re.search(r"(\d{1,2}/\d{1,2}|\d{1,2}月\d{1,2}日|至急|明日|来週|今週|月末)", t)
        body = t[: due.start()] if due else t
        m = None
        for m in QTY.finditer(body):
            pass
        if not m:
            return None
        name = body[: m.start()].strip(" :：-")
        if len(name) < 2:
            return None
        qcell, dcell = body[m.start():m.end()], (due.group(1) if due else "")
        qconf = dconf = None
        ink = [True, True, bool(dcell)]
    q = parse_qty(qcell)
    if not name:
        reasons.append(["品番", "品名のマスに字の跡があるが読めない" if ink[0] else "品名が空"])
    if q["数量"] is None:
        reasons.append(["数量", q["問題"][0] if q["問題"] else
                        ("数量のマスに字の跡があるが読めない" if ink[1] else "数量が書かれていない")])
    else:
        reasons += [["数量", p] for p in q["問題"]]
        if qconf is not None and qconf < CONF_MIN:
            reasons.append(["数量", f"数量の読み取りが不確か（確度{qconf:.0f}）"])
    d = parse_due(dcell, ref)
    if d["問題"]:
        reasons.append(["納期", d["問題"]])
    elif dcell and dconf is not None and dconf < CONF_MIN:
        reasons.append(["納期", f"納期の読み取りが不確か（確度{dconf:.0f}）"])
    elif not dcell and len(ink) > 2 and ink[2]:
        reasons.append(["納期", "納期のマスに字の跡があるが読めない"])
    return dict(品名_読み取り=name, 数量=q["数量"], 単位=q["単位"], 数量_原文=qcell,
                納期=d["納期"], 納期_日付=d["納期_日付"], 納期_原文=dcell,
                数量確度=None if qconf is None else round(qconf, 1),
                納期確度=None if dconf is None else round(dconf, 1),
                読み取り確度=None if ln.get("conf") is None else round(ln["conf"], 1),
                y=ln.get("y"), 理由=reasons)


# ---------------------------------------------------------------- 3. 品番への変換

def norm(s: str) -> str:
    s = unicodedata.normalize("NFKC", s or "").lower()
    s = "".join(chr(ord(c) + 0x60) if "ぁ" <= c <= "ゖ" else c for c in s)  # ひらがな → カタカナ
    return re.sub(r"[\s・･\-ー—（）()\[\]【】/,、。]", "", s)


def base(name: str) -> str:
    """括弧の付記を落とした品名（比べる用）"""
    return norm(re.sub(r"[(（][^)）]*[)）]", "", name))


SPEC = re.compile(r"(\d+(?:\.\d+)?)\s*(kg|mg|g|ml|l|cm|mm|本|個|枚|袋|組|足|箱|ロール|セット|包)(?![a-z])(?:入り?)?")


def spec_tokens(s: str) -> set[str]:
    """規格の字（600g・10mL・45L・350本・12枚 など）。数字＋単位だけを取り、数字は 600 と 600.0 をそろえる"""
    t = nfkc(s).lower()
    return {f"{float(n):g}{u}" for n, u in SPEC.findall(t)}


def strip_spec(s: str) -> str:
    return SPEC.sub(" ", nfkc(s).lower())


class Matcher:
    """品名 → 品番。決められないときは決めない（品番を空にして候補だけ返す）。

    - 書かれた品名が品名とそのまま一致する品が1つ → 決める
    - 書かれた品名を含む（または前方が一致する）品が1つだけ → 決める（前方一致の場合）
    - そういう品が2つ以上（大/小・45L/10L・入数違いの同名）→ 書かれた規格（g・mL・L・入数・サイズ）で
      1つに絞れれば決める。規格が無い・絞れないなら同点として決めない
    - 品番の大小や一覧の並び順で同点を決めることはしない"""

    def __init__(self, catalog: list[dict], aliases: dict[str, str]):
        self.catalog = catalog
        self.by_code = {c["品番"]: c for c in catalog}
        self.keys = [(norm(c["品名"]), c["品番"]) for c in catalog]
        self.specs = {c["品番"]: spec_tokens(c["品名"] + " " + c.get("規格", "")) for c in catalog}
        self.aliases = {norm(k): v for k, v in aliases.items()}

    @staticmethod
    def _score(n: str, k: str) -> float:
        if not n or not k:
            return 0.0
        if n == k:
            return 1.0
        if k.startswith(n):  # 書かれた品名が品名の頭（略記）。短すぎる頭は決め手にしない
            return 0.9 if len(n) >= 3 and len(n) >= len(k) * 0.3 else 0.8
        if n.startswith(k):  # 品名の後ろに字が足されている。足された字が短いか規格だけなら同じ品
            tail = n[len(k):]
            return 0.9 if len(tail) <= 2 or not norm(strip_spec(tail)) else 0.8
        if n in k or k in n:
            return 0.8
        return difflib.SequenceMatcher(None, n, k).ratio() * 0.75

    def label(self, code: str) -> str:
        c = self.by_code.get(code)
        return f"{code} {c['品名']} {c.get('規格', '')}".strip() if c else code

    def match(self, name: str) -> dict:
        n = norm(name)
        if n and n in self.aliases:
            code = self.aliases[n]
            return dict(品番=code, 確度=1.0, 根拠="辞書", 候補=[code], 競合=1, 理由=[])
        part = norm(strip_spec(name))  # 規格の字を除いた品名（「なたねサラダ油 600g」→ なたねサラダ油）
        want = spec_tokens(name)
        scored = []
        for order, (k, code) in enumerate(self.keys):
            s = self._score(n, k)
            if part and part != n and len(part) >= 2:
                s = max(s, self._score(part, k))
            scored.append((s, order, code))
        scored.sort(key=lambda t: (-t[0], t[1]))  # 同点は一覧の並び順（決めるのには使わない）
        best = scored[0][0] if scored else 0.0
        if best >= 1.0:
            # 括弧の付記だけが違う品（「麦みそ」と「麦みそ(箱入り)」）も競合。FAXでは括弧ごと省かれる
            group = [c for s, _, c in scored if s >= 1.0 or base(self.by_code[c]["品名"]) in (n, part)]
        elif best >= 0.8:
            group = [c for s, _, c in scored if s >= 0.8]  # 書かれた品名を含む品はすべて競合
        else:
            group = []
        rest = [c for _, _, c in scored if c not in group]
        cands = (group + rest)[: min(8, max(5, len(group)))]
        reasons: list[list[str]] = []
        code, conf, why = "", best, ""
        if group:
            # 候補の品名のどれにも入っている規格の字（「16mL」「綿棒2本」）は決め手にならないので除く
            common = set.intersection(*(spec_tokens(self.by_code[c]["品名"]) for c in group))
            want = want - common if len(group) >= 2 else want
            hits = [c for c in group if want and want <= self.specs[c]]
            if len(group) == 1 and best >= 0.9:
                if want and not hits:
                    reasons.append(["品番", f"書かれた規格「{' '.join(sorted(want))}」が一覧（{self.label(group[0])}）と合わない"])
                    why = "規格が合わない"
                else:
                    code, why = group[0], "一致"
            elif want and len(hits) == 1:
                code, conf, why = hits[0], max(best, 0.9), "規格で一致"
                cands = [hits[0]] + [c for c in cands if c != hits[0]]
            elif want and not hits:
                reasons.append(["品番", f"書かれた規格「{' '.join(sorted(want))}」に合う候補がない"])
                why = "規格が合わない"
            elif len(group) >= 2:
                if want:
                    reasons.append(["品番", f"規格「{' '.join(sorted(want))}」でも候補が{len(hits)}つ残る"])
                elif best >= 0.9:
                    reasons.append(["品番", f"同じ品名の候補が{len(group)}つ。規格（g・mL・入数・大小）が書かれていないので決めない"])
                else:
                    reasons.append(["品番", f"品名の一部が合う候補が{len(group)}つ。1つに決めない"])
                why = "同点" if best < 0.9 else "同名で規格違い"
            else:
                reasons.append(["品番", "品名の一部だけが一致（あいまい）"])
                why = "あいまい"
        elif best >= 0.6:
            reasons.append(["品番", "似た品名はあるが一致しない（あいまい）"])
            why = "あいまい"
        else:
            reasons.append(["品番", "一覧に近い品名がない"])
            why = "不明"
        if not code:
            conf = min(conf, 0.85)
        return dict(品番=code, 確度=round(conf, 2), 根拠=why, 候補=cands, 競合=len(group), 理由=reasons)


def build_rows(lines: list[dict], matcher: Matcher, ref: dt.date) -> list[dict]:
    """読み取った行 → 確認画面の行。判定: 一致（そのまま書き出せる）/ 要確認 / 不明（品番の当てが無い）"""
    rows = []
    for ln in lines:
        r = parse_line(ln, ref)
        if r is None:
            continue
        m = matcher.match(r["品名_読み取り"])
        r.update({k: m[k] for k in ("品番", "確度", "根拠", "候補", "競合")})
        r["照合品番"] = m["品番"]
        ocr = r["数量確度"] is not None or r["納期確度"] is not None
        if ocr and not m["品番"] and m["確度"] < 0.6 and not any(k == "数量" for k, _ in r["理由"]):
            # 品名が一覧と合わない（崩れて読めた）行は、同じ行の数量・納期も崩れていることがある（10→16 のような数字の読み違いは印が付かない）
            r["理由"].append(["数量", "品名が一覧と合わない行なので、数量・納期も画像と照らす"])
        if m["品番"] and r["理由"]:
            # 品名は1つに決まったが、数量・納期・行の切り方が怪しい。要確認の行は品番を入れずに始める
            # （決まった品番は候補の先頭に置く）。数量だけ見て品番を見ずに通すことを防ぐ
            r["品番"] = ""
            r["理由"].insert(0, ["品番", f"品名は {m['品番']} に一致（候補の先頭）。下の点を確かめてから選ぶ"])
        r["理由"] = m["理由"] + r["理由"]
        if r["品番"]:
            r["判定"] = "一致"
        else:
            r["判定"] = "要確認" if r["照合品番"] or r["確度"] >= 0.6 else "不明"
        rows.append(r)
    return rows


# ---------------------------------------------------------------- 4. 確認画面

def review_html(image_rel: str, rows: list[dict], catalog: list[dict], title: str,
                sheet_issues: list[str] | None = None, ref: dt.date | None = None) -> str:
    """確認画面。未確認の行（要確認・不明・用紙ごと要確認）が残るあいだはCSVを書き出させない"""
    def js(v):  # <script> の中に安全に埋める
        return json.dumps(v, ensure_ascii=False).replace("</", "<\\/")
    cat = [dict(c=c["品番"], n=c["品名"], s=c.get("規格", "")) for c in catalog]
    keep = ("品名_読み取り", "品番", "確度", "根拠", "候補", "競合", "数量", "単位", "納期", "納期_日付", "納期_原文",
            "数量_原文", "判定", "理由")
    data = [{k: r.get(k) for k in keep} for r in rows]
    ref = ref or dt.date.today()
    return f"""<!doctype html><html lang="ja"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{html.escape(title)} 確認画面</title>
<style>
:root{{--bg:#f6f6f2;--sheet:#fff;--ink:#1d2433;--sub:#5b6477;--rule:#d9dce3;--ok:#2f6b4f;--ok-soft:#e3efe8;--warn:#9a5b00;--warn-soft:#fff3dc;--bad:#b23a2a;--bad-soft:#f7e6e2;--indigo:#2b3f7a;--indigo-soft:#e8ecf6}}
*{{box-sizing:border-box}}
body{{margin:0;background:var(--bg);color:var(--ink);font-family:"IPAPGothic","Noto Sans CJK JP",system-ui,sans-serif;font-size:14px}}
header{{padding:12px 20px;border-bottom:2px solid var(--ink);display:flex;justify-content:space-between;align-items:baseline;gap:12px;flex-wrap:wrap;background:var(--sheet)}}
h1{{font-size:18px;margin:0}} .sub{{color:var(--sub);font-size:12px}}
main{{display:grid;grid-template-columns:minmax(0,2fr) minmax(0,3fr);gap:16px;padding:16px 20px}}
.pane{{background:var(--sheet);border:1px solid var(--rule);border-radius:6px;padding:12px;min-width:0}}
img{{max-width:100%;border:1px solid var(--rule)}}
.sheetwarn{{background:var(--bad-soft);border:1px solid var(--bad);border-radius:6px;padding:10px 12px;margin-bottom:10px;line-height:1.5}}
.sheetwarn b{{color:var(--bad)}} .sheetwarn ul{{margin:4px 0 6px;padding-left:1.3em}} .sheetwarn label{{font-weight:600;cursor:pointer}}
.bar{{display:flex;justify-content:space-between;align-items:baseline;gap:8px;margin:0 0 6px}}
.count{{font-weight:700}} .count.zero{{color:var(--ok)}} .count.some{{color:var(--warn)}}
.hint{{color:var(--sub);font-size:12px}}
table{{border-collapse:collapse;width:100%;table-layout:fixed}}
th,td{{border-bottom:1px solid var(--rule);padding:6px 5px;text-align:left;vertical-align:top}}
th{{font-size:11.5px;color:var(--sub);font-weight:600}}
td.no{{color:var(--sub);font-variant-numeric:tabular-nums}}
tr.need{{background:#fffbf2}}
select,input,textarea,button{{font:inherit}}
select,input,textarea{{width:100%;padding:3px 5px;border:1px solid var(--rule);border-radius:4px;background:#fff;color:var(--ink)}}
input[type=checkbox]{{width:auto;margin:0 4px 0 0;vertical-align:-2px}}
textarea{{resize:vertical;min-height:2.6em;line-height:1.35}}
.flagged{{border-color:var(--warn)!important;background:var(--warn-soft)!important}}
.empty{{border-color:var(--bad)!important;background:var(--bad-soft)!important}}
.full{{font-size:12px;color:var(--ok);margin-top:3px;line-height:1.35}}
.cands{{display:flex;flex-direction:column;gap:3px;margin-top:4px}}
.cands .lab{{font-size:11px;color:var(--sub)}}
.cands button,.hits button{{font-size:12px;text-align:left;background:#fff;border:1px dashed var(--warn);border-radius:4px;padding:2px 6px;cursor:pointer;color:var(--ink);line-height:1.35}}
.hits button{{border-style:solid;border-color:var(--rule)}}
.find{{margin-top:4px;font-size:12px}}
.hits{{display:flex;flex-direction:column;gap:2px;margin-top:3px}} .hits .none{{font-size:12px;color:var(--sub)}}
.iso{{font-size:11px;color:var(--sub);margin-top:2px;white-space:nowrap}}
.b{{display:inline-block;padding:1px 8px;border-radius:999px;font-size:11.5px;font-weight:700;white-space:nowrap}}
.ok{{background:var(--ok-soft);color:var(--ok)}} .warn{{background:var(--warn-soft);color:var(--warn)}} .bad{{background:var(--bad-soft);color:var(--bad)}} .done{{background:var(--indigo-soft);color:var(--indigo)}}
.why{{margin:4px 0 0;padding-left:1.1em;font-size:11.5px;color:var(--warn);line-height:1.4}}
.ack{{margin-top:4px;font-size:12px;padding:2px 8px;border:1px solid var(--warn);background:#fff;color:var(--warn);border-radius:4px;cursor:pointer}}
.del{{white-space:nowrap;font-size:12px;padding:2px 6px;border:1px solid var(--rule);background:#fff;color:var(--sub);border-radius:4px;cursor:pointer}}
.foot{{display:flex;gap:12px;align-items:center;flex-wrap:wrap;margin-top:12px}}
.btn{{padding:6px 14px;border-radius:6px;border:1px solid var(--indigo);background:var(--indigo);color:#fff;cursor:pointer}}
.btn.blocked{{background:#8d96ad;border-color:#8d96ad}}
.btn2{{padding:5px 12px;border-radius:6px;border:1px solid var(--indigo);background:#fff;color:var(--indigo);cursor:pointer}}
#msg{{font-size:13px}} #msg.err{{color:var(--bad)}} #msg.okm{{color:var(--ok)}}
@media(max-width:1000px){{main{{grid-template-columns:1fr}}}}
</style>
<header><h1>{html.escape(title)}</h1><span class="sub">読み取り結果の確認 → 直す → CSVを書き出す（販売管理に取り込む）。受信日 {ref.isoformat()}</span></header>
<main>
<section class="pane"><img src="{html.escape(image_rel)}" alt="受け取ったFAX注文書"></section>
<section class="pane">
<div id="sheet"></div>
<div class="bar"><span class="count" id="count"></span><span class="hint">品番が空の行は、候補か「一覧から探す」で選ぶ</span></div>
<table><colgroup><col style="width:28px"><col style="width:20%"><col><col style="width:60px"><col style="width:66px"><col style="width:92px"><col style="width:150px"><col style="width:50px"></colgroup>
<thead><tr><th>#</th><th>読み取った品名</th><th>品番（候補から選ぶ・一覧から探す）</th><th>数量</th><th>単位</th><th>納期</th><th>判定</th><th></th></tr></thead><tbody id="rows"></tbody></table>
<div class="foot"><button class="btn2" id="add">＋ 行を追加（読み落とし・電話の注文）</button><button class="btn" id="dl">CSVを書き出す</button><span id="msg" role="status"></span></div>
</section></main>
<script>
const CAT={js(cat)}, ROWS={js(data)}, SHEET={js(list(sheet_issues or []))}, REF={js(ref.isoformat())};
const BY=Object.fromEntries(CAT.map(x=>[x.c,x]));
const esc=s=>String(s??'').replace(/[&<>"']/g,c=>({{'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}}[c]));
const label=c=>{{const x=BY[c];return x?`${{x.c}} ${{x.n}}${{x.s?' '+x.s:''}}`:c}};
const fold=s=>String(s||'').normalize('NFKC').toLowerCase().replace(/[\\u3041-\\u3096]/g,ch=>String.fromCharCode(ch.charCodeAt(0)+0x60)).replace(/[\\s・･\\-ー—（）()\\[\\]【】\\/,、。]/g,'');
const IDX=CAT.map(x=>({{x,k:fold(x.c+' '+x.n+' '+x.s),n:fold(x.n),c:fold(x.c)}}));
function search(q){{const ts=String(q||'').normalize('NFKC').split(/\\s+/).map(fold).filter(Boolean);if(!ts.length)return[];
 return IDX.filter(e=>ts.every(t=>e.k.includes(t))).map(e=>[e.c===ts[0]?0:e.n.startsWith(ts[0])?1:2,e.n.length,e]).sort((a,b)=>a[0]-b[0]||a[1]-b[1]).slice(0,8).map(a=>a[2].x.c)}}
const iso=d=>`${{d.getFullYear()}}-${{String(d.getMonth()+1).padStart(2,'0')}}-${{String(d.getDate()).padStart(2,'0')}}`;
function parseDue(raw){{let t=String(raw||'').normalize('NFKC').replace(/\\s/g,'');if(!t)return{{ok:true,canon:'',iso:''}};
 t=t.replace(/[(（][月火水木金土日][)）]$/,'').replace(/(まで|迄|必着|着|納品|納入|頃|ごろ)$/,'');
 const ref=new Date(REF+'T00:00:00'),add=n=>{{const d=new Date(ref);d.setDate(d.getDate()+n);return iso(d)}};
 const W={{'至急':null,'明日':1,'明後日':2,'本日':0,'今日':0,'来週':null,'今週':null}};
 if(t in W)return{{ok:true,canon:t,iso:W[t]===null?'':add(W[t])}};
 if(t==='月末'||t==='今月末')return{{ok:true,canon:'月末',iso:iso(new Date(ref.getFullYear(),ref.getMonth()+1,0))}};
 const m=t.match(/^(\\d{{1,2}})[\\/月.\\-](\\d{{1,2}})日?$/);
 if(m){{const mo=+m[1],da=+m[2];let d=new Date(ref.getFullYear(),mo-1,da);if(d.getMonth()!==mo-1)return{{ok:false}};
  if(d<ref&&(ref-d)/864e5>180)d=new Date(ref.getFullYear()+1,mo-1,da);return{{ok:true,canon:`${{mo}}/${{da}}`,iso:iso(d)}}}}
 return{{ok:false}}}}
// 行の状態。flags = 仕組みが付けた「確かめてほしい点」。品番の点は品番を選べば、数量・納期の点はそのマスを直すか「確認した」で消える
const S=ROWS.map(r=>({{name:r.品名_読み取り||'',code:r.品番||'',qty:r.数量,unit:r.単位||'',due:r.納期_原文||r.納期||'',
 cands:r.候補||[],nc:r.競合||0,init:r.判定,flags:(r.理由||[]).map(([k,m])=>({{k,m,open:true}})),del:false,added:false}}));
const tb=document.getElementById('rows');
function check(s){{const errs=[];
 if(!s.code)errs.push(['品番','品番を選んでください']);
 if(!(Number.isInteger(s.qty)&&s.qty>0))errs.push(['数量','数量を入れてください']);
 const d=parseDue(s.due);if(!d.ok)errs.push(['納期',`納期「${{s.due}}」を読めません（例: 10/20・10月20日・明日・至急）`]);
 const open=s.flags.filter(f=>f.open&&!(f.k==='品番'&&s.code));
 return{{errs,open,d,ok:!errs.length&&!open.length}}}}
function badge(s,c){{if(c.ok)return s.init==='一致'&&!s.edited?'<span class="b ok">一致</span>':'<span class="b done">確認済み</span>';
 return !s.code&&s.init==='不明'?'<span class="b bad">不明</span>':'<span class="b warn">要確認</span>'}}
function codeCell(s,i){{const opts=[...new Set([s.code,...s.cands].filter(Boolean))];
 return `<select data-i="${{i}}" class="code"><option value="">選んでください</option>${{opts.map(c=>`<option value="${{esc(c)}}"${{c===s.code?' selected':''}}>${{esc(label(c))}}</option>`).join('')}}</select>
 <div class="full"></div><div class="cands"></div>
 <input class="find" type="search" placeholder="一覧から探す（品名・規格・品番）" data-i="${{i}}"><div class="hits"></div>`}}
function render(){{tb.innerHTML='';S.forEach((s,i)=>{{if(s.del)return;const tr=document.createElement('tr');tr.dataset.i=i;
 tr.innerHTML=`<td class="no"></td><td><textarea class="name" rows="2" data-i="${{i}}">${{esc(s.name)}}</textarea></td><td>${{codeCell(s,i)}}</td>
 <td><input class="qty" type="number" min="1" step="1" value="${{s.qty??''}}" data-i="${{i}}"></td><td><input class="unit" value="${{esc(s.unit)}}" data-i="${{i}}"></td>
 <td><input class="due" value="${{esc(s.due)}}" data-i="${{i}}"><div class="iso"></div></td><td class="st"></td><td><button class="del" data-i="${{i}}" title="この行を消す">削除</button></td>`;
 tb.appendChild(tr)}});update()}}
function update(){{let n=0,need=0;
 tb.querySelectorAll('tr').forEach(tr=>{{const i=+tr.dataset.i,s=S[i],c=check(s);n++;if(!c.ok)need++;
  tr.querySelector('.no').textContent=n;tr.classList.toggle('need',!c.ok);
  const sel=tr.querySelector('select.code');sel.classList.toggle('empty',!s.code);
  tr.querySelector('.full').textContent=s.code&&label(s.code).length>20?label(s.code):'';
  const cd=tr.querySelector('.cands'),chips=s.cands.slice(0,Math.min(6,s.nc>=2?s.nc:3));
  cd.innerHTML=!s.code&&chips.length?'<span class="lab">候補（押すと選ぶ。ほかは上の一覧か、下で探す）</span>'+chips.map(x=>`<button type="button" data-c="${{esc(x)}}">${{esc(label(x))}}</button>`).join(''):'';
  const fl=k=>c.open.some(f=>f.k===k)||c.errs.some(e=>e[0]===k);
  tr.querySelector('.qty').classList.toggle('flagged',fl('数量'));tr.querySelector('.due').classList.toggle('flagged',fl('納期'));
  tr.querySelector('.iso').textContent=c.d.ok&&c.d.iso?'→ '+c.d.iso:'';
  const msgs=[...c.open.map(f=>f.m),...c.errs.filter(e=>!c.open.some(f=>f.k===e[0])).map(e=>e[1])];
  const ack=c.open.some(f=>f.k!=='品番');
  tr.querySelector('.st').innerHTML=badge(s,c)+(msgs.length?`<ul class="why">${{msgs.map(m=>`<li>${{esc(m)}}</li>`).join('')}}</ul>`:'')+(ack?`<button class="ack" data-i="${{i}}" type="button">確認した</button>`:'')}});
 const sa=document.getElementById('sheetack'),sheetOpen=SHEET.length&&!(sa&&sa.checked);
 const cnt=document.getElementById('count');cnt.textContent=`${{n}}行中 ${{need}}行が要確認`+(sheetOpen?'・用紙の確認が未了':'');
 cnt.className='count '+(need||sheetOpen?'some':'zero');
 document.getElementById('dl').classList.toggle('blocked',!!(need||sheetOpen||!n));
 const msg=document.getElementById('msg');if(msg.classList.contains('err')){{const b=blockers();msg.textContent=b.join('。');if(!b.length)msg.className=''}}}}
function blockers(){{const b=[],bad=[];let n=0;S.forEach(s=>{{if(s.del)return;n++;if(!check(s).ok)bad.push(n)}});
 if(bad.length)b.push(`まだ書き出せません: ${{bad.join('・')}}行目が未確認です`);
 const sa=document.getElementById('sheetack');if(SHEET.length&&!(sa&&sa.checked))b.push((bad.length?'':'まだ書き出せません: ')+'用紙の確認（上の赤い枠）が済んでいません');
 if(!n)b.push('行がありません');return b}}
function setCode(i,c){{const s=S[i];s.code=c;s.edited=true;if(c&&!s.cands.includes(c))s.cands=[c,...s.cands];
 const tr=tb.querySelector(`tr[data-i="${{i}}"]`);const td=tr.children[2];td.innerHTML=codeCell(s,i);update()}}
tb.addEventListener('change',e=>{{const t=e.target,i=+t.dataset.i;if(t.classList.contains('code'))setCode(i,t.value)}});
tb.addEventListener('input',e=>{{const t=e.target,i=+t.dataset.i,s=S[i];if(!s)return;
 if(t.classList.contains('name'))s.name=t.value;
 if(t.classList.contains('qty')){{const v=t.value.trim();s.qty=/^\\d+$/.test(v)?parseInt(v,10):null;s.edited=true;if(s.qty>0)s.flags.forEach(f=>{{if(f.k==='数量')f.open=false}})}}
 if(t.classList.contains('unit')){{s.unit=t.value;s.edited=true}}
 if(t.classList.contains('due')){{s.due=t.value;s.edited=true;if(parseDue(s.due).ok)s.flags.forEach(f=>{{if(f.k==='納期')f.open=false}})}}
 if(t.classList.contains('find')){{const h=t.nextElementSibling,r=search(t.value);
  h.innerHTML=t.value.trim()?(r.length?r.map(c=>`<button type="button" data-c="${{esc(c)}}">${{esc(label(c))}}</button>`).join(''):'<span class="none">一覧に見つかりません</span>'):'';return}}
 update()}});
tb.addEventListener('click',e=>{{const t=e.target.closest('button');if(!t)return;const tr=t.closest('tr'),i=+tr.dataset.i,s=S[i];
 if(t.dataset.c){{setCode(i,t.dataset.c);return}}
 if(t.classList.contains('ack')){{s.flags.forEach(f=>{{if(f.k!=='品番')f.open=false}});s.edited=true;update();return}}
 if(t.classList.contains('del')){{s.del=true;tr.remove();update()}}}});
document.getElementById('add').onclick=()=>{{S.push({{name:'',code:'',qty:null,unit:'',due:'',cands:[],init:'要確認',flags:[],del:false,added:true,edited:true}});render();
 const last=tb.lastElementChild;if(last)last.querySelector('.name').focus()}};
if(SHEET.length){{document.getElementById('sheet').innerHTML=`<div class="sheetwarn" role="alert"><b>用紙ごと要確認</b>（行が抜けている・つながっている可能性があります）<ul>${{SHEET.map(m=>`<li>${{esc(m)}}</li>`).join('')}}</ul><label><input type="checkbox" id="sheetack"> FAXの画像と照らし、行の抜け・余分がないことを確かめた</label></div>`;
 document.getElementById('sheetack').addEventListener('change',update)}}
document.getElementById('dl').onclick=()=>{{const msg=document.getElementById('msg'),b=blockers();
 if(b.length){{msg.textContent=b.join('。');msg.className='err';return}}
 const q=v=>`"${{String(v??'').replace(/"/g,'""')}}"`,lines=['品番,品名,規格,数量,単位,納期,納期日付'];let n=0;
 S.forEach(s=>{{if(s.del)return;n++;const x=BY[s.code]||{{}},d=parseDue(s.due);lines.push([s.code,x.n,x.s,s.qty,s.unit,d.canon,d.iso].map(q).join(','))}});
 const blob=new Blob(["\\ufeff"+lines.join('\\r\\n')],{{type:'text/csv'}});const a=document.createElement('a');a.href=URL.createObjectURL(blob);a.download='order.csv';a.click();
 msg.textContent=`order.csv を書き出しました（${{n}}行）`;msg.className='okm'}};
render();
</script></html>"""


# ---------------------------------------------------------------- 5. 通しで動かす

ROW_FIELDS = ["品名_読み取り", "品番", "判定", "照合品番", "確度", "根拠", "理由", "数量", "単位", "納期", "納期_日付",
              "数量_原文", "納期_原文", "数量確度", "納期確度", "読み取り確度", "候補"]


def write_rows_csv(path: Path, rows: list[dict], sheet_issues: list[str]):
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=ROW_FIELDS, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow({**r, "候補": " ".join(r["候補"]), "理由": " / ".join(f"{k}: {m}" for k, m in r["理由"]),
                        "数量": "" if r["数量"] is None else r["数量"]})
        for m in sheet_issues:  # 用紙ごとの問題も残す（行ではないので品番などは空）
            w.writerow({"品名_読み取り": "（用紙）", "判定": "要確認", "理由": f"用紙: {m}"})


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("image")
    ap.add_argument("catalog")
    ap.add_argument("out_dir")
    ap.add_argument("--aliases", default=None)
    ap.add_argument("--title", default="FAX注文書")
    ap.add_argument("--date", default=None, help="受信日（YYYY-MM-DD）。納期の「明日」「10/20」を日付にする基準。既定は今日")
    ap.add_argument("--lines", default=None,
                    help="読み取り済みの行（1行=「品名 | 数量 | 納期」）。画像を読むAIの結果を渡すとき。無ければTesseractで読む")
    a = ap.parse_args()
    out = Path(a.out_dir)
    out.mkdir(parents=True, exist_ok=True)
    ref = dt.date.fromisoformat(a.date) if a.date else dt.date.today()
    catalog = list(csv.DictReader(open(a.catalog, newline="", encoding="utf-8")))
    aliases = {}
    if a.aliases and Path(a.aliases).exists():
        aliases = {r["別名"]: r["品番"] for r in csv.DictReader(open(a.aliases, newline="", encoding="utf-8"))}
    m = Matcher(catalog, aliases)
    if a.lines:
        lines, sheet_issues = lines_from_text(a.lines), []
    else:
        lines, sheet_issues = read_sheet(a.image)
    rows = build_rows(lines, m, ref)
    write_rows_csv(out / "rows.csv", rows, sheet_issues)
    img_rel = os.path.relpath(Path(a.image).resolve(), out.resolve())
    (out / "review.html").write_text(review_html(str(img_rel), rows, catalog, a.title, sheet_issues, ref), encoding="utf-8")
    cnt = {k: sum(r["判定"] == k for r in rows) for k in ("一致", "要確認", "不明")}
    sheet = f"・用紙ごと要確認（{len(sheet_issues)}件）" if sheet_issues else ""
    print(f"注文行 {len(rows)}行（一致 {cnt['一致']}・要確認 {cnt['要確認']}・不明 {cnt['不明']}{sheet}）→ {out/'rows.csv'} / {out/'review.html'}")


if __name__ == "__main__":
    main()
