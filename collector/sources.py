#!/usr/bin/env python3
"""収集定義（宣言）とパーサ。**新しい収集の追加はここに30行足すだけ**。

フェッチの礼儀・生の保存・重複排除・件数とスキーマの検査・障害ログ・原子的書き込みは
すべて runtime.py が引き受ける。ここに書くのは「どこから取り、何を取り出すか」だけ。
"""

import re

# ---------------- パーサ（変わるのはここだけ） ----------------

def parse_kyuden_curtail(html: str) -> list:
    """九電の再エネ出力制御見通し。表の全行（本土＋離島3地点×3日）を返す。"""
    i = html.find("再生可能エネルギー出力制御見通し")
    if i < 0:
        return []       # 件数0はruntimeがアラートにする（黙って成功しない）
    seg = html[i:i + 8000]
    pub = re.search(r"\((\d+)月(\d+)日\s*(\d+)時(\d+)分発表\)", seg)
    days = []
    out = []
    for r in re.findall(r"<tr[^>]*>(.*?)</tr>", seg, re.S):
        cells = [re.sub(r"<[^>]+>", "", c).replace("&nbsp;", " ").strip()
                 for c in re.findall(r"<t[dh][^>]*>(.*?)</t[dh]>", r, re.S)]
        cells = [c for c in cells if c]
        for c in cells:
            m = re.match(r"(\d+)月(\d+)日", c)
            if m and (m.group(1), m.group(2)) not in days:
                days.append((m.group(1), m.group(2)))
        if cells and cells[0] in ("九州本土", "対馬", "壱岐", "五島", "離島"):
            area = cells[0]
            for (mm, dd), v in zip(days[:3], cells[1:4]):
                out.append({"id": f"kyuden-{area}-2026-{int(mm):02d}-{int(dd):02d}",
                            "area": area, "date": f"2026-{int(mm):02d}-{int(dd):02d}",
                            "curtail": "なし" if v.strip() in ("―", "-", "－") else v.strip(),
                            "published": (f"{int(pub.group(1)):02d}-{int(pub.group(2)):02d} "
                                          f"{int(pub.group(3)):02d}:{int(pub.group(4)):02d}") if pub else None})
    return out


def parse_tohan_books(html: str) -> list:
    """トーハン週間ベストセラー総合。順位・書名・著者・出版社・価格。"""
    m = re.search(r"(\d{4})年(\d+)月(\d+)日調べ", html)
    if not m:
        return []
    date = f"{m.group(1)}-{int(m.group(2)):02d}-{int(m.group(3)):02d}"
    j = html.find("総合ランキング")
    k = html.find("文芸書", j)
    seg = html[j:k if k > 0 else j + 20000]
    txt = [re.sub(r"\s+", " ", t).strip() for t in re.split(r"<[^>]+>", seg)]
    txt = [t for t in txt if t and not t.startswith("http")]
    out, buf = [], []
    for t in txt:
        buf.append(t)
        if re.match(r"^本体[\d,]+円", t):
            g = buf[-4:]
            out.append({"id": f"tohan-{date}-{len(out)+1}", "date": date, "rank": len(out) + 1,
                        "title": g[0], "author": g[1] if len(g) > 1 else "",
                        "publisher": g[2] if len(g) > 2 else "", "price": t})
            buf = []
    return out


# ---------------- 宣言（1ソース＝1エントリ） ----------------

SOURCES = {
    "kyuden_curtail": {
        "name": "kyuden_curtail",
        "url": "https://www.kyuden.co.jp/td_power_usages/pc.html",
        "fetch": {"interval": 2.0, "retries": 4},
        "raw": {"save": True},          # 当日分しか表示されない＝一回性あり
        "expect": {"rows": [3, 24], "schema": ["area", "date", "curtail"]},
        "parser": parse_kyuden_curtail,
        "cadence": "毎日 07:00 / 17:10 JST",
        "grade": "A",
        "note": "見通しは当日限り。過去の実績は非公表なので、これを逃すと二度と取れない",
    },
    "tohan_books": {
        "name": "tohan_books",
        "url": "https://www.tohan.jp/bestsellers/",
        "fetch": {"interval": 2.0, "retries": 3},
        "raw": {"save": True},          # 過去4週分しか表示されない
        "expect": {"rows": [10, 10], "schema": ["rank", "title", "publisher"]},
        "parser": parse_tohan_books,
        "cadence": "毎週木 07:00 JST",
        "grade": "A",
        "note": "ジャンル別はHTMLに無くPDFのみ（2026-08-24確認）",
    },
}
