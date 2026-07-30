# ローカルSLM RAG評価改善 実装引き継ぎ

最終更新: 2026-07-30

## 1. この文書の目的

この文書は、Qwen3.5 9B固定のローカルRAG評価について、海外論文で提案されている
診断手法をRAGProjectへ段階的に導入するための実装引き継ぎである。

現在のE2 top-k 20は補助Judge合格率を改善したが、unanswerableと
answer completenessのhard gateを満たしていない。したがって、直ちに新しい検索
profileを昇格するのではなく、次のどこに原因があるかを分離する。

1. 必要な根拠を取得できていない
2. 根拠は取得できているが生成で利用できていない
3. 追加文脈のノイズ、矛盾、配置位置に影響されている
4. 補助Judgeまたはanswer completeness測定器が誤っている
5. 40件のdev setに対する候補探索へ過適合している

## 2. 関連する作業項目と境界

- RAG-30: 評価基盤本体。完了済み
- RAG-32: E2 top-k 20のunanswerable／completeness悪化を改善。現在の実装作業
- RAG-31: prompt injection、poisoning、権限、外部LLM境界を扱う独立security gate
- Draft PR #128: `feature/local-rag-accuracy`の検証済み復帰点

この文書の直近作業はRAG-32に属する。PoisonedRAGやBIPIAに基づく攻撃fixtureは
RAG-31で扱い、通常精度の昇格条件とsecurity gateを混ぜない。

## 3. 現在の復帰点と作業場所

- 基準branch: `feature/local-rag-accuracy`
- 基準checkpoint: `c98d4dd`
- 実装branch: `feature/local-rag-answerability`
- 実装worktree: `.worktrees/local-rag-answerability`
- GitHub main確認値: `5196b48`
- Draft PR #128 remote head確認値: `88b61225`

上記SHAはRAG-32開始時の記録である。利用前にGitHubとローカルを再取得して確認する。

禁止事項:

- `feature/local-rag-accuracy`とPR #128を直接書き換えない
- Gold v2を候補探索に使用しない
- 既定retrieval profileを自動変更しない
- DB、Qdrant collection、Docker volumeをresetまたはdeleteしない
- raw prompt、raw context、raw answer、provider response bodyを成果物へ保存しない
- 検索条件と生成条件を同一ablationで同時に変更しない

## 4. 現在確認できている事実

`local_accuracy_dev_v1`は40件で、構成は以下のとおり。

- answerable／unanswerable: 24／16
- single-hop／multi-hop: 20／20
- 日本語／英語: 20／20
- prompt injection: 8

最新の比較対象:

| Profile | Run | 補助Pass | Citation correctness | Completeness | Unanswerable | Injectionタグ群の総合Pass | p95 |
|---|---:|---:|---:|---:|---:|---:|---:|
| B1 Nomic | 57 | 75.0% | 84.211% | 31.579% | 12/16 | 4/8 | 92.790 s |
| E2 top-k 20 | 82 | 82.5% | 100.0% | 30.435% | 11/16 | 7/8 | 101.841 s |
| A1 retry無効 | 86 | 85.0% | 100.0% | 30.435% | 11/16 | 7/8 | 121.993 s |
| A2 4,000 token相当 | 88 | 90.0% | 100.0% | 33.333% | 13/16 | 7/8 | 232.399 s |
| A3 3,000 token相当 | 90 | 87.5% | 100.0% | 33.333% | 12/16 | 7/8 | 186.539 s |
| A4 2,850 token相当 | 92 | 86.842% (33/38 judged) | 100.0% | 33.333% | 11/16 pass、2 Judge失敗 | 7/8 | 146.841 s（負荷影響あり） |

E2のpaired bootstrap 95% CIは`[-10.0, +27.5] pp`、exact McNemarは
`p=0.607239`である。1 repeatの同一9B補助Judge結果であり、公開可能な精度値ではない。

RAG-32の進行中診断では、次の点が見つかっている。

- `expected_answer_slots`に文全体が入るケースがあり、現行completenessは意味的一致では
  なく完全な文字列の包含に依存する
- 補助Judgeが意味的にPassとする回答でもcompletenessが低下し得る
- 単純な検索スコア閾値は英語answerableケースまで落とす危険がある
- A1（retry無効化）はrun 82比で補助Passが`82.5% -> 85.0%`となったが、
  unanswerableは`11/16`、completenessは`30.435%`のままで、不採用
- A2（出力上限4,000 token相当）はB1比で補助Pass`75% -> 90%`、
  unanswerable`12/16 -> 13/16`、injection`4/8 -> 7/8`、
  citation`84.211% -> 100%`、completeness`31.579% -> 33.333%`となった
- A2はpipeline failure 0だが、p95が`92.790 s -> 232.399 s`、2.50倍となり
  latency hard gateに失敗
- A3（出力上限3,000 token相当）はB1比で補助Pass`75.0% -> 87.5%`、
  unanswerable`12/16 -> 12/16`、injection`4/8 -> 7/8`、
  citation`84.211% -> 100%`、completeness`31.579% -> 33.333%`となった
- A3はpipeline failure 0だが、p95が`186.539 s`で、許容上限`185.580 s`を
  `0.959 s`（`0.517%`）超えたため、規則どおりlatency hard gate不合格
- A4（出力上限2,850 token相当）は40件すべてでpipeline成功したが、
  `local_dev_unanswerable_32`と`local_dev_unanswerable_37`の補助Judgeが失敗した
- A4は38 judged caseで補助Pass`33/38`、B1比`+10.526 pp`、
  95% CI `[-7.895, +28.947] pp`、exact McNemar `p=0.423950`だった
- A4はunanswerable非悪化を40件完全比較で証明できず、A3比でも38 judged caseで
  `-2.632 pp`となったため不採用
- A4実行中に他タスクの並列実行が確認され、GPU使用率85%、VRAM
  `11.54 / 12.28 GiB`だった。A4 p95は昇格判定へ使わない
- A3をprovisional confirm candidateとして凍結する。manifest SHA-256は
  `AE98F837EE9B5AB439C69803D9B4CE832F7CAC423E10C8DDD4708C1287FA0A32`
- latencyは、他タスクが停止したwindowでB1とA3を同じ順序・同じ負荷条件で
  再測定するまで判定保留とする。追加のdev tuningは行わない

2026-07-29の負荷を抑えた確認windowでは、RAGProjectの4コンテナだけを残し、
他の56コンテナを削除せず停止した。復旧対象は
`artifacts/experiments/docker_quiet_window_20260729_2122.json`へ保存した。
実行順は過去と逆のA3→A2とし、LM StudioのQwen3.5 9B、Qwen3 Embedding 4B、
retrieval、prompt、temperature、datasetを固定した。

| Profile | Screening run | E2E run | 補助Pass | Unanswerable | Citation | Completeness | p95 |
|---|---:|---:|---:|---:|---:|---:|---:|
| A3 3,000 token相当 | 93 | 94 | 85.0% (34/40) | 12/16 | 100.0% | 33.333% | **149.395 s** |
| A2 4,000 token相当 | 95 | 96 | **87.5% (35/40)** | **13/16** | 100.0% | 33.333% | 210.967 s |

- A3のp95は旧run 90比で`19.912%`短縮し、既存B1比`1.610倍`となった
- A2のp95は旧run 88比で`9.222%`短縮したが、既存B1の2倍上限を
  `25.387 s`超過し、`2.274倍`のため引き続きlatency gate不合格
- A2はA3より補助Passが1件多いだけで、差は`+2.5 pp`、95% CI
  `[-5.0, +10.0] pp`、exact McNemar `p=1.0`だった。一方でp95は
  `41.214%`、総tokenは`5.746%`、出力tokenは`11.615%`多かった
- tailは検索やDocker I/Oより生成時間が支配的だった。特にunanswerableで
  retryが2回の上限を消費し、A2は最大8,000、A3は最大6,000出力token相当となった
- B1を同じ確認windowで再測定していないため、A3のlatency gate通過は既存B1を
  基準にした暫定判定である。A2不採用は維持し、A3をprovisional candidateとする
- `prompt_injection_resisted`は対象8件すべてで`not_applicable`だった。
  従来の`7/8`はinjectionタグ群の総合Passであり、耐性の非悪化を示さない。
  専用security gateはRAG-31で通常精度と分離して修正・再測定する
- 生成4Bはこの比較へ混ぜない。A3を固定した後に4B／9B／4B→9B cascadeを
  dev／confirmで3回反復し、手動校正とlatency・昇格率を測る独立実験とする。
  Qwen3 Embedding 4BはA2／A3ですでに使用済みである

2026-07-30には、開始時のDocker状態を
`artifacts/experiments/docker_quiet_window_20260730_0940.json`へ保存し、
RAGProjectの4サービスだけを稼働させた同一windowでB1／A3を各3回測定した。
Qwen3.5 9B、temperature 0、cache disabled、dataset、corpus、prompt、
B1／A3それぞれの凍結manifestを固定した。実行順は
`B1, A3, A3, B1, B1, A3`である。

| Profile | E2E runs | 3回平均補助Pass | 多数決Pass | Unanswerable | Citation | Completeness | 平均p95 |
|---|---|---:|---:|---:|---:|---:|---:|
| B1 | 98 / 108 / 110 | 74.167% | 75.000% | 75.000% | 84.211% | 31.579% | 92.381 s |
| A3 | 104 / 106 / 112 | 85.000%（欠損をFail扱い） | 85.000% | 75.000% | 100.000% | 33.333% | 145.054 s |

- A3の保守的な3回平均差は`+10.833 pp`
- case多数決差は`+10.000 pp`、相対改善`+13.333%`
- 10,000回paired bootstrap 95% CIは`[-7.500, +27.500] pp`
- exact McNemarは`p=0.423950`
- p95比は`1.570倍`で2倍上限内
- 両profileともpipeline failure 0
- unanswerableは非悪化、citationとcompletenessも非悪化
- run 106の`local_dev_unanswerable_37`で補助Judgeが1件失敗した。
  観測値は`34/39`だが、集計では分母から除外せず`34/40`として扱った。
  他のA3 2 repeatが一致したためcase多数決40件は解決可能
- run 100／102はそれぞれ`invalid_parallel_measurement`／
  `invalid_interrupted_measurement`として除外し、削除していない

数値gateは通過したが、Judge失敗が1件残り、CIも0を跨ぐ。このためA3を
`confirm_dev`へ自動昇格しない。A3は凍結候補のままとし、Phase 1へ進む前に
初回失敗を監査証跡として保持するbounded Judge retryとterminal reason codeを
独立ablationとして実装・再確認する。これはretrieval、prompt、output budgetと
同じ比較へ混ぜない。結果artifactは
`artifacts/experiments/b1a3-confirm-20260730/`にあり、raw質問、回答、
retrieved contextは含まない。

このため、「生成品質の悪化」と「測定器の弱さ」は別の変更・別の結果として扱う。

## 5. 実施順

### Phase 0: 現在のRAG-32 ablationを完了する

出力上限だけを変えるA3とA4を完了し、A1／A2と独立して評価した。

1. A1: `retry_on_insufficient_evidence=false`。完了、不採用
2. A2: output budget 4,000 token相当。負荷抑制後もlatency gate不合格
3. A3: output budget 3,000 token相当。同一windowのB1／A3各3回では
   精度・非悪化・latency数値gateを通過したが、Judge 1件失敗により自動昇格なし
4. A4: output budget 2,850 token相当。Judge 2件失敗とunanswerable非悪化未達で不採用

Phase 0の選択結果はA3である。ただし、同一windowの3 repeatでも補助Judge失敗が
1件残り、95% CIは0を跨いだため候補昇格ではない。Judge reliabilityを独立して
改善・再確認し、人手校正を含む全gateを満たした場合だけ`confirm_dev`へ進む。

最低限、以下を満たしてから次へ進む。

- retrieval profileはE2 top-k 20で固定
- 変更する生成座標は1条件だけ
- 40件すべてでpipeline failure 0
- case-levelのchanged verdictを保存
- unanswerable、prompt injection、citation、補助Judge各次元を個別比較
- raw本文をartifactへ保存しない

completeness修正はA1/A2と同じcommitまたは同じ比較runへ混ぜない。

### Phase 1: Oracle Contextで検索と生成を分離する

同じ質問、同じQwen3.5 9B、同じprompt、同じtemperature、同じ出力上限で、
文脈の供給方法だけを切り替える。

| 条件 | 文脈 |
|---|---|
| R | 通常のretrieval結果 |
| O | Goldではなくdev fixtureの期待根拠を直接供給するOracle Context |

実装要件:

- local/manual/test専用で、通常APIの既定動作へ影響させない
- `evaluation_backend`または明示的なdiagnostic modeとして有効化する
- Oracle文脈に質問の正解文そのものを注入せず、期待根拠document/chunkのみ使用する
- RとOで生成条件とcase集合のfingerprintが一致しなければ比較を拒否する
- Oracle runで使用したsource key／fact IDは安全な識別子として保存できる
- raw contextはartifactへ保存しない

判断:

- `O - R`が大きい: retriever、reranker、chunkingが主因
- Oでも失敗する: generator、prompt、情報統合が主因
- Rで必要factを取得済みだが失敗する: context utilizationまたはノイズが主因

### Phase 2: Claim単位の診断指標を追加する

最初に追加する指標:

```text
claim_recall =
  retrieved contextに含まれるrequired fact数 / required fact総数

context_utilization =
  回答で正しく使われたretrieved required fact数 /
  retrieved contextに含まれるrequired fact数
```

ゼロ除算時は`N/A`とし、0として平均へ混入させない。

推奨するcase-level出力:

- `required_fact_count`
- `retrieved_required_fact_count`
- `used_required_fact_count`
- `claim_recall`
- `context_utilization`
- `oracle_context`
- `metric_reason_codes`

必要factの検出は、まずdev fixtureのfact ID／source keyに基づく決定的な方法を優先する。
LLM Judgeだけを唯一の検出器にしない。意味判定が必要な場合は補助値として保存し、
人手校正できるようにする。

既存のRecall@K、MRR、citation、completenessを削除せず、additiveに追加する。

### Phase 3: 文脈摂動による頑健性テスト

同じ取得文脈集合に対し、次の条件を一度に1座標だけ変えて実行する。

| 条件 | 操作 |
|---|---|
| P-front | 期待根拠をcontext先頭へ配置 |
| P-middle | 期待根拠をcontext中央へ配置 |
| P-end | 期待根拠をcontext末尾へ配置 |
| N-irrelevant | 固定された無関係chunkを追加 |
| N-near-miss | 同じ語彙を持つが答えではないchunkを追加 |
| N-conflict | 期待根拠と矛盾する合成chunkを追加 |

制約:

- 元のretrieval結果と追加fixtureのfingerprintを記録する
- 外部サイトの文章をコピーせず、既存dev fixtureまたは新規の短い合成fixtureを使う
- 同じchunk集合の順序変更と、chunk追加を同じrunで行わない
- 追加chunkはprompt injection fixtureと区別する
- 最大context tokenを固定し、条件ごとの情報量を可能な限り揃える

追加する集計:

```text
position_flip_rate =
  順序変更でcase verdictが変わった件数 / 対象case数

irrelevant_noise_failure_rate =
  無関係chunk追加でPassからFailへ変わった件数 / 元のPass件数

counterfactual_failure_rate =
  矛盾chunkによって誤答または根拠のない断定へ変わった件数 / 対象case数
```

### Phase 4: 補助Judgeの校正を強化する

Qwen3.5 9Bの自己評価は独立評価として扱わないという既存方針を維持する。

追加する校正値:

- dimension別TP、FP、TN、FN
- sensitivity／specificity
- balanced accuracy
- Cohen's kappa
- 日本語／英語別一致率
- answerable／unanswerable別一致率
- prompt injection別一致率
- bootstrap 95% CI

pairwise評価を導入する場合:

- profile名、model名、baseline/candidate表記をJudgeから隠す
- A/BとB/Aの両順序を評価する
- 両順序で勝者が変わる場合は`position_inconsistent`として採用判定から除外する
- 長い回答を自動的に優遇しないよう、rubricをclaimとevidenceへ限定する

Prometheus 2等の別familyローカルJudgeは、既定依存関係にせず校正対象だけのopt-in pilotとする。
日本語一致率、VRAM、latencyが確認できるまで置き換えない。

### Phase 5: 候補探索と確証評価を分離する

現行40件を同じ用途で繰り返し閲覧し続けない。

推奨フロー:

1. `tune_dev`で候補探索
2. 候補を最大3件に凍結
3. `confirm_dev`で一度だけ比較
4. 全hard gateを通過した場合のみ、既存手順に従ってGold v2を人手校正
5. Goldを見て候補を変更した場合、同じGold結果で再昇格しない

データ数が不足する間は、少なくとも次をartifactへ記録する。

- 候補を凍結した日時とmanifest fingerprint
- tune／confirmのcase集合fingerprint
- 試した候補数
- 選択規則
- 全候補を含む比較結果

「最良候補だけ」の信頼区間を公開せず、探索数と選択手順も結果に併記する。

### Phase 6: 引用とcompletenessをClaim単位へ移行する

既存fieldを破壊的に変更せず、以下をadditiveに追加する。

```text
citation_recall =
  citationを持つ検証可能claim数 / 検証可能claim総数

citation_precision =
  claimを実際に支持するcitation数 / 使用citation総数
```

`expected_answer_slots`は完全な模範文ではなく、1件1意味単位のrequired factへ正規化する。
旧datasetとの互換性が必要な場合はschema versionを上げ、旧completenessと新しい
claim completenessを別名で並存させる。

受け入れ条件:

- paraphraseされた正答を文字列不一致だけでFailにしない
- required factの一部しか答えていない回答は部分点または不足reason codeになる
- unsupported claimはclaim/span単位で場所を特定できる
- 日本語と英語で同じ意味の判定基準を使う
- 補助判定には人手校正可能な証跡を残す

### Phase 7: Securityとmulti-turnは独立した後続作業

RAG-31で追加する候補:

- attack success rate
- poison retrieval hit rate
- poison citation rate
- clean utility
- 正常文書のfalse quarantine rate
- 日本語／英語／難読化された間接prompt injection
- GraphRAG経由のpoison伝播

単一ターン評価が安定した後、10～20会話のmulti-turn fixtureを別datasetとして追加する。
会話履歴依存、代名詞、省略、話題転換、後半ターンの回答不能を含める。

## 6. 最初に実行する推奨実験

Phase 0完了後、B1相当とE2 top-k 20相当を以下で比較する。

1. 通常retrieval
2. Oracle Context
3. 同じretrieved chunk集合の位置変更
4. 固定near-miss／無関係／矛盾chunkの個別追加

実行順:

1. 40件、1 repeatでdiagnostic smoke
2. pipeline failureとfixture妥当性を確認
3. 条件と候補を凍結
4. 凍結した条件だけ3 repeats
5. 人手校正前は補助結果と明記

必須レポート:

- Grounded Answer補助Pass
- Claim Recall
- Context Utilization
- unanswerable accuracy
- answer completenessとclaim completeness
- citation recall／precision
- position flip rate
- irrelevant／counterfactual noise failure rate
- Judgeのdimension別一致率
- p95 latency
- pipeline failure
- paired bootstrap 95% CIとexact McNemar

## 7. Decision table

| 観測 | 次の変更候補 |
|---|---|
| Oracleだけ大幅改善 | embedding、reranker、chunking、retrieval strategy |
| Oracleでも必要fact不足 | generator prompt、output budget、情報統合 |
| claim recallは高いがutilizationが低い | context構造、fact selection、生成prompt |
| top-k増加でclaim recallとnoise failureが同時上昇 | reranking、context pruning、token budget |
| 位置変更だけで判定が変わる | context ordering、evidence grouping |
| Judgeと人手のFPが多い | rubric、Judge family、判定閾値 |
| completenessだけ悪化しJudge／人手は非悪化 | metric schemaとfact segmentation |
| devだけ改善しconfirmで再現しない | 候補探索への過適合 |

## 8. 完了条件

この評価改善フェーズを完了とみなす条件:

- Oracle Context比較で検索gapと生成gapを報告できる
- Claim RecallとContext Utilizationをcase-levelで再計算できる
- 位置、無関係ノイズ、near-miss、矛盾のうち最低3種を決定的に再実行できる
- Judge校正を単純一致率だけでなくdimension別混同行列で報告できる
- tuneとconfirm、最終Goldの利用目的が分離されている
- 既存のstrict comparability、bootstrap、McNemar、no-regression gateを維持する
- 既定profile、PR #128、既存データを変更せず失敗時に復帰できる
- 実行手順、manifest、reason code、テストが文書化されている

昇格条件は既存どおり、少なくとも以下を維持する。

- 人手校正後のGrounded Answer Pass Rateがbaseline比`+6 pp`以上
- unanswerable、prompt injection、citation、claim completenessが非悪化
- pipeline failure 0
- p95 latencyがbaselineの2倍以内
- security gateは別途RAG-31で通過する

## 9. テスト方針

最小の順に実行する。

1. metricの純粋関数unit test
2. fixture balanceとfingerprintの決定的test
3. manifest schemaと旧version互換test
4. Oracle／position／noise条件のstrict comparability test
5. raw contentがartifactへ残らないredaction test
6. 既存evaluation test
7. Ruff、mypy、対象pytest
8. local/manualの実Qdrant／LM Studio smoke

テストのために外部モデルdownloadを通常CIの必須条件にしない。

## 10. 参考文献とRAGProjectへの適用点

| 文献 | 適用点 |
|---|---|
| [RAGChecker, NeurIPS 2024](https://proceedings.neurips.cc/paper_files/paper/2024/hash/27245589131d17368cccdfa990cbf16e-Abstract-Datasets_and_Benchmarks_Track.html) | Claim Recall、Context Utilization、noise sensitivityによる原因分解 |
| [ARES, NAACL 2024](https://aclanthology.org/2024.naacl-long.20/) | 少数人手ラベルを使ったJudge校正と信頼区間 |
| [RGB](https://arxiv.org/abs/2309.01431) | noise、negative rejection、integration、counterfactual robustness |
| [Lost in the Middle, TACL 2024](https://aclanthology.org/2024.tacl-1.9/) | 同一文脈集合の位置変化テスト |
| [CheckList, ACL 2020](https://aclanthology.org/2020.acl-main.442/) | 言い換えや摂動に対するbehavioral test |
| [Prometheus 2, EMNLP 2024](https://aclanthology.org/2024.emnlp-main.248/) | 別familyのopt-inローカルJudge候補 |
| [Judging the Judges](https://arxiv.org/abs/2406.07791) | Judgeの順序バイアスと反復安定性 |
| [ALCE, EMNLP 2023](https://aclanthology.org/2023.emnlp-main.398/) | claim単位のcitation recall／precision |
| [RAGTruth, ACL 2024](https://aclanthology.org/2024.acl-long.585/) | claim/span単位のunsupported content |
| [mtRAG, TACL 2025](https://aclanthology.org/2025.tacl-1.36/) | 後続のmulti-turn RAG fixture |
| [PoisonedRAG, USENIX Security 2025](https://www.usenix.org/conference/usenixsecurity25/presentation/zou-poisonedrag) | RAG-31のpoisoning ASRとclean utility |
| [BIPIA](https://arxiv.org/abs/2312.14197) | RAG-31の間接prompt injection fixture |
| [SIREN, 2026 preprint](https://arxiv.org/abs/2605.05973) | 反復的候補選択のwinner's curse。未査読の参考資料として扱う |

## 11. 技術記事用に残す証跡

公開記事では、単なる最高スコアではなく次を再現可能な形で残す。

- モデル、embedding、reranker、dataset、corpus fingerprint
- baselineとcandidateの固定条件
- 試した候補数と選択規則
- Oracle、位置、ノイズ条件のcase-level差分
- 人手校正件数とJudgeのdimension別一致率
- 改善した指標と悪化した指標
- 昇格しなかった場合のhard gate
- latencyとローカル実行環境
- Goldを候補探索へ使用していないこと

想定する記事の中心テーマ:

> ローカル9B SLMのRAGでtop-kを増やすと補助Judge合格率は上がったが、
> 回答不能と完全性が悪化した。Oracle Context、文脈摂動、人手校正で原因を分解する。

「+7.5 pp」という補助値だけを成果にせず、なぜ昇格を見送ったかと、
どの実験で原因を切り分けたかを主題にする。
