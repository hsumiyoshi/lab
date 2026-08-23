#!/usr/bin/env python3
"""無停止率の計測（2026-08-23）。リーグ追加の関門「直近30日で全リーグ95%以上」を測るための計器。

**なぜ必要か**: 「無停止であること自体が堀」と定義し、「95%を超えたら1本追加してよい」という
関門まで置いたのに、**その95%を測る手段が無かった**——数字を運用の条件にしておきながら
測れない状態は、それ自体が「静かに壊れる」型の欠陥（issue #18と同系統）。

**測り方**: GitHub Actionsの実行履歴（schedule起動のみ）を直近30日ぶん取り、
ワークフローごとに `成功した予定実行 / 期待される予定実行` を出す。
期待回数はワークフローのcron定義から機械的に数える（人が書いた表を信じない）。

- 認証: GITHUB_TOKEN があれば使う。無くてもpublicリポなら読める（レート制限のみ）
- 出力: docs/uptime.md（公開側の透明性にも寄与＝「壊れ方を出す」差別化の一部）
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
WINDOW = 30
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


def expected_runs(wf_path: Path, days: int = WINDOW) -> int:
    """cron定義から期待実行回数を数える（毎日=days回、曜日指定=その曜日の回数）。"""
    text = wf_path.read_text()
    total = 0
    for cron in re.findall(r'cron:\s*"([^"]+)"', text):
        parts = cron.split()
        if len(parts) != 5:
            continue
        dow = parts[4]
        if dow == "*":
            total += days
        else:
            wanted = set()
            for token in dow.split(","):
                if "-" in token:
                    a, b = token.split("-")
                    wanted |= set(range(int(a), int(b) + 1))
                else:
                    wanted.add(int(token))
            # cronのdow: 0=日曜。過去days日を数える
            today = datetime.now(UTC).date()
            total += sum(1 for i in range(days)
                         if ((today - timedelta(days=i)).weekday() + 1) % 7 in wanted)
    return total


def main():
    since = (datetime.now(UTC) - timedelta(days=WINDOW)).strftime("%Y-%m-%d")
    rows = []
    for wf, jp in NAMES.items():
        path = HERE / ".github" / "workflows" / wf
        if not path.exists():
            continue
        exp = expected_runs(path)
        try:
            runs = api(f"/repos/{REPO}/actions/workflows/{wf}/runs"
                       f"?event=schedule&created=>{since}&per_page=100")["workflow_runs"]
        except Exception as e:
            print(f"{jp}: 実行履歴の取得に失敗 {type(e).__name__}")
            continue
        ok = sum(1 for r in runs if r["conclusion"] == "success")
        ng = sum(1 for r in runs if r["conclusion"] not in ("success", None))
        # 予定実行がまだ1回も来ていないリーグは「開設直後」として判定対象外にする
        # （0/30 を 0% と表示すると、動いていないのか始まっていないのかが混ざる）
        if not runs:
            rows.append({"league": jp, "wf": wf, "expected": 0, "success": 0,
                         "failed": 0, "rate": None})
            continue
        first = min(r["created_at"][:10] for r in runs)
        age = (datetime.now(UTC).date() - datetime.fromisoformat(first).date()).days + 1
        exp = min(exp, max(1, expected_runs(path, days=age)))
        rate = ok / exp if exp else 0.0
        rows.append({"league": jp, "wf": wf, "expected": exp, "success": ok,
                     "failed": ng, "rate": rate})

    measured = [r for r in rows if r["rate"] is not None]
    rows.sort(key=lambda r: (r["rate"] is not None, r["rate"] if r["rate"] is not None else 1))
    gate_ok = bool(measured) and all(r["rate"] >= GATE for r in measured)
    lines = ["# 無停止率（直近30日・予定実行のみ）", "",
             f"生成: {datetime.now(JST):%Y-%m-%d %H:%M} JST / 関門: 全リーグ {GATE:.0%} 以上で翌月1本追加してよい", "",
             f"**判定: {'✅ 追加してよい' if gate_ok else '⛔ 追加より修理が先'}**", "",
             "| リーグ | 成功 | 期待 | 失敗 | 無停止率 |", "|---|---|---|---|---|"]
    for r in rows:
        if r["rate"] is None:
            lines.append(f"| {r['league']} | — | — | — | 開設直後（予定実行まだ）|")
            continue
        mark = "" if r["rate"] >= GATE else " ⚠"
        lines.append(f"| {r['league']}{mark} | {r['success']} | {r['expected']} | {r['failed']} | {r['rate']:.0%} |")
    lines += ["", "注: 手動実行(workflow_dispatch)は数えない——**放っておいても動くか**が知りたいので。",
              "開設直後のリーグは、期待回数を開設からの日数で丸めている。"]
    out = HERE / "docs" / "uptime.md"
    out.parent.mkdir(exist_ok=True)
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))


if __name__ == "__main__":
    main()
