#!/usr/bin/env python3
"""出荷タイミングゲーム: 毎週1ロットを「どの開市日に売るか」の戦略対戦。

ルール:
- 各ISO週（開市日3日以上）が1ラウンド。週内のちょうど1日を選んで全量出荷
- 判断に使えるのは前営業日までの価格のみ（因果性）。oracleだけが答えを見る
- スコア = 選んだ日の卸売単価（円/kg）。oracle比が予測・助言の価値の上限

戦略:
- oracle     : 週内最高値の日（後知恵の上限）
- first_day  : 週の最初の開市日に即売り（保存リスク回避型の慣行に相当）
- mid_day    : 週の真ん中の開市日
- last_day   : 週の最後の開市日まで引っ張る
- weekshape8 : 直近8週の曜日別相対価格が最も高い曜日に売る（カレンダー派）
- stop_rule  : 前営業日の価格が直近21日平均以上なら今日売る、なければ待つ
               （最終日は強制出荷。オプショナル・ストッピング型）
"""

from pathlib import Path

import pandas as pd

HERE = Path(__file__).parent
MIN_OPEN = 3
WARMUP_WEEKS = 8


def load() -> dict:
    frames = [pd.read_csv(f, parse_dates=["date"]) for f in (HERE / "data").glob("veg_*.csv")]
    df = pd.concat(frames, ignore_index=True)
    df = df[df["origin"] == "総計"]
    return {item: g.set_index("date")["price"].sort_index() for item, g in df.groupby("item")}


def weeks_of(s: pd.Series):
    iso = s.index.isocalendar()
    for (y, w), idx in s.groupby([iso["year"], iso["week"]]).groups.items():
        days = sorted(idx)
        if len(days) >= MIN_OPEN:
            yield (y, w), days


def weekday_shape(s: pd.Series, upto, weeks=8) -> dict:
    """直近weeks週の曜日別相対価格（週平均=1に正規化）"""
    hist = s[s.index < upto].tail(weeks * 6)
    if len(hist) < 12:
        return {}
    rel = hist / hist.rolling(6, min_periods=3).mean()
    return rel.groupby(rel.index.dayofweek).mean().to_dict()


def run(s: pd.Series) -> pd.DataFrame:
    rows = []
    week_list = list(weeks_of(s))
    for (y, w), days in week_list[WARMUP_WEEKS:]:
        prices = {d: s[d] for d in days}
        row = {"week": f"{y}-W{w:02d}",
               "oracle": max(prices.values()),
               "first_day": prices[days[0]],
               "mid_day": prices[days[len(days) // 2]],
               "last_day": prices[days[-1]]}
        # weekshape8: 直近8週の曜日形で最良の開市曜日を選ぶ
        shape = weekday_shape(s, days[0])
        if shape:
            best = max(days, key=lambda d: shape.get(d.dayofweek, 0))
            row["weekshape8"] = prices[best]
        # stop_rule: 前営業日価格 >= 直近21日平均 なら売る
        hist_all = s[s.index < days[0]]
        chosen = days[-1]
        for d in days[:-1]:
            past = s[s.index < d]
            if len(past) < 21:
                continue
            if past.iloc[-1] >= past.tail(21).mean():
                chosen = d
                break
        row["stop_rule"] = prices[chosen]
        rows.append(row)
    return pd.DataFrame(rows).set_index("week").dropna()


if __name__ == "__main__":
    STRATS = ["oracle", "weekshape8", "stop_rule", "first_day", "mid_day", "last_day"]
    agg = {}
    print("=== 出荷タイミングゲーム（週次・対oracle%） ===")
    for item, s in sorted(load().items()):
        res = run(s)
        pct = {n: res[n].mean() / res["oracle"].mean() * 100 for n in STRATS}
        agg[item] = pct
        n = len(res)
        gap = res["oracle"].mean() - max(res[n2].mean() for n2 in STRATS[1:])
        best = max(STRATS[1:], key=lambda n2: pct[n2])
        print(f"\n{item}（{n}週, oracle平均 {res['oracle'].mean():.0f}円/kg）")
        for name in STRATS:
            print(f"  {name:<12}{pct[name]:>6.1f}%")
        print(f"  最良ヒューリスティック: {best} / 予測の価値の上限: {gap:.1f}円/kg")
    table = pd.DataFrame(agg).T
    print("\n=== 総合（対oracle%平均） ===")
    print(table[STRATS[1:]].mean().sort_values(ascending=False).round(1).to_string())
