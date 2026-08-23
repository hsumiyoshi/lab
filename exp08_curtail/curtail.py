#!/usr/bin/env python3
"""出力制御リーグ（実験8・S8）: 九電の「翌日の再エネ出力制御見通し」を先回りして当てる。

**設計上の要点**: 制御の「実績量」は時系列で公表されていない（確認済み 2026-08-23）。
公表されるのは毎日17:00の**見通し（九州本土・離島3地点／3日先まで）**だけ。
そこで予測対象を「実績量」ではなく**九電が17:00に出す見通しそのもの**にする。
ラベルが毎日必ず publish され、曖昧さがなく、oracle が自明という利点がある。

- 予測対象: 翌日の九州本土の出力制御（あり/なし）
- 提出: 毎日07:00 JST（＝九電の17:00発表より10時間早い）→ picks/ に凍結
- 採点: 同日17:05に、九電の発表と突き合わせ
- 脳死ベンチ: `always_none`（常に「なし」）と `persistence`（今日と同じ）
- 機体: `solar`（日射予報が高く需要が低い休日ほど制御と予測）ほか、材料が貯まってから追加
- 判定日: n>=60日。**夏は制御がほぼ出ないので、本番は春（4-5月）と秋（10-11月）**
- 礼儀: 1日2回・User-Agent明示・robots順守。負荷をかけない
"""

import json
import re
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

JST = timezone(timedelta(hours=9))
HERE = Path(__file__).resolve().parent
URL = "https://www.kyuden.co.jp/td_power_usages/pc.html"
UA = "Mozilla/5.0 (personal research ledger; https://github.com/hsumiyoshi/lab)"


def fetch_outlook() -> dict:
    """九電のでんき予報から出力制御見通しを取る。返り値: {日付: 'あり'/'なし'} と発表時刻。"""
    req = urllib.request.Request(URL, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=30) as r:
        h = r.read().decode("utf-8", errors="replace")
    i = h.find("再生可能エネルギー出力制御見通し")
    if i < 0:
        raise RuntimeError("見通しセクションが見つからない（ページ改変の可能性）")
    seg = h[i:i + 8000]
    pub = re.search(r"\((\d+)月(\d+)日\s*(\d+)時(\d+)分発表\)", seg)
    rows = re.findall(r"<tr[^>]*>(.*?)</tr>", seg, re.S)
    # 日付はセル単位で拾う。生HTMLだと "8月24日<br>(月曜日)" のようにタグで分断されるため
    days, honshu = [], None
    for r_ in rows:
        cells = [re.sub(r"<[^>]+>", "", c).replace("&nbsp;", " ").strip()
                 for c in re.findall(r"<t[dh][^>]*>(.*?)</t[dh]>", r_, re.S)]
        cells = [c for c in cells if c]
        for c in cells:
            m = re.match(r"(\d+)月(\d+)日", c)
            if m and (m.group(1), m.group(2)) not in days:
                days.append((m.group(1), m.group(2)))
        if cells and cells[0] == "九州本土":
            honshu = cells[1:4]
    if honshu is None or not days:
        raise RuntimeError("表の形が変わった（九州本土の行を特定できない）")
    year = datetime.now(JST).year
    out = {}
    for (m, d), v in zip(days[:3], honshu):
        out[f"{year}-{int(m):02d}-{int(d):02d}"] = "なし" if v.strip() in ("―", "-", "－") else v.strip()
    return {"published": f"{int(pub.group(1)):02d}-{int(pub.group(2)):02d} "
                         f"{int(pub.group(3)):02d}:{int(pub.group(4)):02d}" if pub else "不明",
            "outlook": out}


def submit():
    """翌日分の予測を、九電の発表より前に凍結する（あと出し禁止）。"""
    now = datetime.now(JST)
    target = (now + timedelta(days=1)).date()
    hist = json.loads((HERE / "data" / "outlook_history.json").read_text()) \
        if (HERE / "data" / "outlook_history.json").exists() else {}
    today_val = hist.get(str(now.date()), "なし")
    picks = {
        "always_none": "なし",                 # 脳死ベンチ1: 常になし
        "persistence": today_val,              # 脳死ベンチ2: 今日と同じ
    }
    (HERE / "picks").mkdir(exist_ok=True)
    (HERE / "picks" / f"{target}.json").write_text(json.dumps(
        {"target": str(target), "submitted_at": now.isoformat(timespec="seconds"), "picks": picks},
        ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"提出 {target}: {picks}")


def collect_and_score():
    """17:05に九電の発表を取り込み、提出済みの予測を採点する。"""
    d = HERE / "data"
    d.mkdir(exist_ok=True)
    hist_f = d / "outlook_history.json"
    hist = json.loads(hist_f.read_text()) if hist_f.exists() else {}
    res = fetch_outlook()
    hist.update(res["outlook"])
    hist_f.write_text(json.dumps(hist, ensure_ascii=False, indent=1, sort_keys=True), encoding="utf-8")
    print(f"取込（{res['published']} 発表）: {res['outlook']}")

    led_f = d / "ledger.json"
    led = json.loads(led_f.read_text()) if led_f.exists() else {}
    for f in sorted((HERE / "picks").glob("*.json")):
        day = f.stem
        if day not in hist or day in led:
            continue
        p = json.loads(f.read_text())["picks"]
        led[day] = {"actual": hist[day],
                    "picks": p,
                    "hit": {k: (v == hist[day]) for k, v in p.items()}}
        print(f"採点 {day}: 実際 {hist[day]} / {led[day]['hit']}")
    led_f.write_text(json.dumps(led, ensure_ascii=False, indent=1, sort_keys=True), encoding="utf-8")
    report(led, hist)


def report(led, hist):
    rows = list(led.values())
    n_ctrl = sum(1 for v in hist.values() if v != "なし")
    lines = ["# 出力制御リーグ（九電の見通しを先回りする）", "",
             f"予測対象: 翌日の九州本土の出力制御（あり/なし） / 生成: {datetime.now(JST):%Y-%m-%d %H:%M} JST", "",
             f"収集済み {len(hist)}日 / うち制御ありの日 **{n_ctrl}日**（夏は基底率がほぼゼロ。本番は春と秋）", ""]
    if rows:
        names = sorted({k for r in rows for k in r["hit"]})
        lines += [f"## 成績（{len(rows)}日）", "", "| 機体 | 的中 | 的中率 |", "|---|---|---|"]
        for n in names:
            h = [r["hit"][n] for r in rows if n in r["hit"]]
            lines.append(f"| {n} | {sum(h)}/{len(h)} | {sum(h)/len(h):.1%} |")
        lines += ["", "注: 制御ありの日がゼロの間は `always_none` が100%になる。"
                  "**この指標が意味を持つのは制御が実際に出る季節から**——それまでは収集期間。"]
    else:
        lines += ["（初回の採点待ち）"]
    (HERE / "reports" / "curtail_forward.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines[:8]))


if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1 and sys.argv[1] == "collect":
        collect_and_score()
    else:
        submit()
