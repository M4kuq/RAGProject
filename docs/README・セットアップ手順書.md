# AI/LLMエンジニア向けポートフォリオ提出用 RAGシステム

## README・セットアップ手順書 v1.0（Batch 8 完全版）

---

## 1. 文書概要

### 1.1 目的

本書は、GitHub 提出時に第三者が本プロジェクトの概要・設計意図・起動手順・デモ手順を再現できる README 構成とセットアップ手順を定義する。

### 1.2 対象範囲

- project overview
- architecture
- tech stack
- features
- design highlights
- local setup
- `.env.example` 方針
- Docker Compose 方針
- migration 実行
- seed 投入
- app/worker/frontend 起動
- test 実行
- demo 実行
- docs 一覧
- portfolio appeal points
- known limitations
- Phase2 roadmap

### 1.3 非対象

- 既存 `README.md` の直接上書き
- Docker/K8s 実装
- 実コード変更

### 1.4 前提文書

- 要件定義書 v1.1
- 基本設計書 v1.2
- DDL草案 v1.8
- API設計書 v1.9 最終版
- RAG パイプライン詳細設計書 v1.4 最終版
- Worker / Job 詳細設計書 v1.4 最終版
- テスト設計書 v1.0
- Alembic migration設計書 v1.0
- OpenAPI 3.1 契約設計書 v1.1
- Seed・デモシナリオ設計書 v1.0
- 実装分解書 v1.1

---

## 2. README 推奨章構成

1. プロジェクト概要
2. 想定ユースケース（ポートフォリオ用途）
3. 主な機能
4. アーキテクチャ
5. 技術スタック
6. セットアップ
7. migration / seed
8. 起動手順（backend/worker/frontend）
9. テスト実行
10. デモ手順（5分版）
11. 設計書一覧
12. 既知の制約
13. Phase2 roadmap

---

## 3. README 本文ドラフト（転記用）

## 3.1 Project Overview

本プロジェクトは、AI/LLM エンジニア向けポートフォリオ提出用の RAG システムです。  
文書取り込み、検索、回答生成、citation 表示、評価、監査、非同期ジョブ制御を Phase1 で成立させることを目的とします。

## 3.2 Architecture

- Frontend: React + TypeScript + TanStack Query
- Backend: FastAPI + SQLAlchemy + Pydantic
- Worker: DB queue polling
- DB: PostgreSQL
- Vector DB: Qdrant
- LLM: ローカル実行前提（Phase1）

## 3.3 Features

- RAG ask/search
- citation grounding
- document versioning + approve/archive
- worker retry/reclaim
- evaluation run
- admin / viewer RBAC

---

## 4. `.env.example` 方針

- `.env.example` にはキー名のみ記載し、実値は含めない
- ローカル実値は `.env` に設定（git 管理外）
- secret / token / password は README 本文に記載しない

推奨カテゴリ:
- DB接続
- セッション/CSRF
- LLM/embedding
- Qdrant
- ファイル保存先
- ログレベル

---

## 5. ローカルセットアップ手順（設計）

## 5.1 前提

- Docker / Docker Compose
- Python 3.11 系
- Node.js LTS

## 5.2 手順

1. リポジトリ取得
2. `.env.example` から `.env` 作成
3. 依存サービス起動（postgres/qdrant など）
4. `alembic upgrade head`
5. seed 投入
6. backend 起動
7. worker 起動
8. frontend 起動

## 5.3 migration 実行ポリシー

- 初回起動前に migration head 適用を必須とする
- ローカル再構築時のみ `downgrade base -> upgrade head` を許容

---

## 6. テスト実行手順（設計）

実行順（推奨）:
1. lint / format / type
2. unit / repository / service
3. migration / DB constraint
4. API / integration
5. worker / RAG
6. frontend component/hook
7. E2E（必要時）

---

## 7. デモ手順（README掲載版）

Seed・デモシナリオ設計書の 5分台本に従う。

最短導線:
1. ログイン（viewer/admin 差分）
2. upload -> ingest job -> approve
3. /rag/ask 正常回答 + citation
4. 文書 v2 承認後の回答差分
5. archive 後の retrieval 対象外
6. no_context（422）
7. evaluation / debug の要点

---

## 8. 設計ハイライト（アピールポイント）

- DDL/API/RAG/Worker を分離しつつ整合
- citation 検証可能な marker 形式
- DB queue + lease ownership + reclaim 設計
- migration 順序（循環FK/複合FK/partial unique）を明示
- テスト設計を実装前に固定

---

## 9. 設計書一覧（README掲載）

- 要件定義書
- 基本設計書
- ER図 / テーブル設計書
- DDL草案
- API設計書
- 状態遷移仕様書
- 機能仕様補完書
- 画面仕様書
- フロントエンド詳細設計書
- バックエンド詳細設計書
- RAG パイプライン詳細設計書
- Worker / Job 詳細設計書
- テスト設計書
- Alembic migration設計書
- OpenAPI 3.1 契約設計書
- 実装分解書
- プロンプト設計書
- Seed・デモシナリオ設計書
- README・セットアップ手順書（本書）

---

## 10. Known Limitations（Phase1）

- 外部依存を含む長時間負荷試験は未実施
- OpenAPI YAML 本体は設計後続タスク
- E2E は最小導線を優先

---

## 11. Phase2 Roadmap（概要）

- 運用監視強化（metrics/alerts/runbook 拡張）
- evaluation 高度化
- prompt / retrieval 改善ループ
- 外部LLM切替オプション整備

---

## 12. 完了条件

- 第三者が README から環境構築とデモ再現ができる章構成になっている
- migration/seed/start/test/demo の順序が明確
- セキュリティ方針（secret 非記載）が明記されている
- ポートフォリオ訴求ポイントが明示されている

---

## 13. 停止条件（要人間判断）

- 既存 README と章構成方針が衝突する
- セットアップ手順が実際の実装構成と矛盾する
- デモ導線が面接要件と衝突する

---

## 14. 未対応事項・残リスク

- 実 README 反映時に実コマンド名の調整が必要
- CI 実行コマンドは実装完了後に最終確定

---

## 15. 次に作成すべき設計書

- セキュリティチェックリスト（Batch 9 完全版）

