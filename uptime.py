#!/usr/bin/env python3
"""無停止率の計測（2026-08-23）。リーグ追加の関門「直近30日で全リーグ95%以上」を測るための計器。

**なぜ必要か**: 「無停止であること自体が堀」と定義し、「95%を超えたら1本追加してよい」という
関門まで置いたのに、**その95%を測る手段が無かった**——数字を運用の条件にしておきながら
測れない状態は、それ自体が「静かに壊れる」型の欠陥（issue #18と同系統）。

**測り方**: cron定義から「予定時刻」を1つずつ並べ、各予定にGitHub Actionsの実行を1件ずつ
突き合わせる。成功・失敗・未実行の3つに分ける——**失敗と未実行は原因も直し方も違う**のに、
以前は両方が「成功でない」に潰れていた。

- 認証: GITHUB_TOKEN があれば使う。無くてもpublicリポなら読める（レート制限のみ）
- 出力: docs/uptime.md（公開側の透明性にも寄与＝「壊れ方を出す」差別化の一部）

**分母の決め方**（2026-09-11に作り直した。3つ直している）:

1. **まだ来ていない予定を分母に入れない。** 計器は水曜12:00 JST、青果CIは水曜14:00 JSTなので、
   その日の予定を数えたうえで成功に数えられず、**毎週かならず1回ぶん損をしていた**（青果50%の正体）
2. **起動の遅れを待つ。** GitHub Actionsの定時起動は数時間ずれる（2026-09-09の青果は4時間29分遅れ）。
   予定時刻から `GRACE` 時間は「まだ来ていない」として数えない
3. **回数の少ないリーグは窓を延ばす。** 30日固定だと週次は分母4で、**1回の失敗が25pt**になる。
   直った失敗が窓から出るまで⛔が続き、関門が実態を映さない。予定が `MIN_EXPECTED` 件に届くまで
   窓を最大 `MAX_WINDOW` 日まで遡る（リーグ開設前までは遡らない）
"""

import json
import os
import re
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

UTC = timezone.utc
JST = timezone(timedelta(hours=9))
HERE = Path(__file__).resolve().parent
REPO = "hsumiyoshi/lab"
WINDOW = 30        # 既定の窓（日）。日次リーグはこれで足りる
MAX_WINDOW = 120   # 回数の少ないリーグで遡ってよい上限（日）
MIN_EXPECTED = 8   # これだけの予定が入るまで窓を延ばす
GRACE = 6          # 予定時刻からこの時間は起動待ちとみなす（実測の最大遅延4.5時間に余裕を足す）
GATE = 0.95

NAMES = {
    "forward.yml": "電力", "veg_forward.yml": "青果", "quake_forward.yml": "地震",
    "weather_forward.yml": "気象", "curtail.yml": "出力制御",
    "disaster.yml": "全球災害", "books.yml": "書籍",
}


def api(path: str):
    req = urllib.request.Request(f"https://api.github.com{path}",
                                 headers={"Accept": "application/vnd.github+json",
                                          "User-Agent": "lab-uptime"})
    tok = os.environ.get("GITHUB_TOKEN", "")
    if tok:
        req.add_header("Authorization", f"Bearer {tok}")
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.loads(r.read())


def cron_slots(wf_path: Path, start: datetime, end: datetime) -> list[datetime]:
    """cron定義を予定時刻の列に開く。このリポジトリのcronは分・時が固定値で、日・月は `*`。"""
    slots = []
    for cron in re.findall(r'cron:\s*"([^"]+)"', wf_path.read_text()):
        parts = cron.split()
        if len(parts) != 5:
            continue
        minute, hour, _dom, _mon, dow = parts
        if not minute.isdigit() or not hour.isdigit():
            continue
        wanted = set()
        if dow != "*":
            for token in dow.split(","):
                if "-" in token:
                    a, b = token.split("-")
                    wanted |= set(range(int(a), int(b) + 1))
                else:
                    wanted.add(int(token))
        day = start.date()
        while day <= end.date():
            # cronのdow: 0=日曜
            if dow == "*" or (day.weekday() + 1) % 7 in wanted:
                t = datetime(day.year, day.month, day.day, int(hour), int(minute), tzinfo=UTC)
                if start <= t <= end:
                    slots.append(t)
            day += timedelta(days=1)
    return sorted(slots)


def window_start(wf_path: Path, born: datetime, now: datetime) -> datetime:
    """予定が MIN_EXPECTED 件入るまで窓を遡る。リーグ開設前までは遡らない。"""
    deadline = now - timedelta(hours=GRACE)
    for days in range(WINDOW, MAX_WINDOW + 1, 7):
        start = max(now - timedelta(days=days), born)
        if len(cron_slots(wf_path, start, deadline)) >= MIN_EXPECTED or start == born:
            return start
    return max(now - timedelta(days=MAX_WINDOW), born)


def judge(slots: list[datetime], runs: list[dict]) -> tuple[int, int, int, int]:
    """予定1件に実行1件を割り当て、成功・失敗・未実行に分ける。

    再実行で同じ予定に複数の実行がぶら下がることがある。**成功が1つでもあれば成功**とする
    ——知りたいのは「その日の台帳が埋まったか」であって、何回で埋まったかではない。
    """
    verdict = {t: None for t in slots}
    for r in runs:
        at = datetime.fromisoformat(r["created_at"].replace("Z", "+00:00"))
        prev = [t for t in slots if t <= at]
        if not prev or at - prev[-1] > timedelta(hours=24):
            continue  # どの予定にも紐づかない（窓の外の起動）
        t = prev[-1]
        if r["conclusion"] == "success":
            verdict[t] = "ok"
        elif r["conclusion"] not in (None, "success") and verdict[t] != "ok":
            verdict[t] = "ng"
    ok = sum(1 for v in verdict.values() if v == "ok")
    ng = sum(1 for v in verdict.values() if v == "ng")
    streak = 0
    for t in reversed(slots):  # 直近から遡って、崩れるまで数える
        if verdict[t] != "ok":
            break
        streak += 1
    return ok, ng, len(slots) - ok - ng, streak


def main():
    now = datetime.now(UTC)
    rows = []
    for wf, jp in NAMES.items():
        path = HERE / ".github" / "workflows" / wf
        if not path.exists():
            continue
        try:
            born = datetime.fromisoformat(
                api(f"/repos/{REPO}/actions/workflows/{wf}")["created_at"].replace("Z", "+00:00"))
            start = window_start(path, born, now)
            runs = api(f"/repos/{REPO}/actions/workflows/{wf}/runs"
                       f"?event=schedule&created=>{start:%Y-%m-%d}&per_page=100")["workflow_runs"]
        except Exception as e:
            print(f"{jp}: 実行履歴の取得に失敗 {type(e).__name__}")
            continue
        slots = cron_slots(path, start, now - timedelta(hours=GRACE))
        if not slots:
            # 予定実行がまだ1回も来ていないリーグは「開設直後」として判定対象外にする
            # （0/30 を 0% と表示すると、動いていないのか始まっていないのかが混ざる）
            rows.append({"league": jp, "expected": 0, "success": 0, "failed": 0,
                         "missing": 0, "days": 0, "streak": 0, "rate": None})
            continue
        ok, ng, miss, streak = judge(slots, runs)
        rows.append({"league": jp, "expected": len(slots), "success": ok, "failed": ng,
                     "missing": miss, "days": (now - start).days, "streak": streak,
                     "rate": ok / len(slots)})

    measured = [r for r in rows if r["rate"] is not None]
    rows.sort(key=lambda r: (r["rate"] is not None, r["rate"] if r["rate"] is not None else 1))
    gate_ok = bool(measured) and all(r["rate"] >= GATE for r in measured)
    lines = ["# 無停止率（予定実行のみ）", "",
             f"生成: {datetime.now(JST):%Y-%m-%d %H:%M} JST / 関門: 全リーグ {GATE:.0%} 以上で翌月1本追加してよい", "",
             f"**判定: {'✅ 追加してよい' if gate_ok else '⛔ 追加より修理が先'}**", "",
             "| リーグ | 成功 | 失敗 | 未実行 | 予定 | 窓 | 無停止率 | 連続成功 |",
             "|---|---|---|---|---|---|---|---|"]
    for r in rows:
        if r["rate"] is None:
            lines.append(f"| {r['league']} | — | — | — | — | — | 開設直後（予定実行まだ）| — |")
            continue
        mark = "" if r["rate"] >= GATE else " ⚠"
        lines.append(f"| {r['league']}{mark} | {r['success']} | {r['failed']} | {r['missing']} "
                     f"| {r['expected']} | {r['days']}日 | {r['rate']:.0%} | {r['streak']}回 |")
    lines += ["",
              "注: 手動実行(workflow_dispatch)は数えない——**放っておいても動くか**が知りたいので。",
              f"予定時刻から{GRACE}時間は起動待ちとして数えない（定時起動は実測で最大4.5時間遅れる）。",
              f"回数の少ないリーグは、予定が{MIN_EXPECTED}件入るまで窓を最大{MAX_WINDOW}日まで遡る"
              "——週次を30日で測ると分母4になり、1回の失敗が25pt動いてしまう。",
              "**失敗と未実行を分けている。** 落ちたのか、そもそも起動しなかったのかで直す場所が違う。",
              "**連続成功は「いま健全か」を見る列。** 無停止率は過去の失敗を窓から出るまで引きずるので、"
              "直ったかどうかはこちらで読む。"]
    out = HERE / "docs" / "uptime.md"
    out.parent.mkdir(exist_ok=True)
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))


if __name__ == "__main__":
    main()
