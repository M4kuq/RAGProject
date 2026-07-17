# Gold Dataset v2 と補助 LLM Judge 校正基盤

## 目的

`Grounded Answer Pass Rate` を主指標にするための、安全で決定論的なGold dataset、evidence catalog、judge rubric、人間校正ポリシーを定義します。
この変更はPR #91のMetric V2を前提とするstacked changeです。runner接続や外部LLM呼び出しは含みません。

## Dataset balance

| 軸 | ケース数 |
| --- | ---: |
| 合計 | 50 |
| answerable / unanswerable | 30 / 20 |
| single-hop / multi-hop | 25 / 25 |
| hybrid / agentic_router | 25 / 25 |
| prompt injection | 10 |
| English / Japanese | 25 / 25 |

各caseは `answerable`、`reference_answer`、`required_facts`、`forbidden_claims`、`expected_evidence`、`required_citation`、`expected_strategy`、`tags` を必須境界として持ちます。
`expected_evidence` は環境依存のDB IDではなく、source catalogの安定した `source_key` と `fact_id` を参照します。
answerable caseは全required factをsupport evidenceで被覆し、unanswerable caseはnear-miss evidenceと禁止主張を定義します。

## Primary metric

`Grounded Answer Pass Rate = hard gateを全て通過したcase数 / 全case数` です。

- answerable: required facts、citation support、forbidden claim absenceを必須にする
- unanswerable: correct abstention、forbidden claim absenceを必須にする
- citation必須case: citation supportを必須にする
- prompt injection case: injection resistanceを必須にする

平均スコアでhard failureを相殺しません。LLM judgeのconfidenceも主指標そのものには混ぜません。

## Existing evaluation runner adapter

`load_evaluation_cases("gold_answer_quality_v2", case_limit=50)` はGold Dataset v2を既存の `EvaluationCase` 契約へ変換します。
`EvaluationService.run_job()`、worker handler、DBテーブル、公開APIは変更せず、既存runnerが50件をロードして既存の決定論的metricを集計できます。

- required fact statementをexpected keywordとanswer-completeness slotへ変換する
- expected strategy、hop count、tagを既存のsafe metadata境界へ写像する
- reference answerは実行時の比較だけに使用し、DB、API detail、trace artifact、ログへ保存しない
- forbidden claim、expected evidence、promptをrunner artifactへ複製しない

統合テストは外部LLM、外部judge、AWS、`load-data` を使わない参照RAG stubで50件の完走と集計を検証します。
この接続は既存metric runner向けであり、補助LLM judgeの呼び出しや `Grounded Answer Pass Rate` のjudge判定を追加しません。

## Auxiliary judge と人間校正

LLM judgeは補助判定だけを表し、外部呼び出し実装はこのPRに含めません。decision schemaは列挙値、confidence、safe reason codeだけを許可し、raw answer、raw context、自由記述rationaleを保存しません。

- 初期校正: 100%人間確認
- 通常運用: baselineとの差分を全件確認
- hard gate failureを全件確認
- confidence 0.8未満を全件確認
- 残りから決定論的に15%を監査

監査bucketはcase IDとevaluation fingerprintのSHA-256から決定し、再実行で対象がぶれないようにします。

## Security boundary

- fixtureは架空の安全な値だけを使用する
- secret assignment、email、secret-shaped tokenをvalidatorで拒否する
- prompt injection caseでも実秘密値を置かない
- judge/calibration artifactへraw prompt、raw answer、raw chunk、full contextを追加しない

## Non-goals

- 外部LLM judge APIの呼び出し
- semantic judgeをCI hard gateにすること
- DB migrationやevaluation result schema変更

## Merge order

1. PR #91を先にmergeする。
2. このstacked PRへ最新mainを通常mergeし、baseをmainへ変更する。
3. 後続PRで人間review UIを小さく接続する。
