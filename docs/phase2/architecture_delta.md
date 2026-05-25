# Phase2 Architecture Delta

## Phase1からの差分

Phase1 は dense retrieval、rerank、citation、confidence、evaluation、MCP、Web UI を成立させた。Phase2 では検索方式を増やす前に、検索戦略の選択理由、score、latency、fallback、評価結果を保存・比較できる土台を追加する。

## PR-20の境界

PR-20 は architecture baseline であり、runtime の検索挙動は変えない。既存処理は `strategy_type = dense` として明示されるだけで、dense retrieval と rerank の流れは Phase1 のまま維持する。

## 追加する設計面

- strategy は Python enum と DB CHECK の両方で固定する。
- retrieval run は strategy と redacted trace のヘッダを持つ。
- retrieval run item は retrieval source と score breakdown の保存先を持つ。
- system_settings は後続PRの feature flag と default 値を持つ。
- evaluation metrics は既存 `evaluation_runs` / `evaluation_run_items` / `evaluation_results` を優先し、strategy比較に必要な metric spec をDTOで固定する。

## 後続PRへの依存

PR-21 は PR-20 の trace column に具体的な redacted trace を保存する。PR-22 は strategy別評価datasetとmetricsを拡張する。PR-23以降は sparse/hybrid/router/debug UI がこの schema と設定を利用する。

## Phase3に残すもの

Graph-RAG、OCR、multimodal、AWS、S3、OIDC/OAuth、production online evaluation本格運用は Phase3 の対象とする。Phase2 の strategy enum は将来拡張可能に保つが、PR-20では graph 用 table や graph strategy は追加しない。
