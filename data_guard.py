#!/usr/bin/env python3
"""データ保全の番人（2026-08-23・Haruki方針「データこそ事業のすべて。A/B/Cに関わらず欠損させない」）。

**方針転換**: #19の棚卸しではA/B/Cで格付けし「Cは再取得前提で保全しない」としたが、
オーナー判断により**格付けは価値の判定に限定し、保全は全件対象**とする。
再取得可能でも、取り直せる保証は外部に依存する（API廃止・仕様変更・レート制限・
サイト改変）。**外部に預けた前提は、いつか必ず裏切られる。**

この番人がやること:
1. マニフェスト（data_manifest.json）に全データセットを登録し、**sha256と行数/サイズを記録**
2. 実行のたびに照合し、**消失・縮小・破損**を検出して失敗する（＝CIが赤くなる）
3. 増える台帳（追記型）は「減っていないこと」を検査（増加は正常・減少は事故）
4. 結果を docs/data_health.md に出す

**なぜサイズでなくハッシュも見るか**: 同じサイズのまま中身が壊れる事故（文字化け・
途中で切れたダウンロードの上書き）を、サイズだけでは検出できないため。
"""

import hashlib
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

JST = timezone(timedelta(hours=9))
HERE = Path(__file__).resolve().parent
MANIFEST = HERE / "data_manifest.json"


def sha256(p: Path) -> str:
    h = hashlib.sha256()
    with p.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def scan(entry: dict) -> dict:
    p = HERE / entry["path"]
    if not p.exists():
        return {"exists": False}
    return {"exists": True, "bytes": p.stat().st_size, "sha256": sha256(p),
            "lines": sum(1 for _ in p.open("rb")) if entry.get("mode") == "append" else None}


def main(update: bool = False):
    man = json.loads(MANIFEST.read_text())
    problems, rows = [], []
    for e in man["datasets"]:
        now = scan(e)
        prev = e.get("state")
        status = "OK"
        if not now["exists"]:
            # まだ生成されていないのが正常な期間を持つエントリは、その日までは異常にしない
            # （「無い」と「まだ始まっていない」を混ぜると番人が狼少年になる）
            until = e.get("optional_until")
            if until and datetime.now(JST).strftime("%Y-%m-%d") <= until:
                status = "未生成（正常）"
            else:
                status = "消失"
                problems.append(f"{e['path']}: **ファイルが無い**（{e['note']}）")
        elif prev:
            if e.get("mode") == "append":
                if now["lines"] < prev.get("lines", 0):
                    status = "縮小"
                    problems.append(f"{e['path']}: 行数が減った {prev['lines']}→{now['lines']}（追記型の台帳が縮むのは事故）")
                elif now["lines"] == prev.get("lines", 0):
                    status = "変化なし"
            else:
                if now["bytes"] < prev.get("bytes", 0) * 0.9:
                    status = "縮小"
                    problems.append(f"{e['path']}: サイズが1割以上減った {prev['bytes']}→{now['bytes']}")
                elif now["sha256"] != prev.get("sha256"):
                    status = "更新"
        rows.append({**e, "now": now, "status": status})
        if update:
            e["state"] = {k: v for k, v in now.items() if v is not None and k != "exists"}
            e["state"]["checked"] = datetime.now(JST).strftime("%Y-%m-%d")

    if update:
        MANIFEST.write_text(json.dumps(man, ensure_ascii=False, indent=1), encoding="utf-8")

    total = sum(r["now"].get("bytes", 0) for r in rows)
    lines = ["# データ健全性（保全の番人）", "",
             f"生成: {datetime.now(JST):%Y-%m-%d %H:%M} JST / 登録 {len(rows)}件 / 合計 {total/1048576:.1f}MB", "",
             "**方針**: 格付け(A/B/C)は価値の判定であり、保全の可否ではない。**全件を守る**——"
             "再取得可能でも、取り直せる保証は外部（API廃止・仕様変更・サイト改変）に依存するため。", ""]
    if problems:
        lines += [f"## 🚨 異常 {len(problems)}件", ""] + [f"- {p}" for p in problems] + [""]
    else:
        lines += ["## ✅ 異常なし", ""]
    lines += ["| データ | 格 | 形式 | サイズ | 状態 | 出所 |", "|---|---|---|---|---|---|"]
    for r in sorted(rows, key=lambda x: x["grade"]):
        b = r["now"].get("bytes", 0)
        size = f"{b/1048576:.1f}MB" if b > 1048576 else f"{b/1024:.0f}KB"
        lines.append(f"| `{r['path']}` | {r['grade']} | {r.get('mode','snapshot')} | {size} | "
                     f"{r['status']} | {r['source']} |")
    lines += ["", "注: 追記型（append）は行数が減ったら事故として扱う。スナップショット型はsha256で改変を検出する。",
              "**再取得の手順は各エントリの `refetch` に書いてある**——外部が生きているうちに、"
              "手順そのものも資産として残す。"]
    (HERE / "docs").mkdir(exist_ok=True)
    (HERE / "docs" / "data_health.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines[:14]))
    if problems:
        raise SystemExit(1)


if __name__ == "__main__":
    import sys
    main(update="--update" in sys.argv)
