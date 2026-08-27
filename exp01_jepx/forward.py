#!/usr/bin/env python3
"""フォワード運用ランナー。

- 戦略凍結日: 2026-08-12（この日までの477日はインサンプル。以降は変更禁止）
- フォワード開始: 2026-08-14 受渡分（凍結時点でオークション未実施の最初の日）
- 方式: ステートレス台帳。毎回、凍結ルールでFORWARD_START以降を全再計算する。
  状態ファイルなし・欠測に強い・公開成績は再実行で誰でも検証可能。

毎日の使い方:
    python3 forward.py          # データ更新→台帳更新→明日のpicks生成→レポート出力

出場機体: ベースライン = clock(固定時間帯) / weekshape(曜日形状) をここで計算。
         参加機体（tenki / hybrid 等）は「予測提出型」——
         picks/ に提出されたJSONだけで台帳入りする（コードは参加者の手元、
         2026-08-13に本リポジトリから分離。凍結済みロジックは不変）
oracle は事後の上限参照値としてのみ記載。
"""

import json
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd


def _get_json(url: str, tries: int = 5):
    """気象APIのGET。429（レート制限）は待ち時間を別枠で長く取る。

    2026-08-22: Open-Meteoの429で提出ジョブが落ちた。2/4/8秒の待機は
    レート制限の解除には短すぎる（サーバ側の窓は分単位）。429だけ
    Retry-Afterを尊重し、無ければ30/60/120/240秒と伸ばす。
    """
    for attempt in range(tries):
        try:
            with urllib.request.urlopen(url, timeout=60) as res:
                return json.load(res)
        except urllib.error.HTTPError as e:
            if attempt == tries - 1:
                raise
            if e.code == 429:
                wait = int(e.headers.get("Retry-After") or 0) or 30 * (2 ** attempt)
                print(f"  429 レート制限 → {wait}秒待機（{attempt + 1}/{tries - 1}回目）")
                time.sleep(min(wait, 300))
            else:
                time.sleep(2 ** (attempt + 1))
        except Exception:
            if attempt == tries - 1:
                raise
            time.sleep(2 ** (attempt + 1))

import fetch as jepx_fetch
import sim
import weather

HERE = Path(__file__).parent
OUT = HERE / "output"
JST = ZoneInfo("Asia/Tokyo")
FORWARD_START = pd.Timestamp("2026-08-14")
CONTRACT_ISSUES: list = []   # 契約検査の結果（レポート先頭に自己申告する）
FREEZE_NOTE = "戦略凍結: 2026-08-12 / フォワード開始: 2026-08-14受渡分"

STRAT_NAMES = ["clock", "weekshape", "tenki", "hybrid", "oracle"]


# ---------------- ハイブリッド（フォワード専用機体） ----------------

def _dev_series(upto_day: pd.Timestamp) -> pd.Series:
    """日ごとの「日射予報の平年乖離」。平年=直近28日の実測平均（因果的）"""
    wa, wf = sim.WEATHER_A, sim.WEATHER_F
    norm = wa["rad"].rolling(28).mean().shift(1)
    dev = (wf["rad"] - norm).abs().dropna()
    return dev[dev.index < upto_day]


def dev_signed_text(rad_forecast, day: pd.Timestamp, dev, thr) -> str:
    """乖離の符号つき表示（issue #11）。判定は従来どおり絶対値、これは表示専用。

    符号が見えない絶対値表示が「強=晴れて暑い日」という人間とAI双方の
    誤読を3日間許した（2026-08-16に発覚）ため、方向を常に添える。
    """
    if rad_forecast is None or dev is None or thr is None:
        return ""
    norm = sim.WEATHER_A.loc[sim.WEATHER_A.index < day, "rad"].tail(28).mean() \
        if sim.WEATHER_A is not None else None
    if norm is None or pd.isna(norm):
        return f"乖離 {dev:.0f}（閾値 {thr:.0f}）"
    signed = rad_forecast - norm
    arrow = "普段より晴れる" if signed > 0 else "普段より曇る"
    return (f"乖離 {signed:+.0f} W/m2（{arrow}方向。直近28日平均 {norm:.0f}、"
            f"判定は絶対値 {dev:.0f} vs 閾値 {thr:.0f}）")


def rad_dev(day: pd.Timestamp, rad_forecast: float | None = None):
    """対象日の乖離と、対象日前日までの75%閾値を返す"""
    wa = sim.WEATHER_A
    hist_dev = _dev_series(day)
    if len(hist_dev) < 60:
        return None, None
    norm = wa.loc[wa.index < day, "rad"].tail(28).mean()
    rf = rad_forecast if rad_forecast is not None else (
        sim.WEATHER_F.loc[day, "rad"] if day in sim.WEATHER_F.index else None)
    if rf is None or pd.isna(norm):
        return None, None
    return abs(rf - norm), hist_dev.quantile(0.75)


# ---------------- 台帳 ----------------
# 予測提出型: ベースライン（clock/weekshape）はここで計算し、参加機体（tenki/hybrid等）は
# picks/ に提出されたJSONだけで台帳入りする。提出が無い日は無得点（欠場）。

PICKS_DIR = HERE / "picks"


def load_picks(day: pd.Timestamp) -> dict:
    f = PICKS_DIR / f"{day:%Y-%m-%d}.json"
    if not f.exists():
        return {}
    data = json.loads(f.read_text(encoding="utf-8"))
    # 機体名の正規化: 旧picksの haruki_ プレフィックスは表示名から外す（履歴は無変換で保持）
    return {name.removeprefix("haruki_"): (set(v["charge"]), set(v["discharge"]))
            for name, v in data.items() if not name.startswith("_")}


def load_picks_bt(day: pd.Timestamp) -> dict:
    """参戦前バックテスト補完picks（bt_YYYY-MM-DD.json）。

    Haruki決定（2026-08-17, issue #12）: 累計金額はBT補完込みの1本で勝負し、
    BT率を表示して読者が意識的に割り引く。補完picksは当時入手可能だった
    データのみで再現生成する（因果順守）。実picksに存在する機体は対象外。
    """
    f = PICKS_DIR / f"bt_{day:%Y-%m-%d}.json"
    if not f.exists():
        return {}
    data = json.loads(f.read_text(encoding="utf-8"))
    return {name: (set(v["charge"]), set(v["discharge"]))
            for name, v in data.items() if not name.startswith("_")}


def picks_meta(day: pd.Timestamp) -> dict:
    f = PICKS_DIR / f"{day:%Y-%m-%d}.json"
    if not f.exists():
        return {}
    return json.loads(f.read_text(encoding="utf-8")).get("_meta", {})


def build_ledger(df: pd.DataFrame) -> pd.DataFrame:
    days = [d for d in df["date"].drop_duplicates().sort_values() if d >= FORWARD_START]
    rows = []
    for day in days:
        today = df[df["date"] == day].set_index("koma")["sp"]
        hist = df[df["date"] < day]
        # 信号は提出時の_metaを正とする（採点時再計算とのズレを防ぐ）。無ければ再計算
        signal = picks_meta(day).get("signal")
        if not signal:
            dev, thr = rad_dev(day)
            signal = "強" if (dev is not None and dev >= thr) else "平常"
        row = {"date": day, "signal": signal}
        for name, (c, d) in load_picks(day).items():
            row[name] = sim.run_day(today, c, d)
        # ベースライン2機体は2026-08-27まで、提出せず採点時にコードから再計算していた。
        # 先読みは無かったが「価格公表前にコミット済み」だけが当てはまらず、
        # ロジックを書き換えれば過去の成績も動いた（Haruki判断「A」で提出型へ移行）。
        # 提出が無い日——移行前と、提出そのものが落ちた日——だけ再計算で埋め、
        # 事前コミットでないことを rc_ 列に残す。黙って埋めると成績表で区別できなくなる
        if "clock" not in row:
            row["clock"] = sim.run_day(today, *sim.strat_clock())
            row["rc_clock"] = 1
        if "weekshape" not in row:
            # 提出側と同じく today は渡さない。pred_weekshape は現在この引数を
            # 使っていないが、採点時だけ実価格を渡す形を残すと、いつか誰かが
            # 使った瞬間に先読みになる（2026-08-27に疑って確認した箇所）
            pred = sim.pred_weekshape(hist, None, day)
            if pred is not None:
                row["weekshape"] = sim.run_day(today, *sim.choose_split(pred))
                row["rc_weekshape"] = 1
        for name, (c, d) in load_picks_bt(day).items():
            if name not in row:
                row[name] = sim.run_day(today, c, d)
                row[f"bt_{name}"] = 1
        row["oracle"] = sim.run_day(today, *sim.choose_split(today))
        rows.append(row)
    return pd.DataFrame(rows).set_index("date") if rows else pd.DataFrame()


# ---------------- 明日のpicks ----------------

def next_delivery_day() -> pd.Timestamp:
    """まだ前日スポットの入札が締まっていない（10:00 JST前の）最初の受渡日"""
    now = datetime.now(JST)
    auction_day = now.date() if now.hour < 10 else now.date() + timedelta(days=1)
    return pd.Timestamp(auction_day + timedelta(days=1))


def live_forecast(target: pd.Timestamp):
    """live予報APIから対象日の全国加重の日射・気温を取る"""
    rad, tmp = 0.0, 0.0
    for name, (lat, lon, wr, wt) in weather.POINTS.items():
        url = (f"https://api.open-meteo.com/v1/forecast?latitude={lat}&longitude={lon}"
               f"&hourly=temperature_2m,shortwave_radiation&timezone=Asia%2FTokyo&forecast_days=4")
        j = _get_json(url)
        h = pd.DataFrame(j["hourly"])
        h["time"] = pd.to_datetime(h["time"])
        day_rows = h[h["time"].dt.normalize() == target]
        r = day_rows[day_rows["time"].dt.hour.between(9, 15)]["shortwave_radiation"].mean()
        t = day_rows[day_rows["time"].dt.hour.between(9, 21)]["temperature_2m"].mean()
        if pd.isna(r) or pd.isna(t):
            return None, None
        rad += r * wr
        tmp += t * wt
    return rad, tmp


def koma_to_range(k: int) -> str:
    start = (k - 1) * 30
    return f"{start//60:02d}:{start%60:02d}"


def make_picks(df: pd.DataFrame, target: pd.Timestamp) -> dict:
    """ベースラインのpicks計算＋提出済み機体のpicks読込（表示用）"""
    hist = df[df["date"] < target]
    rf, _tf = live_forecast(target)
    dev, thr = rad_dev(target, rad_forecast=rf)
    picks = {"clock": sim.strat_clock()}
    pred = sim.pred_weekshape(hist, None, target)
    picks["weekshape"] = sim.choose_split(pred) if pred is not None else None
    picks.update(load_picks(target))  # 提出済み機体（提出が無ければ載らない=欠場）
    sub = picks_meta(target)
    meta = {"rad_forecast": rf, "dev": dev, "thr": thr,
            "signal": sub.get("signal") or ("強" if (dev is not None and thr is not None and dev >= thr) else "平常")}
    return picks, meta


# ---------------- チャート ----------------

# 機体→色の固定割当（dataviz検証済みパレット。順序・対応は変更しない）
COLORS = {"weekshape": "#2a78d6", "tenki": "#eb6834",
          "hybrid": "#1baf7a", "clock": "#eda100", "tenki_v2": "#9b6bd3", "tenki_v3": "#c44e52",
          "tenki_v4": "#6b7f2e"}
INK, INK2, MUTED = "#0b0b0b", "#52514e", "#898781"
GRID, BASE, SURFACE = "#e1e0d9", "#c3c2b7", "#fcfcfb"


def plot_ledger(ledger: pd.DataFrame, dest: Path) -> None:
    """oracle到達率(%)の累積推移。ベンチマーク(clock)を基準線として明示する。

    縦軸は「答えを見た理論上限(oracle)の何%を取れたか」。各機体の線は
    **事前コミット済みのフォワード日だけ**を累積するので、終端の値は
    成績表の「対oracle（Fwd）」と一致する（BT補完日は比率に混ぜない）。

    clockの到達率に水平線を引き、その下を塗る。clockは「毎日同じ時間に売買する」
    だけの脳死ベンチで、電力価格の日内周期のせいで予測ゼロでも高い到達率が出る。
    この帯を描かないと、90%台という数字を実力と誤読する（規約2「脳死との差」）。

    図の中の文字はASCIIのみ。CIランナーに日本語フォントが無く豆腐になるため、
    日本語の説明はHTML側のキャプションが持つ。
    """
    import matplotlib.pyplot as plt
    MIN_D = 4  # 累積比が暴れる立ち上がりは描かない（分母が小さく数日で±15pt振れる）
    names = [n for n in COLORS if n in ledger.columns]

    def rate(name, precommitted_only=True):
        """到達率(%)の累積系列。既定は事前コミット済みの日だけ（表と同じ基準）。"""
        mask = ledger[name].notna()
        if precommitted_only:
            # BT補完（参戦前の穴埋め）も再計算（提出が無い日）も、価格公表前に
            # コミットされていないので除く。成績表の対oracleと同じ分母になる
            for pre in ("bt_", "rc_"):
                col = f"{pre}{name}"
                if col in ledger.columns:
                    mask &= ledger[col].fillna(0) != 1
        idx = ledger.index[mask]
        if len(idx) < MIN_D:
            return None
        num = ledger.loc[idx, name].cumsum()
        den = ledger.loc[idx, "oracle"].cumsum()
        r = (num / den.where(den > 0) * 100).iloc[MIN_D - 1:]
        return r.dropna()

    series = {n: s for n in names if (s := rate(n)) is not None and len(s)}
    if not series:
        return
    fig, ax = plt.subplots(figsize=(10, 5.2))
    fig.patch.set_facecolor(SURFACE)
    ax.set_facecolor(SURFACE)

    # ベンチマーク帯: clockの到達率より下＝予測がベンチに負けている領域。
    # clockが事前コミットの日をまだ持たない移行期は全日ベースで引く——基準線を
    # 消すと「90%台は実力か」を判断する手がかりがページから無くなる。
    # clockは strat_clock() が引数を取らない完全固定ルールなので、再計算しても
    # 結果は動かない（weekshapeと違ってパラメータが無い）。ただし明記はする
    bench_recomputed = "clock" not in series
    bseries = series.get("clock")
    if bseries is None:
        bseries = rate("clock", precommitted_only=False)
    bench = float(bseries.iloc[-1]) if bseries is not None and len(bseries) else None
    if bench is not None:
        ax.axhspan(0, bench, color=BASE, alpha=0.16, lw=0, zorder=0)
        ax.axhline(bench, color=INK2, lw=1.2, zorder=1)

    last_x = max(s.index[-1] for s in series.values())
    first_x = min(s.index[0] for s in series.values())

    # 縦軸は勝負が起きている帯だけに寄せる。0%からの余白は情報を持たず、
    # 見せたい差（ベンチ82%台 vs 首位91%台）を潰してしまう
    lo = min([float(s.min()) for s in series.values()] + ([bench] if bench else []))
    ax.set_ylim(max(0.0, lo - 4), 103)

    ax.axhline(100, color=MUTED, lw=1.2, ls="--", zorder=1)

    # 終端ラベルの衝突回避: y昇順に並べ、最小間隔を満たすまで押し上げる
    ends = sorted(((float(s.iloc[-1]), n, s) for n, s in series.items()))
    span = (103 - max(0.0, lo - 4))
    gap = span * 0.042
    last_x_off = last_x + (last_x - first_x) * 0.035
    placed, prev = [], None
    for y, n, s in ends:
        yy = y if prev is None else max(y, prev + gap)
        placed.append((yy, y, n, s))
        prev = yy
    for yy, y, n, s in placed:
        is_bench = n == "clock"
        ax.plot(s.index, s.values, color=COLORS[n], marker="o", ms=2.6,
                lw=2.4 if is_bench else 1.9, zorder=3 if is_bench else 2)
        if is_bench:
            continue  # clockの終端ラベルは下のベンチマーク注記が兼ねる（線と重なるため）
        # 引き出し線はデータの線と混同されないよう細い灰色にする
        ax.annotate(f"{n} {y:.1f}%", xy=(s.index[-1], y), xytext=(last_x_off, yy),
                    textcoords="data", color=COLORS[n], fontsize=8.5, va="center",
                    zorder=4, annotation_clip=False,
                    arrowprops=dict(arrowstyle="-", lw=0.6, color=MUTED,
                                    shrinkA=0, shrinkB=3))
    ax.annotate("oracle 100%", xy=(last_x_off, 100), xytext=(0, 6),
                textcoords="offset points", color=MUTED, fontsize=8.5,
                va="bottom", zorder=4, annotation_clip=False)
    if bench is not None:
        ax.annotate(f"benchmark  clock {bench:.1f}%", xy=(last_x_off, bench),
                    xytext=(0, 6), textcoords="offset points",
                    color=INK2, fontsize=8.5, va="bottom", fontweight="bold",
                    zorder=4, annotation_clip=False)
        ax.annotate("no forecast at all"
                    + ("  (recomputed)" if bench_recomputed else ""),
                    xy=(last_x_off, bench),
                    xytext=(0, -9), textcoords="offset points",
                    color=MUTED, fontsize=7.8, va="center",
                    zorder=4, annotation_clip=False)

    ax.set_ylabel("% of oracle (cumulative, forward days only)",
                  color=INK2, fontsize=9)
    ax.grid(color=GRID, lw=0.7, axis="y")
    for sp in ("top", "right"):
        ax.spines[sp].set_visible(False)
    for sp in ("left", "bottom"):
        ax.spines[sp].set_color(BASE)
    ax.tick_params(colors=INK2, labelsize=8.5)
    ax.set_xlim(right=last_x + (last_x - first_x) * 0.30)
    fig.tight_layout()
    fig.savefig(dest, dpi=130, facecolor=SURFACE)
    plt.close(fig)



# ---------------- 当日の解剖（勝敗の原因分析用の記録） ----------------

def day_anatomy(df: pd.DataFrame, day: pd.Timestamp) -> str:
    """採点済みの1日について、各機体の売買コマと因子の値を記録する。

    ステートレス: picksファイル（提出時_meta込み）と価格データから毎回再生成。
    """
    today = df[df["date"] == day].set_index("koma")["sp"]
    hist = df[df["date"] < day]
    meta = picks_meta(day)

    picks = {"clock": sim.strat_clock()}
    pred = sim.pred_weekshape(hist, None, day)
    if pred is not None:
        picks["weekshape"] = sim.choose_split(pred)
    picks.update(load_picks(day))
    picks["oracle"] = sim.choose_split(today)

    lines = [f"### {day.date()}（信号: {meta.get('signal', '?')}）", ""]
    rad_f, dev, thr = meta.get("rad_forecast"), meta.get("dev"), meta.get("thr")
    rad_a = (f"{sim.WEATHER_A.loc[day, 'rad']:.0f}"
             if sim.WEATHER_A is not None and day in sim.WEATHER_A.index
             else "未確定（実測は約5日遅れ）")
    if rad_f is not None and dev is not None and thr is not None:
        lines.append(f"- 因子: 日射予報 {rad_f:.0f} W/m2 / 実測 {rad_a} / "
                     + dev_signed_text(rad_f, day, dev, thr))
    else:
        lines.append("- 因子: picksの_meta欠落")
    cheap = today.idxmin()
    rich = today.idxmax()
    lines.append(f"- 価格の形: 最安 {koma_to_range(int(cheap))} {today.min():.2f}円 / "
                 f"最高 {koma_to_range(int(rich))} {today.max():.2f}円 / "
                 f"山谷比 {today.max()/max(today.min(), 0.01):.1f}倍")
    lines += ["", "| 機体 | 充電（買い） | 平均買値 | 放電（売り） | 平均売値 | 損益 |",
              "|---|---|---|---|---|---|"]
    for name, cd in picks.items():
        if not cd:
            continue
        c, d = cd
        buy, sell = today.loc[list(c)].mean(), today.loc[list(d)].mean()
        pnl = sim.run_day(today, c, d)
        lines.append(f"| {name} | {', '.join(koma_to_range(k) for k in sorted(c))} | {buy:.2f}円 "
                     f"| {', '.join(koma_to_range(k) for k in sorted(d))} | {sell:.2f}円 | {pnl:+,.0f}円 |")
    lines.append("")
    # 売買位置チャート（価格カーブ＋機体別マーカー）
    chart_dir = HERE / "reports" / "anatomy"
    chart_dir.mkdir(parents=True, exist_ok=True)
    anatomy_chart(today, picks, chart_dir / f"{day:%Y-%m-%d}.png")
    lines.append(f"![anatomy](anatomy/{day:%Y-%m-%d}.png)")
    lines.append("")
    # 48コマ価格（24列×2行・時刻ヘッダ。太字=最安/最高）
    lines.append("**48コマ価格（円/kWh。太字=最安/最高）**")
    lines.append("")
    kmin, kmax = int(today.idxmin()), int(today.idxmax())
    for half in range(2):
        ks = range(half * 24 + 1, half * 24 + 25)
        lines.append("| " + " | ".join(koma_to_range(k) for k in ks) + " |")
        lines.append("|" + "---|" * 24)
        cells = []
        for k in ks:
            v = f"{today[k]:.2f}" if k in today.index else "—"
            cells.append(f"**{v}**" if k in (kmin, kmax) else v)
        lines.append("| " + " | ".join(cells) + " |")
        lines.append("")
    return "\n".join(lines)


def anatomy_chart(today, picks, dest) -> None:
    """価格カーブ＋各機体の売買位置（▽=買い △=売り）を1枚に"""
    import matplotlib.pyplot as plt
    names = [n for n in ("clock", "weekshape", "tenki", "hybrid", "tenki_v2", "tenki_v3", "tenki_v4", "oracle")
             if n in picks and picks[n]]
    fig, (ax, ax2) = plt.subplots(2, 1, figsize=(10, 6), sharex=True,
                                  height_ratios=[3, 1.6])
    for a in (ax, ax2):
        a.set_facecolor(SURFACE)
        a.grid(color=GRID, lw=0.7)
        for s in ("top", "right"):
            a.spines[s].set_visible(False)
        for s in ("left", "bottom"):
            a.spines[s].set_color(BASE)
        a.tick_params(colors=INK2, labelsize=8.5)
    fig.patch.set_facecolor(SURFACE)
    x = [(k - 1) * 0.5 for k in today.index]
    ax.plot(x, today.values, color=INK, lw=1.8, drawstyle="steps-mid")
    kmin, kmax = int(today.idxmin()), int(today.idxmax())
    for k, va, dy in ((kmin, "top", -0.4), (kmax, "bottom", 0.4)):
        ax.annotate(f"{today[k]:.2f}", ((k - 1) * 0.5, today[k] + dy),
                    color=INK2, fontsize=8.5, ha="center", va=va)
    ax.set_ylabel("JPY/kWh", color=INK2, fontsize=9)
    for i, name in enumerate(names):
        c, d = picks[name]
        color = COLORS.get(name, MUTED)
        ax2.scatter([(k - 1) * 0.5 for k in sorted(c)], [i] * len(c),
                    marker="v", s=52, color=color, label=None)
        ax2.scatter([(k - 1) * 0.5 for k in sorted(d)], [i] * len(d),
                    marker="^", s=52, color=color)
    ax2.set_yticks(range(len(names)), names)
    ax2.set_ylim(-0.7, len(names) - 0.3)
    from matplotlib.ticker import MultipleLocator
    for a in (ax, ax2):
        a.xaxis.set_minor_locator(MultipleLocator(0.5))  # 30分=1コマごとの補助目盛り
        a.grid(which="minor", axis="x", color=GRID, lw=0.4, alpha=0.7)
        a.tick_params(which="minor", length=2, colors=BASE)
    ax2.set_xticks(range(0, 25, 2), [f"{h}" for h in range(0, 25, 2)])
    ax2.set_xlim(-0.5, 24.2)
    ax2.set_xlabel("hour (minor grid = 30min koma / v = buy, ^ = sell)", color=INK2, fontsize=9)
    fig.tight_layout()
    fig.savefig(dest, dpi=130, facecolor=SURFACE)
    plt.close(fig)


def write_anatomy(df: pd.DataFrame, ledger: pd.DataFrame, dest) -> str:
    """採点済み全日の解剖を新しい日から順に1ファイルへ（毎回全再生成）"""
    lines = ["# 日次解剖 — 各機体の売買と因子の記録", "",
             "勝敗の原因を考えるための記録。picks（提出時_meta）と価格データから毎回再生成される。", ""]
    latest = ""
    for day in sorted(ledger.index, reverse=True):
        a = day_anatomy(df, day)
        if not latest:
            latest = a
        lines.append(a)
    dest.write_text("\n".join(lines), encoding="utf-8")
    return latest


# ---------------- レポート ----------------

def absence_warnings(ledger, days: int = 7) -> list:
    """気象入力が落ちて天気系が全滅した日を警告として先頭に出す（issue #18）。

    サイレント劣化を止めた代償として欠場日が生まれる。欠場が黙って混ざると
    「弱かった」と誤読されるため、直近の欠測は台帳の先頭で自己申告する。
    """
    fam = [c for c in ("tenki", "tenki_v2", "tenki_v3", "tenki_v4") if c in ledger.columns]
    if ledger.empty or not fam:
        return []
    out = []
    for day, row in ledger.tail(days).iterrows():
        if not all(pd.isna(row[c]) for c in fam):
            continue
        # 理由を**推測しない**。picksが届いているかで原因が分かれる（2026-08-26受渡:
        # 提出そのものがpush競合で失われたのに「気象の取得失敗」と書いていた）
        f = PICKS_DIR / f"{pd.Timestamp(day):%Y-%m-%d}.json"
        if not f.exists():
            why = ("**提出が届いていない**（picksファイルが無い＝strategies側のCIが"
                   "落ちたか、pushが他のCIと衝突して失われた。気象とは別の原因）")
        else:
            try:
                reasons = set(json.loads(f.read_text()).get("_meta", {})
                              .get("absent", {}).values())
            except Exception:
                reasons = set()
            why = (f"気象予報の取得に失敗（{'/'.join(sorted(reasons))}）"
                   if reasons else "提出は届いたが天気系の入力が揃わなかった")
        out.append(f"> ⚠ {pd.Timestamp(day):%-m/%d}受渡: {why}のため天気系{len(fam)}機体は"
                   f"**欠場**（実力ではなく計測の欠測。後日、予報アーカイブからBT補完される）")
    return (out + [""]) if out else []


def report(ledger: pd.DataFrame, picks, meta, target, anatomy_latest: str = "") -> str:
    lines = [f"# JEPX仮想蓄電池 フォワード運用レポート",
             f"", f"{FREEZE_NOTE} / 生成: {datetime.now(JST):%Y-%m-%d %H:%M} JST", ""]
    lines += absence_warnings(ledger)
    if CONTRACT_ISSUES:
        lines += [f"> 🚨 入力データの契約違反 {len(CONTRACT_ISSUES)}件（採点の信頼性に影響）:"] \
                 + [f">   - {m}" for m in CONTRACT_ISSUES[:5]] + [""]
    if ledger.empty:
        lines += ["## 累計成績", "", "（フォワード対象日の価格はまだ公表されていない。初日の答え合わせを待て）", ""]
    else:
        days = len(ledger)
        strat_names = [c for c in ledger.columns
                       if c not in ("signal", "oracle") and not c.startswith(("bt_", "rc_"))]
        # 対oracleは「出場日ベース」: 各機体が出場した日のoracle合計で割る。
        # 円の絶対額は当日の値幅（レバレッジ）に依存し時変するため、
        # 在籍時期が違う機体を円/日で比べると不公平（Haruki指摘 2026-08-17, issue #12）
        stats = {}
        for n in strat_names:
            mask = ledger[n].notna()
            # 「事前でない日」= BT補完（参戦前の穴埋め）＋ 再計算（提出が無い日）。
            # どちらも価格公表前にコミットされていないので、レートの分母から外す
            notpre = pd.Series(False, index=ledger.index)
            for pre in ("bt_", "rc_"):
                col = f"{pre}{n}"
                if col in ledger.columns:
                    notpre |= ledger[col].fillna(0) == 1
            fwd = mask & ~notpre
            played = int(mask.sum())
            btdays = int((mask & notpre).sum())
            rc = ledger[f"rc_{n}"].fillna(0) == 1 if f"rc_{n}" in ledger.columns else None
            rcdays = int((mask & rc).sum()) if rc is not None else 0
            tot = float(ledger.loc[mask, n].sum())          # BT補完込みの金額（勝負の1本）
            # レート系（対oracle・対clock差）は事前コミット済みのフォワード日のみで計算。
            # 対clock差=同日ペア差: 対oracle%が消せない「時代の取りやすさ」を
            # 常設ベンチ経由で一次補正（Haruki指摘 2026-08-17）
            orc = float(ledger.loc[fwd, "oracle"].sum())
            clk = float(ledger.loc[fwd, "clock"].sum())
            ftot = float(ledger.loc[fwd, n].sum())
            allo = float(ledger.loc[mask, "oracle"].sum())
            stats[n] = (played, btdays, tot,
                        ftot / orc * 100 if orc else 0.0,
                        (ftot - clk) / orc * 100 if orc else 0.0,
                        rcdays,
                        tot / allo * 100 if allo else 0.0)
        order = sorted(strat_names, key=lambda n: -stats[n][3])  # 並びは対oracle（Fwd）＝実力順（Haruki指定）
        lines += ["![cumulative P&L](forward_pnl.png)", "",
                  f"## 累計成績（リーグ{days}日目）", "",
                  "| 機体 | 累計損益 | 事前でない日 | 対oracle（事前コミット日） | 対clock差（pt） |",
                  "|---|---|---|---|---|"]
        for n in order:
            p_, bt_, t_, pct, dpt, rc_, allpct = stats[n]
            kind = ("再計算" if rc_ == bt_ else "BT補完" if rc_ == 0
                    else f"BT{bt_ - rc_}日+再計算{rc_}日")
            btcell = f"{bt_ / p_ * 100:.0f}%（{bt_}/{p_}日・{kind}）" if bt_ else "0%"
            if bt_ == p_:
                # 事前コミット済みの日がゼロ＝レートは未計測。全日ベースの参考値は
                # 括弧で残す（消すとベンチマークそのものが表から消える）
                lines.append(f"| {n} | {t_:,.0f}円 | {btcell} | — （参考 {allpct:.1f}%） | — |")
            else:
                lines.append(f"| {n} | {t_:,.0f}円 | {btcell} | {pct:.1f}% | {dpt:+.1f} |")
        o_tot = float(ledger["oracle"].sum())
        lines.append(f"| oracle | {o_tot:,.0f}円 | — | 100.0% | — |")
        lines.append("")
        lines.append("累計損益は**BT補完込み**（参戦前の欠場日を、当時入手可能だったデータのみで"
                     "再現したバックテストで補完）。**事前でない日**＝価格公表前にコミットされていない日。内訳は**BT補完**（参戦前の穴埋め）と**再計算**（提出が無く採点時にコードから作った日）。clockとweekshapeは2026-08-27まで提出型ではなかったため、それ以前は全日が再計算。"
                     "**対oracle%と対clock差は事前コミット済みのフォワード日のみ**で計算——"
                     "対oracle%は値幅のスケールを、対clock差（常設ベンチとの同日ペア差）は"
                     "時代の取りやすさを一次補正する。完全対等の比較は下の直接対決（共通日のみ）")
        # 期間別（週別）: フォワードだけに集中して見る手段（Haruki要望）
        wk = ledger.index.to_series().dt.strftime("%G-W%V")
        weeks = sorted(wk.unique())
        lines += ["", "## 期間別（ISO週別、*=BT補完を含む）", "",
                  "| 週 | " + " | ".join(strat_names + ["oracle"]) + " |",
                  "|" + "---|" * (len(strat_names) + 2)]
        for w in weeks:
            sel = ledger[wk == w]
            cells = []
            for n in strat_names + ["oracle"]:
                v = float(sel[n].dropna().sum()) if n in sel else 0.0
                star = ""
                if f"bt_{n}" in sel.columns and (sel[f"bt_{n}"].fillna(0) == 1).any():
                    star = "*"
                cells.append(f"{v:,.0f}{star}")
            lines.append(f"| {w} | " + " | ".join(cells) + " |")
        fwd_only = ledger[strat_names].notna()
        for n in strat_names:
            if f"bt_{n}" in ledger.columns:
                fwd_only[n] &= ledger[f"bt_{n}"].fillna(0) != 1
        common = fwd_only.all(axis=1)
        cdays = int(common.sum())
        if cdays:
            lines += ["", f"## 直接対決（全機体出場日 {cdays}日のみ）", "",
                      "| 機体 | 累計損益 | 対oracle |", "|---|---|---|"]
            corc = float(ledger.loc[common, "oracle"].sum())
            for n, t_ in sorted(((n, float(ledger.loc[common, n].sum())) for n in strat_names),
                                key=lambda kv: -kv[1]):
                lines.append(f"| {n} | {t_:,.0f}円 | {t_/corc*100:.1f}% |")
            lines.append(f"| oracle | {corc:,.0f}円 | 100.0% |")
        if anatomy_latest:
            lines += ["## 直近日の解剖（全日分は [daily_anatomy.md](daily_anatomy.md)）", "",
                      anatomy_latest]
    lines += [f"## 明日のpicks（受渡日 {target.date()}、信号: {meta['signal']}）", ""]
    if meta["dev"] is not None:
        lines.append("日射予報の" + dev_signed_text(meta.get("rad_forecast"), target,
                                                    meta["dev"], meta["thr"]))
    lines += ["", "| 機体 | 充電（買い） | 放電（売り） |", "|---|---|---|"]
    for n in picks:
        if picks.get(n):
            c, d = picks[n]
            lines.append(f"| {n} | {', '.join(koma_to_range(k) for k in sorted(c))} | "
                         f"{', '.join(koma_to_range(k) for k in sorted(d))} |")
    lines.append("")
    return "\n".join(lines)


if __name__ == "__main__":
    print("データ更新中…")
    jepx_fetch.fetch(jepx_fetch.fiscal_year(datetime.now(JST).date()))
    for kind in ("actual", "forecast"):
        dest = HERE / "data" / f"weather_{kind}.csv"
        # 気象CSVはgitで追跡している（2026-08-23・issue #19）。CIは毎回まっさらな
        # checkoutから走るため、追跡していなかった頃は毎日フル履歴を取り直し、
        # それがOpen-Meteoの429を招いて提出ジョブごと落としていた。
        if dest.exists():
            try:
                last = pd.read_csv(dest).iloc[-1, 0]
                lag = (datetime.now(JST).date() - pd.Timestamp(last).date()).days
                if lag <= 7:
                    print(f"weather_{kind}: 最終 {last}（{lag}日前）→ 取得スキップ")
                    continue
            except Exception as e:
                print(f"weather_{kind}: 鮮度判定に失敗 {type(e).__name__} → 取りに行く")
        try:
            weather.build(kind).to_csv(dest)
        except Exception as e:   # 429等。既存CSVで走り、欠測は機体側が棄権して申告する
            print(f"!! weather_{kind} の取得に失敗: {type(e).__name__} {e} → 既存CSVで続行")
    sim.load_weather()
    df = sim.load()

    # 入力の契約検査（2026-08-23・issue #19の副産物）。各リーグに散らさず採点の入口で1回だけ
    try:
        import contracts
        spot_raw = pd.read_csv(HERE / "data" / f"spot_{datetime.now(JST).year}.csv")
        spot_raw.columns = [c.strip() for c in spot_raw.columns]
        issues = contracts.run_all(spot_raw, sim.WEATHER_A, sim.WEATHER_F, HERE / "picks")
        for msg in issues:
            print(f"!! 契約違反: {msg}")
        CONTRACT_ISSUES.extend(issues)
    except ImportError:
        print("契約検査: panderaが無いためスキップ")
    except Exception as e:
        print(f"契約検査に失敗: {type(e).__name__} {e}")

    ledger = build_ledger(df)
    reports = HERE / "reports"  # gitで追跡する（コミット履歴＝改竄不能なフォワード記録）
    reports.mkdir(exist_ok=True)
    anatomy_latest = ""
    if not ledger.empty:
        ledger.to_csv(reports / "forward_ledger.csv")
        plot_ledger(ledger, reports / "forward_pnl.png")
        anatomy_latest = write_anatomy(df, ledger, reports / "daily_anatomy.md")
    target = next_delivery_day()
    picks, meta = make_picks(df, target)
    md = report(ledger, picks, meta, target, anatomy_latest)
    (reports / "forward_report.md").write_text(md, encoding="utf-8")
    print(md)
