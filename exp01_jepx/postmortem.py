#!/usr/bin/env python3
"""強信号3連敗の検死（issue #17）。

**問い**: 8/14〜16受渡の負けは「予報が外れた（物理の負け）」のか、
「予報は当たったが市場がそのとおりに値付けしなかった（信念の負け）」のか。

**分解の考え方**:
蓄電池の勝敗は「4コマをどこで買うか」で決まる。天気系は下向き乖離（曇り予報）の日に
「昼の谷は浅いはず」と読んで夜に買う。だから見るべき量は

    昼夜差 = 夜の最安4コマ平均 − 昼の最安4コマ平均
      正 → 昼のほうが安い（clockの読み） / 負 → 夜のほうが安い（天気系の賭け）

これを日射で説明する関係 f を**負け始める前の履歴だけ**から作り、

    予報誤差ぶん = f(実測) − f(予報)    ← 予報が外れていた分
    信念誤差ぶん = 実際     − f(実測)    ← 天気は当たったのに市場が違った分
    合計         = 実際     − f(予報)    ← 天気系にとっての「驚き」の全体

に分ける。**どちらが主犯かで打ち手が変わる**（前者なら気象データの改善、
後者なら市場の値付け癖のモデル化＝issue #15）。
"""
from pathlib import Path

import numpy as np
import pandas as pd

import sim

HERE = Path(__file__).parent
NIGHT = range(1, 11)     # 00:00-05:00
NOON = range(21, 29)     # 10:00-14:00
FIT_END = "2026-08-14"   # ここより前だけで関係を作る（負け始めた日を含めない）


def day_shape(df: pd.DataFrame) -> pd.DataFrame:
    """日ごとに昼夜差を出す。"""
    rows = []
    for day, g in df.groupby("date"):
        p = g.set_index("koma")["sp"]
        night = p[p.index.isin(NIGHT)].nsmallest(4).mean()
        noon = p[p.index.isin(NOON)].nsmallest(4).mean()
        rows.append({"date": day, "night": night, "noon": noon,
                     "gap": night - noon, "mean": p.mean()})
    return pd.DataFrame(rows)


def main() -> None:
    shape = day_shape(sim.load())
    fc = pd.read_csv(HERE / "data" / "weather_forecast.csv", parse_dates=["date"])
    ac = pd.read_csv(HERE / "data" / "weather_actual.csv", parse_dates=["date"])
    d = (shape.merge(fc[["date", "rad"]].rename(columns={"rad": "rad_f"}), on="date")
              .merge(ac[["date", "rad"]].rename(columns={"rad": "rad_a"}), on="date"))

    fit = d[d["date"] < FIT_END].dropna(subset=["rad_a", "gap"])
    a, b = np.polyfit(fit["rad_a"], fit["gap"], 1)
    r = np.corrcoef(fit["rad_a"], fit["gap"])[0, 1]
    f = lambda x: a * x + b   # noqa: E731

    L = ["# 検死: 強信号3連敗は物理の負けか信念の負けか（issue #17）", "",
         f"物差し: **昼夜差 = 夜(00-05時)の最安4コマ平均 − 昼(10-14時)の最安4コマ平均**。",
         "正なら昼が安い（clockの読み）、負なら夜が安い（天気系の賭け）。", "",
         f"日射→昼夜差の関係は **{FIT_END}より前の{len(fit)}日だけ**で推定"
         f"（負け始めた日を含めない）: 昼夜差 = {a:.4f}×日射 {b:+.2f}、相関 {r:.3f}", ""]

    target = d[(d["date"] >= "2026-08-14") & (d["date"] <= "2026-08-17")].copy()
    target["pred_f"] = f(target["rad_f"])
    target["pred_a"] = f(target["rad_a"])
    target["forecast_err"] = target["pred_a"] - target["pred_f"]
    target["belief_err"] = target["gap"] - target["pred_a"]

    L += ["## 予報 vs 実測（8/14〜17受渡）", "",
          "| 受渡 | 日射予報 | 日射実測 | 予報の外れ | 昼夜差(予報から) | 昼夜差(実測から) | 昼夜差(実際) |",
          "|---|---|---|---|---|---|---|"]
    for _, r_ in target.iterrows():
        L.append(f"| {r_['date']:%-m/%d} | {r_['rad_f']:.0f} | {r_['rad_a']:.0f} | "
                 f"**{r_['rad_a']-r_['rad_f']:+.0f}** | {r_['pred_f']:+.2f}円 | "
                 f"{r_['pred_a']:+.2f}円 | **{r_['gap']:+.2f}円** |")

    L += ["", "## 分解", "",
          "| 受渡 | 結果 | 予報誤差ぶん | 信念誤差ぶん | 主犯 |", "|---|---|---|---|---|"]
    for _, r_ in target.iterrows():
        who = "予報" if abs(r_["forecast_err"]) > abs(r_["belief_err"]) else "信念"
        res = "○初勝利" if r_["date"] == pd.Timestamp("2026-08-17") else "×負け"
        L.append(f"| {r_['date']:%-m/%d} | {res} | {r_['forecast_err']:+.2f}円 | "
                 f"{r_['belief_err']:+.2f}円 | **{who}** |")

    loss = target[target["date"] <= "2026-08-16"]
    lfe, lbe = loss["forecast_err"].abs().sum(), loss["belief_err"].abs().sum()
    fe, be = target["forecast_err"].abs().sum(), target["belief_err"].abs().sum()
    L += ["",
          f"**3連敗の3日（8/14〜16）だけ: 予報誤差 {lfe:.2f}円 ({lfe/(lfe+lbe):.0%}) / "
          f"信念誤差 {lbe:.2f}円 ({lbe/(lfe+lbe):.0%})**",
          f"（4日全体では 予報 {fe/(fe+be):.0%} / 信念 {be/(fe+be):.0%}。"
          "8/17の信念誤差 −10.27円が全体を裏返している）", "",
          "## 結論", "",
          f"**3連敗の主犯は予報誤差（{lfe/(lfe+lbe):.0%}）。** 予報は3日とも"
          "「普段より曇る」と言ったが、**実測はいずれも平年並みの晴れ**"
          "（外れ幅 +168〜+214 W/m2）。天気系は曇り前提で夜に買い、"
          "実際には昼に深い谷ができて負けた。**天気の読み違いではなく、"
          "渡された天気予報が外れていた**。",
          "",
          "**そして初勝利（8/17）の従来の説明は誤りだった。** これまで"
          "「現実に雨が降ったので市場もついに崩れを信じた」と記録していたが、"
          "**8/17の実測日射は631 W/m2で4日間の最高値**——晴れていた。"
          "物理どおりなら昼が5.18円安いはずが、実際は**夜が5.09円安かった**"
          "（最安は02:30の11.2円）。つまり**市場が物理と反対に動いた日に、"
          "たまたま夜買いが当たった**。信念誤差 −10.27円は4日間で突出している。",
          "",
          "## 打ち手への含意", "",
          "- **3連敗は気象データの改善で減らせる型**（予報の質が効く）。"
          "複数ソースの突き合わせや、予報の外れやすさ自体の予測に価値がある",
          "- **8/17のような日は気象では取れない**。市場が物理から離れる局面で、"
          "これは issue #15（市場の期待の層）の対象そのもの",
          "- **同じ「強」信号でも中身が違う**——予報が外れた日と、市場が離れた日が"
          "混ざっている。信号を1本の閾値で切っている限り、この2つは分けられない",
          "",
          "## この分解の限界（先に書いておく）", "",
          f"日射→昼夜差の相関は **{r:.3f}**（決定係数 {r**2:.2f}）で、"
          "**日射で説明できるのは昼夜差の2割ほど**しかない。"
          "残りの8割には需要・燃料価格・電源の停止など日射以外の全部が入る。"
          "したがって「信念誤差」と呼んでいる残差は、**厳密には「日射で説明できない分」**"
          "であって、市場心理だけを取り出したものではない。"
          "**3連敗の主犯が予報だという結論は頑健**（予報の外れが+168〜+214と大きく、"
          "残差より一貫して大きい）だが、8/17の解釈は残差の中身次第で変わりうる。", ""]
    return "\n".join(L), target, (a, b, r), f


if __name__ == "__main__":
    text, _, _, _ = main()
    print(text)
