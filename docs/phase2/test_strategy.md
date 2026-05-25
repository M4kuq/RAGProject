# Phase2 PR-20 Test Strategy

## Unit tests

- Python enum values が expected baseline と一致すること。
- Migration の CHECK values と Python enum values が一致すること。
- Trace DTO が JSON serializable であること。
- Trace DTO が raw prompt、raw chunk text、full context、PII、secret 系 key を拒否すること。
- Score breakdown が raw text fields を持たないこと。

## DB / migration tests

- Alembic head が `0003_phase2_strategy_trace` であること。
- `retrieval_runs.strategy_type` の default が `dense` であること。
- invalid `strategy_type` が拒否されること。
- nullable trace JSON columns が許容されること。
- `retrieval_run_items.retrieval_source` と `score_breakdown_json` が保存できること。
- invalid `retrieval_source` が拒否されること。
- ORM columns と migration intent が一致していること。

## Seed tests

- Phase2 retrieval strategy settings が挿入されること。
- seed を複数回実行しても重複しないこと。
- 既存の `rag.default_strategy` がある場合、seed が破壊的に上書きしないこと。

## Regression tests

- `/rag/search` は default dense として成功し、run/item に safe strategy metadata を保存すること。
- `/rag/ask` は default dense として成功し、citation が `retrieval_run_items` 由来であること。
- `payload_snapshot` と `score_breakdown_json` に raw chunk text を含めないこと。

## 手動またはCIで確認すること

- `ruff format --check`
- `ruff check`
- `mypy`
- backend pytest
- 可能なら空DBに対する Alembic upgrade / downgrade / seed smoke
