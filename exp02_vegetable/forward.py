#!/usr/bin/env python3
"""出荷タイミングゲームの週次フォワード運用。

sim.py の戦略（凍結 2026-08-13）をFORWARD_START以降のISO週に適用して採点する。
判断は前営業日までの価格のみ（sim.pyの因果性をそのまま利用）。台帳はステートレス。

戦略: oracle（上限）/ equal（毎営業日に均等出荷=脳死ベンチ）/ first_day /
      weekshape8 / stop_rule。スコアは選んだ日の単価、成績は対oracle%。
採点対象: 週の日曜までデータが揃った週のみ（ベジ探は2〜3日遅れ）。
"""

from datetime import date, timedelta
from pathlib import Path

import pandas as pd

import sim

HERE = Path(__file__).parent
REPORTS = HERE / "reports"
FORWARD_START = date(2026, 8, 17)
FREEZE_NOTE = "戦略凍結 2026-08-13（sim.pyの5戦略＋equal、成績=対oracle%、週=ISO月〜日）"
STRATS = ["oracle", "equal", "weekshape8", "stop_rule", "first_day"]


def build_ledger() -> pd.DataFrame:
    rows = []
    for item, s in sorted(sim.load().items()):
        last_data = s.index.max().date()
        res = sim.run(s)
        for (y, w), days in sim.weeks_of(s):
            ws = date.fromisocalendar(y, w, 1)
            if ws < FORWARD_START or ws + timedelta(days=6) > last_data:
                continue
            week = f"{y}-W{w:02d}"
            if week not in res.index:
                continue
            r = res.loc[week]
            equal = float(pd.Series({d: s[d] for d in days}).mean())
            rows.append({"week": week, "item": item, "oracle": r["oracle"],
                         "equal": round(equal, 1),
                         "weekshape8": r["weekshape8"], "stop_rule": r["stop_rule"],
                         "first_day": r["first_day"]})
    return pd.DataFrame(rows)


def report(ledger: pd.DataFrame) -> str:
    lines = ["# 出荷タイミングゲーム フォワード台帳", "",
             f"{FREEZE_NOTE} / 生成: {pd.Timestamp.now():%Y-%m-%d %H:%M}", ""]
    if len(ledger) == 0:
        lines.append(f"初ラウンドは {FORWARD_START} の週（採点はデータが日曜まで揃う翌週半ば）。")
    else:
        lines.append("| 週 | 品目 | oracle | equal | weekshape8 | stop_rule | first_day |")
        lines.append("|---|---|---|---|---|---|---|")
        for _, r in ledger.iterrows():
            lines.append("| " + " | ".join(str(r[c]) for c in
                         ["week", "item"] + STRATS) + " |")
        lines.append("")
        pct = {n: ledger[n].sum() / ledger["oracle"].sum() * 100 for n in STRATS[1:]}
        rank = sorted(pct.items(), key=lambda kv: -kv[1])
        lines.append("累計対oracle%: " + " / ".join(f"{n} {v:.1f}%" for n, v in rank))
    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    REPORTS.mkdir(exist_ok=True)
    ledger = build_ledger()
    ledger.to_csv(REPORTS / "veg_ledger.csv", index=False)
    (REPORTS / "veg_forward.md").write_text(report(ledger))
    print(report(ledger))
