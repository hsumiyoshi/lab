#!/usr/bin/env python3
"""全球災害リーグ（実験10・S5）: GDACSに翌日いくつ新規イベントが載るかを当てる。

**なぜGDACSか**: NASA FIRMSはAPIキー登録が要る（保留）。GDACSのRSSは鍵なしで全球の
災害イベント（山火事WF・地震EQ・熱帯低気圧TC・洪水FL・干ばつDR）をアラート級つきで配信する。

**なぜこの予測対象か**: RSSは**巻き取り式**（古いイベントは落ちる）ので、
毎日取り込んで自分で履歴を作らない限り**後から遡れない**——#20の類型③「立ち会いが要る」
に該当する、こちらが継続することで初めて生まれる資産。

- 予測対象: 翌日(UTC)に新規登録される**山火事(WF)イベント数**（全球・最頻カテゴリ）
- 提出: 毎日 07:00 JST（＝前日UTCの締めの後）→ picks/ に凍結
- 採点: 翌々日に、収集した履歴で確定
- 脳死ベンチ: `persistence`（今日と同じ）／`ma7`（直近7日平均の四捨五入）
- 判定日: n>=30日
- 礼儀: 1日1回の取得・User-Agent明示
"""

import json
import re
import urllib.request
import sys as _sys, pathlib as _pl
_sys.path.insert(0, str(_pl.Path(__file__).resolve().parent.parent / "collector"))
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pathlib as _pl, sys
sys.path.insert(0, str(_pl.Path(__file__).resolve().parent.parent / "collector"))  # 共通ランタイム

UTC = timezone.utc
JST = timezone(timedelta(hours=9))
HERE = Path(__file__).resolve().parent
RSS = "https://www.gdacs.org/xml/rss.xml"
UA = "Mozilla/5.0 (personal research ledger; https://github.com/hsumiyoshi/lab)"
TARGET_TYPE = "WF"


def fetch_events() -> list:
    from runtime import fetch as _rt
    x = _rt(RSS, interval=2.0, retries=4, ua=UA)
    out = []
    for item in re.findall(r"<item>(.*?)</item>", x, re.S):
        def tag(t):
            m = re.search(rf"<{t}>(.*?)</{t}>", item, re.S)
            return m.group(1).strip() if m else None
        eid, etype, frm = tag("gdacs:eventid"), tag("gdacs:eventtype"), tag("gdacs:fromdate")
        if not (eid and etype and frm):
            continue
        try:
            day = datetime.strptime(frm[:25].strip(), "%a, %d %b %Y %H:%M:%S").date()
        except ValueError:
            continue
        # 生情報の保存（2026-08-24）: 巻き取り式RSSなので、抽出後の5項目だけ残すと
        # 後から「震源の深さ」「影響人口」「severity」を問い直せない。全タグを保持する。
        allfields = {k: v.strip() for k, v in re.findall(r"<([\w:]+)>([^<]*)</\1>", item)}
        out.append({"id": f"{etype}{eid}", "type": etype, "from": str(day),
                    "alert": tag("gdacs:alertlevel"), "country": tag("gdacs:country"),
                    "raw": allfields})
    return out


def collect():
    """RSSを取り込み、イベントIDで重複排除して履歴に積む（巻き取り式なので毎日必要）。"""
    f = HERE / "data" / "events.json"
    hist = json.loads(f.read_text()) if f.exists() else {}
    new = 0
    for e in fetch_events():
        if e["id"] not in hist:
            hist[e["id"]] = e
            new += 1
        elif not hist[e["id"]].get("raw") and e.get("raw"):
            # 生情報の後付け: 保存を始める前に取り込んだイベントにも、まだ配信に
            # 残っているうちに全タグを補う（RSSから落ちたら二度と取れない）
            hist[e["id"]]["raw"] = e["raw"]
    f.parent.mkdir(exist_ok=True)
    f.write_text(json.dumps(hist, ensure_ascii=False, indent=1, sort_keys=True), encoding="utf-8")
    print(f"取込: 新規 {new}件 / 履歴 {len(hist)}件")
    return hist


def daily_counts(hist, etype=TARGET_TYPE) -> dict:
    c = {}
    for e in hist.values():
        if e["type"] == etype:
            c[e["from"]] = c.get(e["from"], 0) + 1
    return c


def submit(hist):
    now = datetime.now(JST)
    target = (datetime.now(UTC) + timedelta(days=1)).date()
    counts = daily_counts(hist)
    days = sorted(counts)
    # 直近の確定日（今日UTCは未確定なので除く）
    settled = [d for d in days if d < str(datetime.now(UTC).date())]
    last = counts.get(settled[-1], 0) if settled else 0
    ma7 = round(sum(counts.get(d, 0) for d in settled[-7:]) / max(1, len(settled[-7:]))) if settled else 0
    picks = {"persistence": last, "ma7": ma7}
    (HERE / "picks").mkdir(exist_ok=True)
    (HERE / "picks" / f"{target}.json").write_text(json.dumps(
        {"target": str(target), "submitted_at": now.isoformat(timespec="seconds"),
         "type": TARGET_TYPE, "picks": picks, "history_days": len(settled)},
        ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"提出 {target}（{TARGET_TYPE}件数）: {picks}")


def score(hist):
    counts = daily_counts(hist)
    led_f = HERE / "data" / "ledger.json"
    led = json.loads(led_f.read_text()) if led_f.exists() else {}
    today_utc = str(datetime.now(UTC).date())
    for f in sorted((HERE / "picks").glob("*.json")):
        day = f.stem
        if day >= today_utc or day in led:
            continue        # 当日UTCはまだ確定しない
        actual = counts.get(day, 0)
        p = json.loads(f.read_text())["picks"]
        led[day] = {"actual": actual, "picks": p,
                    "err": {k: abs(v - actual) for k, v in p.items()}}
        print(f"採点 {day}: 実績 {actual}件 / 誤差 {led[day]['err']}")
    led_f.write_text(json.dumps(led, ensure_ascii=False, indent=1, sort_keys=True), encoding="utf-8")
    report(led, counts)


def report(led, counts):
    rows = list(led.values())
    lines = ["# 全球災害リーグ（GDACS・山火事イベント数）", "",
             f"予測対象: 翌日(UTC)に新規登録される山火事イベント数 / 生成: {datetime.now(JST):%Y-%m-%d %H:%M} JST", "",
             f"収集済み {len(counts)}日分（RSSは巻き取り式のため、**毎日取り込まないと後から遡れない**）", ""]
    if rows:
        names = sorted({k for r in rows for k in r["err"]})
        lines += [f"## 成績（{len(rows)}日）", "", "| 機体 | MAE(件) | 出場 |", "|---|---|---|"]
        for n in names:
            e = [r["err"][n] for r in rows if n in r["err"]]
            lines.append(f"| {n} | {sum(e)/len(e):.2f} | {len(e)} |")
        lines += ["", "注: まだ脳死ベンチ2種のみ。気象データと結合した機体は履歴が貯まってから投入する。判定日 n≥30。"]
    else:
        lines += ["（初回の採点待ち）"]
    recent = sorted(counts)[-7:]
    if recent:
        lines += ["", "## 直近の実績", "", "| 日(UTC) | 件数 |", "|---|---|"] + \
                 [f"| {d} | {counts[d]} |" for d in recent]
    (HERE / "reports" / "disaster_forward.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines[:8]))


if __name__ == "__main__":
    h = collect()
    submit(h)
    score(h)