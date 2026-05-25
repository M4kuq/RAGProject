# Evaluation and Observability Strategy

## 方針

Phase2 では evaluation と observability を補助機能ではなく中核機能として扱う。検索方式を追加する前に、strategy別の結果、score、latency、fallback、失敗理由を比較できる形にする。

## Evaluation metrics

strategy比較では次の metrics を扱う方針とする。

- recall@k
- MRR
- citation coverage
- groundedness
- faithfulness
- no_context rate
- p95 latency
- strategy selection accuracy

保存先は既存 `evaluation_runs` / `evaluation_run_items` / `evaluation_results` を優先する。PR-20 では `StrategyEvaluationMetricSpec` DTO で metric spec を固定し、dataset/case/run/result の関係は壊さない。

## Observability

PR-20 は DB column と DTO を用意する。実際の trace生成、Debug UI表示、LangSmith export は後続PRで実装する。

保存対象:

- strategy type
- redacted query plan metadata
- redacted strategy decision metadata
- latency breakdown
- score breakdown
- retrieval settings snapshot

保存禁止:

- raw prompt
- raw chunk text
- full context
- PII
- secret、token、credential
- 外部API raw request / raw response

## CI方針

CIでは deterministic fixture と fake adapter を基本にする。重い model download、外部API key、LangSmith secret は必須にしない。
