#!/usr/bin/env python3
"""入力データの契約検査（2026-08-23・データ可観測性の5本柱のうち「量」と「スキーマ」）。

なぜ自作でなくOSSか、なぜpanderaか:
- **Elementary**: dbt前提（warehouseのモデルを検査する）。うちはdbtもDWHも無いので適用不可
- **Great Expectations**: 設定資産（context/datasource/suite/checkpoint）が重く、CSV数枚には過剰
- **Soda**: YAMLで書けて軽いが、スキャン設定と接続定義が要る
- **pandera**: DataFrameに直接かける宣言的スキーマ。設定ファイルもサーバも不要で、
  CIで落とすだけならこれで足りる → 採用

検査するのは「静かに壊れる」経路だけ。外部データの形が変わった時に、
気づかないまま採点が続くのが最悪（issue #18のサイレント劣化と同系統）。
"""

import json

import pandas as pd
import pandera.pandas as pa
from pandera.pandas import Check, Column, DataFrameSchema

# JEPXスポット: 1日48コマ・価格は0以上500円未満（2021年の史上最高値251円の2倍を上限に置く）
SPOT = DataFrameSchema({
    "年月日": Column(str),
    "時刻コード": Column(int, Check.in_range(1, 48)),
    "システムプライス(円/kWh)": Column(float, Check.in_range(0.0, 500.0), nullable=True),
}, strict=False, coerce=True)

# 気象: 日射0〜1500 W/m2、気温-30〜45℃、日付の重複なし
WEATHER = DataFrameSchema({
    "rad": Column(float, Check.in_range(0.0, 1500.0)),
    "tmean": Column(float, Check.in_range(-30.0, 45.0)),
}, strict=False, coerce=True)


def check_spot(df: pd.DataFrame) -> list:
    """スキーマ＋量（各日48コマ）。違反はメッセージのリストで返す（例外にしない）。"""
    out = []
    try:
        SPOT.validate(df, lazy=True)
    except pa.errors.SchemaErrors as e:
        out.append(f"spot スキーマ違反 {len(e.failure_cases)}件: "
                   f"{e.failure_cases[['column', 'check']].drop_duplicates().to_dict('records')[:3]}")
    counts = df.groupby("年月日").size()
    bad = counts[counts != 48]
    if len(bad):
        out.append(f"spot 量の異常（48コマでない日 {len(bad)}件）: {list(bad.index[:3])}")
    return out


def check_weather(df: pd.DataFrame, name: str) -> list:
    out = []
    try:
        WEATHER.validate(df, lazy=True)
    except pa.errors.SchemaErrors as e:
        out.append(f"{name} スキーマ違反: "
                   f"{e.failure_cases[['column', 'check']].drop_duplicates().to_dict('records')[:3]}")
    if df.index.duplicated().any():
        out.append(f"{name} 日付が重複: {list(df.index[df.index.duplicated()][:3])}")
    return out


def check_picks(path) -> list:
    """提出picksの不変条件: 各機体4コマずつ・1〜48・重複なし・充電は全て放電より前。

    最後の条件は choose_split の設計（時間順序を守る分割探索）の帰結なので、
    破れていたら提出側のロジックが壊れている。
    """
    out = []
    j = json.loads(path.read_text())
    for name, v in j.items():
        if name.startswith("_"):
            continue
        c, d = v.get("charge", []), v.get("discharge", [])
        if len(set(c)) != 4 or len(set(d)) != 4:
            out.append(f"{path.name}/{name}: コマ数が4でない（充{len(c)}・放{len(d)}）")
            continue
        if not all(1 <= k <= 48 for k in c + d):
            out.append(f"{path.name}/{name}: コマ番号が範囲外")
        if set(c) & set(d):
            out.append(f"{path.name}/{name}: 充放電が重複")
        if max(c) >= min(d):
            out.append(f"{path.name}/{name}: 時間順序違反（充電{max(c)} >= 放電{min(d)}）")
    return out


def run_all(spot: pd.DataFrame, wa, wf, picks_dir) -> list:
    """採点の入口で1回だけ呼ぶ。各リーグに散らさない（本数が増えても保守は1箇所）。"""
    issues = check_spot(spot)
    if wa is not None:
        issues += check_weather(wa, "weather_actual")
    if wf is not None:
        issues += check_weather(wf, "weather_forecast")
    for p in sorted(picks_dir.glob("2026-*.json")):
        issues += check_picks(p)
    return issues
