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

CSS = """
:root{--bg:#fcfcfb;--card:#ffffff;--ink:#0b0b0b;--ink2:#52514e;--muted:#898781;
--line:#e1e0d9;--blue:#2a78d6;--orange:#eb6834;--green:#1baf7a;--yellow:#eda100}
@media(prefers-color-scheme:dark){:root{--bg:#121212;--card:#1c1c1c;--ink:#f0efe9;
--ink2:#b5b3ac;--muted:#7a786f;--line:#33322e}}
*{box-sizing:border-box;margin:0}
body{background:var(--bg);color:var(--ink);font-family:-apple-system,BlinkMacSystemFont,'Hiragino Sans',sans-serif;
padding:16px;max-width:760px;margin:0 auto;line-height:1.6}
h1{font-size:1.2rem;margin:4px 0 2px}
.sub{color:var(--muted);font-size:.75rem;margin-bottom:14px}
.card{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:14px 16px;margin-bottom:12px}
.card h2{font-size:.95rem;margin-bottom:6px}
.tag{font-size:.7rem;padding:1px 8px;border-radius:9px;border:1px solid var(--line);color:var(--ink2);margin-left:6px;font-weight:normal}
table{border-collapse:collapse;width:100%;font-size:.8rem;margin:6px 0}
th{color:var(--ink2);font-weight:normal;text-align:left;border-bottom:1px solid var(--line);padding:3px 8px 3px 0}
td{padding:3px 8px 3px 0;border-bottom:1px solid var(--line)}
tr:last-child td{border-bottom:none}
.win{color:var(--blue);font-weight:bold}
.next{color:var(--ink2);font-size:.78rem;margin-top:4px}
a{color:var(--blue);text-decoration:none}
img{max-width:100%;border-radius:6px;margin-top:6px}
.foot{color:var(--muted);font-size:.7rem;margin-top:16px}
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
    last = rows[-1]
    return {"days": len(rows), "totals": totals, "last": last, "names": names}


def read_weekly(path, date_col="week"):
    f = ROOT / path
    if not f.exists():
        return []
    return list(csv.DictReader(f.open()))


def card_power(p):
    if not p:
        return "<div class='card'><h2>⚡ 電力</h2><p>初日の採点待ち</p></div>"
    strat = {n: v for n, v in p["totals"].items() if n != "oracle"}
    orc = p["totals"].get("oracle", 0) or 1
    leader = max(strat, key=strat.get)
    rows = "".join(
        f"<tr><td{' class=win' if n == leader else ''}>{n}</td>"
        f"<td>{v:,.0f}円</td><td>{v / orc * 100:.1f}%</td>"
        f"<td>{float(p['last'][n]) if p['last'][n] else 0:+,.0f}円</td></tr>"
        for n, v in sorted(strat.items(), key=lambda kv: -kv[1]))
    return f"""<div class='card'><h2>⚡ 電力 — 仮想蓄電池<span class='tag'>毎日</span></h2>
<div class='next'>フォワード{p['days']}日目 / 直近 {p['last']['date'][:10]}（信号: {p['last']['signal']}） / oracle累計 {orc:,.0f}円</div>
<table><tr><th>機体</th><th>累計</th><th>対oracle</th><th>直近日</th></tr>{rows}</table>
<img src='forward_pnl.png' alt='cumulative P&L'>
<div class='next'><a href='{GH}/exp01_jepx/reports/forward_report.md'>台帳</a> ·
<a href='{GH}/exp01_jepx/reports/daily_anatomy.md'>日次解剖</a></div></div>"""


def card_weekly(title, emoji, rows, report, empty_note, cols):
    if not rows:
        body = f"<p class='next'>{empty_note}</p>"
    else:
        head = "".join(f"<th>{c}</th>" for c in cols)
        body_rows = "".join(
            "<tr>" + "".join(f"<td>{r.get(c, '—')}</td>" for c in cols) + "</tr>"
            for r in rows[-4:])
        body = f"<table><tr>{head}</tr>{body_rows}</table>"
    return f"""<div class='card'><h2>{emoji} {title}<span class='tag'>週次</span></h2>
{body}<div class='next'><a href='{report}'>台帳</a></div></div>"""


def build():
    p = read_power()
    veg = read_weekly("exp02_vegetable/reports/veg_ledger.csv")
    qk = read_weekly("exp05_quake/reports/quake_ledger.csv")
    now = datetime.now(JST)
    cards = [card_power(p)]
    cards.append(card_weekly(
        "青果 — 出荷タイミング", "🥬", veg,
        f"{GH}/exp02_vegetable/reports/veg_forward.md",
        "初ラウンドは8/17週（採点は水曜14:00）",
        ["week", "item", "oracle", "equal", "weekshape8", "stop_rule"]))
    cards.append(card_weekly(
        "地震 — 余震件数予測", "🌏", qk,
        f"{GH}/exp05_quake/reports/quake_forward.md",
        "初ラウンドは8/17週（採点は月曜14:00）",
        ["week", "actual", "omori", "flat", "err_omori", "err_flat"]))
    cards.append(f"""<div class='card'><h2>🏠 不動産 / 🛰 衛星<span class='tag'>事前登録</span></h2>
<table><tr><th>予測</th><th>判定</th></tr>
<tr><td><a href='{GH}/exp04_realestate/predictions.md'>M7.1後の熊本: 件数減・価格±5%・ハザード相対差不変</a></td><td>2027年1月末〜</td></tr>
<tr><td><a href='{GH}/exp07_satellite/predictions.md'>NDVI 0.65到達日→本格出荷週（閾値ルール）</a></td><td>2027年7月頃</td></tr></table></div>""")
    html = f"""<!DOCTYPE html><html lang='ja'><head><meta charset='utf-8'>
<meta name='viewport' content='width=device-width,initial-scale=1'>
<title>リーグ台帳</title><style>{CSS}</style></head><body>
<h1>リーグ台帳 — 予測を先に固定し、現実で採点する</h1>
<div class='sub'>生成 {now:%Y-%m-%d %H:%M} JST / 戦略はgitで凍結・コミット履歴が公証 /
<a href='https://github.com/hsumiyoshi/lab'>lab</a> · <a href='{GH}/CHANGELOG.md'>工事ログ</a></div>
{''.join(cards)}
<div class='foot'>すべての採点は自動（GitHub Actions）。負けの記録も消さないことがこの台帳の信頼性。</div>
</body></html>"""
    DOCS.mkdir(exist_ok=True)
    (DOCS / "index.html").write_text(html, encoding="utf-8")
    png = ROOT / "exp01_jepx" / "reports" / "forward_pnl.png"
    if png.exists():
        (DOCS / "forward_pnl.png").write_bytes(png.read_bytes())
    print(f"-> docs/index.html ({len(html):,} bytes)")


if __name__ == "__main__":
    build()
