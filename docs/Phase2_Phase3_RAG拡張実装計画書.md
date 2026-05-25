# Phase2 / Phase3 RAG拡張実装計画書

## 1. 本書の位置づけ

本書は、Phase1で構築したCore RAGを前提に、Phase2とPhase3で追加するRAG拡張の実装方針を整理する。

既存ドキュメントでは、Agentic RAG、Graph-RAG、OCR、OAuth、AWS deployなどをまとめて「Phase2以降」として扱っている箇所がある。本書では実装順序を明確にするため、以下のように切り分ける。

| Phase | 中心テーマ | 主な対象 |
|---|---|---|
| Phase2 | Advanced Retrieval / Agentic Control | Hybrid Retrieval、Agentic-RAG、LLM Router、Query Planner、Context Sufficiency Check、Citation Validation、Debug UI、評価強化、CI評価、Observability |
| Phase3 | Graph / Multimodal / Production Expansion | Graph-RAG、Graph-aware Router、OCR、画像アップロード、マルチモーダルRAG、AWS deploy、S3、OIDC / OAuth、外部LLM、A/B評価 |

この切り方により、Phase2で「RAG検索制御AgentとしてのAgentic-RAGを深く作る」、Phase3で「Graph-RAGとマルチモーダルを加えて高度化する」という説明に統一する。

## 2. Phase2 想定作業まとめ

### 2.1 Phase2の位置づけ

Phase2は、既存のCore RAGを「LLMが検索戦略を自動選択し、評価・デバッグ・改善できるRAG」に拡張するフェーズとする。

Phase1で実装したdense retrieval、rerank、citation、confidence、evaluation、MCP、Web UIを土台に、検索戦略の拡張、LLMによる検索制御、検索過程のtrace保存、strategy別評価を追加する。

### 2.2 Phase2 実装内容まとめ

| 領域 | 実装内容 |
|---|---|
| Hybrid Retrieval | dense検索に加え、BM25 / sparse検索を追加し、RRFなどで統合する |
| Agentic-RAG | LLMが入力内容を分析し、検索戦略を自動選択する |
| Query Analyzer | intent分類、曖昧性検出、keyword-heavy判定、version-specific判定を行う |
| Query Planner | query rewrite、sub-query生成、metadata filter候補生成を行う |
| Strategy Router | dense / hybrid / multi-query / metadata-filtered / version-aware retrievalを選択する |
| Agentic Retrieval Loop | 検索結果が不十分な場合、制限付きで追加検索またはfallbackを行う |
| Context Sufficiency Check | 検索結果が回答に十分かを判定する |
| Citation Validation | 最終回答のcitationがretrieval_run_items由来であることを検証する |
| Retrieval Trace | query plan、strategy decision、score breakdown、latency breakdownを保存する |
| Debug UI | admin向けに検索戦略、score、router判断理由を表示する |
| Evaluation | dense / hybrid / agentic_routerを同一datasetで比較する |
| CI評価 | GitHub Actionsでretrieval evaluation smoke testを実行する |
| Observability | LangSmith等へtraceを送れるadapterを追加する |
| 拡張取り込み | Excel / PowerPoint / HTML / XML / URL取り込みを追加する |
| 文書管理強化 | 差分表示、文書詳細、評価画面、citation文書内遷移を強化する |

Phase2では、Excel / PowerPoint / HTML / XML / URLを対応形式として想定する。既存設計にPhase2以降候補として残っているDOCXについても、必要に応じて同じ取り込み拡張枠で扱う。parent-child chunkはPhase2で強化する。

評価面では、CI/CD起動評価、定期評価、デプロイ後評価、production traceサンプリング評価、online evaluation、alerting、失敗例のdataset昇格、LangSmith連携、SentenceTransformersによる比較実験をPhase2拡張予定とする。

### 2.3 Phase2 作業内容まとめ

#### 2.3.1 Backend

- `RetrievalStrategy` enumを追加する。
- `DenseRetrievalStrategy` / `SparseRetrievalStrategy` / `HybridRetrievalStrategy` を実装する。
- `rag/fusion.py` を追加し、RRF / weighted fusionを実装する。
- `QueryAnalyzer` を追加する。
- `QueryPlanner` を追加する。
- `StrategyRouter` を追加する。
- `AgenticRetrievalExecutor` を追加する。
- `ContextSufficiencyChecker` を追加する。
- `CitationValidator` を追加する。
- `/rag/search` にstrategy指定を追加する。
- `/rag/ask` にagentic router経由の自動検索戦略選択を追加する。
- router失敗時は必ずdense retrievalへfallbackする。
- raw prompt / raw chunk text / PIIをログに出さない。

#### 2.3.2 DB / Migration

`retrieval_runs` に以下を追加する。

- `strategy_type`
- `query_plan_json`
- `strategy_decision_json`
- `latency_breakdown_json`
- `retrieval_settings_json`

`retrieval_run_items` に以下を追加する。

- `retrieval_source`
- `score_breakdown_json`

`system_settings` に以下を追加する。

- `rag.default_strategy`
- `rag.hybrid.enabled`
- `rag.hybrid.fusion_method`
- `rag.router.enabled`
- `rag.router.max_retrieval_calls`
- `rag.router.fallback_strategy`

evaluation用にstrategy別metric保存を拡張する。将来のGraph-RAG用にstrategy enumを拡張可能にしておく。

既存DDLでは、`retrieval_runs` が検索実行ヘッダ、`retrieval_run_items` がRDB final check後の候補・採用chunkを保持する。`payload_snapshot` には表示用情報を保存しつつ、raw chunk textは保存しない方針とする。

また、citationsは `retrieval_run_items` に含まれるchunkからのみ作成可能な構造なので、Phase2のCitation Validationはこの制約と整合する。

#### 2.3.3 Frontend / Admin UI

- Retrieval Debug画面を強化する。
- strategy選択UIを追加する。
  - `dense`
  - `sparse`
  - `hybrid`
  - `multi_query_dense`
  - `multi_query_hybrid`
  - `agentic_router`
- router判断理由を表示する。
- rewritten queryを表示する。
- sub queriesを表示する。
- dense / sparse / fused / rerank scoreを表示する。
- selected contextを表示する。
- fallback有無を表示する。
- latency breakdownを表示する。
- 評価ダッシュボードにstrategy別比較を追加する。
- denseでは失敗し、hybrid / agentic_routerでは成功したケースを表示する。

#### 2.3.4 Evaluation

- 評価datasetを整備する。
- strategy別に以下を計測する。
  - recall@k
  - MRR
  - citation coverage
  - groundedness
  - faithfulness
  - no_context rate
  - p95 latency
  - strategy selection accuracy
- dense / hybrid / agentic_routerを同一条件で比較する。
- 失敗例をdatasetへ昇格する。
- CIで軽量retrieval smoke testを実行する。
- LangSmith等のtraceとevaluation resultを紐付ける。

#### 2.3.5 Observability / Security

- router decision traceを保存する。
- score breakdownを保存する。
- latency breakdownを保存する。
- retrieval / rerank / generationのspanを記録する。
- raw chunk textをtraceへ送らない。
- prompt全文をtraceへ送らない。
- RAG context内の命令をsystem instructionとして扱わない。
- RAG応答から管理操作を直接実行しない。

### 2.4 Phase2で実装すべきAgentic-RAGの範囲

Phase2では、Agentic-RAGを外部操作Agentではなく、RAG内部の検索制御Agentとして実装する。

```text
Agentic-RAG Phase2 Scope
├── Query Analyzer
│   ├── intent classification
│   ├── ambiguity detection
│   ├── keyword-heavy detection
│   └── version-specific detection
│
├── Query Planner
│   ├── query rewrite
│   ├── sub-query generation
│   ├── metadata filter proposal
│   └── candidate strategy selection
│
├── Strategy Router
│   ├── dense
│   ├── hybrid
│   ├── multi_query_dense
│   ├── multi_query_hybrid
│   ├── metadata_filtered
│   ├── version_aware
│   └── fallback_dense
│
├── Agentic Retrieval Executor
│   ├── retrieval budget control
│   ├── context sufficiency check
│   ├── fallback retrieval
│   ├── result merge / dedupe
│   └── rerank
│
├── Validation
│   ├── citation validation
│   ├── no_context detection
│   ├── groundedness check
│   └── old-version source warning
│
└── Trace / Evaluation
    ├── query_plan_json
    ├── strategy_decision_json
    ├── score_breakdown_json
    ├── latency_breakdown_json
    └── strategy comparison metrics
```

### 2.5 Phase2ではやらないこと

| 対象外 | 理由 |
|---|---|
| Graph-RAG | Phase3で実装する |
| OCR-aware Agent | OCRがPhase3対象のため |
| multimodal query planning | 画像・OCR処理がPhase3対象のため |
| 外部システム操作Agent | 要件上、自律Agentによる外部システム操作は対象外 |
| 文書の自動更新 / 自動削除 | admin承認・監査設計と衝突するため |
| 管理画面操作の自動実行 | prompt injectionリスクが高いため |
| 無制限のself-reflection loop | latencyと不安定性が増えるため |
| multi-agent architecture | Phase2の目的に対して過剰なため |

### 2.6 Phase2 完了条件

| 項目 | 完了条件 |
|---|---|
| Hybrid Retrieval | dense + sparse + fusionで検索できる |
| Agentic-RAG | LLMが入力に応じて検索戦略を自動選択できる |
| Query Planning | rewrite / sub-query / metadata filter候補を生成できる |
| Retrieval Loop | 不十分な検索結果に対して、最大1〜2回の追加検索またはfallbackができる |
| Citation Validation | 最終回答のcitationがretrieval_run_items由来であることを検証できる |
| Debug UI | adminがstrategy、score、router判断理由を確認できる |
| Evaluation | dense / hybrid / agentic_routerを数値比較できる |
| CI | retrieval smoke evaluationがCIで動く |
| Observability | query plan、strategy decision、score、latencyをtraceできる |
| Safety | router失敗時はdenseへfallbackし、raw chunk text / prompt / PIIをログに出さない |
| Scope | Graph-RAG、OCR、マルチモーダルはPhase3に残す |

### 2.7 Phase2を一言でまとめると

Phase2では、Hybrid RetrievalとAgentic-RAGを実装し、LLMが入力に応じて検索戦略を自動選択できるようにする。あわせて、検索結果・判断理由・score・latency・評価指標を可視化し、改善実験できるRAG基盤にする。

## 3. Phase3 想定作業まとめ

### 3.1 Phase3の位置づけ

Phase3は、Phase2で完成させたAdvanced Retrieval基盤に、Graph-RAG、OCR、画像・マルチモーダル、OIDC / OAuth、AWS deployを追加する運用拡張フェーズとする。

既存設計でも、Phase3以降はAWS配置、S3切替、外部API切替、OIDC / OAuth、OCR / マルチモーダル、評価オンライン化、CI/CD連携強化へ拡張する想定である。OCRはPhase3で実装し、PaddleOCRを採用する方針と整合する。

### 3.2 Phase3 実装内容まとめ

| 領域 | 実装内容 |
|---|---|
| Graph-RAG | entity / relation抽出、graph index、graph retrievalを実装する |
| Graph-aware Router | Phase2のStrategy Routerにgraph retrievalを追加する |
| Graph + Vector Hybrid | graph retrievalとvector / hybrid retrievalを組み合わせる |
| OCR | PaddleOCRで画像・スキャンPDFからtextを抽出する |
| 画像アップロード | 画像単体アップロードを追加する |
| マルチモーダルRAG | 画像理解、OCR region metadata、source locatorを扱う |
| AWS deploy | AWS上にbackend / frontend / worker / DB / storageを展開する |
| S3 Storage | file storageをlocalからS3へ切替可能にする |
| OIDC / OAuth | local authに加えて外部IdP認証を追加する |
| 外部LLM Provider | local LLMから外部APIへ切替可能にする |
| 高度な評価 | Graph-RAG評価、OCR評価、A/B評価、online evalを強化する |
| 高度な管理機能 | graph debug、OCR debug、運用監視、権限・監査強化を追加する |

### 3.3 Phase3 作業内容まとめ

#### 3.3.1 Backend

- `GraphIndexService` を追加する。
- `EntityExtractionService` を追加する。
- `RelationExtractionService` を追加する。
- `GraphRetrievalStrategy` を追加する。
- `GraphHybridRetrievalStrategy` を追加する。
- `StrategyRouter` にgraph strategyを追加する。
- `OCRService` を追加する。
- `ImageIngestService` を追加する。
- `ExternalLLMProviderAdapter` を追加する。
- `AuthProviderAdapter` を追加する。
- `S3FileStorageAdapter` を追加する。
- `GraphCitationBuilder` を追加する。
- `GraphPathValidator` を追加する。
- graph / OCR / multimodal用のerror handlingを追加する。

#### 3.3.2 DB / Migration

Graph-RAG用テーブルを追加する。

- `graph_entities`
- `graph_relations`
- `graph_entity_mentions`
- `graph_index_runs`
- `graph_retrieval_paths`

OCR用テーブルを追加する。

- `ocr_results`
- `ocr_pages`
- `ocr_regions`

画像・マルチモーダル用metadataを追加する。

- `modality`
- `image_storage_key`
- `source_locator`
- `region_metadata_json`

`retrieval_runs` / `retrieval_run_items` にgraph retrieval用traceを追加する。

- `graph_query_json`
- `graph_path_json`
- `graph_score_breakdown_json`

`document_chunks.modality` を `text` / `ocr_text` / `image_caption` / `table` などへ拡張する。OIDC用にuser identity連携テーブルを追加する。S3移行用にstorage backend識別子を追加する。

既存設計では、OCR text、OCR confidence、OCR region metadata、original image / page参照、OCR source locatorに備える方針が明記されている。

#### 3.3.3 Ingest / Worker

- `ocr_ingest` jobを追加する。
- `graph_index_build` jobを追加する。
- `graph_reindex` jobを追加する。
- `image_ingest` jobを追加する。
- document ingest後にentity / relation抽出を実行する。
- OCR後にocr_text chunkを生成する。
- image upload後にcaption / OCR / metadataを生成する。
- graph indexの差分更新を実装する。
- version更新時にgraph差分を再構築する。
- S3 storageへのupload / downloadをworkerから利用可能にする。

#### 3.3.4 Frontend / Admin UI

- Graph Debug画面を追加する。
- entity / relation一覧を表示する。
- graph pathを表示する。
- graph retrieval結果と元chunk citationを表示する。
- OCR結果確認画面を追加する。
- OCR region / confidenceを表示する。
- 画像アップロードUIを追加する。
- 画像 / OCR citation panelを追加する。
- OIDC login導線を追加する。
- AWS deploy環境向け設定画面を追加する。
- A/B評価ダッシュボードを追加する。
- online evaluation / alerting画面を強化する。

#### 3.3.5 Evaluation

Graph-RAG評価を追加する。

- entity extraction accuracy
- relation extraction accuracy
- graph path relevance
- multi-hop QA accuracy
- graph citation coverage

OCR評価を追加する。

- OCR text accuracy
- OCR confidence calibration
- region alignment

Multimodal RAG評価を追加する。

- image-grounded answer accuracy
- multimodal citation correctness

A/B評価を追加する。

- dense vs hybrid vs agentic_router vs graph_rag
- graph_only vs graph + vector

online evaluationを本格化する。production trace samplingを強化し、alertingを評価結果と連携する。

#### 3.3.6 Infrastructure / Security

- AWS deployを実装する。
- local file storageからS3へ切替可能にする。
- DB / Qdrant / worker構成を本番寄りに整理する。
- Secrets Manager等でsecret管理する。
- OIDC / OAuthを導入する。
- external LLM providerを設定可能にする。
- PII / raw document / OCR textの外部送信ポリシーを強化する。
- audit logを運用監査向けに拡張する。
- rate limit / access controlを強化する。

基本設計上も、Phase3以降の拡張性としてexternal LLM、S3、OCR、online eval、OIDC、LangSmith、SentenceTransformersが挙げられている。

### 3.4 Phase3で実装するGraph-RAGの範囲

```text
Graph-RAG Phase3 Scope
├── Graph Ingest
│   ├── entity extraction
│   ├── relation extraction
│   ├── entity mention linking
│   └── document version aware graph update
│
├── Graph Store / Index
│   ├── graph_entities
│   ├── graph_relations
│   ├── graph_entity_mentions
│   └── graph_index_runs
│
├── Graph Retrieval
│   ├── entity lookup
│   ├── relation traversal
│   ├── multi-hop retrieval
│   ├── graph neighborhood expansion
│   └── graph + vector hybrid retrieval
│
├── Router Integration
│   ├── graph
│   ├── graph_hybrid
│   ├── multi_hop_graph
│   └── fallback_hybrid / fallback_dense
│
├── Citation
│   ├── graph node -> source chunk mapping
│   ├── graph edge -> source chunk mapping
│   ├── graph path citation
│   └── citation validation
│
└── Evaluation / Debug
    ├── graph path debug
    ├── entity / relation debug
    ├── multi-hop QA evaluation
    └── graph citation coverage
```

### 3.5 Phase3 完了条件

| 項目 | 完了条件 |
|---|---|
| Graph-RAG | entity / relationを抽出し、graph retrievalで回答根拠に使える |
| Graph Router | LLM Routerがgraph / hybrid / denseを選択できる |
| Graph Citation | graph node / edgeから元chunkへcitationできる |
| OCR | 画像・スキャンPDFからOCR textを抽出し、chunk化できる |
| Multimodal | 画像・OCR由来の根拠をcitation panelで表示できる |
| AWS | AWS環境にbackend / frontend / worker / storageを展開できる |
| S3 | local storageからS3へ切替できる |
| OIDC | 外部IdPでログインできる |
| External LLM | local LLMと外部LLM providerを切替できる |
| Evaluation | Graph-RAG / OCR / multimodal / A/B評価ができる |
| Security | 外部API送信、OCR text、画像、graph情報のPII保護と監査ができる |

### 3.6 Phase3を一言でまとめると

Phase3では、Phase2のAgentic-RAG / Hybrid基盤にGraph-RAGを追加し、OCR・画像・マルチモーダル・AWS・OIDCまで拡張する。RAGを検索改善フェーズから、本番運用・高度検索・マルチモーダル対応フェーズへ進める。

## 4. 最終整理

| Phase | 中心テーマ | 主な実装 |
|---|---|---|
| Phase2 | Advanced Retrieval / Agentic Control | Hybrid Retrieval、Agentic-RAG、LLM Router、Query Planner、Context Sufficiency Check、Citation Validation、Debug UI、評価強化、CI評価、Observability |
| Phase3 | Graph / Multimodal / Production Expansion | Graph-RAG、Graph-aware Router、OCR、画像アップロード、マルチモーダルRAG、AWS deploy、S3、OIDC / OAuth、外部LLM、A/B評価 |

Phase2では、Core RAGを検索改善・評価改善の対象として拡張する。Phase3では、Graph-RAG、OCR、画像、クラウド、本番運用の要素を追加し、RAG基盤をより高度な運用フェーズへ進める。

## 5. 現状との整合メモ

- Phase1はDocker Composeローカル検証環境を基準とし、AWS deploy、Terraform、remote MCP、OAuth、OCR、Graph-RAG、Agentic RAGを実装対象外にしている。
- 既存READMEの「Phase2以降」という表現は大枠の将来範囲を示すものとして扱う。本書では詳細な実装順序として、Hybrid Retrieval / Agentic-RAG / 評価・Debug強化をPhase2、Graph-RAG / OCR / Multimodal / AWS / OIDCをPhase3へ整理する。
- 既存設計のOCR方針は「Phase3でPaddleOCR」となっているため、本書でもOCR-aware Agent、OCR ingest、画像・スキャンPDF対応はPhase3に置く。
- 既存設計のS3、OIDC / OAuth、外部LLM provider、AWS deployはPhase3以降の運用拡張として扱う。
- citationsは `retrieval_run_items` 由来のchunkに限定する既存方針を維持し、Phase2ではCitation Validationとして強化する。
- raw prompt、raw chunk text、PIIをログやtraceへ出さない方針はPhase2 / Phase3でも維持する。
