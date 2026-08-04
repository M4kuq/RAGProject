# RAG-31 security closure matrix

## 結論

RAG-31全体はまだ完了ではない。Draft PR #141はRAG-31 security sliceの正規レビュー対象として利用できるが、受入条件2、5、6に残作業があるため、RAG-31を完了へ遷移したり、インターネット公開向けsecurity profileが完成したと主張したりしてはいけない。

コードレビューはDraft PR #141へ集約する。#141を採用する場合、PR #133〜#140を個別にmergeしない。個別PRは#141がmainへ取り込まれたことを再取得で確認するまでcloseしない。

機械可読な取得値は [rag31_security_closure_manifest.json](rag31_security_closure_manifest.json) に固定した。本文・質問・取得chunk・回答・PII・canary・secretは記録していない。

## Remote snapshot

- 取得時刻: `2026-08-04T11:19:25.065Z`
- GitHub main: `96e5fc82ad2c253d000633ff9e9431e88998f74a`
- Draft PR #141 audited head: `8de37d299940bd29d8971af5b9e8c28c776e8bc5`
- #133〜#141: すべてopen、Draft、mergeable clean、全check成功
- #141: 21 commits、96 changed files、Backend・Frontend・Docker compose smoke成功

GitHub compareで#133〜#140の全headは#141 headのancestorだった。全比較で`behind_by=0`かつsource headがmerge baseである。#133 headから#135 headも`ahead_by=1`、`behind_by=0`であり、#135が#133を包含するstacked関係を確認した。

source PRのchanged-file unionは93ファイル、#141は96ファイルだった。欠落は0で、#141固有の3ファイルは次だけである。

1. `backend/alembic/versions/0024_security_integration_merge.py`
2. `backend/tests/test_rag31_security_integration_gate.py`
3. `docs/security/rag31_security_integration_gate.md`

## 受入条件

| ID | 判定 | 実装・検証 | 限界 / 次のissue |
|---|---|---|---|
| 1 | satisfied | PR #133/#135。direct、indirect、日英、難読化、Agentic/Graph境界のsynthetic fixtureとQwen3.5 9B screeningを実装 | 観測数が小さく、0件成功は母集団の安全を意味しない |
| 2 | partial | observe、context quarantine、user block、全context時abstain、clean policy action率を実装。control bypassは11/14から0/14 | 既定は`observe_only`のまま。独立down-rank未実装。RAG-72でfalse-positive校正後に強制profileを昇格する |
| 3 | satisfied | PR #139。provenance、trust、review/quarantineをdocumentからdense/sparse/hybrid/Agentic/Graph/citation/cacheへ伝播 | legacy rowは後方互換のためtrusted/approvedとして扱う |
| 4 | satisfied | PR #140。server-owned allowlist、deny-by-default、read/write effect、typed/bounded args、callback前拒否、raw-free audit | 現在の公開surfaceはread-only。将来の外部write toolは再監査が必要 |
| 5 | partial | PR #135/#136/#137。1 request escalation guard、原子的rate/concurrency/daily work-unit、server-owned model allowlist | work unitは実token/通貨ではなく、実Qwen provider circuit breakerも未接続。RAG-70 |
| 6 | partial | PR #134/#138。external deny-by-default、provider allowlist、PII mask、unmaskable block、local endpoint exact-host trust | data classification、明示同意状態、provider retention強制が未実装。RAG-71 |
| 7 | satisfied for implemented surfaces | PR #141複合gateでprompt/control bypass、PII leakage、budget bypass、unauthorized tool、quarantine visibility、clean境界、pipeline failureを全て0件に固定 | RAG-70/RAG-71の実provider機能を追加した時点でgate拡張が必要 |

## PR responsibility

| PR | Jira | 責務 | #141での扱い |
|---|---|---|---|
| #133 | RAG-31 | Prompt injection Phase 1、threat model、raw-free gate | #135のancestorとして包含 |
| #134 | RAG-61 | External model PII egress | merge parentとして包含 |
| #135 | RAG-62 | Poisoning、難読化、Agentic/Graph/cascade boundary | #133を含むmerge parent |
| #136 | RAG-63 | 認証後rate/concurrency/daily work-unit | merge parent、0023の一方 |
| #137 | RAG-64 | Server-owned model selection | merge parentとして包含 |
| #138 | RAG-65 | Local endpoint exact-host trust | merge parentとして包含 |
| #139 | RAG-66 | Corpus provenance/trust/quarantine | merge parent、0023の一方 |
| #140 | RAG-67 | Agentic/MCP tool execution policy | merge parentとして包含 |
| #141 | RAG-68 | 競合解消、0024 migration merge、複合gate | 正規レビュー対象 |

## Review and merge decision

推奨経路は#141だけをレビュー対象とする方法である。#141は各source headを履歴上のparent/ancestorとして保持し、117 changed blobの照合でmismatch 0、source file unionの欠落0、単一migration head、統合CI 3/3成功を確認済みである。

個別PRを別々にmergeする経路は推奨しない。特に#135は#133をbaseにし、#136と#139は同じ`0022_eval_reliability`から別々の0023を作るため、merge順とmigration head解消をmain上で再実施する必要がある。squash mergeを混ぜると#141のancestor関係もmainで再現されない。

#141をmergeできるという技術判定と、RAG-31が全受入条件を満たしたという完了判定は別である。#141は既存surfaceの防御を統合するincremental security PRとしてレビュー可能だが、RAG-31はRAG-70、RAG-71、RAG-72が完了するまで`not_closed`とする。

## Recovery

- 最小復帰: Draft PR #141をmergeしない。
- code基準: main `96e5fc82ad2c253d000633ff9e9431e88998f74a`。
- source復帰: manifestに固定した#133〜#140 headを個別に再取得できる。
- migration: `0024_security_merge`はno-opで、2本の0023を単一headへ束ねるだけである。
- runtime: 各sliceのrunbookにあるkill switchを使用する。DB/Qdrant/Neo4j/volume resetを復帰手順にしない。
- PR運用: #141がmainへ入ったことを再取得する前に#133〜#140をcloseしない。今回のgoalではmerge、Draft解除、close、base変更を行わない。

## Follow-up

- RAG-70: 実Qwen Flash→Plusへprovider別token/金額budget、昇格回数、circuit breakerを接続する。
- RAG-71: external egressへdata classification、明示同意、retention policyを追加する。
- RAG-72: prompt injection強制policyを拡張fixtureとfalse-positive校正後に昇格する。

これらは通常精度のCalibrated Grounded Answer Pass Rateへ混ぜず、RAG-31 security gateとして独立評価する。
