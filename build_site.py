#!/usr/bin/env python3
"""リーグ横断ダッシュボード生成（GitHub Pages用の静的HTML1枚）。

reports/ の台帳から docs/index.html を機械生成する。CIの最後に実行される。
スマホ前提・自己完結（外部読み込みなし）・ライト/ダーク両対応。
"""

import csv
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).parent
DOCS = ROOT / "docs"
JST = ZoneInfo("Asia/Tokyo")
GH = "https://github.com/hsumiyoshi/lab/blob/main"

# 機体の固定色（チャートと同一。色は機体に付き、順位では変えない）
DOT = {"weekshape": "#2a78d6", "haruki_tenki": "#eb6834",
       "haruki_hybrid": "#1baf7a", "clock": "#eda100", "oracle": "#898781"}

CSS = """
:root{--bg:#f6f5f1;--card:#fcfcfb;--ink:#1a1915;--ink2:#52514e;--muted:#898781;
--line:#e1e0d9;--line2:#eceae4;--accent:#2a78d6;--chartbg:#fcfcfb}
@media(prefers-color-scheme:dark){:root{--bg:#131311;--card:#1d1c19;--ink:#f0efe9;
--ink2:#b5b3ac;--muted:#7a786f;--line:#33322d;--line2:#282722;--accent:#6ea8e8}}
*{box-sizing:border-box;margin:0;padding:0}
body{background:var(--bg);color:var(--ink);line-height:1.65;padding:28px 18px 40px;
font-family:-apple-system,BlinkMacSystemFont,'Hiragino Sans','Noto Sans JP',sans-serif}
.wrap{max-width:720px;margin:0 auto}
header{margin-bottom:20px}
h1{font-size:1.45rem;letter-spacing:.01em;line-height:1.3}
.tagline{color:var(--ink2);font-size:.85rem;margin-top:2px}
.stamp{color:var(--muted);font-size:.72rem;margin-top:6px}
.stamp a{color:var(--muted)}
.tiles{display:grid;grid-template-columns:repeat(3,1fr);gap:10px;margin:18px 0 22px}
.tile{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:12px 14px}
.tile .k{color:var(--muted);font-size:.68rem;letter-spacing:.05em}
.tile .v{font-size:1.25rem;font-weight:700;font-variant-numeric:tabular-nums;margin-top:2px}
.tile .s{color:var(--ink2);font-size:.72rem}
section{background:var(--card);border:1px solid var(--line);border-radius:14px;
padding:18px 20px;margin-bottom:14px}
.head{display:flex;align-items:baseline;gap:8px;flex-wrap:wrap;margin-bottom:2px}
.head h2{font-size:1.02rem}
.cad{font-size:.66rem;color:var(--ink2);border:1px solid var(--line);
border-radius:99px;padding:1px 9px;letter-spacing:.04em}
.meta{color:var(--ink2);font-size:.78rem;margin-bottom:8px}
table{border-collapse:collapse;width:100%;font-size:.84rem;font-variant-numeric:tabular-nums}
th{color:var(--muted);font-weight:500;font-size:.7rem;text-align:right;
padding:4px 0 4px 12px;border-bottom:1px solid var(--line)}
th:first-child{text-align:left;padding-left:0}
td{padding:5px 0 5px 12px;text-align:right;border-bottom:1px solid var(--line2)}
td:first-child{text-align:left;padding-left:0;white-space:nowrap}
tr:last-child td{border-bottom:none}
.dot{display:inline-block;width:9px;height:9px;border-radius:50%;margin-right:7px;vertical-align:baseline}
.lead td{font-weight:700}
.pos{color:#1baf7a}.neg{color:#d64545}
.fig{background:var(--chartbg);border:1px solid var(--line);border-radius:10px;
padding:6px;margin-top:12px}
.fig img{width:100%;display:block;border-radius:6px}
.links{margin-top:10px;font-size:.78rem}
.links a{color:var(--accent);text-decoration:none;margin-right:14px}
.links a:hover{text-decoration:underline}
.empty{display:flex;align-items:center;gap:10px;color:var(--ink2);font-size:.84rem;
padding:10px 0 4px}
.pulse{width:8px;height:8px;border-radius:50%;background:var(--accent);flex:none;opacity:.75}
.judg{color:var(--muted);font-size:.74rem;white-space:nowrap}
footer{color:var(--muted);font-size:.72rem;margin-top:20px;line-height:1.7}
@media(max-width:560px){.tiles{grid-template-columns:repeat(3,1fr);gap:7px}
.tile{padding:9px 10px}.tile .v{font-size:1.05rem}section{padding:14px 15px}}
"""


def read_power():
    f = ROOT / "exp01_jepx" / "reports" / "forward_ledger.csv"
    if not f.exists():
        return None
    rows = list(csv.DictReader(f.open()))
    if not rows:
        return None
    names = [c for c in rows[0] if c not in ("date", "signal")]
    totals = {n: sum(float(r[n]) for r in rows if r[n]) for n in names}
    return {"days": len(rows), "totals": totals, "last": rows[-1], "names": names}


def read_csv(path):
    f = ROOT / path
    return list(csv.DictReader(f.open())) if f.exists() else []


def fmt_delta(v):
    cls = "pos" if v >= 0 else "neg"
    return f"<span class='{cls}'>{v:+,.0f}</span>"


def card_power(p):
    if not p:
        return ("<section><div class='head'><h2>⚡ 電力 — 仮想蓄電池</h2>"
                "<span class='cad'>毎日</span></div><div class='empty'>"
                "<span class='pulse'></span>初日の採点待ち</div></section>")
    strat = {n: v for n, v in p["totals"].items() if n != "oracle"}
    orc = p["totals"].get("oracle", 0) or 1
    leader = max(strat, key=strat.get)
    rows = []
    for i, (n, v) in enumerate(sorted(strat.items(), key=lambda kv: -kv[1]), 1):
        last = float(p["last"][n]) if p["last"][n] else 0.0
        rows.append(
            f"<tr{' class=lead' if n == leader else ''}>"
            f"<td><span class='dot' style='background:{DOT.get(n, '#888')}'></span>{n}</td>"
            f"<td>{v:,.0f}円</td><td>{v / orc * 100:.1f}%</td><td>{fmt_delta(last)}</td></tr>")
    sig = p["last"]["signal"]
    return f"""<section><div class='head'><h2>⚡ 電力 — 仮想蓄電池</h2><span class='cad'>毎日 13:30 採点</span></div>
<div class='meta'>直近 {p["last"]["date"][:10]}・信号「{sig}」 / oracle累計 {orc:,.0f}円（理論上限）</div>
<table><tr><th>機体</th><th>累計</th><th>対oracle</th><th>直近日</th></tr>{"".join(rows)}</table>
<div class='fig'><img src='forward_pnl.png' alt='累積損益チャート'></div>
<div class='links'><a href='{GH}/exp01_jepx/reports/forward_report.md'>台帳</a>
<a href='{GH}/exp01_jepx/reports/daily_anatomy.md'>日次解剖</a>
<a href='{GH}/exp01_jepx/picks/'>picks（公証）</a></div></section>"""


def card_weekly(emoji, title, cadence, rows, cols, headers, report, empty):
    head = (f"<div class='head'><h2>{emoji} {title}</h2>"
            f"<span class='cad'>{cadence}</span></div>")
    if not rows:
        body = f"<div class='empty'><span class='pulse'></span>{empty}</div>"
    else:
        th = "".join(f"<th>{h}</th>" for h in headers)
        trs = "".join("<tr>" + "".join(f"<td>{r.get(c, '—')}</td>" for c in cols)
                      + "</tr>" for r in rows[-4:])
        body = f"<table><tr>{th}</tr>{trs}</table>"
    return (f"<section>{head}{body}"
            f"<div class='links'><a href='{report}'>台帳</a></div></section>")


def build():
    p = read_power()
    veg = read_csv("exp02_vegetable/reports/veg_ledger.csv")
    qk = read_csv("exp05_quake/reports/quake_ledger.csv")
    now = datetime.now(JST)

    if p:
        strat = {n: v for n, v in p["totals"].items() if n != "oracle"}
        orc = p["totals"].get("oracle", 0) or 1
        leader = max(strat, key=strat.get)
        tiles = f"""<div class='tiles'>
<div class='tile'><div class='k'>フォワード</div><div class='v'>{p['days']}日目</div><div class='s'>無停止が堀</div></div>
<div class='tile'><div class='k'>電力の首位</div><div class='v'>{strat[leader] / orc * 100:.0f}%</div><div class='s'>{leader}・対oracle</div></div>
<div class='tile'><div class='k'>稼働リーグ</div><div class='v'>5</div><div class='s'>電力・青果・地震・不動産・衛星</div></div>
</div>"""
    else:
        tiles = ""

    html = f"""<!DOCTYPE html><html lang='ja'><head><meta charset='utf-8'>
<meta name='viewport' content='width=device-width,initial-scale=1'>
<title>リーグ台帳</title><style>{CSS}</style></head><body><div class='wrap'>
<header><h1>リーグ台帳</h1>
<div class='tagline'>予測を先に固定し、現実に採点させる公開実験</div>
<div class='stamp'>更新 {now:%Y-%m-%d %H:%M} JST・全採点は自動（GitHub Actions）・
<a href='https://github.com/hsumiyoshi/lab'>lab</a> /
<a href='{GH}/CHANGELOG.md'>工事ログ</a></div></header>
{tiles}
{card_power(p)}
{card_weekly("🥬", "青果 — 出荷タイミング", "水曜 14:00 採点", veg,
             ["week", "item", "oracle", "equal", "weekshape8", "stop_rule"],
             ["週", "品目", "oracle", "均等", "weekshape8", "stop_rule"],
             f"{GH}/exp02_vegetable/reports/veg_forward.md",
             "初ラウンド 8/17週 — 東京市場の単価で「どの日に売るか」を競う")}
{card_weekly("🌏", "地震 — 余震件数予測", "月曜 14:00 採点", qk,
             ["week", "actual", "omori", "flat", "err_omori", "err_flat"],
             ["週", "実績", "大森則", "横ばい", "誤差(大森)", "誤差(横ばい)"],
             f"{GH}/exp05_quake/reports/quake_forward.md",
             "初ラウンド 8/17週 — 大森則で熊本余震の週次件数を予測する")}
<section><div class='head'><h2>📋 事前登録予測</h2><span class='cad'>公証済み</span></div>
<table>
<tr><th>予測</th><th>判定</th></tr>
<tr><td><a href='{GH}/exp04_realestate/predictions.md' style='color:var(--accent);text-decoration:none'>🏠 M7.1後の熊本 — 件数は減る・価格は±5%・ハザード相対差は不変</a></td><td class='judg'>2027年1月〜</td></tr>
<tr><td><a href='{GH}/exp07_satellite/predictions.md' style='color:var(--accent);text-decoration:none'>🛰 嬬恋NDVIの0.65到達日 → キャベツ本格出荷週</a></td><td class='judg'>2027年7月頃</td></tr>
</table></section>
<footer>戦略はgitで凍結し、picksは価格公表前にコミットされる——コミット履歴が改竄不能な公証。
負けの記録も消さないことが、この台帳の信頼性の根拠。</footer>
</div></body></html>"""
    DOCS.mkdir(exist_ok=True)
    (DOCS / "index.html").write_text(html, encoding="utf-8")
    png = ROOT / "exp01_jepx" / "reports" / "forward_pnl.png"
    if png.exists():
        (DOCS / "forward_pnl.png").write_bytes(png.read_bytes())
    print(f"-> docs/index.html ({len(html):,} bytes)")


if __name__ == "__main__":
    build()
