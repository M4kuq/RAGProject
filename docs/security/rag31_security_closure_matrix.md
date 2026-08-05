# RAG-31 final security integration / closure matrix

## 判定

PR #145は、PR #144 headを第1親、PR #142 headを第2親とする通常merge commitを持ち、PR #142、#143、#144を履歴付きで包含するRAG-31のcanonical follow-up integrationである。RAG-31受入条件1〜7とcombined gateが同一branch・同一CIで成功した場合、判定は **implementation closure ready** とする。

この判定はproduction secure/deployedを意味しない。PR #145、PR #141、既存source PRはDraftのまま維持し、merge、deploy、close、retarget、Draft解除を行わない。RAG-31もDoneへ遷移しない。

機械可読なSHA、fingerprint、件数、理由コード、rollbackは [rag31_security_closure_manifest.json](rag31_security_closure_manifest.json) に固定した。評価入力、取得evidence、生成出力、PII、canary、secretは保存しない。

## Integration snapshot

- Jira: `RAG-73`
- Draft PR: `#145`
- base: PR #141 branch `feature/rag-security-integration` / `c2ff8bf57a674e881efc4741d32eb440d87c2bfe`
- code base: PR #144 / `fb4b47f3a7d8d58c055399a82b26d8ea961a49b1`
- merge source: PR #142 / `6f430554aee08d7070ed04bc3a6e65cdb159a4aa`
- merge commit: `830b52c520afb85af8ad63dd93b390951c0aa540`
- PR #143 head `041a0d0e4abf275b04c43bbe8f8dd0b7fcca7ed4`はPR #144のancestor
- source changed-file union: 52、重複0、integration欠落0
- Alembic: single head `0025_qwen_cost_controls`

RAG-69はfollow-up実装前のclosure監査として保持し、履歴を変更しない。本書とRAG-73はRAG-70/71/72完了後の最終再監査だけを扱う。

## 受入条件1〜7

| ID | 判定 | 同一integration stateの証拠 | 測定限界 / 残余risk |
|---|---|---|---|
| 1 | satisfied | PR #133/#135/#142のmetadata-only synthetic datasetと直接・間接・多言語・難読化・Agentic・Graph境界。RAG-72の新fingerprintを3 repeats実測 | 観測0件は未知攻撃のゼロリスクを意味しない |
| 2 | satisfied | internet-facing overrideで`block_user_quarantine_context`を昇格。block、context quarantine、全context時abstain、clean action監視、rollback overrideを統合 | 独立score down-rankは採用せず、検出evidenceを完全quarantineする。通常/CI既定は`observe_only` |
| 3 | satisfied | provenance、trust、review/quarantineをdense/sparse/hybrid/Agentic/Graph/citation/cacheへ伝播し、未承認visibility 0を維持 | legacy rowは後方互換でtrusted/approved |
| 4 | satisfied | server-owned tool allowlist、deny-by-default、typed/bounded args、read/write分離、実行前拒否、raw-free audit | 将来の外部write toolは再監査が必要 |
| 5 | satisfied | RAG-63 admissionを前段に維持し、RAG-70でPostgreSQL atomic token/cost/escalation ledger、shared rate/concurrency/circuit、1 request 1 escalationを接続 | live billing reconciliationは未実施。Qwenはdefault disabled |
| 6 | satisfied | RAG-71 exact provider/model/purpose、server consent、classification、region/retention/training、mask/secret/re-identification gateをFlash/Plus両方のreservation前に適用 | operator ruleはactivation前にprovider一次資料と再照合が必要 |
| 7 | satisfied | blocked/quarantined injectionからtransport/Plus/reservationへの到達0、content-driven escalation/policy/budget bypass 0、egress拒否後reservation 0、tool/corpus/clean/pipeline非回帰をcomposite testで検証 | synthetic transportのみ。production secure/deployedは未主張 |

## Cross-cutting control order

1. RAG-63が認証済みrequestをadmitする。
2. 強制injection policyがuser attackをblockし、取得evidenceをquarantineする。blockまたは全quarantineではgenerationへ進まない。
3. Qwen候補はserver-produced strategy、retrieval sufficiency、structured Flash failureだけから決める。user/retrieved/tool contentはrouting、consent、policy、budget値に使わない。
4. RAG-71がexact routeとserver consentを評価し、拒否時はledger reserve前に停止する。
5. RAG-70 ledgerがatomic reserveし、成功usageをfinalizeする。Plus失敗はcompleted Flashへ戻し、安全なFlashがなければabstain/fail-closedとする。

## 個別測定を混ぜない

- RAG-72: dataset fingerprint `fb80b56...e8626e`、case-set fingerprint `87122e...d963e`、30 generation / 14 boundary / 3 repeats。candidate多数決attack success 0/30、pipeline failure 0/180、clean utility 100%、case多数決95%上限9.50%。
- RAG-71: case-set fingerprint `d03e61...dea24`、26 scenarios。consent bypass 0/10、exact-policy系 bypass 0/8、re-identification bypass 0/2、clean mutation 0。
- RAG-70: focused 17 passed / PostgreSQL-only 1 local skip、PR #144 Backend CI 1135 passed / 6 skipped。budget race、content-driven Plus、one-escalation、circuit、fallback gateを個別に保持。

これらはcase set、fingerprint、測定単位が異なる。新しい精度、ASR、Gold v2、通常精度profileへ合算しない。今回追加するcombined gateはcontrol compositionの0-bypass/非回帰判定であり、個別モデル評価の再集計ではない。

## Merge順と復帰点

1. 明示承認後にPR #141を先にmainへmergeする。
2. PR #145をmainへretargetし、全CIとancestry/file-set検証を再実行する。
3. 明示承認後にPR #145だけをcanonical follow-upとしてmergeする。PR #142/#143/#144を個別に先行mergeしない。

現時点の復帰はPR #145をmergeしないことで完了する。code起点はPR #144 headとPR #142 headである。runtime復帰は`QWEN_CASCADE_ENABLED=false`、internet-facing injection rollback override、external egress deny/empty allowlistsを使う。DB、Qdrant、Neo4j、Docker volumeのresetは不要であり、実行してはならない。

## 未解決risk

- live Qwen互換性、provider quota、実請求照合、production deployment smokeは未実施。
- adaptive/未知prompt injectionと将来のwrite toolは別途監視・再監査が必要。
- frontend dependency auditの既存14件はRAG-31 integrationへ混ぜず、次の独立security goal候補とする。
