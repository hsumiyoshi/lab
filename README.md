# lab — 没頭実験場

物理世界×データの実験リポジトリ。実験ごとにディレクトリを切る。

- 目的・評価方法・観察記録は本社リポジトリ（`company/docs/experiments.md`）が持つ。ここはコードとデータだけ
- 実験は大半が死ぬ前提。死んだらディレクトリごと残して触らない（アーカイブは年に一度まとめて）
- データファイル（`data/`）と生成物（`output/`）はコミットしない。例外: CIが更新する `reports/` と、遡って再取得できない `archive/`
- **データ保管の3分類**（2026-08-13策定）:
  - **A 再取得可能**（出典側がアーカイブ: JEPX年度CSV・ベジ探・不動産API・Sentinel-2・Open-Meteo）→ gitignoreされたローカルキャッシュのみ。喪失してもスクリプト再実行で復元できる
  - **B 再取得不可**（P2P地震APIは直近約2,000件しか遡れない）→ `archive/` にコミット（現在48KB・年1〜2MB増でgitの範囲内）
  - **C 改訂されるスナップショット**（不動産の追補、picksの提出時刻）→ 判定・提出の時点の値をコミットして公証（predictions.mdの判定手順、picks/）
  - 単体50MBを超える保管が必要になったら gzip圧縮コミット → GitHub Releases（1ファイル2GBまで・リポジトリ容量外）→ 外部オブジェクトストレージ（Cloudflare R2等）の順に格上げする。現時点でコミット済み総量は約1MBで問題なし

## リーグ台帳（コックピット）

全リーグ共通ルール: 予測はデータ確定前にコミット（履歴=公証）／oracleと脳死ベンチを必ず置く／
採点は自動・ステートレス／判定日は事前設定。詳細は本社 `experiments.md` のリーグ規約。

| リーグ | 予測対象 | 周期 | 見る場所 | 次のイベント |
|---|---|---|---|---|
| 電力 | 翌日48コマの価格形状→充放電 | 毎日 | [forward_report.md](exp01_jepx/reports/forward_report.md) | 毎日13:30 JSTに自動更新 |
| 青果 | 週内の単価推移→出荷タイミング | 週次 | [veg_forward.md](exp02_vegetable/reports/veg_forward.md) | 初ラウンド8/17週、採点は水曜14:00 |
| 地震 | 熊本余震の週次件数（大森則） | 週次 | [quake_forward.md](exp05_quake/reports/quake_forward.md) | 初ラウンド8/17週、採点は月曜14:00 |
| 不動産 | M7.1後の熊本の件数・価格（事前登録） | 四半期 | [predictions.md](exp04_realestate/predictions.md) | 判定2027-01-31（Q3公表後） |
| 衛星 | 春のNDVI進度→本格出荷週 | 年次 | [predictions.md](exp07_satellite/predictions.md) | 予測コミット期限2027-06-01 |

## 実験一覧

| ディレクトリ | 素材 | 状態 |
|---|---|---|
| `exp01_jepx/` | JEPXスポット価格 | **フォワード運用中**（2026-08-14〜、戦略凍結2026-08-12） |
| `exp02_vegetable/` | 東京都中央卸売市場の青果日報（ベジ探） | フォワード運用開始（2026-08-17週〜） |
| `exp04_realestate/` | 不動産取引価格×ハザード（不動産情報ライブラリ） | 事前登録予測を公証済み |
| `exp05_quake/` | 地震履歴（P2P地震情報API） | フォワード運用開始（2026-08-17週〜） |
| `exp07_satellite/` | Sentinel-2 NDVI（嬬恋キャベツ地帯） | 予測ルール公証済み、2027年春が初戦 |

## セットアップ

```bash
pip install pandas matplotlib numpy rasterio shapely
```
