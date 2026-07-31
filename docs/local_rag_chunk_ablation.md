# RAG-60 日本語チャンク dev-only ablation

## 目的と結論の境界

この実験は、現行既定の `512 / 128 / whitespace_v1 / fixed_v1` を変更せず、
日本語を空白だけで数えることによる巨大chunk化と、section境界を跨ぐchunk化の影響を
dev-onlyで測る。既存 `local_accuracy_dev_v1` のfacts/questionsを使うが、元corpusが
1 sourceあたり短文1件のため、そのままでは全profileが同じchunkになる。このため、
同じfactを中立的な長文・3 sectionへ配置した
`local_accuracy_chunk_dev_v1` stress corpusを補助診断として使う。

stress corpusの結果はGoldや公開精度ではない。retrieval-only screeningでC0を上回り、
no-contextを悪化させない候補だけを最大2件選び、その後にだけ
`qwen/qwen3.5-9b`・A3でend-to-end確認する。Gold v2は開かない。

## Profile

| ID | Tokenizer | Size / overlap | Boundary | 位置付け |
|---|---|---:|---|---|
| C0 | `whitespace_v1` | 512 / 128 | `fixed_v1` | 現行既定と同じbaseline |
| C1 | `japanese_aware_v1` | 512 / 128 | `fixed_v1` | tokenizer単独差分 |
| C2 | `japanese_aware_v1` | 256 / 64 | `fixed_v1` | 小chunk |
| C3 | `japanese_aware_v1` | 768 / 128 | `fixed_v1` | 大chunk |
| C4 | `japanese_aware_v1` | 512 / 128 | `structure_v1` | section境界保持 |

`japanese_aware_v1` は非ASCIIを約1 token/文字、ASCIIを約4文字/tokenとして扱う
軽量ヒューリスティックであり、Qwen tokenizerそのものではない。元文字列を保持し、
日本語文字間へ空白を挿入しない。

## 隔離と復帰点

- branch: `feature/local-rag-chunk-ablation`
- worktree: `.worktrees/local-rag-chunk-ablation`
- 基準commit: `64fba6abfd9bfb69b1c541fd1c17b22b3564775f`
- PR #131 remote head: `a50cc917d698ce7ab699ad5de8c4a1c991ec6ed7`
- 基準26ファイルはGit blob SHA全件一致
- PR #128、PR #131、root checkout、現行default profileは変更しない
- Qdrant collection名はdataset、stress corpus、chunk profile、resolved embedding、
  dimensionのhashで分離する
- collection、DB、Docker volumeのdelete/resetを実行しない

戻す場合はこのbranchを採用しなければよい。既定 `ChunkingConfig()` はC0のままである。
実験用collectionは `*_rag60_<hash>` として残し、削除は別途明示承認がある場合だけ行う。

## 実行

geometryだけ:

```bash
cd backend
python -m app.scripts.run_local_chunk_ablation \
  --confirm-local-only \
  --geometry-only \
  --git-sha <commit-sha> \
  --output-dir ../artifacts/rag60-chunk-ablation
```

実Qdrant・実LM Studio embedding・BGE reranker:

```bash
cd backend
python -m app.scripts.run_local_chunk_ablation \
  --confirm-local-only \
  --git-sha <commit-sha> \
  --output-dir ../artifacts/rag60-chunk-ablation
```

BGEがローカルcacheにない場合は `reranker_model_unavailable` で停止する。モデル取得を
明示的に許可する場合だけ `--allow-model-download` を付ける。取得対象はモデルファイル
だけで、質問・chunk・answerはHugging Faceへ送信しない。

retrieval-onlyで選ばれたC0/C3/C1のA3 end-to-end補助評価:

```bash
cd backend
python -m app.scripts.run_local_chunk_end_to_end \
  --confirm-local-only \
  --allow-model-download \
  --repeats 1 \
  --git-sha <commit-sha> \
  --output-dir ../artifacts/rag60-chunk-ablation
```

生成・補助JudgeはともにLM Studioの `qwen/qwen3.5-9b`、temperature 0、
context上限6,000文字、output上限12,000文字、baseline promptで固定する。
同一9B Judgeの結果は補助判定であり、人手校正済みの公開精度ではない。

## ログ

`artifacts/rag60-chunk-ablation/activity.jsonl` はappend-onlyで、各行に以下を残す。

- UTC timestamp、run ID、event、status、reason codes
- Git SHA、dataset/stress corpus/chunk profile fingerprint
- requested/resolved embedding model、dimension、reranker
- chunk geometry、collection名、top-k、rerank-top-n
- Recall、fact recall、MRR、no-context、p95、pipeline failure
- finalistとpromotion decision

JSON/Markdown artifactにはraw question、chunk、answer、contextを保存しない。caseはSHA-256
hashだけを保存する。`.env`、API key、credential、PIIを読出し・表示・保存しない。

## Gate

候補は次を全て満たす場合だけend-to-endへ進む。

1. pipeline failureが0
2. no-context rateがC0以下
3. Recall@Nとfact Recall@NがC0以上
4. Recall@N、fact Recall@N、MRRの少なくとも1つがC0より高い
5. 上記候補からfact Recall、Recall、MRR、p95の順で最大2件

候補がなければA3は現状の凍結候補のまま、chunk profileも既定へ昇格しない。

## 2026-07-31 実測結果

実行基準はcommit `64fba6abfd9bfb69b1c541fd1c17b22b3564775f`。Gold v2、
PR #128、PR #131、既定profile、既存collection/volumeは変更していない。

### Geometry

| Profile | Chunk数 | factを含むchunk | 中央文字数 | 最大文字数 |
|---|---:|---:|---:|---:|
| C0 | 100 | 40 | 3,064 | 3,548 |
| C1 | 240 | 40 | 917 | 1,904 |
| C2 | 480 | 40 | 428 | 953 |
| C3 | 160 | 40 | 1,049 | 2,857 |
| C4 | 280 | 40 | 530 | 1,904 |

geometry runは `20260731T072752Z-8717fdfa`。

### Retrieval-only

run `20260731T075356Z-41947a69`、Embeddingは
`text-embedding-qwen3-embedding-4b` 2560次元、rerankerは
`BAAI/bge-reranker-v2-m3`、top-k 20、rerank-top-n 3。

| Profile | Recall@3 | fact Recall@3 | MRR | no-context | p95 ms |
|---|---:|---:|---:|---:|---:|
| C0 | 67.50% | 67.50% | 0.8000 | 0% | 37,760.97 |
| C1 | 75.00% | 72.50% | 0.8583 | 0% | 10,421.26 |
| C2 | 70.00% | 67.50% | 0.8083 | 0% | 5,205.46 |
| C3 | **77.50%** | **77.50%** | **0.8875** | 0% | 15,353.88 |
| C4 | 70.00% | 70.00% | 0.8250 | 0% | 9,798.95 |

C3はC0比でRecall/fact Recallともに+10ポイント、C1はRecall+7.5ポイント、
fact Recall+5ポイントだったため、end-to-end候補はC3/C1とした。

### Qwen3.5 9B / A3 end-to-end補助評価

run `20260731T091901Z-a0d8f91e`。最初のpreflight run
`20260731T091752Z-c8e97aef` は生成モデル未loadのため
`generation_model_not_loaded` で安全停止した。LM Studio v1 local APIで
ダウンロード済み9Bをloadし、requested/resolved modelが
`qwen/qwen3.5-9b` で一致したことを確認してから再実行した。

| Profile | 補助Pass | Answerable | Unanswerable | Injection総合 | Injection resistance | Fact support | Citation | Failure | E2E p95 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| C0 | 60.00% (24/40) | 50.00% | 75.00% | 0% | 25.00% | 60.87% | 100% | 1 | 261.170 s |
| C3 | **62.50% (25/40)** | **58.33%** | 68.75% | 0% | 12.50% | **70.83%** | 100% | 0 | 260.792 s |
| C1 | 55.00% (22/40) | 41.67% | 75.00% | 0% | 12.50% | 54.17% | 100% | 0 | **254.360 s** |

paired case差分はC3が4改善/3悪化/21双方Pass/12双方Fail、C1が
3改善/5悪化/19双方Pass/13双方Fail。C3は補助Pass+2.5ポイント、
answerable+8.33ポイント、fact support+9.96ポイント、failure 0へ改善したが、
unanswerable -6.25ポイント、prompt-injection resistance -12.5ポイントで
guardrailを悪化させた。C1は補助Pass -5ポイント、answerable -8.33ポイント、
fact support -6.70ポイントで悪化した。

検索p95はC3で61.61%、C1で73.87%短縮した。一方、end-to-end p95は
C3で0.14%、C1で2.61%しか短縮せず、generation p95は全profileで約237秒、
Judge p95は約11〜13秒だった。したがって検索高速化をend-to-end高速化とは
表現しない。

回答生成のinput token合計はC3で23.96%、C1で34.67%減ったが、output token合計は
C3で34.59%、C1で31.34%増えた。このtoken集計には補助Judgeのtokenは含まない。

### 判定

- `selected_for_additional_repeats=[]`
- C3はunanswerableとprompt injectionを悪化させたため3反復へ進めない
- C1は総合品質を悪化させたため3反復へ進めない
- C0/defaultを維持し、C1/C3を昇格しない
- 同一9B Judgeの1反復なので公開精度やCalibrated Pass Rateとは呼ばない
- Gold v2を開かない

artifact `20260731T091901Z-a0d8f91e-end-to-end.json` は各profile 40件、
distinct case hash 40件を保持し、raw question/chunk/answer/context用のkeyおよび
fixture本文の混入がないことを検査済み。将来runでは各case完了時にも同じraw-free
項目を `activity.jsonl` へcheckpointする。

### 復帰と次の診断

既定 `ChunkingConfig()` はC0のままで、戻す操作は不要。実験branchを採用しなければ
production behaviorは変わらない。実験collectionは削除せずfingerprint別に保持する。

次は追加chunk探索より先に、Oracle Contextで検索gapと生成gapを分離し、C3で正しい
fact取得が増えた一方、unanswerable/prompt injectionが悪化した原因を確認する。
通常精度とsecurity gateは混ぜず、security側ではretrieved chunk poisoningと
prompt injection resistanceを独立評価する。
