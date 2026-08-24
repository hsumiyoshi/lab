#!/usr/bin/env python3
"""スモークテスト（2026-08-23）: 壊れたコードが本番スケジュールに乗る前に落とす。

**外部APIを叩かない**——コミット済みデータだけで、各リーグの「壊れやすい部分」を検査する。
これまでの障害5件のうち、外部要因（429・IPv6）以外の3件はここで捕まえられた:
  ①依存の宣言漏れ（import できるか）
  ②成果物が保存されない（gitignore で無視されていないか）
  ③自分のコピー運用によるリグレッション（機能が消えていないか）
"""

import importlib.util
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
FAILS = []


def check(name: str, ok: bool, detail: str = ""):
    # 失敗時だけ理由を出す（成功時に失敗理由が並ぶと、通っているのか落ちているのか読めない）
    print(f"{'✅' if ok else '❌'} {name}{('' if ok else ' — ' + detail) if detail else ''}")
    if not ok:
        FAILS.append(f"{name}: {detail}")


def import_module(path: Path):
    spec = importlib.util.spec_from_file_location(path.stem, path)
    m = importlib.util.module_from_spec(spec)
    sys.path.insert(0, str(path.parent))
    spec.loader.exec_module(m)
    return m


def main():
    # ① 依存: 各リーグの本体がimportできるか（matplotlib未導入で落ちた事故の再発防止）
    for rel in ["exp01_jepx/forward.py", "exp01_jepx/contracts.py", "exp02_vegetable/forward.py",
                "exp03_weather/forecast.py", "exp08_curtail/curtail.py",
                "exp10_disaster/disaster.py", "exp10_disaster/firms.py", "exp11_books/books.py",
                "uptime.py", "build_site.py"]:
        p = HERE / rel
        if not p.exists():
            check(f"import {rel}", False, "ファイルが無い")
            continue
        try:
            import_module(p)
            check(f"import {rel}", True)
        except Exception as e:
            check(f"import {rel}", False, f"{type(e).__name__}: {e}")

    # ② 成果物の保存経路: 台帳ディレクトリがgitignoreされていないか
    for d in ["exp01_jepx/picks", "exp01_jepx/reports", "exp03_weather/data", "exp08_curtail/data",
              "exp10_disaster/data", "exp11_books/data", "exp01_jepx/data/weather_actual.csv"]:
        r = subprocess.run(["git", "check-ignore", "-q", d], cwd=HERE)
        check(f"追跡可能 {d}", r.returncode != 0, "gitignoreされている（収集しても保存されない）")

    # ③ リグレッション: 台帳の自己申告機能が消えていないか（コピー運用で一度消した）
    try:
        fwd = import_module(HERE / "exp01_jepx" / "forward.py")
        check("欠場の自己申告 absence_warnings", hasattr(fwd, "absence_warnings"))
        check("契約違反バナー CONTRACT_ISSUES", hasattr(fwd, "CONTRACT_ISSUES"))
    except Exception as e:
        check("forward.py の機能検査", False, str(e))

    # ④ 契約検査そのものが壊れていないか（故障を注入して検出できるか）
    try:
        import pandas as pd
        c = import_module(HERE / "exp01_jepx" / "contracts.py")
        bad = pd.DataFrame({"年月日": ["2026/01/01"] * 47, "時刻コード": list(range(1, 48)),
                            "システムプライス(円/kWh)": [10.0] * 47})
        check("契約検査が量の異常を検出", len(c.check_spot(bad)) > 0, "48コマ欠けを見逃した")
    except Exception as e:
        check("契約検査の動作", False, str(e))

    # ⑤ 収集骨格: 宣言が揃っているか＋アラートが本当に鳴るか（鳴らない検査は無意味）
    try:
        sys.path.insert(0, str(HERE / "collector"))
        rt = import_module(HERE / "collector" / "runtime.py")
        sc = import_module(HERE / "collector" / "sources.py")
        for name, spec in sc.SOURCES.items():
            missing = {"name", "url", "expect", "parser", "grade"} - set(spec)
            check(f"宣言 {name}", not missing, f"項目が欠けている {sorted(missing)}")
        check("アラート: 0件を検知", len(rt.check_expectations("t", [], {"rows": [1, 5]})) > 0)
        check("アラート: 列欠けを検知",
              len(rt.check_expectations("t", [{"a": 1}], {"schema": ["a", "b"]})) > 0)
        check("アラート: 正常系は無音",
              len(rt.check_expectations("t", [{"a": 1}], {"rows": [1, 5], "schema": ["a"]})) == 0)
    except Exception as e:
        check("収集骨格の検査", False, f"{type(e).__name__}: {e}")

    print()
    if FAILS:
        print(f"❌ {len(FAILS)}件の失敗:")
        for f in FAILS:
            print(" -", f)
        sys.exit(1)
    print("✅ 全項目通過")


if __name__ == "__main__":
    main()
