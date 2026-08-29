# lab — prediction leagues that score themselves

**I run 10 prediction leagues against real-world data. Every prediction is committed
to git *before* the outcome exists, so I cannot move the goalposts afterwards.
The losses stay in the ledger.**

📊 **[Live dashboard](https://lab.hsumiyoshi.com/)** — updated daily by CI

Each league has three things: a **dumb baseline** it must beat, an **oracle**
(the best possible result in hindsight) to measure against, and a **scoring date
fixed in advance**. Right now my weather-driven strategies are ahead of the
clockwork baseline on the electricity league — but they lost three days straight
in mid-August, and that record is still there.

**Why git?** A commit timestamp is a notary that costs nothing. Day-ahead
electricity prices are published at 10:00 JST; my picks are committed at 07:00 JST.
Anyone can check the history and see the order of events.

| | |
|---|---|
| Leagues running | 10 (electricity, vegetables, earthquakes, weather, curtailment, disasters, books, real estate, satellite, AI) |
| Electricity league | day 14 of forward operation, 12 committed pick files |
| Code | this repo (public). Private strategies submit picks via Actions — the participant flow, rehearsed on myself |

---

# lab — 予測を出してから採点される実験場

**現実のデータに対して10のリーグを回しています。予測は結果が出る前にgitへコミットするので、
後から動かせません。負けた記録も台帳に残したままです。**

📊 **[ダッシュボード](https://lab.hsumiyoshi.com/)** — CIが毎日更新

各リーグには3つが必ずあります: 越えるべき**脳死ベンチ**、後から見た最善である**oracle**、
そして**事前に決めた採点日**。いま電力リーグでは天気を読む機体が時計どおりの脳死ベンチを
上回っていますが、8月中旬には3日連続で負けており、その記録もそのまま残っています。

**なぜgitか**: コミット時刻はタダで手に入る公証です。翌日渡しの電力価格は10:00 JSTに
公表され、こちらのpicksは07:00 JSTにコミットされます。**順序は誰でも履歴で確認できます。**

---

以下は運用のための内部文書です。

## リポジトリの方針

物理世界×データの実験リポジトリ。実験ごとにディレクトリを切る。

- 目的・評価方法・観察記録は本社リポジトリ（`company/docs/experiments.md`）が持つ。ここはコードとデータだけ
- 実験は大半が死ぬ前提。死んだらディレクトリごと残して触らない（アーカイブは年に一度まとめて）
- データファイル（`data/`）と生成物（`output/`）はコミットしない。例外: CIが更新する `reports/` と、遡って再取得できない `archive/`
- **データ資産の棚卸しと保全方針**（2026-08-13策定 → 2026-08-22 棚卸し実施）:

| データ | 量 | 再取得 | 観測窓 | 独自性の源泉 | 保全 | 格付 |
|---|---|---|---|---|---|---|
| 提出済みpicks（8機体） | 21ファイル | **不可** | あり | 自分が出した予測そのもの。提出時刻がgit履歴で公証される | コミット | **A** |
| フォワード台帳・解剖 | 52KB | **不可**（再計算はできるが提出の事実は再現不能） | あり | 同上 | コミット | **A** |
| 地震アーカイブ | 48KB | **不可**（APIは直近約2,000件のみ） | あり | 継続収集した者だけが持つ | コミット | **A** |
| 競艇の最終オッズ | 17,251レース | 可（公式が過去日も表示） | 薄い | 収集の手間のみ | 別リポにgzipコミット | **B** |
| JEPXスポット価格 | 6MB | 可（年度CSV） | なし | なし | gitignore | C |
| 気象（実測・予報アーカイブ） | 0.5MB | 可（Open-Meteo） | なし | なし | gitignore | C |
| 卸売市場の日次単価 | 0.5MB | 可（ベジ探） | なし | なし | gitignore | C |
| 不動産取引（東京34.6万＋熊本3.3万） | **508MB** | 可（要APIキー・数時間） | なし | 整形と照合の手間 | gitignore | C |
| 衛星NDVI（嬬恋） | 8KB | 可（AWS公開COG） | 弱い | **継続した時系列は自分だけ** | コミット | B |

- **評価軸は3つ**: ①再取得可能性（今日ゼロから取り直せるか）②観測窓（その時に測った者しか持てないか）③独自性の源泉（手間か・時間か・関係性か）
- **棚卸しで分かったこと**: 原材料（価格・気象・オッズ・取引）は**全部誰でも取れる**。本当に他者が持てないのは**自分が出した予測と、その採点の履歴**だけ。つまり資産は素材ではなく**素材に下した判断の記録**にある
- **保全方針**: 格付Aはgit本体にコミット（現在の総量は約1MBで余裕）。Bは軽ければコミット・重ければgzip。Cは保全しない（再取得前提・gitignore）。**Git LFSは原則不使用**（無料枠の上限が運用リスクになるため。50MB超が必要になれば gzip → GitHub Releases → 外部ストレージの順に格上げ）
- **既知のリスク**: 格付Aは単一リポジトリにしか存在しない（GitHubが唯一の保全先）。年1回、Aのみのアーカイブを別媒体へ退避することを検討

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
