# データ健全性（保全の番人）

生成: 2026-08-31 02:14 JST / 登録 26件 / 合計 23.7MB

**方針**: 格付け(A/B/C)は価値の判定であり、保全の可否ではない。**全件を守る**——再取得可能でも、取り直せる保証は外部（API廃止・仕様変更・サイト改変）に依存するため。

## ✅ 異常なし

| データ | 格 | 形式 | サイズ | 状態 | 出所 |
|---|---|---|---|---|---|
| `exp01_jepx/reports/forward_ledger.csv` | A | append | 3KB | OK | 自前の採点 |
| `exp05_quake/archive/quakes.csv` | A | append | 50KB | 変化なし | P2P地震情報API |
| `exp03_weather/data/ledger.json` | A | append | 1KB | OK | 自前の採点 |
| `exp08_curtail/data/outlook_history.json` | A | append | 0KB | OK | 九電でんき予報 |
| `exp08_curtail/data/ledger.json` | A | append | 1KB | 変化なし | 自前の採点 |
| `exp10_disaster/data/events.json` | A | append | 1.4MB | OK | GDACS RSS |
| `exp10_disaster/data/ledger.json` | A | append | 1KB | 変化なし | 自前の採点 |
| `exp10_disaster/data/firms_daily.json` | A | append | 0KB | 変化なし | NASA FIRMS（要キー） |
| `exp11_books/data/rankings.json` | A | append | 4KB | 変化なし | トーハン週間ベストセラー |
| `exp11_books/data/ledger.json` | A | append | 0KB | 変化なし | 自前の採点 |
| `collector/data/kyuden_curtail.json` | A | append | 5KB | OK | 九電でんき予報（収集基盤） |
| `collector/data/tohan_books.json` | A | append | 6KB | 変化なし | トーハン週間ベストセラー（収集基盤） |
| `exp07_satellite/data/tsumagoi_ndvi.csv` | B | append | 3KB | 変化なし | Sentinel-2 AWS公開COG |
| `exp01_jepx/data/spot_2026.csv` | C | append | 1.8MB | OK | JEPX公式CSV |
| `exp01_jepx/data/spot_2025.csv` | C | append | 4.3MB | 変化なし | JEPX公式CSV |
| `exp01_jepx/data/weather_actual.csv` | C | append | 25KB | OK | Open-Meteo archive |
| `exp01_jepx/data/weather_forecast.csv` | C | append | 25KB | 変化なし | Open-Meteo historical-forecast |
| `exp02_vegetable/data/veg_31700.csv` | C | append | 93KB | 変化なし | 東京都中央卸売市場（ベジ探） |
| `exp02_vegetable/data/veg_33400.csv` | C | append | 96KB | 変化なし | 同上 |
| `exp02_vegetable/data/veg_34100.csv` | C | append | 144KB | 変化なし | 同上 |
| `exp02_vegetable/data/veg_34400.csv` | C | append | 140KB | 変化なし | 同上 |
| `exp04_realestate/data_archive/trades_tokyo.csv.gz` | C | snapshot | 5.6MB | OK | 不動産情報ライブラリAPI（要キー） |
| `exp04_realestate/data_archive/trades_kumamoto.csv.gz` | C | snapshot | 726KB | OK | 同上 |
| `exp04_realestate/data_archive/kumamoto_city_flood.csv.gz` | C | snapshot | 384KB | OK | ハザードAPI |
| `exp04_realestate/data_archive/raw_api_responses.tar.gz` | C | snapshot | 8.9MB | OK | 同上 |
| `exp04_realestate/data_archive/geocode.json.gz` | C | snapshot | 6KB | OK | ジオコーディング結果 |

注: 追記型（append）は行数が減ったら事故として扱う。スナップショット型はsha256で改変を検出する。
**再取得の手順は各エントリの `refetch` に書いてある**——外部が生きているうちに、手順そのものも資産として残す。
