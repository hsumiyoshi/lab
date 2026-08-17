#!/usr/bin/env python3
"""oracle残差の帰因分析（issue #1）。

weekshapeの各日の取りこぼし（oracle損益 − weekshape損益）を
カウンターファクチュアル置換で分解する:
  充電側の外し = 充電だけoracleに置換したときの改善
  放電側の外し = 放電だけoracleに置換したときの改善
  交互作用     = 残差 −（充電側＋放電側）
層別: 平日/週末 × 信号（強/平常、日射予報の平年乖離で判定）。

高速化のためrun_day/choose_splitと同値のnumpy実装を使う（冒頭でsim実装との
一致をサンプル日でassert——分析が凍結採点系と食い違わないことの保証）。
"""

from pathlib import Path

import numpy as np
import pandas as pd

import forward
import sim

HERE = Path(__file__).parent
REPORTS = HERE / "reports"


def fast_run_day(prices: np.ndarray, charge: set, discharge: set) -> float:
    """sim.run_dayと同値（コマ番号1..48のnumpy版）"""
    soc = 0.0
    pnl = 0.0
    for k in range(1, 49):
        p = prices[k - 1]
        if k in charge and soc < sim.CAP:
            buy = min(sim.POWER, (sim.CAP - soc) / sim.EFF)
            soc += buy * sim.EFF
            pnl -= buy * p
        elif k in discharge and soc > 0:
            sell = min(sim.POWER, soc)
            soc -= sell
            pnl += sell * sim.EFF * p
    return pnl


def fast_oracle(prices: np.ndarray):
    """sim.choose_split(当日実価格)と同値の厳密探索。

    充電は境界前の最安4（容量制約が効かないため厳密）。放電は
    「時間順最後のコマだけ減量」の構造を使い、最後コマLを総当たり
    ＋L以前の上位3で厳密に解く。
    """
    best, best_pair = -1e18, None
    idx = np.arange(1, 49)
    for b in range(4, 45):          # 境界コマ（この位置以降が放電候補）
        before = prices[:b]
        c = set(idx[:b][np.argsort(before)[:4]])
        after_idx = idx[b:]
        after_p = prices[b:]
        if len(after_idx) < 4:
            continue
        # 放電: 最後コマLを走査し、L以前（境界以降）の上位3を満量で
        for li in range(3, len(after_idx)):
            pool_p = after_p[:li]
            if len(pool_p) < 3:
                continue
            top3 = np.sort(pool_p)[-3:]
            d = set(after_idx[np.argsort(after_p[:li])[-3:]]) | {int(after_idx[li])}
            v = fast_run_day(prices, c, d)
            if v > best:
                best, best_pair = v, (c, d)
    return best_pair


def main():
    sim.load_weather()
    df = sim.load()
    dates = sorted(d for d in df["date"].unique())

    # --- 同値性の検証（凍結採点系と食い違っていないことの保証） ---
    for probe in dates[-3:]:
        today = df[df["date"] == probe].set_index("koma")["sp"]
        pv = today.reindex(range(1, 49)).to_numpy()
        if np.isnan(pv).any():
            continue
        co, do_ = sim.choose_split(today)
        fo = fast_oracle(pv)
        assert abs(sim.run_day(today, co, do_) - fast_run_day(pv, *fo)) < 1e-6, probe

    rows = []
    for day in dates:
        today = df[df["date"] == day].set_index("koma")["sp"]
        pv = today.reindex(range(1, 49)).to_numpy()
        if np.isnan(pv).any():
            continue
        hist = df[df["date"] < day]
        pred = sim.pred_weekshape(hist, None, day)
        if pred is None:
            continue
        cm, dm = sim.choose_split(pred)
        if not cm:
            continue
        oc, od = fast_oracle(pv)
        base = fast_run_day(pv, cm, dm)
        opnl = fast_run_day(pv, oc, od)
        r = opnl - base
        chg = fast_run_day(pv, oc, dm) - base   # 充電だけ直した改善
        dis = fast_run_day(pv, cm, od) - base   # 放電だけ直した改善
        dev, thr = forward.rad_dev(pd.Timestamp(day))
        signal = None if dev is None else ("強" if dev >= thr else "平常")
        rows.append({"date": pd.Timestamp(day), "oracle": opnl, "ws": base,
                     "resid": r, "charge": chg, "discharge": dis,
                     "inter": r - chg - dis,
                     "daytype": "平日" if pd.Timestamp(day).dayofweek < 5 else "週末",
                     "signal": signal or "判定不能"})
    a = pd.DataFrame(rows).set_index("date")
    a.to_csv(REPORTS / "attribution.csv")

    def agg(sub):
        n = len(sub)
        r, c, d, i = (sub[k].sum() for k in ("resid", "charge", "discharge", "inter"))
        pct = (lambda x: x / r * 100 if r else 0.0)
        return {"日数": n, "残差計": round(r), "残差/日": round(r / n, 1),
                "充電側%": round(pct(c)), "放電側%": round(pct(d)),
                "交互%": round(pct(i)),
                "対oracle%": round(sub["ws"].sum() / sub["oracle"].sum() * 100, 1)}

    total = agg(a)
    strata = {}
    for dt in ("平日", "週末"):
        for sg in ("強", "平常"):
            sub = a[(a["daytype"] == dt) & (a["signal"] == sg)]
            if len(sub):
                strata[f"{dt}×{sg}"] = agg(sub)

    lines = ["# oracle残差の帰因分析（weekshape、issue #1）", "",
             f"生成: {pd.Timestamp.now():%Y-%m-%d %H:%M} / 対象 {len(a)}日"
             f"（{a.index.min():%Y-%m-%d}〜{a.index.max():%Y-%m-%d}）", "",
             "残差＝oracle損益−weekshape損益。充電側/放電側＝その側だけoracleに"
             "置換したときの改善（カウンターファクチュアル分解）。交互＝両方直して"
             "初めて出る分。信号は日射予報の平年乖離（現行定義を過去に遡及適用）", "",
             """## 読み方（実例つき）

各機体は毎日「買い4コマ・売り4コマ」を選ぶ。oracleは答えを見た神様の8コマ。
**残差＝神様の儲け−weekshapeの儲け＝取りこぼし（円）**。分解は「もしも」を2回試す:
買い4コマだけ神様に差し替えて戻る額＝**充電側**、売りだけ＝**放電側**。

実例（2026-06-02）: weekshapeそのまま0円 → 買いだけ差し替え+79円 → 売りだけ差し替え+48円 →
両方（=oracle）+127円。79+48=127でピッタリ＝買いと売りが独立にズレた日。

足し算が合わない分が**交互**。実例（2026-05-21）: 売りだけ直すと−189円と悪化する——
電池は買ってからでないと売れないため、片方だけの差し替えは時間順序を壊す。
交互が大きい日＝日内の形が丸ごと変わっていて部分修正が効かない日（強信号日に多い）。

集計の%は取りこぼし円額の構成比。対oracle%は神様を100点にした点数。
""",
             "## 全体", "",
             "| | " + " | ".join(total.keys()) + " |",
             "|---|" + "---|" * len(total),
             "| weekshape | " + " | ".join(str(v) for v in total.values()) + " |",
             "", "## 層別（平日/週末 × 信号）", "",
             "| 層 | " + " | ".join(next(iter(strata.values())).keys()) + " |",
             "|---|" + "---|" * len(total)]
    for k, v in strata.items():
        lines.append(f"| {k} | " + " | ".join(str(x) for x in v.values()) + " |")

    worst = a.nlargest(5, "resid")[["oracle", "ws", "resid", "charge", "discharge", "signal", "daytype"]]
    lines += ["", "## 取りこぼしワースト5日", "",
              "| 日付 | oracle | weekshape | 残差 | 充電側 | 放電側 | 信号 | 曜日 |",
              "|---|---|---|---|---|---|---|---|"]
    for d, r in worst.iterrows():
        lines.append(f"| {d:%Y-%m-%d} | {r['oracle']:.0f} | {r['ws']:.0f} | "
                     f"{r['resid']:.0f} | {r['charge']:.0f} | {r['discharge']:.0f} | "
                     f"{r['signal']} | {r['daytype']} |")
    lines.append("")
    (REPORTS / "attribution.md").write_text("\n".join(lines), encoding="utf-8")
    print("\n".join(lines))


if __name__ == "__main__":
    main()
