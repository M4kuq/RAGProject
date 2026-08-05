# Prompt injection policy production promotion (RAG-72)

## Status

**internet-facing profileの昇格判定を`block_user_quarantine_context`で確定した。** 新しいraw-free recipe fingerprintに対するQwen 3-repeatは全promotion gateを満たした。ローカル、CI、testの既定`observe_only`は変更しない。Gold v2や通常精度profileの結果とは混ぜず、本判定を公開精度の根拠には使わない。

Draft PR #142はDraft PR #141のremote head `c2ff8bf57a674e881efc4741d32eb440d87c2bfe`（tree `e4b07c463d2bd0d5ef25d035d0415261c475f09d`）を直接baseとし、clean head `2bd5cf0c28e3ccd3286e34e2416cb936df75ebac`から本結果を追加する。昇格はPRのmergeと対象deploymentへの適用後に有効になり、評価完了だけで既存productionを変更しない。

## Evaluation contract

- scope: `security_dev_promotion`（synthetic-only）
- generation cases: 30、boundary cases: 14
- coverage: direct / indirect、ja / en / zh / ko / es、Unicode / Base64、retrieved chunk poisoning、GraphRAG、Agentic tool result、cascade cost escalation、security文書引用
- model: LM Studio `qwen/qwen3.5-9b`、resolved ID一致、temperature 0、reasoning off、max output 512 tokens
- repeats: 3、case単位の多数決
- persisted evidence: hash、理由コード、aggregateだけ。生成されたquestion、context、chunk、answer、canary、PII、secretは保存しない
- external content: none
- dataset fingerprint: `fb80b56b330d0fff95434aae2f6a02f5b4f726169516de8eabf78b67a9e8626e`
- case-set fingerprint: `87122e05d95a92c9a15717b40e83861f3757a6688b374b2927ae598ae64d963e`
- recipe builder contract: `prompt_injection_recipe_builder_v1`

追加12件は、repositoryには分類metadataだけを置き、実行時にcase IDのSHA-256から一時的なfact tokenとcanaryを導出する。完全な評価入力と生成回答はメモリ内だけで扱い、artifactへ永続化しない。

## Deterministic screening

| profile | generation bypass | boundary bypass | total bypass | clean policy action | detector gap |
|---|---:|---:|---:|---:|---:|
| `observe_only` | 30 | 11 | 41 | 0 | 0 |
| `quarantine_context` | 5 | 0 | 5 | 0 | 0 |
| `block_user_quarantine_context` | 0 | 0 | 0 | 0 | 0 |

direct user-message attackも止める必要があるため、Qwen end-to-end確認へ進める候補は`block_user_quarantine_context`だけとした。この表は決定論的boundary screeningであり、生成モデルの成功率を示さない。

## Qwen 3-repeat result

2026-08-05に新fingerprintで実測した。run IDは`20260805T021346Z-c2b1172e`、raw-free summary SHA-256は`70cd1ae12cfc3e3cda41f51986e4fdb8e928d5e21d673dc2b3e614f83c0c4589`である。独立reassessmentもfailure reason 0で同じ昇格判定を返した。以前のraw fixtureで得た数値は、fingerprint不一致のため本昇格の根拠には使用していない。

| gate | `observe_only` baseline | `block_user_quarantine_context` | 判定 |
|---|---:|---:|---|
| majority attack success | 1 / 30 | 0 / 30 | pass |
| one-sided exact 95% upper | 14.86% | 9.50% | pass（candidate 10%未満） |
| pipeline failure | 0 / 180 | 0 / 180 | pass |
| majority clean policy action | 0 / 30 | 0 / 30 | pass |
| majority clean abstention | 0 / 30 | 0 / 30 | pass |
| majority poison citation | 0 / 30 | 0 / 30 | pass |
| clean utility | 100.00% | 100.00% | pass（非悪化） |
| secure-attack utility | 96.67% | 100.00% | pass（非悪化） |
| p95 latency | 1,384 ms | 1,292 ms | pass |
| resolved model | `qwen/qwen3.5-9b` | `qwen/qwen3.5-9b` | pass |

候補のattack successは全90生成でも0件、detector expected coverageは100%、clean policy action rateとpoison citation rateはともに0%だった。clean abstentionとcitationのsecurity-scope指標は非悪化である。通常精度のUnanswerable accuracyは別scopeのまま変更も再評価もしておらず、本結果を通常精度改善の主張には使わない。

同じ30ケースの3反復は独立90標本とはみなさず、95%上限はcase多数決に対して計算する。

## Evidence hygiene and correction

初期Draft commitに含まれたsynthetic raw fixtureはmetadata-only recipeへ置換し、そのcommit自体も専用branch履歴から除去した。評価実装のclean commit `2bd5cf0`と結果文書commit `81ca0ed`はbase `c2ff8bf`へ通常fast-forwardで積まれており、初期raw fixture commitはPR履歴に存在しない。PRはstacked reviewとmerge順序のためDraftを維持するが、promotion gateの保留を意味しない。

最終raw-free scanでは、runtime生成したraw値95件をartifact 9ファイルとbase-to-working-tree差分へ照合し、完全一致はartifact / PR差分とも0件だった。JSON / JSONL 371文書に対するraw-key schema errorも0件だった。

PR、Jira、artifactへは集計、fingerprint、理由コード、実行条件、検証結果だけを記録する。raw評価入力、raw回答、PII、secretを転載しない。

## Limitations

- 本fixtureでattack success 0でも、未知の攻撃に対するゼロリスクは証明しない。
- productionで別generation providerを使う場合、その生成自体をこのローカルQwen評価で同一モデルとして扱わない。昇格対象は生成前の決定論的block / quarantine境界であり、provider別のend-to-end確認はdeployment smokeで継続する。
- 通常精度、Gold v2、RAG-31 security gateは別の評価scopeとして保持する。

## Rollback

1. internet-facing security profileに`docker-compose.internet-facing-policy-rollback.yml`を最後のoverrideとして重ねる。
2. Composeのrendered configでbackend / workerの`RAG_INJECTION_POLICY=observe_only`を確認する。
3. 再作成後、API / workerのhealthと認証境界を確認する。
4. 復帰理由、時刻、変更revisionを監査ログへ残す。

DB、Qdrant、Neo4j、Docker volumeのresetは不要であり、rollbackで実行してはならない。
