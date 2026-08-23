# データ健全性（保全の番人）

生成: 2026-08-23 22:33 JST / 登録 24件 / 合計 15.8MB

**方針**: 格付け(A/B/C)は価値の判定であり、保全の可否ではない。**全件を守る**——再取得可能でも、取り直せる保証は外部（API廃止・仕様変更・サイト改変）に依存するため。

## 🚨 異常 7件

- exp01_jepx/data/spot_2026.csv: **ファイルが無い**（市場価格。全戦略の採点根拠）
- exp01_jepx/data/spot_2025.csv: **ファイルが無い**（前年度の価格）
- exp02_vegetable/data/veg_31700.csv: **ファイルが無い**（きゅうり等の日次単価）
- exp02_vegetable/data/veg_33400.csv: **ファイルが無い**（トマト等）
- exp02_vegetable/data/veg_34100.csv: **ファイルが無い**（キャベツ等）
- exp02_vegetable/data/veg_34400.csv: **ファイルが無い**（レタス等）
- exp07_satellite/data/tsumagoi_ndvi.csv: **ファイルが無い**（嬬恋のNDVI）

| データ | 格 | 形式 | サイズ | 状態 | 出所 |
|---|---|---|---|---|---|
| `exp01_jepx/reports/forward_ledger.csv` | A | append | 2KB | 変化なし | 自前の採点 |
| `exp05_quake/archive/quakes.csv` | A | append | 46KB | 変化なし | P2P地震情報API |
| `exp03_weather/data/ledger.json` | A | append | 0KB | 変化なし | 自前の採点 |
| `exp08_curtail/data/outlook_history.json` | A | append | 0KB | 変化なし | 九電でんき予報 |
| `exp08_curtail/data/ledger.json` | A | append | 0KB | 変化なし | 自前の採点 |
| `exp10_disaster/data/events.json` | A | append | 63KB | 変化なし | GDACS RSS |
| `exp10_disaster/data/ledger.json` | A | append | 0KB | 変化なし | 自前の採点 |
| `exp10_disaster/data/firms_daily.json` | A | append | 0KB | 変化なし | NASA FIRMS（要キー） |
| `exp11_books/data/rankings.json` | A | append | 2KB | 変化なし | トーハン週間ベストセラー |
| `exp11_books/data/ledger.json` | A | append | 0KB | 未生成（正常） | 自前の採点 |
| `exp07_satellite/data/tsumagoi_ndvi.csv` | B | append | 0KB | 消失 | Sentinel-2 AWS公開COG |
| `exp01_jepx/data/spot_2026.csv` | C | append | 0KB | 消失 | JEPX公式CSV |
| `exp01_jepx/data/spot_2025.csv` | C | append | 0KB | 消失 | JEPX公式CSV |
| `exp01_jepx/data/weather_actual.csv` | C | append | 24KB | 変化なし | Open-Meteo archive |
| `exp01_jepx/data/weather_forecast.csv` | C | append | 24KB | 変化なし | Open-Meteo historical-forecast |
| `exp02_vegetable/data/veg_31700.csv` | C | append | 0KB | 消失 | 東京都中央卸売市場（ベジ探） |
| `exp02_vegetable/data/veg_33400.csv` | C | append | 0KB | 消失 | 同上 |
| `exp02_vegetable/data/veg_34100.csv` | C | append | 0KB | 消失 | 同上 |
| `exp02_vegetable/data/veg_34400.csv` | C | append | 0KB | 消失 | 同上 |
| `exp04_realestate/data_archive/trades_tokyo.csv.gz` | C | snapshot | 5.6MB | OK | 不動産情報ライブラリAPI（要キー） |
| `exp04_realestate/data_archive/trades_kumamoto.csv.gz` | C | snapshot | 726KB | OK | 同上 |
| `exp04_realestate/data_archive/kumamoto_city_flood.csv.gz` | C | snapshot | 384KB | OK | ハザードAPI |
| `exp04_realestate/data_archive/raw_api_responses.tar.gz` | C | snapshot | 8.9MB | OK | 同上 |
| `exp04_realestate/data_archive/geocode.json.gz` | C | snapshot | 6KB | OK | ジオコーディング結果 |

注: 追記型（append）は行数が減ったら事故として扱う。スナップショット型はsha256で改変を検出する。
**再取得の手順は各エントリの `refetch` に書いてある**——外部が生きているうちに、手順そのものも資産として残す。
