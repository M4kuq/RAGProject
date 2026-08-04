# Server-owned model selection

## 目的

インターネット公開・認証ユーザー向けの`POST /api/v1/rag/ask`で、利用者が任意の
provider/model IDを指定して高価な外部modelを強制する経路を閉じる。将来のQwen
FlashからPlusへの昇格は、request文字列やretrieved/tool contentではなく、server側の
routerとbudget gateだけが決定する。

Jira: RAG-64

## 監査結果

- PR #134 / RAG-61は、外部model送信前のPII masking、provider allowlist、既定denyを
  実装済みである。最新headのBackend CIとCompose Smokeは成功しており、local worktreeの
  12 changed file blobもremoteと一致した。
- PR #136 / RAG-63は、認証済みRAG入口へper-user/shared rate、concurrency、daily work-unit
  gateを追加した。
- ただし最新mainでは、`RagAskRequest.model_key`のproviderが既知なら、model ID自体は
  任意文字列を受け付けていた。外部providerのcredentialが設定済みの場合、認証ユーザーが
  高価なmodelを直接選べる。

PII egress、入口budget、model selectionは別の防御層である。RAG-64はPR #134/#136へ
stackせず、最新mainから独立してmodel selectionだけを制御する。

## Policy

### 安全な既定

- `RAG_USER_MODEL_SELECTION_ENABLED=false`
- `RAG_USER_SELECTABLE_MODEL_KEYS=[]`
- requestの`model_key`省略、またはconfigured defaultと同じ正規化済みkeyだけを許可する。
- local既定の`lmstudio:qwen3.5-9b`は従来どおり利用できる。
- 追加modelは検索、message/run保存、generation factory、external transportより前に403で
  拒否する。

拒否レスポンスのreason codeは`model_selection_denied`。監査logは次だけを持つ。

- `reason_code`
- 正規化済みの既知provider
- `user_model_selection_disabled`または`model_not_allowlisted`

model ID、質問、retrieved context、user ID、credentialはlogへ保存しない。

### 明示opt-in

追加modelをuser-selectableにする場合だけ、次の両方を設定する。

```text
RAG_USER_MODEL_SELECTION_ENABLED=true
RAG_USER_SELECTABLE_MODEL_KEYS=openai:gpt-entry,gemini:gemini-entry
```

allowlistはexact keyであり、providerだけの許可やwildcardはない。`google:`は`gemini:`へ
正規化し、重複は除去する。allowlistがあるのにenableがfalse、不正provider、空model、
128文字超過はstartup時にfail closedする。

frontendの一般external model一覧は`VITE_ENABLE_EXTERNAL_MODEL_SELECTION=true`の場合だけ
表示する。NVIDIAは既存の専用`VITE_ENABLE_NVIDIA_API=true`を使う。frontend flagは表示制御で
しかなく、backend exact allowlistを越える権限を持たない。

## FlashからPlusへの将来cascade

1. Flashをconfigured default、またはuser-selectable allowlistへ置く。
2. Plusは`RAG_USER_SELECTABLE_MODEL_KEYS`へ置かない。
3. server内部routerだけが、検索十分性、Flash失敗reason、task class、残budgetを使ってPlusを
   選ぶ。
4. request prompt、retrieved chunk、tool outputがmodel keyやtierを返しても無視する。
5. Plus昇格にはRAG-63のrate/daily gateに加え、将来provider token/金額と昇格回数を課金する。

この構成では、認証ユーザーが`qwen:*plus*`相当を直接送っても、server allowlistに無ければ
Plus transportへ到達しない。

## API動作

| request | 結果 |
|---|---|
| `model_key`なし | configured defaultを使用 |
| configured defaultと同じkey | 許可 |
| opt-in済みexact allowlist key | 許可 |
| known providerの非allowlist model | 403 `model_selection_denied` |
| unknown provider / malformed key | 422 `unsupported_model` |

denyはchat session所有権とduplicate確認後、query planning、vector search、message/run作成より
前に行う。既存duplicateのreplayは新たなmodel callを行わないため、再課金しない。

## Rollback

従来の追加model選択を一時的に復元する場合は、backendをenableし、必要なexact keyだけを
allowlistへ列挙する。frontendも対応する明示flagを有効にする。任意model/wildcardへ戻す設定は
用意しない。

完全停止は`RAG_USER_MODEL_SELECTION_ENABLED=false`と空allowlist。DB/Qdrant/volumeのresetは
不要で、schema migrationもない。

## 検証ログ

- focused backend policy: `5 passed`
- same-provider non-allowlist escalation: `1 passed`
- backend full: `975 passed, 19 skipped, 3 known warnings`
- backend Ruff format/lint: pass
- backend full mypy: `Success: no issues found in 272 source files`
- focused frontend: `2 files, 31 tests passed`
- frontend full: `16 files, 105 tests passed`
- frontend TypeScript/Vite production build: pass、144 modules

## 残余risk

- model IDのallowlistはrequest強制を防ぐが、provider側の実単価変更は検知しない。
- PR #136がmainへ入るまでは、model policyとshared daily work-unit gateは別branchである。
- Qwen provider/cascade実装時にはPlusのtoken/金額、昇格回数、circuit breakerを別途追加する。
- model selection denialの監査は現状per-event loggerである。高頻度のaggregate bucketとalertは
  PR #136のshared admission telemetry統合時に追加する。
- operatorが高価なmodelを明示allowlistへ入れれば利用者は選択できる。設定変更権限とreviewを
  productionで制限する必要がある。
