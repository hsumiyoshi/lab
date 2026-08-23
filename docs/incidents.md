# 障害ログ（自動記録）

CIが落ちるたびにこの表へ1行追記される（`if: failure()` ステップ）。**手で消さない**。

**なぜ台帳に残すか**: 2026-08-19の青果リーグの失敗を「CI未発火」と誤診し、ローカル実行で結果だけ埋めた結果、真因（matplotlib未導入）が4日間隠れていた。**手で作った成果物は故障を覆い隠す**。落ちた事実そのものを資産として残す。

| 発生(UTC) | ワークフロー | 実行ログ | 状態 |
|---|---|---|---|
| 2026-08-19T05:34Z | exp02 veg forward weekly | [run](https://github.com/hsumiyoshi/lab/actions/runs/32219960903) | 解決（matplotlib未導入→requirements.txtへ集約 2026-08-23） |
| 2026-08-21T22:20Z | submit picks（strategies側） | — | 解決（Open-Meteo 429→気象CSVのgit追跡＋長時間バックオフ） |
| 2026-08-23T09:41Z | exp10 global disaster league | [run](https://github.com/hsumiyoshi/lab/actions/runs/32631678717) | 解決（新リーグのdata/がgitignore＋NASAへIPv6到達不能） |
| 2026-08-23T22:18Z | exp08 curtailment league | [run](https://github.com/hsumiyoshi/lab/actions/runs/32670176818) | 未対応 |
