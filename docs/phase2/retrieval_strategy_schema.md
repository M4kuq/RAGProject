# Retrieval Strategy Schema

## RetrievalStrategy

| value | PR-20 behavior | future owner |
|---|---|---|
| `dense` | default。Phase1互換動作 | PR-20 |
| `sparse` | 保存可能なenumのみ | PR-23 |
| `hybrid` | 保存可能なenumのみ | PR-24 |
| `multi_query_dense` | 保存可能なenumのみ | PR-27以降 |
| `multi_query_hybrid` | 保存可能なenumのみ | PR-27以降 |
| `metadata_filtered` | 保存可能なenumのみ | PR-27以降 |
| `version_aware` | 保存可能なenumのみ | PR-27以降 |
| `agentic_router` | 保存可能なenumのみ | PR-28以降 |
| `fallback_dense` | 保存可能なenumのみ | PR-28以降 |

## RetrievalSource

`retrieval_run_items.retrieval_source` は item 単位の取得元を表す。PR-20では `dense` を保存できる土台を作り、`sparse`, `hybrid`, `rerank`, `fallback_dense`, `metadata_filter` は後続PRのために予約する。

## Trace DTO

PR-20 の DTO は以下を固定する。

- `QueryPlanTrace`
- `StrategyDecisionTrace`
- `LatencyBreakdown`
- `RetrievalSettingsSnapshot`
- `ScoreBreakdown`
- `StrategyEvaluationMetricSpec`

DTO は Pydantic v2 model とし、JSON serializable であることをテストする。拡張用 extra fields は許容するが、key 名に raw prompt、chunk text、full context、PII、secret、token、credential を示す語を含む場合は拒否する。

## Default dense snapshot

Phase1互換実行では、`retrieval_settings_json` に以下のような安全な snapshot を保存する。

```json
{
  "strategy_type": "dense",
  "default_strategy": "dense",
  "top_k": 5,
  "rerank_top_n": 5,
  "modality": "text",
  "logical_document_filter_count": 0,
  "hybrid_enabled": false,
  "router_enabled": false,
  "trace_enabled": true,
  "fusion_method": "rrf"
}
```

query文字列、prompt本文、chunk本文、context本文は含めない。
