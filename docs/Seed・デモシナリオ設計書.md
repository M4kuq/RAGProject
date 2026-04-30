# AI/LLMエンジニア向けポートフォリオ提出用 RAGシステム

## Seed・デモシナリオ設計書 v1.0（Batch 7 完全版）

---

## 1. 文書概要

### 1.1 目的

本書は、ポートフォリオ提出および面接デモで RAG システムの価値を短時間で示すため、
seed データ、デモ手順、期待回答、期待 citation、失敗系デモ方針を定義する。

### 1.2 対象範囲

- seed user / role
- sample documents
- old version / new version
- document upload / approve / archive シナリオ
- 質問例 / 期待回答 / 期待 citation
- no_context デモ
- job retry / reclaim デモ
- evaluation デモ
- admin debug デモ
- viewer/admin 権限差分デモ
- README 掲載順

### 1.3 非対象

- 実 seed SQL / migration 実装
- 実データ投入スクリプト実装
- 本番業務データ設計

### 1.4 前提文書

- 要件定義書 v1.1
- API設計書 v1.9 最終版
- DDL草案 v1.8
- 画面仕様書 v1.3 最終版
- フロントエンド詳細設計書 v1.4 最終版
- バックエンド詳細設計書 v1.4 最終版
- RAG パイプライン詳細設計書 v1.4 最終版
- Worker / Job 詳細設計書 v1.4 最終版
- テスト設計書 v1.0
- プロンプト設計書 v1.1

---

## 2. seed データ方針

### 2.1 基本原則

- PII を含む実在個人データは使用しない
- ドメイン中立の架空データを使用する
- 回答根拠が説明しやすい短文書を優先
- old/new version 差分が明確な文書を含める

### 2.2 ユーザー / ロール

- `admin_demo@example.local`（role: admin）
- `viewer_demo@example.local`（role: viewer）

備考:
- パスワードは設計書に平文記載しない
- 実投入値は `.env` またはローカル初期化手順で管理

### 2.3 system settings（最小）

- RAG top_k
- confidence label threshold
- worker polling interval

---

## 3. サンプル文書セット

## 3.1 文書セット構成

### Doc-A（仕様ガイド）

- v1: 機能一覧 5項目
- v2: 機能一覧 6項目（1項目追加、1項目文言変更）

目的:
- 版差分で citation が切り替わることを見せる

### Doc-B（運用ルール）

- archive 条件、権限差分、監査ログ方針

目的:
- admin/viewer 差分質問に使う

### Doc-C（FAQ）

- no_context を起こしやすい範囲外質問を定義

目的:
- `no_context_found` デモに使う

## 3.2 文書メタ要件

- 各文書は title / source_label / section / page情報が追跡可能
- chunk 分割後も出典説明ができる段落構造を維持

---

## 4. デモシナリオ（本番）

## 4.1 シナリオ0: ログインと権限差分

1. viewer でログイン
2. admin 専用画面にアクセス不可（403/guard）を確認
3. admin でログインし管理画面アクセス可を確認

## 4.2 シナリオ1: 文書アップロード〜承認

1. admin で Doc-A v1 を upload
2. ingest job 監視（queued->running->succeeded）
3. approve 実行
4. active version 反映を確認

## 4.3 シナリオ2: RAG質問（正常）

質問例:
- 「Doc-A の機能一覧を要約してください」

期待:
- 回答に citation marker 相当根拠が表示される
- citation panel で source_label/page が追跡できる

## 4.4 シナリオ3: 版差分

1. Doc-A v2 を upload + approve
2. 同じ質問を再実行
3. v1 と v2 で citation 対象が変化することを確認

## 4.5 シナリオ4: archive 後の retrieval 対象外

1. logical document を archive
2. archive 前と同じ質問を実行
3. archive 文書が retrieval 候補に入らないことを確認

## 4.6 シナリオ5: no_context

質問例:
- 「文書に存在しない外部情報を質問」

期待:
- `no_context_found`（422）
- 回答本文や citation を生成しない

## 4.7 シナリオ6: job retry / reclaim（管理デモ）

1. ingest 失敗ケースを再実行（retry）
2. reclaim 後 cleanup 方針が守られることを debug で確認

## 4.8 シナリオ7: evaluation

1. evaluation_run 作成
2. run/item/result が保存される
3. faithfulness / groundedness / citation coverage を表示

## 4.9 シナリオ8: admin debug view

表示項目:
- retrieval_run
- retrieval_run_items
- citations
- job trace

制約:
- viewer には表示しない

---

## 5. 質問例と期待結果

## 5.1 正常質問テンプレート

- Q1: Doc-A の機能一覧は？
- Q2: Doc-B の archive 条件は？
- Q3: Doc-A v1 と v2 の差分は？

期待結果:
- 各回答に 1件以上の valid citation
- source_label / page 情報が UI 上で辿れる

## 5.2 no_context 質問テンプレート

- Q4: seed 文書にない固有名詞を含む質問

期待結果:
- 422 no_context_found
- assistant 回答未保存

---

## 6. 期待 citation 定義

- citation は retrieval_run_items 由来のみ
- `document_version_id` を直接表示しない
- archive 後文書は citation 対象外
- old_version_flag の挙動は API設計書に準拠

---

## 7. citation_build_failed の扱い

- 通常デモでは発生させない
- 障害デモを実施する場合は「内部失敗例」として別セッションで実施
- 面接デモ本線には含めない

---

## 8. README 掲載順（推奨）

1. 概要（何ができるか）
2. アーキテクチャ
3. デモ前準備
4. デモシナリオ（0〜8）
5. 期待結果（回答・citation・評価）
6. 既知の制約
7. Phase2 roadmap

---

## 9. 人間向けデモ台本（5分想定）

- 0:00-0:45 ログインと権限差分
- 0:45-2:00 upload -> job -> approve
- 2:00-3:15 RAG正常回答 + citation
- 3:15-4:00 版差分 + archive 対象外
- 4:00-4:30 no_context
- 4:30-5:00 evaluation/debug 要点説明

---

## 10. 完了条件

- ポートフォリオデモで必要な seed / シナリオ / 期待結果が定義済み
- admin/viewer 差分が明確
- no_context / archive / retry/reclaim / evaluation をカバー
- PII 非含有方針が明記されている

---

## 11. 停止条件（要人間判断）

- 文書内容が既存 API/DDL/RAG 契約と矛盾する
- 面接デモ要件（時間・見せ方）と衝突する
- seed 方針がセキュリティ方針と衝突する

---

## 12. 未対応事項・残リスク

- 実 seed 投入コマンドは Batch 8 README 手順で確定
- デモ動画素材は実装完了後に別途作成

---

## 13. 次に作成すべき設計書

- README / セットアップ手順書（Batch 8 完全版）

