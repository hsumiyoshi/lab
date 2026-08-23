#!/usr/bin/env python3
"""気象リーグ（実験3・issue #10）: 気象庁の予報を常設ベンチにして戦う。

**なぜこのリーグか**: 脳死ベンチ（persistence）だけでなく、**国家予算の予報**を
常設ベンチに置く唯一のリーグ。負けても情報になる——「どの条件で気象庁に負けるか」
が分かれば、電力リーグの入力（予報の信頼度）にそのまま効く。

- 予測対象: 翌日の東京（アメダス44132）の**日最高気温**
- 提出: 毎日18:00 JST（気象庁の17:00発表を見た後）→ picks/YYYY-MM-DD.json に凍結
- 採点: 翌々日。絶対誤差（℃）。oracle = 誤差0
- 機体: jma（気象庁そのまま）/ openmeteo（別モデル）/ blend（平均）/ debias（自分の系統誤差を差し引く）
- 脳死ベンチ: persistence（今日の実績＝明日の予測）
- 判定日: n>=30日。撤退基準は本社 experiments.md の規約に明文化
"""

import json
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

JST = timezone(timedelta(hours=9))
HERE = Path(__file__).resolve().parent
PICKS = HERE / "picks"
DATA = HERE / "data"
STATION = "44132"          # アメダス東京
AREA = "130000"            # 東京都の府県天気予報
DEBIAS_WINDOW = 30


def _get(url: str, tries: int = 3):
    for i in range(tries):
        try:
            with urllib.request.urlopen(url, timeout=30) as r:
                return json.loads(r.read())
        except Exception:
            if i == tries - 1:
                raise


def jma_tomorrow_max(target) -> float | None:
    """気象庁の府県天気予報から対象日の最高気温を取る（17:00発表を想定）。"""
    j = _get(f"https://www.jma.go.jp/bosai/forecast/data/forecast/{AREA}.json")
    for series in j[0]["timeSeries"]:
        a = series["areas"][0]
        if "temps" not in a:
            continue
        for t, v in zip(series["timeDefines"], a["temps"]):
            ts = datetime.fromisoformat(t)
            if ts.date() == target and ts.hour >= 9 and v != "":
                return float(v)
    return None


def openmeteo_tomorrow_max(target) -> float | None:
    url = ("https://api.open-meteo.com/v1/forecast?latitude=35.69&longitude=139.75"
           "&daily=temperature_2m_max&timezone=Asia%2FTokyo&forecast_days=3")
    try:
        j = _get(url, tries=2)
    except Exception as e:
        print(f"openmeteo取得失敗（{type(e).__name__}）→ この機体は棄権")
        return None
    d = j["daily"]
    for t, v in zip(d["time"], d["temperature_2m_max"]):
        if t == target.isoformat() and v is not None:
            return float(v)
    return None


def amedas_daily_max(day) -> float | None:
    """アメダスの3時間ファイルを1日分たどって日最高気温を出す（実況＝採点の正解）。"""
    temps = []
    for h in range(0, 24, 3):
        url = f"https://www.jma.go.jp/bosai/amedas/data/point/{STATION}/{day:%Y%m%d}_{h:02d}.json"
        try:
            j = _get(url, tries=2)
        except Exception:
            continue
        for rec in j.values():
            t = rec.get("temp")
            if t and t[1] == 0:      # 品質フラグ0=正常
                temps.append(float(t[0]))
    return max(temps) if temps else None


def load_ledger() -> dict:
    f = DATA / "ledger.json"
    return json.loads(f.read_text()) if f.exists() else {}


def save_ledger(d):
    DATA.mkdir(exist_ok=True)
    (DATA / "ledger.json").write_text(json.dumps(d, ensure_ascii=False, indent=1), encoding="utf-8")


def debias_offset(ledger) -> float:
    """直近30日で jma がどれだけ系統的にズレていたか（実況−予報の平均）。"""
    devs = [v["actual"] - v["picks"]["jma"]
            for v in list(ledger.values())[-DEBIAS_WINDOW:]
            if v.get("actual") is not None and v.get("picks", {}).get("jma") is not None]
    return round(sum(devs) / len(devs), 2) if devs else 0.0


def submit():
    """翌日分の予測を凍結してコミット対象に書く（あと出し禁止）。"""
    now = datetime.now(JST)
    target = (now + timedelta(days=1)).date()
    ledger = load_ledger()
    jma = jma_tomorrow_max(target)
    om = openmeteo_tomorrow_max(target)
    today_actual = amedas_daily_max(now.date())      # persistence用（当日の途中までの最高）
    off = debias_offset(ledger)

    picks = {}
    if jma is not None:
        picks["jma"] = jma
        picks["debias"] = round(jma + off, 1)
    if om is not None:
        picks["openmeteo"] = om
    if jma is not None and om is not None:
        picks["blend"] = round((jma + om) / 2, 1)
    if today_actual is not None:
        picks["persistence"] = today_actual          # 脳死ベンチ

    PICKS.mkdir(exist_ok=True)
    (PICKS / f"{target}.json").write_text(json.dumps(
        {"target": str(target), "submitted_at": now.isoformat(timespec="seconds"),
         "debias_offset": off, "picks": picks,
         "absent": [n for n in ("jma", "openmeteo") if n not in picks]},
        ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"提出 {target}: {picks}（debias補正 {off:+.2f}℃）")


def score():
    """提出済みで未採点の日を、アメダス実況で採点する。"""
    ledger = load_ledger()
    today = datetime.now(JST).date()
    for f in sorted(PICKS.glob("*.json")):
        day = datetime.fromisoformat(f.stem).date()
        if day >= today or f.stem in ledger and ledger[f.stem].get("actual") is not None:
            continue
        actual = amedas_daily_max(day)
        if actual is None:
            print(f"{day}: 実況が取れず採点保留")
            continue
        p = json.loads(f.read_text())
        ledger[f.stem] = {"actual": actual, "picks": p["picks"],
                          "err": {k: round(abs(v - actual), 2) for k, v in p["picks"].items()}}
        print(f"採点 {day}: 実況 {actual}℃ / 誤差 {ledger[f.stem]['err']}")
    save_ledger(ledger)
    report(ledger)


def report(ledger):
    rows = [v for v in ledger.values() if v.get("actual") is not None]
    lines = ["# 気象リーグ（気象庁が常設ベンチ）", "",
             f"予測対象: 翌日の東京の日最高気温 / 採点: 絶対誤差(℃) / 生成: {datetime.now(JST):%Y-%m-%d %H:%M} JST", ""]
    if not rows:
        lines += ["（初日の採点待ち）"]
    else:
        names = sorted({k for r in rows for k in r["err"]})
        lines += [f"## 成績（{len(rows)}日）", "", "| 機体 | MAE(℃) | 最大誤差 | 出場 |", "|---|---|---|---|"]
        stats = []
        for n in names:
            e = [r["err"][n] for r in rows if n in r["err"]]
            if e:
                stats.append((sum(e) / len(e), n, max(e), len(e)))
        for mae, n, mx, cnt in sorted(stats):
            mark = "**" if n == "jma" else ""
            lines.append(f"| {mark}{n}{mark} | {mae:.2f} | {mx:.1f} | {cnt} |")
        lines += ["", "注: `jma`＝気象庁の17:00発表そのまま（強敵ベンチ）。"
                  "`persistence`＝今日の実績を明日の予測にする脳死ベンチ。"
                  "判定日 n≥30日。"]
    (HERE / "reports" / "weather_forward.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines[:12]))


if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1 and sys.argv[1] == "score":
        score()
    else:
        submit()
        score()
