#!/usr/bin/env python3
"""フォワード運用ランナー。

- 戦略凍結日: 2026-08-12（この日までの477日はインサンプル。以降は変更禁止）
- フォワード開始: 2026-08-14 受渡分（凍結時点でオークション未実施の最初の日）
- 方式: ステートレス台帳。毎回、凍結ルールでFORWARD_START以降を全再計算する。
  状態ファイルなし・欠測に強い・公開成績は再実行で誰でも検証可能。

毎日の使い方:
    python3 forward.py          # データ更新→台帳更新→明日のpicks生成→レポート出力

出場機体: ベースライン = clock(固定時間帯) / weekshape(曜日形状) をここで計算。
         参加機体（haruki_tenki / haruki_hybrid 等）は「予測提出型」——
         picks/ に提出されたJSONだけで台帳入りする（コードは参加者の手元、
         2026-08-13に本リポジトリから分離。凍結済みロジックは不変）
oracle は事後の上限参照値としてのみ記載。
"""

import json
import time
import urllib.request
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd


def _get_json(url: str, tries: int = 4):
    for attempt in range(tries):
        try:
            with urllib.request.urlopen(url, timeout=60) as res:
                return json.load(res)
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
FREEZE_NOTE = "戦略凍結: 2026-08-12 / フォワード開始: 2026-08-14受渡分"

STRAT_NAMES = ["clock", "weekshape", "haruki_tenki", "haruki_hybrid", "oracle"]


# ---------------- ハイブリッド（フォワード専用機体） ----------------

def _dev_series(upto_day: pd.Timestamp) -> pd.Series:
    """日ごとの「日射予報の平年乖離」。平年=直近28日の実測平均（因果的）"""
    wa, wf = sim.WEATHER_A, sim.WEATHER_F
    norm = wa["rad"].rolling(28).mean().shift(1)
    dev = (wf["rad"] - norm).abs().dropna()
    return dev[dev.index < upto_day]


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
# 予測提出型: ベースライン（clock/weekshape）はここで計算し、参加機体（haruki等）は
# picks/ に提出されたJSONだけで台帳入りする。提出が無い日は無得点（欠場）。

PICKS_DIR = HERE / "picks"


def load_picks(day: pd.Timestamp) -> dict:
    f = PICKS_DIR / f"{day:%Y-%m-%d}.json"
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
        row = {"date": day, "signal": signal,
               "clock": sim.run_day(today, *sim.strat_clock())}
        pred = sim.pred_weekshape(hist, today, day)
        row["weekshape"] = sim.run_day(today, *sim.choose_split(pred)) if pred is not None else 0.0
        for name, (c, d) in load_picks(day).items():
            row[name] = sim.run_day(today, c, d)
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
COLORS = {"weekshape": "#2a78d6", "haruki_tenki": "#eb6834",
          "haruki_hybrid": "#1baf7a", "clock": "#eda100"}
INK, INK2, MUTED = "#0b0b0b", "#52514e", "#898781"
GRID, BASE, SURFACE = "#e1e0d9", "#c3c2b7", "#fcfcfb"


def plot_ledger(ledger: pd.DataFrame, dest: Path) -> None:
    import matplotlib.pyplot as plt
    names = [n for n in COLORS if n in ledger.columns]
    cum = ledger[names + ["oracle"]].fillna(0.0).cumsum()  # 欠場日は0（参加せず稼がず）
    fig, ax = plt.subplots(figsize=(10, 5))
    fig.patch.set_facecolor(SURFACE)
    ax.set_facecolor(SURFACE)
    ax.plot(cum.index, cum["oracle"], color=MUTED, lw=1.4, ls="--", label="oracle (bound)")
    for name in names:
        ax.plot(cum.index, cum[name], color=COLORS[name], lw=2.0, label=name)
    # 終端の直接ラベルは累積線では衝突しやすいため置かない。
    # 低コントラスト色の緩和はレポート内の成績テーブル（table view）が担う
    # 信号「強」の日をhybrid線上に打点
    strong = ledger.index[ledger["signal"] == "強"]
    if len(strong) and "haruki_hybrid" in cum.columns:
        ax.plot(strong, cum.loc[strong, "haruki_hybrid"], "o",
                color=COLORS["haruki_hybrid"], ms=5, mec=SURFACE, mew=1.2)
    ax.set_title("Virtual battery forward test — cumulative P&L (JPY, 10kWh)",
                 color=INK, fontsize=11)
    ax.grid(color=GRID, lw=0.7)
    for s in ("top", "right"):
        ax.spines[s].set_visible(False)
    for s in ("left", "bottom"):
        ax.spines[s].set_color(BASE)
    ax.tick_params(colors=INK2, labelsize=8.5)
    ax.legend(loc="upper left", fontsize=8.5, frameon=False, labelcolor=INK2)
    fig.tight_layout()
    fig.savefig(dest, dpi=130, facecolor=SURFACE)
    plt.close(fig)


# ---------------- レポート ----------------

def report(ledger: pd.DataFrame, picks, meta, target) -> str:
    lines = [f"# JEPX仮想蓄電池 フォワード運用レポート",
             f"", f"{FREEZE_NOTE} / 生成: {datetime.now(JST):%Y-%m-%d %H:%M} JST", ""]
    if ledger.empty:
        lines += ["## 累計成績", "", "（フォワード対象日の価格はまだ公表されていない。初日の答え合わせを待て）", ""]
    else:
        days = len(ledger)
        names = [c for c in ledger.columns if c not in ("signal", "oracle")] + ["oracle"]
        lines += ["![cumulative P&L](forward_pnl.png)", "",
                  f"## 累計成績（{days}日）", "", "| 機体 | 累計損益 | 円/日 | 対oracle |", "|---|---|---|---|"]
        totals = ledger[names].sum()
        for n in names:
            lines.append(f"| {n} | {totals[n]:,.0f}円 | {totals[n]/days:.1f} | {totals[n]/totals['oracle']*100:.1f}% |")
        last = ledger.iloc[-1]
        lines += ["", f"## 直近日（{ledger.index[-1].date()}、信号: {last['signal']}）", "",
                  "| 機体 | 損益 |", "|---|---|"]
        for n in names:
            if pd.notna(last[n]):
                lines.append(f"| {n} | {last[n]:+,.0f}円 |")
        lines.append("")
    lines += [f"## 明日のpicks（受渡日 {target.date()}、信号: {meta['signal']}）", ""]
    if meta["dev"] is not None:
        lines.append(f"日射予報の平年乖離 {meta['dev']:.0f} W/m2（閾値 {meta['thr']:.0f}）")
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
        # 6時間以内に更新済みならスキップ（レート制限への節度・picksワークフローとの二重取得回避）
        if dest.exists() and (datetime.now().timestamp() - dest.stat().st_mtime) < 6 * 3600:
            print(f"weather_{kind}: fresh, skip")
            continue
        weather.build(kind).to_csv(dest)
    sim.load_weather()
    df = sim.load()

    ledger = build_ledger(df)
    reports = HERE / "reports"  # gitで追跡する（コミット履歴＝改竄不能なフォワード記録）
    reports.mkdir(exist_ok=True)
    if not ledger.empty:
        ledger.to_csv(reports / "forward_ledger.csv")
        plot_ledger(ledger, reports / "forward_pnl.png")
    target = next_delivery_day()
    picks, meta = make_picks(df, target)
    md = report(ledger, picks, meta, target)
    (reports / "forward_report.md").write_text(md, encoding="utf-8")
    print(md)
