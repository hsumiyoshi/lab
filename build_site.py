#!/usr/bin/env python3
"""リーグ横断ダッシュボード生成（GitHub Pages用）。

site_template.html（デザインテンプレート）に reports/ の実データを配線して
docs/index.html を生成する。置換契約はテンプレート冒頭コメント参照:
①SAMPLEブロック削除 ②TPLコメント展開 ③{{マーカー}}置換
"""

import csv
import re
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).parent
DOCS = ROOT / "docs"
JST = ZoneInfo("Asia/Tokyo")

DOT = {"weekshape": "#2a78d6", "tenki": "#eb6834",
       "hybrid": "#1baf7a", "clock": "#eda100", "oracle": "#898781"}


def read_power():
    f = ROOT / "exp01_jepx" / "reports" / "forward_ledger.csv"
    if not f.exists():
        return None
    rows = list(csv.DictReader(f.open()))
    if not rows:
        return None
    names = [c for c in rows[0] if c not in ("date", "signal")]
    totals = {n: sum(float(r[n]) for r in rows if r[n]) for n in names}
    return {"days": len(rows), "totals": totals, "last": rows[-1]}


def read_csv(path):
    f = ROOT / path
    return list(csv.DictReader(f.open())) if f.exists() else []


def power_table(p):
    strat = {n: v for n, v in p["totals"].items() if n != "oracle"}
    orc = p["totals"].get("oracle", 0) or 1
    leader = max(strat, key=strat.get)
    rows = []
    order = sorted(strat.items(), key=lambda kv: -kv[1]) + [("oracle", p["totals"]["oracle"])]
    for n, v in order:
        last = float(p["last"][n]) if p["last"][n] else 0.0
        cls = " class='lead'" if n == leader else ""
        delta = f"<span class='{'pos' if last >= 0 else 'neg'}'>{last:+,.0f}</span>"
        rows.append(
            f"<tr{cls}><td><span class='dot' style='background:{DOT.get(n, '#888')}'></span>{n}</td>"
            f"<td>{v:,.0f}円</td><td>{v / orc * 100:.1f}%</td><td>{delta}</td></tr>")
    return "".join(rows)


def latest_line(p):
    strat = {n: float(p["last"][n]) for n in p["totals"]
             if n != "oracle" and p["last"][n]}
    orc = float(p["last"]["oracle"])
    ranked = sorted(strat.items(), key=lambda kv: -kv[1])
    body = " > ".join(f"{n} {v:+,.0f}円" for n, v in ranked)
    return (f"{p['last']['date'][:10]}（信号「{p['last']['signal']}」）: "
            f"{body}（oracle上限 {orc:+,.0f}円）")


def leagues(veg, qk):
    """各リーグを電力と同格のトップレベルセクションとして生成（開幕前から領域を確保）"""
    GHB = "https://github.com/hsumiyoshi/lab/blob/main"
    def sec(emoji, name, cad, rule, body, link):
        return (f"<section class='half'><div class='head'><h2>{emoji} {name}</h2>"
                f"<span class='cad'>{cad}</span></div>"
                f"<div class='meta'>{rule}</div>{body}"
                f"<div class='links'><a href='{link}'>台帳</a></div></section>")
    def empty(msg):
        return f"<div class='empty'><span class='pulse'></span>{msg}</div>"
    def table(rows, cols, headers):
        th = "".join(f"<th>{h}</th>" for h in headers)
        trs = "".join("<tr>" + "".join(f"<td>{r.get(c, chr(8212))}</td>" for c in cols) + "</tr>"
                      for r in rows[-4:])
        return f"<table><tr>{th}</tr>{trs}</table>"
    return "".join([
        sec("🥬", "青果リーグ — 出荷タイミング", "週次・水曜14:00採点",
            "東京市場の日次単価で「週のどの日に売るか」を競う。脳死ベンチ=毎営業日の均等出荷",
            table(veg, ["week", "item", "oracle", "equal", "weekshape8", "stop_rule"],
                  ["週", "品目", "oracle", "均等", "weekshape8", "stop_rule"]) if veg
            else empty("初戦 8/17週 — 結果はここに刻まれていく"),
            f"{GHB}/exp02_vegetable/reports/veg_forward.md"),
        sec("🌏", "地震リーグ — 余震件数予測", "週次・月曜14:00採点",
            "大森則（余震の減衰法則）で熊本余震の週次件数を予測。脳死ベンチ=前週横ばい",
            table(qk, ["week", "actual", "omori", "flat", "err_omori", "err_flat"],
                  ["週", "実績", "大森則", "横ばい", "誤差(大森)", "誤差(横ばい)"]) if qk
            else empty("初戦 8/17週 — 結果はここに刻まれていく"),
            f"{GHB}/exp05_quake/reports/quake_forward.md"),
        sec("🏠", "不動産 — 熊本地震の事前登録予測", "四半期・判定2027年1月〜",
            "M7.1直後に公証した3つの予言: 取引件数は減る／価格は±5%以内／ハザード内外差は不変",
            empty("対象データ（2026Q3）の公表を待機中 — 予言は先に置いてある"),
            f"{GHB}/exp04_realestate/predictions.md"),
        sec("🛰", "衛星 — キャベツ出荷週の事前登録予測", "年次・判定2027年7月頃",
            "嬬恋のNDVI 0.65到達日から本格出荷週を当てる閾値ルールを公証済み",
            empty("2027年春の生育観測を待機中"),
            f"{GHB}/exp07_satellite/predictions.md"),
    ])


def build():
    tpl = (ROOT / "site_template.html").read_text(encoding="utf-8")
    p = read_power()
    veg = read_csv("exp02_vegetable/reports/veg_ledger.csv")
    qk = read_csv("exp05_quake/reports/quake_ledger.csv")
    now = datetime.now(JST)

    html = re.sub(r"<!--SAMPLE-->.*?<!--/SAMPLE-->", "", tpl, flags=re.S)
    html = re.sub(r"<!--TPL:(.*?)/TPL-->", lambda m: m.group(1), html, flags=re.S)
    html = (html.replace("{{DAYS}}", str(p["days"] if p else 0))
                .replace("{{UPDATED}}", f"{now:%Y-%m-%d %H:%M}")
                .replace("{{POWER_TABLE}}", power_table(p) if p else "")
                .replace("{{LATEST_LINE}}", latest_line(p) if p else "初日の採点待ち")
                .replace("{{LEAGUES}}", leagues(veg, qk)))
    html = re.sub(r"<!--[\s\S]*?-->", "", html)  # 本番出力はコメント（契約説明含む）を全除去
    assert "{{" not in html, "置換漏れあり"

    DOCS.mkdir(exist_ok=True)
    (DOCS / "index.html").write_text(html, encoding="utf-8")
    png = ROOT / "exp01_jepx" / "reports" / "forward_pnl.png"
    if png.exists():
        (DOCS / "forward_pnl.png").write_bytes(png.read_bytes())
    anas = sorted((ROOT / "exp01_jepx" / "reports" / "anatomy").glob("*.png"))
    if anas:
        (DOCS / "latest_anatomy.png").write_bytes(anas[-1].read_bytes())
    print(f"-> docs/index.html ({len(html):,} bytes)")


if __name__ == "__main__":
    build()
