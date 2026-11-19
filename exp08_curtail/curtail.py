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
import time
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

JST = timezone(timedelta(hours=9))
HERE = Path(__file__).resolve().parent
URL = "https://www.kyuden.co.jp/td_power_usages/pc.html"
# 九電のサーバー保守中（2026-10-22〜）は、見通しの表が一時ページへ移る。本来のページを先に読み、
# 表が無ければ一時ページを読む（保守明けに一時ページが古いまま残っても、本来のページが勝つ）
TEMP_URL = "https://www.kyuden.co.jp/td/power-usages/temporary.html"
UA = "Mozilla/5.0 (personal research ledger; https://github.com/hsumiyoshi/lab)"


TRIES = 3          # 九電が応答しない時の試し直し（2026-11-20、02:20 JST に30秒の時間切れで1回で落ちた）
WAITS = (60, 180)  # 試し直しの前に待つ秒。合わせて約4分＋通信の時間


def fetch_outlook(raw_tag: str = "", tries: int = TRIES, waits=WAITS) -> dict:
    """九電のでんき予報から出力制御見通しを取る。返り値: {日付: 'あり'/'なし'} と発表時刻。

    通信の失敗（時間切れ・接続できない）でも、表の形の失敗でも、一時ページを試す。
    両方とも通信で落ちた時は、待ってから全体を試し直す。表の形の失敗は、試し直しても同じなので待たない。"""
    err = None
    for attempt in range(tries):
        net_fail = False
        for url in (URL, TEMP_URL):
            try:
                return _parse_outlook(url, raw_tag)
            except RuntimeError as e:
                err = e
            except OSError as e:      # TimeoutError・URLError・HTTPError は、どれも OSError の仲間
                err = e
                net_fail = True
                print(f"  {url}: {type(e).__name__} {e}")
        if not net_fail or attempt == tries - 1:
            break
        w = waits[min(attempt, len(waits) - 1)]
        print(f"  九電が応答しない → {w}秒待って試し直す（{attempt + 2}/{tries}回目）")
        time.sleep(w)
    raise err


def _parse_outlook(url: str, raw_tag: str = "") -> dict:
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=60) as r:
        h = r.read().decode("utf-8", errors="replace")
    i = h.find("再生可能エネルギー出力制御見通し")
    if i < 0:
        raise RuntimeError("見通しセクションが見つからない（ページ改変の可能性）")
    seg = h[i:i + 8000]
    # 一時ページは全角の括弧で「（10月31日 17時0分発表）」と書く
    pub = re.search(r"[\(（](\d+)月(\d+)日\s*(\d+)時(\d+)分発表[\)）]", seg)
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
    today = datetime.now(JST).date()
    out = {}
    for (m, d), v in zip(days[:3], honshu):
        out[str(_near_date(m, d, today))] = "なし" if v.strip() in ("―", "-", "－") else v.strip()
    # 生情報の保存: 抽出後のフラグだけでなく、表の全行（離島含む）と発表時刻を残す
    #（2026-08-24。当日分しか表示されないページなので、後から問い直す手段が他に無い）
    allrows = []
    for r_ in rows:
        cells = [re.sub(r"<[^>]+>", "", c).replace("&nbsp;", " ").strip()
                 for c in re.findall(r"<t[dh][^>]*>(.*?)</t[dh]>", r_, re.S)]
        cells = [c for c in cells if c]
        if cells:
            allrows.append(cells)
    rawdir = HERE / "data" / "raw"
    rawdir.mkdir(parents=True, exist_ok=True)
    # 朝の読み直し（raw_tag="_am"）は別の名前にする。夕方の予定の回は GitHub の遅れで翌日の02時ごろに動き、
    # 朝の回と同じ日付の名前になる。名前を分けないと、後から書いた方が先の控えを消す
    (rawdir / f"kyuden_outlook_{datetime.now(JST):%Y-%m-%d}{raw_tag}.json").write_text(
        json.dumps({"fetched_at": datetime.now(JST).isoformat(timespec="seconds"),
                    "days": days, "table": allrows}, ensure_ascii=False, separators=(",", ":")),
        encoding="utf-8")
    return {"published": f"{int(pub.group(1)):02d}-{int(pub.group(2)):02d} "
                         f"{int(pub.group(3)):02d}:{int(pub.group(4)):02d}" if pub else "不明",
            "pub_date": str(_near_date(pub.group(1), pub.group(2), today)) if pub else None,
            "outlook": out}


def _near_date(m, d, today):
    """年の無い「月・日」を、今日にいちばん近い年の日付にする（12/31 に読む「1月1日」を翌年にする）。"""
    best = None
    for y in (today.year - 1, today.year, today.year + 1):
        try:
            c = datetime(y, int(m), int(d)).date()
        except ValueError:      # 2月29日の無い年
            continue
        if best is None or abs((c - today).days) < abs((best - today).days):
            best = c
    return best


def merge_outlook(res: dict) -> int:
    """見通しを台帳に入れる。どの発表の値かを outlook_source.json に残し、古い発表で新しい値を上書きしない。

    採点のラベルは「前の日の17:00の発表」の値だけ（collect_and_score）。2日先・3日先の値で採点すると、
    「可能性あり」が「当日指示予定」と合わず、必ず外れになるため、発表の日付を値と一緒に持つ（2026-11-20）。"""
    d = HERE / "data"
    d.mkdir(exist_ok=True)
    hist_f, src_f = d / "outlook_history.json", d / "outlook_source.json"
    hist = json.loads(hist_f.read_text()) if hist_f.exists() else {}
    src = json.loads(src_f.read_text()) if src_f.exists() else {}
    if not res.get("pub_date"):
        print("  発表の日付が読めない → 台帳に入れない")
        return 0
    n = 0
    for day, v in res["outlook"].items():
        if src.get(day, "") > res["pub_date"]:
            print(f"  {day}: 手元の値（{src[day]} 発表）の方が新しい → そのまま")
            continue
        hist[day], src[day] = v, res["pub_date"]
        n += 1
    hist_f.write_text(json.dumps(hist, ensure_ascii=False, indent=1, sort_keys=True), encoding="utf-8")
    src_f.write_text(json.dumps(src, ensure_ascii=False, indent=1, sort_keys=True), encoding="utf-8")
    return n


def refetch_outlook():
    """朝の回で、前の日の17:00の発表を読み直す（夕方の回が落ちた日の取りこぼしを拾う）。

    九電のページは、次の17:00まで前の日の発表を出している。夕方の回が落ちても、朝に読めば採点のラベルが残る
    （2026-11-20、夕方の回が九電の時間切れで落ちた）。17:00 を過ぎた後は読まない——
    その日の発表は翌日分のラベルそのものなので、予測を凍結する前に手元へ入れない。
    読めなくても提出は止めない（ここは予備。採点の取り込みは夕方の回が持つ）。"""
    now = datetime.now(JST)
    if now.hour >= 17:
        print("朝の読み直し: 17:00 を過ぎたので読まない")
        return
    try:
        res = fetch_outlook(raw_tag="_am")
    except Exception as e:
        print(f"朝の読み直し: 取れなかった（提出は続ける）: {type(e).__name__} {e}")
        return
    # 取った後にも確かめる。試し直しで17:00をまたいだ時や、九電が早めに出した時に、その日の発表を入れない
    if not res.get("pub_date") or res["pub_date"] >= str(now.date()):
        print(f"朝の読み直し: 発表が {res['published']} なので入れない（前の日より前の発表だけ入れる）")
        return
    n = merge_outlook(res)
    print(f"朝の読み直し（{res['published']} 発表・{n}日分を更新）: {res['outlook']}")


def submit():
    """翌日分の予測を、九電の発表より前に凍結する（あと出し禁止）。"""
    now = datetime.now(JST)
    target = (now + timedelta(days=1)).date()
    refetch_outlook()
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
    res = fetch_outlook()
    n = merge_outlook(res)
    print(f"取込（{res['published']} 発表・{n}日分を更新）: {res['outlook']}")
    hist_f, src_f = d / "outlook_history.json", d / "outlook_source.json"
    hist = json.loads(hist_f.read_text()) if hist_f.exists() else {}
    src = json.loads(src_f.read_text()) if src_f.exists() else {}

    led_f = d / "ledger.json"
    led = json.loads(led_f.read_text()) if led_f.exists() else {}
    for f in sorted((HERE / "picks").glob("*.json")):
        day = f.stem
        if day not in hist or day in led:
            continue
        # ラベルは前の日の17:00の発表の値だけ。まだ無い日は採点を待つ（前の日の発表を取り逃した日は、欠けのまま）
        want = str(datetime.strptime(day, "%Y-%m-%d").date() - timedelta(days=1))
        if src.get(day) != want:
            print(f"採点を待つ {day}: 手元の値は {src.get(day, '発表の日付なし')} の発表（{want} の発表が要る）")
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
    today = str(datetime.now(JST).date())
    lost = [f.stem for f in sorted((HERE / "picks").glob("*.json")) if f.stem < today and f.stem not in led]
    if lost:
        lines += [f"採点できなかった日 {len(lost)}日（前の日の発表を取り逃した）: {', '.join(lost[-5:])}", ""]
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
