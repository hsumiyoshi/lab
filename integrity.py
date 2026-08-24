#!/usr/bin/env python3
"""システム整合性チェック（2026-08-24）。

**動機**: 1日に3回、同じ形の失敗をした——作る → 台帳や表示への配線を忘れる → 後で誰かが見つける。
①新リーグのdata/がgitignoreで保存されない ②移行したソースがマニフェスト未登録
③新設リーグがダッシュボードに載っていない。**個別に直しても、次に作る時にまた別の配線が抜ける。**

そこで「部品が存在するか」ではなく**「部品どうしの関係が繋がっているか」**を検査する。
- smoke_test.py = 部品が動くか（import・アラートが鳴るか）
- data_guard.py = データが消えていないか
- uptime.py     = 動き続けているか
- **integrity.py = 配線が繋がっているか** ← これ

見つけるのは「静かに壊れる」種類だけ。**壊れていないものを親切に指摘しない**（狼少年にしない）。
"""

import json
import re
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
PROBLEMS = []
CHECKED = 0


def check(ok: bool, msg: str):
    global CHECKED
    CHECKED += 1
    if not ok:
        PROBLEMS.append(msg)


def ignored(path: str) -> bool:
    return subprocess.run(["git", "check-ignore", "-q", path], cwd=HERE).returncode == 0


def main():
    site = (HERE / "build_site.py").read_text()
    tpl = (HERE / "site_template.html").read_text()
    manifest = {d["path"] for d in json.loads((HERE / "data_manifest.json").read_text())["datasets"]}
    workflows = {f.name: f.read_text() for f in (HERE / ".github" / "workflows").glob("*.yml")}
    sys.path.insert(0, str(HERE / "collector"))
    from sources import SOURCES

    # ① リーグ → ダッシュボード（パネル・ナビ・JSのid配列の3箇所すべて）
    leagues = {  # ディレクトリ: パネルid
        "exp01_jepx": "lg-power", "exp02_vegetable": "lg-veg", "exp05_quake": "lg-quake",
        "exp03_weather": "lg-weather", "exp08_curtail": "lg-curtail",
        "exp10_disaster": "lg-disaster", "exp11_books": "lg-books",
        "exp04_realestate": "lg-re", "exp07_satellite": "lg-sat",
    }
    for d, pid in leagues.items():
        if not (HERE / d).exists():
            continue
        # パネルは build_site.py が生成する場合と、テンプレに直書きの場合がある（電力は後者）
        check(f'"{pid}"' in site or f"'{pid}'" in site or f'id="{pid}"' in tpl,
              f"{d}: ダッシュボードにパネル {pid} が無い")
        check(f'href="#{pid}"' in tpl, f"{d}: ナビに {pid} が無い（パネルはあっても辿り着けない）")
        check(f'"{pid}"' in tpl, f"{d}: JSのid配列に {pid} が無い（切替が効かない）")

    # ② 台帳・収集データ → マニフェスト登録（登録漏れは保全の番人の視界外）
    for name in SOURCES:
        check(f"collector/data/{name}.json" in manifest,
              f"collector/{name}: data_manifest.json に未登録（番人が見ない）")
    for d in ("exp03_weather", "exp08_curtail", "exp10_disaster", "exp11_books"):
        led = f"{d}/data/ledger.json"
        if (HERE / led).exists():
            check(led in manifest, f"{led}: マニフェスト未登録")

    # ③ マニフェスト → git追跡（登録しているのに追跡外＝CI上では存在しない）
    for p in manifest:
        if (HERE / p).exists():
            check(not ignored(p), f"{p}: マニフェストにあるがgitignore（CIでは消失に見える）")

    # ④ ワークフロー → 実行するスクリプトの実在
    for wf, body in workflows.items():
        for m in re.finditer(r"python3 (\S+\.py)", body):
            script = m.group(1)
            wd = re.search(r"working-directory: (\S+)", body)
            cand = (HERE / wd.group(1) / script) if wd else (HERE / script)
            check(cand.exists() or (HERE / script).exists(),
                  f"{wf}: 実行対象 {script} が見つからない")

    # ⑤ ワークフロー → 生成物をコミットしているか（収集して捨てる事故の再発防止）
    for wf, body in workflows.items():
        if "python3" in body and "git add" in body and wf not in ("smoke.yml",):
            adds = re.search(r"git add ([^\n]+)", body)
            check(bool(adds and adds.group(1).strip()), f"{wf}: git add の対象が空")

    # ⑥ 収集宣言 → CIで実行されているか（宣言だけして動かないのを防ぐ）
    coll = workflows.get("collector.yml", "")
    check("collect.py" in coll, "collector.yml が collect.py を実行していない")

    print(f"整合性チェック: {CHECKED}項目")
    if PROBLEMS:
        print(f"\n❌ 配線が切れている {len(PROBLEMS)}件:")
        for p in PROBLEMS:
            print(" -", p)
        sys.exit(1)
    print("✅ すべて繋がっている")


if __name__ == "__main__":
    main()
