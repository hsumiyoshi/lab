#!/usr/bin/env python3
"""リーグ横断ダッシュボード生成（GitHub Pages用）。

site_template.html（デザインテンプレート）に reports/ の実データを配線して
docs/index.html を生成する。置換契約はテンプレート冒頭コメント参照:
①SAMPLEブロック削除 ②TPLコメント展開 ③{{マーカー}}置換
"""

import base64
import csv
import json
import re
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).parent
DOCS = ROOT / "docs"
JST = ZoneInfo("Asia/Tokyo")

DOT = {"weekshape": "#2a78d6", "tenki": "#eb6834",
       "hybrid": "#1baf7a", "clock": "#eda100", "tenki_v2": "#9b6bd3", "tenki_v3": "#c44e52",
       "tenki_v4": "#6b7f2e", "oracle": "#898781"}


def read_power():
    f = ROOT / "exp01_jepx" / "reports" / "forward_ledger.csv"
    if not f.exists():
        return None
    rows = list(csv.DictReader(f.open()))
    if not rows:
        return None
    names = [c for c in rows[0]
             if c not in ("date", "signal") and not c.startswith("bt_")]
    def is_bt(r, n):
        return r.get(f"bt_{n}", "") in ("1", "1.0")
    # 累計はBT補完込みの金額（Haruki決定 2026-08-17, issue #12）。
    # レート（対oracle）は事前コミット済みフォワード日のみで計算
    totals = {n: sum(float(r[n]) for r in rows if r[n]) for n in names}
    btdays = {n: sum(1 for r in rows if r[n] and is_bt(r, n)) for n in names}
    played = {n: sum(1 for r in rows if r[n]) for n in names}
    fwd_tot = {n: sum(float(r[n]) for r in rows if r[n] and not is_bt(r, n)) for n in names}
    orc_fwd = {n: sum(float(r["oracle"]) for r in rows if r[n] and not is_bt(r, n)) for n in names}
    return {"days": len(rows), "totals": totals, "btdays": btdays, "played": played,
            "fwd_tot": fwd_tot, "orc_fwd": orc_fwd, "last": rows[-1]}


def read_csv(path):
    f = ROOT / path
    return list(csv.DictReader(f.open())) if f.exists() else []


def power_table(p):
    strat = {n: v for n, v in p["totals"].items() if n != "oracle"}
    pct = {n: p["fwd_tot"][n] / (p["orc_fwd"].get(n) or 1) * 100 for n in strat}
    fwd_has = {n for n in strat if p["btdays"].get(n, 0) < p["played"].get(n, 0)}
    leader = max((n for n in fwd_has), key=pct.get, default=max(pct, key=pct.get))  # 実力首位はフォワード実績のある機体から
    rows = []
    order = sorted(strat.items(), key=lambda kv: -pct[kv[0]]) + [("oracle", p["totals"]["oracle"])]
    for n, v in order:
        # 最終行に値が無い＝その日は欠場。0円と書くと「参加して稼げなかった」に
        # 読めてしまう（issue #18「欠測を実力と誤読させない」と同じ病気）
        raw = p["last"][n]
        last = float(raw) if raw else None
        cls = (" class='lead'" if n == leader
               else " class='aside'" if n in ('clock', 'oracle') else "")
        delta = ("<span class='abs' title='この日は提出が無く欠場'>欠場</span>"
                 if last is None else
                 f"<span class='{'pos' if last >= 0 else 'neg'}'>{last:+,.0f}</span>")
        bt = p["btdays"].get(n, 0)
        btcell = f"{bt / p['played'][n] * 100:.0f}%" if bt else "—"
        pcell = ("—" if (n != "oracle" and bt == p["played"].get(n, 0))
                 else f"{(100.0 if n == 'oracle' else pct[n]):.1f}%")
        # 並びは対oracle順。それを2列目に置かないと、左端の「累計」が
        # 昇順にも降順にも見えず、表が壊れているように読める
        tag = ("<span class='tag'>ベンチ</span>" if n == "clock"
               else "<span class='tag'>参照</span>" if n == "oracle" else "")
        rows.append(
            f"<tr{cls}><td><span class='dot' style='background:{DOT.get(n, '#888')}'></span>{n}{tag}</td>"
            f"<td>{pcell}</td><td>{v:,.0f}円</td><td>{btcell}</td><td>{delta}</td></tr>")
    return "".join(rows)


def latest_line(p):
    strat = {n: float(p["last"][n]) for n in p["totals"]
             if n != "oracle" and p["last"][n]}
    orc = float(p["last"]["oracle"])
    ranked = sorted(strat.items(), key=lambda kv: -kv[1])
    body = " > ".join(f"{n} {v:+,.0f}円" for n, v in ranked)
    return (f"{p['last']['date'][:10]}（信号「{p['last']['signal']}」）: "
            f"{body}（oracle上限 {orc:+,.0f}円）")


def data_uri(path):
    p = ROOT / path
    if not p.exists():
        return None
    return "data:image/png;base64," + base64.b64encode(p.read_bytes()).decode()


def rule_svg(key):
    f = ROOT / "rule_diagrams.html"
    if not f.exists():
        return ""
    t = f.read_text(encoding="utf-8")
    m = re.search(rf"<!--SVG:{key}-->([\s\S]*?)<!--/SVG:{key}-->", t)
    return f"<div class='flow'>{m.group(1)}</div>" if m else ""


def ai_score_body(ai):
    if not ai or not ai.get("rounds"):
        return "<div class='empty'><span class='pulse'></span>初回ラウンド準備中</div>"
    r = ai["rounds"][-1]
    head = "<tr><th>命題（" + r["id"] + "・判定日つき）</th><th>外挿</th><th>回帰</th><th>構造</th><th>結果</th></tr>"
    trs = ""
    for q in r["questions"]:
        oc = "—" if q["outcome"] is None else ("○" if q["outcome"] else "×")
        trs += ("<tr><td>" + q["q"] + "（" + q["resolve"][5:].replace("-", "/") + "）</td>"
                + "".join(f"<td>{q['pred'][k]:.2f}</td>" for k in ("外挿AI", "回帰AI", "構造AI"))
                + f"<td>{oc}</td></tr>")
    note = "<p class='tnote'>" + r.get("provenance", "") + "。確率は各人格の主観確率（1.00=確実に起きる）</p>"
    return f"<table>{head}{trs}</table>{note}"


def schedule_note(ledger_rows, weekday: int, hour: int, first_scoring: str, label: str) -> str:
    """採点予定の文言を実態から作る（2026-08-24）。

    固定文字列だと過ぎた予定を未来のことのように書き続ける（8/19の青果は失敗していたのに
    「水曜14:00に載る」と待っている表示のままだった）。一方、**初回ラウンドが終わる前は
    「採点対象が無い」のが正常**であり、それを失敗と書くのも同じ種類の嘘になる
    （8/17の地震は成功していたのに「CI失敗」と表示していた）。両方を区別する。

    first_scoring: 初めて採点が成立しうる日時（ラウンドの週が終わった後の最初の採点機会）
    """
    now = datetime.now(JST)
    ahead = (weekday - now.weekday()) % 7
    nxt = (now + timedelta(days=ahead)).replace(hour=hour, minute=0, second=0, microsecond=0)
    if nxt <= now:
        nxt += timedelta(days=7)
    first = datetime.fromisoformat(first_scoring).replace(tzinfo=JST)

    if ledger_rows:
        return f"次回採点: {nxt:%-m/%d(%a) %H:%M}"
    if now < first:
        return f"初採点は {first:%-m/%d(%a) %H:%M}——それまでは収集期間（ラウンドの週が終わるまで採点対象が無い）"
    prev = nxt - timedelta(days=7)
    return (f"未採点——採点機会 {prev:%-m/%d %H:%M} を過ぎても結果が出ていない"
            f"（{label}）。次の機会は {nxt:%-m/%d(%a) %H:%M}")


def league_panels(veg, qk, ai, new=None):
    """左ナビで切り替わるリーグ別パネル（ルール／成績表／当日・次イベントの3ブロック）"""
    GHB = "https://github.com/hsumiyoshi/lab/blob/main"
    def card(title, body):
        return f"<section><div class='head'><h2>{title}</h2></div>{body}</section>"
    def empty(msg):
        return f"<div class='empty'><span class='pulse'></span>{msg}</div>"
    def table(rows, cols, headers):
        th = "".join(f"<th>{h}</th>" for h in headers)
        trs = "".join("<tr>" + "".join(f"<td>{r.get(c, chr(8212))}</td>" for c in cols) + "</tr>"
                      for r in rows[-6:])
        return f"<table><tr>{th}</tr>{trs}</table>"
    def mae_table(led, unit):
        """誤差型の台帳（{日付: {err: {機体: 値}}}）から平均誤差の表を作る。"""
        rows = [v for v in led.values() if isinstance(v, dict) and v.get("err")]
        if not rows:
            return empty("初採点待ち")
        names = sorted({k for r in rows for k in r["err"]})
        body = "".join(
            f"<tr><td>{n}</td><td>{sum(r['err'][n] for r in rows if n in r['err'])/max(1,len([r for r in rows if n in r['err']])):.2f}</td>"
            f"<td>{len([r for r in rows if n in r['err']])}</td></tr>" for n in names)
        return f"<table><tr><th>機体</th><th>平均誤差({unit})</th><th>出場</th></tr>{body}</table>"

    def hit_table(led):
        """的中型の台帳（{日付: {hit: {機体: bool}}}）から的中率の表を作る。"""
        rows = [v for v in led.values() if isinstance(v, dict) and v.get("hit")]
        if not rows:
            return empty("初採点待ち")
        names = sorted({k for r in rows for k in r["hit"]})
        body = "".join(
            f"<tr><td>{n}</td><td>{sum(1 for r in rows if r['hit'].get(n))}/{len(rows)}</td>"
            f"<td>{sum(1 for r in rows if r['hit'].get(n))/len(rows):.0%}</td></tr>" for n in names)
        return f"<table><tr><th>機体</th><th>的中</th><th>的中率</th></tr>{body}</table>"

    def machines(items):
        cards = "".join(
            f"<div class='mc'><span class='mdot' style='background:{c}'></span>"
            f"<div><div class='mname'>{n}</div><div class='mdesc'>{d}</div></div></div>"
            for n, c, d in items)
        return f"<div class='mgrid'>{cards}</div>"
    def fig(path, alt):
        uri = data_uri(path)
        return (f"<div class='fig'><img src='{uri}' alt='{alt}'></div>" if uri else "")
    def panel(pid, rule_html, mcards, score_body, today_body, link):
        return (f"<article id='{pid}' class='panel'>"
                + card("📖 ルールと出場機体", f"<div class='meta'>{rule_html}</div>{mcards}")
                + card("🏆 成績表", score_body)
                + card("📍 当日 / 次のイベント", today_body)
                + f"<div class='links'><a href='{link}'>台帳（生データ）</a></div></article>")
    return "".join([
        panel("lg-veg",
              "仮想の出荷者として、東京市場の日次単価で「週のどの日に売るか」を毎週選ぶ。"
              "品目はきゅうり・トマト・キャベツ・レタス。答え合わせは週明けの実勢価格" + rule_svg("veg"),
              machines([
                  ("equal", "#eda100", "毎営業日に均等出荷する脳死ベンチマーク"),
                  ("weekshape8", "#2a78d6", "直近8週の曜日別の価格の形で売り日を選ぶ"),
                  ("stop_rule", "#1baf7a", "前日価格が21日平均を超えたら売る（見切り型）"),
                  ("first_day", "#eb6834", "週の初日に即売り（保存リスク回避の慣行）"),
                  ("oracle", "#898781", "週内最高値の日に売った場合の理論上限（参照値）"),
              ]),
              (table(veg, ["week", "item", "oracle", "equal", "weekshape8", "stop_rule"],
                     ["週", "品目", "oracle", "均等", "weekshape8", "stop_rule"]) if veg
               else empty(schedule_note(veg, 2, 14, "2026-08-26T14:00", "CI失敗またはデータ未確定")))
              + fig("exp02_vegetable/reports/veg_chart.png", "直近90日の日次単価"),
              empty(schedule_note(veg, 2, 14, "2026-08-26T14:00", "CI失敗またはデータ未確定")),
              f"{GHB}/exp02_vegetable/reports/veg_forward.md"),
        panel("lg-quake",
              "大森則（余震が時間のべき乗で減る経験則）をフィットし、熊本余震（2026-07-28 M7.1）の"
              "翌週件数を予測する。採点は対数誤差" + rule_svg("quake"),
              machines([
                  ("flat", "#eda100", "前週と同じ件数と予測する脳死ベンチマーク"),
                  ("omori", "#eb6834", "改良大森則 n(t)=K/(t+c)^p を毎週フィットして外挿"),
                  ("oracle", "#898781", "実績そのもの（誤差ゼロの参照値）"),
              ]),
              (table(qk, ["week", "actual", "omori", "flat", "err_omori", "err_flat"],
                     ["週", "実績", "大森則", "横ばい", "誤差(大森)", "誤差(横ばい)"]) if qk
               else empty(schedule_note(qk, 0, 14, "2026-08-24T14:00", "CI失敗またはデータ未確定")))
              + fig("exp05_quake/reports/quake_forward.png", "日別余震件数と大森則フィット"),
              empty(schedule_note(qk, 0, 14, "2026-08-24T14:00", "CI失敗またはデータ未確定")),
              f"{GHB}/exp05_quake/reports/quake_forward.md"),
        panel("lg-re",
              "2026-07-28 熊本地震（M7.1）の直後に、市場への影響を予言してコミット済み。"
              "外れてもそのまま残す" + rule_svg("re"),
              machines([
                  ("予言1", "#2a78d6", "2026Q3の取引件数は前年比で減る（中心-10%）"),
                  ("予言2", "#1baf7a", "価格は±5%以内——恐怖による割引は現れない"),
                  ("予言3", "#eb6834", "洪水ハザード内外の相対価格差は地震後も不変"),
              ]),
              empty("対象データ（2026Q3）の公表待ち — 予言は先に置いてある")
              + fig("exp04_realestate/reports/re_chart.png", "判定対象データ: 熊本×東京の四半期単価"),
              empty("判定 2027年1月〜（不動産取引データの公表後）"),
              f"{GHB}/exp04_realestate/predictions.md"),
        panel("lg-sat",
              "Sentinel-2衛星で嬬恋のキャベツ畑の緑（NDVI）を観測し、生育の進み方から"
              "東京市場への本格出荷週を予測する" + rule_svg("sat"),
              machines([
                  ("平年並み", "#eda100", "毎年W25と予測する脳死ベンチマーク"),
                  ("threshold", "#2a78d6", "NDVI 0.65到達日が6/上旬より遅ければW26、早ければW25"),
              ]),
              empty("2027年春の生育観測から — 予測のコミット期限は2027-06-01")
              + fig("exp07_satellite/reports/ndvi_chart.png", "NDVIと入荷量の3年比較"),
              empty("判定 2027年7月頃（東京市場の入荷実績で）"),
              f"{GHB}/exp07_satellite/predictions.md"),
        panel("lg-weather",
              "気象庁の17:00発表を常設ベンチにして、翌日の東京の最高気温を当てにいく。"
              "国家予算の予報が相手なので、**負けても「どの条件で負けるか」が分かる**のが狙い",
              machines([
                  ("jma", "#eda100", "気象庁の発表そのまま（強敵ベンチ）"),
                  ("persistence", "#898781", "今日の実績＝明日の予測（脳死ベンチ）"),
                  ("openmeteo", "#2a78d6", "別モデル（Open-Meteo）の予報"),
                  ("blend", "#1baf7a", "気象庁とOpen-Meteoの平均"),
                  ("debias", "#eb6834", "直近30日の自分の系統誤差を差し引く"),
              ]),
              (mae_table(new.get("weather", {}), "℃") if new and new.get("weather")
               else empty("初採点待ち（提出は毎日18:00 JST）")),
              empty("次回: 毎日18:00に提出、翌々日に採点"),
              f"{GHB}/exp03_weather/reports/weather_forward.md"),
        panel("lg-curtail",
              "九電が17:00に出す「翌日の再エネ出力制御の見通し」を、**発表の10時間前に**当てる。"
              "実績量は非公表と分かったため、予測対象を『九電が出す見通しそのもの』に設計変更した",
              machines([
                  ("always_none", "#898781", "常に「なし」（脳死ベンチ）"),
                  ("persistence", "#eda100", "今日と同じ（脳死ベンチ2）"),
              ]),
              (hit_table(new.get("curtail_led", {})) if new and new.get("curtail_led")
               else empty(f"収集中（{len(new.get('curtail_hist', {})) if new else 0}日分）。"
                          "夏は制御がほぼ出ないので本番は春と秋")),
              empty("提出 07:00 / 採点 17:10 JST"),
              f"{GHB}/exp08_curtail/reports/curtail_forward.md"),
        panel("lg-disaster",
              "全球の災害イベント（GDACS）が明日いくつ立ち上がるかを当てる。"
              "**RSSは巻き取り式で古いものが落ちる**ため、毎日取り込まないと後から遡れない——"
              "継続そのものが資産になる型",
              machines([
                  ("persistence", "#eda100", "今日と同じ件数（脳死ベンチ）"),
                  ("ma7", "#2a78d6", "直近7日平均"),
                  ("FIRMS", "#eb6834", "参考: 衛星が観測した熱異常のピクセル数（人の集計との差を見る）"),
              ]),
              (mae_table(new.get("disaster_led", {}), "件") if new and new.get("disaster_led")
               else empty(f"収集中（イベント{len(new.get('disaster_ev', {})) if new else 0}件）")),
              empty("毎日07:00 JSTに取込・提出・採点"),
              f"{GHB}/exp10_disaster/reports/disaster_forward.md"),
        panel("lg-books",
              "今週のベストセラー上位10冊のうち、来週も残るのは何冊か。"
              "1位当ては運の比重が大きいので、**ヒットの持続性そのもの**を測る設計にした",
              machines([
                  ("all_stay", "#898781", "10冊すべて残る（脳死ベンチ）"),
                  ("persistence", "#eda100", "前回の実績と同じ"),
                  ("mean", "#2a78d6", "過去の平均"),
              ]),
              (mae_table(new.get("books_led", {}), "冊") if new and new.get("books_led")
               else empty(f"収集中（{len(new.get('books_rank', {})) if new else 0}週分）。初採点は8/27")),
              empty("毎週木曜07:00 JSTに取得・採点・提出"),
              f"{GHB}/exp11_books/reports/books_forward.md"),
        panel("lg-ai",
              "互いに矛盾する推論スタイルを凍結した3つのAI人格が、毎週同じ公開情報だけを与えられた"
              "独立セッションとして、今週のリーグの結末に確率で賭ける（互いの答えは見えない）。"
              "採点はBrier（(確率−結果)²・低いほど良い）。常に0.5と答える臆病者ベンチ（Brier 0.25）に勝てない人格は没",
              machines([
                  ("外挿AI", "#eb6834", "直近の傾向はそのまま続く。機構の説明より最新データの勢いを信じる"),
                  ("回帰AI", "#2a78d6", "極端な観測は平均に戻る。少数標本の好成績は幻。基準率を最重視"),
                  ("構造AI", "#1baf7a", "機構で説明できる予測だけを信じる。因果の筋が通らない傾向は雑音"),
                  ("臆病者", "#eda100", "すべての問いに0.5と答える脳死ベンチマーク（Brier 0.25）"),
              ]),
              ai_score_body(ai),
              empty("次回: 毎週水曜に新ラウンドの賭けを公開、前週分を採点"),
              f"{GHB}/ai_league.json"),
    ])


def timeline(veg, qk):
    """これからの予定。日付は直書きせずcronの曜日から計算する。

    以前は「8/17(月) 地震リーグ初戦」のように固定文字列で持っていたため、
    その日を過ぎても「これからの予定」に残り続けていた（過去が未来として並ぶ）。
    """
    now = datetime.now(JST)

    def nxt(weekday, hh, mm):
        """次に来るその曜日の時刻。今日ちょうどでも過ぎていれば翌週にする。"""
        d = now.replace(hour=hh, minute=mm, second=0, microsecond=0)
        ahead = (weekday - d.weekday()) % 7
        if ahead == 0 and d <= now:
            ahead = 7
        return d + timedelta(days=ahead)

    def prev(rows):
        return f"（前回 {rows[-1]['week']}）" if rows and rows[-1].get("week") else ""

    WD = "月火水木金土日"
    q, v = nxt(0, 14, 0), nxt(2, 14, 0)   # quake_forward: 月14:00 / veg_forward: 水14:00
    items = [
        ("毎日 13:30", "⚡ 電力リーグ採点（picksは毎朝7:00に自動提出）"),
        (f"{q:%-m/%d} ({WD[q.weekday()]}) 14:00", f"🌏 地震リーグの採点{prev(qk)}"),
        (f"{v:%-m/%d} ({WD[v.weekday()]}) 14:00", f"🥬 青果リーグの採点{prev(veg)}"),
        ("毎週水曜", "🤖 AIリーグ: 3人格の賭けを公開・前週分を採点"),
        ("2027年1月〜", "🏠 不動産の事前登録予測を判定"),
        ("2027年6月", "🛰 衛星の出荷週予測をコミット（7月に判定）"),
    ]
    return "".join(f"<li><b>{d}</b><span>{s}</span></li>" for d, s in items)


def build():
    tpl = (ROOT / "site_template.html").read_text(encoding="utf-8")
    p = read_power()
    veg = read_csv("exp02_vegetable/reports/veg_ledger.csv")
    ai = json.loads((ROOT / "ai_league.json").read_text(encoding="utf-8")) if (ROOT / "ai_league.json").exists() else None
    qk = read_csv("exp05_quake/reports/quake_ledger.csv")
    # 新設4リーグ（2026-08-23開設）。台帳はJSON。無ければ空で描く
    def read_json(path):
        f = ROOT / path
        try:
            return json.loads(f.read_text())
        except Exception:
            return {}
    new = {"weather": read_json("exp03_weather/data/ledger.json"),
           "curtail_led": read_json("exp08_curtail/data/ledger.json"),
           "curtail_hist": read_json("exp08_curtail/data/outlook_history.json"),
           "disaster_led": read_json("exp10_disaster/data/ledger.json"),
           "disaster_ev": read_json("exp10_disaster/data/events.json"),
           "firms": read_json("exp10_disaster/data/firms_daily.json"),
           "books_led": read_json("exp11_books/data/ledger.json"),
           "books_rank": read_json("exp11_books/data/rankings.json")}
    now = datetime.now(JST)

    html = re.sub(r"<!--SAMPLE-->.*?<!--/SAMPLE-->", "", tpl, flags=re.S)
    html = re.sub(r"<!--TPL:(.*?)/TPL-->", lambda m: m.group(1), html, flags=re.S)
    html = (html.replace("{{DAYS}}", str(p["days"] if p else 0))
                .replace("{{UPDATED}}", f"{now:%Y-%m-%d %H:%M}")
                .replace("{{POWER_TABLE}}", power_table(p) if p else "")
                .replace("{{LATEST_LINE}}", latest_line(p) if p else "初日の採点待ち")
                .replace("{{LEAGUE_PANELS}}", league_panels(veg, qk, ai, new))
                .replace("{{TIMELINE}}", timeline(veg, qk)))
    for src_name, path in (("forward_pnl.png", "exp01_jepx/reports/forward_pnl.png"),
                           ("latest_anatomy.png", None)):
        pass
    pnl = data_uri("exp01_jepx/reports/forward_pnl.png")
    anas = sorted((ROOT / "exp01_jepx" / "reports" / "anatomy").glob("*.png"))
    ana = ("data:image/png;base64," + base64.b64encode(anas[-1].read_bytes()).decode()) if anas else None
    if pnl:
        html = html.replace('src="forward_pnl.png"', f'src="{pnl}"')
    if ana:
        html = html.replace('src="latest_anatomy.png"', f'src="{ana}"')
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
