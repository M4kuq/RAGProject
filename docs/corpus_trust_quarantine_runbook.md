# Corpus provenance・trust・quarantine 運用／検証ログ

最終更新: 2026-08-04
Jira: `RAG-66`
branch: `feature/corpus-trust-provenance`
base: GitHub `main` `96e5fc82ad2c253d000633ff9e9431e88998f74a`

## 1. 目的と境界

取得文書にprompt injectionや改ざん済みchunkが混入した場合でも、PostgreSQLを最終的な可視性の権威として、dense、sparse、hybrid、agentic、Graph、citation、評価経路から未承認／隔離済みsourceを除外する。

この変更は汚染検知器そのものを追加しない。既存のsecurity gate／detector実装と重複させず、検知結果を永続化し、全検索経路へ安全に反映し、誤検知時に復旧できる境界を担当する。自動detector連携前でも、admin APIを安全な運用fallbackとして利用できる。

次は保存しない。

- Qdrant／Neo4j trust payloadへのchunk本文、取得URL、自由入力reason
- audit metadataへのchunk本文、PII、credential、`.env`値
- test artifactへの実ユーザー文書本文

既存DB、Qdrant collection、Docker volumeはreset／deleteしていない。migration検証は新規の一時PostgreSQLコンテナだけで実行した。

## 2. データ契約

`document_versions`へ次を追加する。

| field | 値 | 用途 |
|---|---|---|
| `source_provenance` | `legacy`, `admin_upload`, `external_url`, `evaluation_fixture` | source取得経路の監査 |
| `source_trust_level` | `trusted`, `external_untrusted` | source trust分類の監査 |
| `security_review_status` | `pending`, `approved`, `quarantined` | 検索可視性の権威 |
| `security_review_reason_code` | allowlist済みcodeまたはnull | 判断理由。自由文は禁止 |
| `security_reviewed_at` | UTC timestampまたはnull | 判断時刻 |

legacy rowはmigrationで`legacy / trusted / approved`になる。既存Qdrant／Neo4j payloadにfieldがない場合も同じ値として扱い、既存corpusを突然不可視にしない。

新規sourceの初期値は次のとおり。

| source | provenance | trust | initial review |
|---|---|---|---|
| admin upload | `admin_upload` | `trusted` | `pending` |
| external URL | `external_url` | `external_untrusted` | `pending` |
| isolated evaluation fixture | `evaluation_fixture` | `trusted` | `approved` |
| migration済み既存row | `legacy` | `trusted` | `approved` |

`source_trust_level`だけでは検索可否を決めない。検索可否は`security_review_status == approved`で決めるため、external sourceでもreview後は使用でき、trusted sourceでもquarantineできる。

## 3. 状態遷移と復旧

`POST /api/v1/documents/{logical_document_id}/versions/{document_version_id}/security-review`はadmin認可とCSRFを必須にする。

| 操作 | 前状態 | 後状態 | active | 非同期整合性 |
|---|---|---|---|---|
| 通常approve | `pending` | `approved` | true | Qdrant mirror／Graph job |
| quarantine | `approved`または`pending` | `quarantined` | false | Qdrant mirror／Graph job、cache marker更新 |
| review release | `quarantined` | `approved` | falseのまま | Qdrant mirror／Graph job |
| 通常approve | release済み`approved` | `approved` | true | Qdrant mirror／Graph job、cache marker更新 |

quarantineから通常approveへの直行は`409 document_version_quarantined`で拒否する。復旧は「security reviewでapprovedへ戻す」「通常approveで再active化する」の二段階で、review解除だけでは検索へ戻らない。

同じstatus／reasonの再送は完全に冪等で、review timestamp、audit、Qdrant job、Graph job、cache markerを更新しない。

## 4. Defense in depth

| 経路 | prefilter／伝播 | 最終防御 |
|---|---|---|
| Qdrant dense／hybrid | `pending`／`quarantined`を`must_not`、3分類fieldをindex／mirror | PostgreSQL hydrationで`ready + active + approved + active document` |
| PostgreSQL dense／sparse | SQLで`approved`を要求 | 同じqueryがsource of truth |
| Agentic／evaluation／fake pipeline | 共通retrieval／evidence queryで`approved`を要求 | DB hydration |
| Neo4j Graph | projectionへ3 field、Cypherで`approved`を要求 | PostgreSQL Graph repositoryで再filter |
| Graph citation | old-version例外でも`approved`を必須化 | validatorが`inactive_source_chunk`として除外 |
| direct citation source API | locator SQLで`approved`のみ | 非承認sourceは404 |
| retrieval cache | corpus fingerprintに`approved`条件、状態変更時marker更新 | cache hit再構築時もDBで`approved`確認 |
| Qdrant consistency sweep | DBとprovenance／trust／reviewのdriftを検知 | 不整合pointをinactiveへ安全側修復 |

Qdrant／Neo4j mirrorが遅延または失敗しても、PostgreSQL hydrationがfail closedになる。逆にQdrant payloadだけを改ざんして`approved`へ戻しても、DBがquarantinedなら回答／citationへ到達しない。

## 5. 監査とPII境界

監査eventは`document.version_security_review_updated`で、次だけを記録する。

- logical document ID、document version ID
- allowlist済みreview status／reason code
- 遷移前にactiveだったか
- actor user ID、request ID

取得URL、文書名、本文、chunk、PIIをreview audit metadataへ複製しない。external LLMへの送信もこの変更では追加していない。PII maskingと外部送信境界はgeneration provider側の独立gateとして扱う。

## 6. Rollback／recovery

### 誤隔離からの復旧

1. adminがsourceを再確認する。
2. `status=approved, reason_code=admin_review_passed`でreview releaseする。
3. responseの`is_active=false`とmirror／Graph job IDを確認する。
4. 通常approveを実行し、初めてactiveへ戻す。
5. retrieval smokeとcitation sourceを確認する。

### code／schemaの復帰

- codeの比較基準はGitHub `main` `96e5fc82ad2c253d000633ff9e9431e88998f74a`。
- `feature/local-rag-accuracy`、PR #128、既存root worktreeを変更しない。
- appを0022互換codeへ戻す場合だけ、migration `0023_corpus_trust`を`0022_eval_reliability`へdowngradeする。
- downgradeは5列を削除するが、既存document rowは保持することを一時PostgreSQLで確認済み。
- Qdrant／Neo4j fieldはadditiveなのでcollection／graphを削除しない。旧codeは追加payloadを無視できる。

## 7. 2026-08-04 検証ログ

| gate | 結果 |
|---|---|
| Git diff whitespace | clean |
| Python AST | 275 files parsed |
| security focused pytest | 20 passed |
| Qdrant index／mirror assertions | 2 passed |
| Neo4j projection／query／citation assertions | 3 passed |
| backend full pytest | 980 passed, 17 skipped, 3 existing warnings |
| Ruff lint | passed |
| Ruff format check | 275 files formatted |
| mypy | 182 source files, no issues |
| frontend test | 55 passed |
| frontend production build | passed |
| Alembic PostgreSQL roundtrip | 0022 fixture → 0023 upgrade → downgrade → re-upgrade passed |

PostgreSQL roundtripでは、0022時点でlegacy document rowを作成し、upgrade後に`legacy/trusted/approved`のbackfillと5 check constraintsを確認した。downgrade後もrowが残り、再upgradeでも同じ結果になることを確認した。一時containerは停止後`--rm`で自動削除した。

既知warningはStarlette TestClient移行、passlib `crypt` deprecation、未登録`integration` pytest markで、今回差分による新規warningではない。

## 8. 次の統合点

完全自動化では、既存detector／security gateがallowlist済みreason codeでこの状態遷移serviceを呼ぶ。検知、PII masking、外部LLM送信可否、Plus強制によるcost attack、MCP tool権限は独立評価を維持し、通常精度のGold結果へ混ぜない。

参考となる脅威分類:

- OWASP LLM01 Prompt Injection: https://genai.owasp.org/llmrisk/llm01-prompt-injection/
- OWASP LLM10 Unbounded Consumption: https://genai.owasp.org/llmrisk/llm102025-unbounded-consumption/
- NIST Adversarial Machine Learning taxonomy: https://doi.org/10.6028/NIST.AI.100-2e2025
