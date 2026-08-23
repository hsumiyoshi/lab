#!/usr/bin/env python3
"""書籍ランキングリーグ（実験11・S10）: 来週のトップ10に何冊が残るかを当てる。

**なぜこの素材か**: 既存9本が価格・物理・制度に偏っており、**文化消費（人が何を選ぶか）**
の象限が空だった。週次・鍵不要・1回の取得で済む軽さも利点。

**なぜこの予測対象か**: 「来週の1位」は当てにくく運の要素が大きい一方、
**「今週のトップ10のうち何冊が来週も残るか（0〜10）」**は
①正解が機械的に確定 ②脳死ベンチ（全部残る＝10）が明確 ③oracleが自明、と
リーグ規約の3条件を満たす。ヒットの持続性という現象そのものを測れる。

- 提出: 毎週木曜07:00 JST（トーハンの集計日=火曜前後の公表後）→ picks/ に凍結
- 採点: 翌週の取得時に自動
- 脳死ベンチ: `all_stay`（10冊）／`persistence`（前回の実績残存数）
- 判定日: n>=12週
- 礼儀: 週1回の取得のみ
"""

import json
import re
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

JST = timezone(timedelta(hours=9))
HERE = Path(__file__).resolve().parent
URL = "https://www.tohan.jp/bestsellers/"
UA = "Mozilla/5.0 (personal research ledger; https://github.com/hsumiyoshi/lab)"


def fetch_top10() -> dict:
    req = urllib.request.Request(URL, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=40) as r:
        h = r.read().decode("utf-8", errors="replace")
    m = re.search(r"(\d{4})年(\d+)月(\d+)日調べ", h)
    if not m:
        raise RuntimeError("集計日が見つからない（ページ改変の可能性）")
    date = f"{m.group(1)}-{int(m.group(2)):02d}-{int(m.group(3)):02d}"
    j = h.find("総合ランキング")
    k = h.find("文芸書", j)
    seg = h[j:k if k > 0 else j + 20000]
    txt = [re.sub(r"\s+", " ", t).strip() for t in re.split(r"<[^>]+>", seg)]
    txt = [t for t in txt if t and not t.startswith("http")]
    items, buf = [], []
    for t in txt:
        buf.append(t)
        if re.match(r"^本体[\d,]+円", t):
            g = buf[-4:]
            items.append({"title": g[0], "author": g[1] if len(g) > 1 else "",
                          "publisher": g[2] if len(g) > 2 else ""})
            buf = []
    if len(items) < 10:
        raise RuntimeError(f"トップ10を取り切れない（{len(items)}件）——パーサが壊れた可能性")
    return {"date": date, "top10": items[:10]}


def run():
    d = HERE / "data"
    d.mkdir(exist_ok=True)
    hist_f = d / "rankings.json"
    hist = json.loads(hist_f.read_text()) if hist_f.exists() else {}
    cur = fetch_top10()
    prev_dates = sorted(hist)
    is_new = cur["date"] not in hist
    hist[cur["date"]] = cur["top10"]
    hist_f.write_text(json.dumps(hist, ensure_ascii=False, indent=1, sort_keys=True), encoding="utf-8")
    print(f"取得 {cur['date']}: {'新規' if is_new else '既知'} / 履歴 {len(hist)}週")

    # 採点: 前回の提出（=前回のトップ10がいくつ残るか）を今回の実績で
    led_f = d / "ledger.json"
    led = json.loads(led_f.read_text()) if led_f.exists() else {}
    if prev_dates:
        prev = prev_dates[-1]
        pick_f = HERE / "picks" / f"{prev}.json"
        if pick_f.exists() and prev not in led and is_new:
            prev_titles = {x["title"] for x in hist[prev]}
            actual = len(prev_titles & {x["title"] for x in cur["top10"]})
            p = json.loads(pick_f.read_text())["picks"]
            led[prev] = {"scored_against": cur["date"], "actual": actual, "picks": p,
                         "err": {k: abs(v - actual) for k, v in p.items()}}
            print(f"採点 {prev}→{cur['date']}: 残存 {actual}冊 / 誤差 {led[prev]['err']}")
            led_f.write_text(json.dumps(led, ensure_ascii=False, indent=1, sort_keys=True), encoding="utf-8")

    # 提出: 今回のトップ10のうち何冊が来週も残るか
    scored = [v["actual"] for v in led.values()]
    picks = {"all_stay": 10,
             "persistence": scored[-1] if scored else 10,
             "mean": round(sum(scored) / len(scored)) if scored else 10}
    (HERE / "picks").mkdir(exist_ok=True)
    (HERE / "picks" / f"{cur['date']}.json").write_text(json.dumps(
        {"basis_date": cur["date"], "submitted_at": datetime.now(JST).isoformat(timespec="seconds"),
         "picks": picks, "titles": [x["title"] for x in cur["top10"]]},
        ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"提出 {cur['date']}: 来週の残存数予測 {picks}")
    report(led, hist)


def report(led, hist):
    rows = list(led.values())
    lines = ["# 書籍ランキングリーグ（トップ10の残存数）", "",
             f"予測対象: 今週のトップ10のうち来週も残る冊数（0〜10） / 生成: {datetime.now(JST):%Y-%m-%d %H:%M} JST", "",
             f"収集済み {len(hist)}週", ""]
    if rows:
        names = sorted({k for r in rows for k in r["err"]})
        lines += [f"## 成績（{len(rows)}週）", "", "| 機体 | MAE(冊) | 出場 |", "|---|---|---|"]
        for n in names:
            e = [r["err"][n] for r in rows if n in r["err"]]
            lines.append(f"| {n} | {sum(e)/len(e):.2f} | {len(e)} |")
        actuals = [r["actual"] for r in rows]
        lines += ["", f"実績の残存数: {actuals}（平均 {sum(actuals)/len(actuals):.1f}冊）",
                  "", "注: 判定日 n≥12週。**ヒットの持続性そのもの**を測る指標なので、"
                  "数字が安定していれば「文化消費の慣性」の定量値になる。"]
    else:
        lines += ["（初回の採点は翌週）"]
    (HERE / "reports" / "books_forward.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines[:8]))


if __name__ == "__main__":
    run()
