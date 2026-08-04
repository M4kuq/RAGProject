# RAG-31 security integration gate

最終更新: 2026-08-04

## 目的

RAG-31のsecurity sliceを個別成功のまま扱わず、同一code stateで組み合わせたときの
設定、service、migration、security gateの非回帰を確認する。この統合branchは検証用であり、
個別Draft PRまたは`main`を自動mergeしない。

利用前提は次のとおりである。

- インターネット公開、RAGは認証ユーザーのみ。
- 質問と文書はPIIを含み得る。
- 外部LLM送信はlocal-firstで避け、必要時も送信前maskとfail-closed policyを要求する。
- 将来のFlashからPlusへの昇格はserver-owned policyによる完全自動処理とする。
- 通常精度gateとsecurity gateを混ぜない。

## 固定した入力

起点はGitHub `main`の`96e5fc82ad2c253d000633ff9e9431e88998f74a`である。
GitHub MCPでremote headを取得し、local source branchの変更blobと照合した。

| PR | Jira / scope | Remote head | Local source head | Checked blobs | Mismatch |
|---|---|---|---|---:|---:|
| #133 | RAG-31 Phase 1 injection gate | `6e88b6ebb7750d769c1fd8e19c9751c09376d088` | `3e9555600f22e9618bc4902c0a720343830a9557` | 13 | 0 |
| #134 | RAG-61 PII egress | `076aceabaff80871ca3f61aa9aace301b7072d5b` | `5ea44703d91a69a0465153f95ba0f5896ff57a41` | 12 | 0 |
| #135 | RAG-62 Phase 2 injection/cascade | `a72fac96fb897dbe9aaad0c0241d83981b2a7982` | `df829e930d0c99707c2701825a05cb4c2f2c5474` | 16 | 0 |
| #136 | RAG-63 public abuse controls | `6e0b5032b0fe75f405e16a5331d1e7b44487df0b` | `964ff80cf55ef76f33e41d3311644a45affe1db4` | 15 | 0 |
| #137 | RAG-64 server-owned model policy | `c9d548fc499cf216d1538f9aec05a65081bfba09` | `c9ddcf0d1d5b8bc6642b478c349bcaf3be7ea848` | 8 | 0 |
| #138 | RAG-65 local endpoint trust | `dbcb139392930bf9819d6a214dc1335b932994c3` | `a3219790d7592b5a95990a70f756e2085dcd634f` | 3 | 0 |
| #139 | RAG-66 corpus provenance/quarantine | `4c7db1cee7dbbfcda150403a9f997879d635bf3c` | `87687dc0c515ea8d6ea5d7d2dc4fcc7d61cfe7d3` | 40 | 0 |
| #140 | RAG-67 Agentic/MCP tool policy | `db39cce9daff9236b845cab2fa5a4dd98ed37c81` | `6ce06e10b256aa38ed31579674c9631e4fcceac0` | 10 | 0 |

#135は#133をbaseにしている。統合時は#135のlocal branchを一度だけmergeし、#133を
二重適用していない。比較対象はPRごとの差分であり、合計117 blobを確認した。
PII worktreeにあった未追跡Docker client設定は統合対象外とした。

## 統合履歴

| Order | Integration commit | Input | Result |
|---:|---|---|---|
| 1 | `523183c` | #133 / #135 | 自動merge |
| 2 | `8929ca0` | #134 | 自動merge |
| 3 | `d744bf2` | #136 | configと脅威モデルを両方保持して解消 |
| 4 | `9a712ae` | #137 | egress/model-key validatorを両方保持 |
| 5 | `1c60355` | #138 | model/egress/local-host validatorとURL正規化を保持 |
| 6 | `ef508e1` | #139 | corpus本体は自動merge、migrationをmerge revision化 |
| 7 | `c8ba696` | #140 | PII egress guardとtool policy importを両方保持 |
| 8 | `411eed3` | RAG-68 composite gate | integrated metric fixtureを追加 |

競合解消では、片方のpolicyを削除して見かけ上mergeする方法を採用していない。
`docs/security/RAGProject-threat-model.md`は全体モデルを正とし、#136のadmission-control
focused modelを補足節として保持した。

### Migration graph

#136と#139は、どちらも`0022_eval_reliability`から分岐する独立`0023`を持つ。
元migrationは変更せず、no-opの`0024_security_merge`を追加した。

```text
0022_eval_reliability
  |-- 0023_rag_abuse_controls --|
  |-- 0023_corpus_trust --------|-- 0024_security_merge (head)
```

このmerge revisionはschema/dataを重複変更しない。実PostgreSQLの隔離`tmpfs` DBで、
fresh upgrade、`0022`への両branch downgrade、再upgradeを確認した。

## Composite gate contract

`backend/tests/test_rag31_security_integration_gate.py`は既存fixtureだけを再利用し、rawの
質問、chunk、answer、PII、canary、secretを結果へ出力しない。次の集計値をすべて0件で
hard gateする。

| Metric | Meaning |
|---|---|
| `prompt_control_bypass_count` | Phase 2 injection/control bypass |
| `prompt_clean_utility_failure_count` | clean caseのpolicy起因failure |
| `prompt_detector_gap_count` | expected detector coverage gap |
| `pii_leakage_count` | mask後textに残ったsynthetic fixture value |
| `egress_policy_failure_count` | block/mask/allow契約の不一致 |
| `egress_clean_utility_failure_count` | clean egress textの不要な変更 |
| `budget_bypass_count` | untrusted originまたはexhausted budgetからのPlus許可 |
| `budget_clean_utility_failure_count` | eligible trusted policyの誤拒否 |
| `unauthorized_tool_allowance_count` | unknown/write toolの許可 |
| `tool_clean_utility_failure_count` | allowlisted read toolの誤拒否 |
| `quarantine_visibility_bypass_count` | pending/quarantined/unknown sourceの可視化 |
| `corpus_clean_visibility_failure_count` | approved/legacy-safe sourceの誤非表示 |
| `default_policy_drift_count` | egress、abuse、model、endpoint、tool defaultのdrift |

認証、CSRF、chat owner、document ownerの非回帰は、同じfull backend suiteに含まれる既存
API/session/owner testsを正とする。Composite gateは既存testを置換しない。

## 検証結果

### Security and backend

- Composite gate: `1 passed`、13 metricすべて0件。
- Focused integrated suite: `197 passed, 13 skipped`。
- Backend full: `1079 passed, 19 skipped`、既知warning 3件。
- Ruff: all checks passed。
- Ruff format: 296 files already formatted。
- mypy: 296 source files、issue 0件。

### Migration and runtime

- `alembic heads`: `0024_security_merge (head)`の1件。
- fresh PostgreSQL upgrade: success。
- `0024 -> 0022`: 両`0023` downgrade success。
- `0022 -> 0024`: re-upgrade success。
- 検証DBはhost port非公開、`tmpfs`、一時containerで実行後に停止・自動削除。
- `docker compose -f docker-compose.ci.yml config --quiet`: success。

### Frontend

- Vitest: 16 files、105 tests passed。
- TypeScript/Vite production build: success。
- React Router v7 future warningは既知warningとして保持。

## 検出した失敗と判断

成功結果だけを残さず、次を記録する。

1. GitHub file metadataを並列取得するとSHAが取得できなかった。逐次取得へ切り替え、
   117 blobをremote/localで再比較して不一致0を確認した。
2. `apply_patch`が8分を超えたmigration patchを停止した。4変更中3件が完全適用、1件だけ
   未適用であることを確認し、残る1 hunkのみ再適用した。marker 0件とdiff checkを確認した。
3. 初回focused suiteは、endpoint-policy testが先にproduction SQLite abuse validatorへ到達して
   1件失敗した。test fixtureへ有効なPostgreSQL URLを指定し、検証対象をendpoint policyへ
   分離した。production abuse gateの順序や強度は変更していない。
4. Composite gate初回mypyはcascade origin tupleを一般`str`と推論して1件失敗した。
   公開type aliasで注釈し、runtime testとmypyを再実行した。
5. bundled PythonにAlembic CLIがなかったため、backend test imageへ切り替えた。
6. 一時Docker network作成はaddress pool枯渇で開始前に失敗した。volume/containerは未作成。
   既定bridgeの非公開内部IPへ切り替え、同じmigration往復を成功させた。

## Promotion decision

local統合gateはpassである。ただし`main`への昇格条件は、最終remote headでのGitHub Backend CIと
Compose Smoke成功、Draft PRのmergeable確認、各個別PRのreview判断である。個別PRを飛ばして
integration branchだけを自動mergeしてはならない。

## Recovery and stop boundaries

- Code起点: GitHub `main` `96e5fc82ad2c253d000633ff9e9431e88998f74a`。
- 個別復帰点: 上表の各remote head。integration作業で既存branch/PRを変更しない。
- 最小復帰: integration Draft PRをmergeせずcloseする。`main`、個別PR、runtime dataは不変。
- Migration復帰: `0024_security_merge`はno-op。隔離検証では`0022_eval_reliability`までの
  downgradeと再upgradeが成功している。本番DBへ自動適用しない。
- Policyごとの緊急復帰は各runbookを参照する。復帰switchがPII egress、write tool、
  endpoint trust、認証/owner boundaryをfail-openにしてはならない。
- Gold v2、通常精度profile、root checkout、既存PostgreSQL/Qdrant/Neo4j、既存Docker volume、
  LM Studio loaded modelは変更しない。

関連文書:

- `docs/security/prompt_injection_security_gate.md`
- `docs/security/prompt_injection_security_phase2.md`
- `docs/phase4/external_model_pii_egress_gate.md`
- `docs/security/rag_public_abuse_controls.md`
- `docs/phase4/server_owned_model_selection.md`
- `docs/security/local_model_endpoint_policy.md`
- `docs/corpus_trust_quarantine_runbook.md`
- `docs/security/agentic_mcp_tool_execution_policy.md`
