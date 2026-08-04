# Prompt injection security gate Phase2

## 結論

RAG-31 Phase2は、retrieved chunk poisoning、難読化prompt injection、
Agentic tool-result contamination、将来のFlashからPlusへのcost attackを、
通常RAG精度と分離した14件のsynthetic fixtureで評価する。

決定論的controlでは `observe_only` の境界bypassが11/14 (78.6%)、
`block_user_quarantine_context` が0/14 (0%)で、差は-78.6ポイントだった。
Qwen3.5 9Bのgeneration 8件では両profileともattack success 0、
clean utility 100%だったため、LLM実測上の厳密な改善は主張しない。
既定値は変更せず、Phase2 candidateとしてreviewする。

## Scopeと復帰点

Phase1の復帰点はDraft PR #133である。Phase2はそのheadからstackした独立branchで、
PR #133、PII PR #134、Gold v2、通常精度profile、DB、Qdrant、Docker volumeを変更しない。
設定を `RAG_INJECTION_POLICY=observe_only` に戻すとPhase2の強制block/quarantineを停止できる。

## Trust boundaryと対策

| Boundary | 攻撃 | Phase2 control |
|---|---|---|
| userからretrieval前 | Unicode、Base64、多言語の複合命令 | strongest policyでは検索前に422でfail closed |
| retrieved evidenceからgeneration | poisoned chunk | 命令ではなくuntrusted dataとして検査・隔離 |
| tool resultからLLM planner | planner誘導、tool強制 | raw snippetをplanner payloadへ渡さず集計理由だけを渡す |
| cascade decision | Plus強制、budget bypass | trusted policy、eligibility、request/user/day budgetをすべて要求 |

Base64 decodeは入力20,000文字、深さ2、候補8件、decoded 4,000 byteに制限し、
UnicodeはNFKC後にformat controlを除去する。外部decoderや新規production dependencyは使わない。
future cascade guardはdeny-by-defaultの純粋関数で、まだprovider routingへ接続しない。

## Fixtureとgate

fixtureは14件で、generation 8、tool planner 3、cascade 3。
retrieved poisoning、Base64、Unicode、多言語、planner contamination、cost escalationを含む。
実データ、PII、secret、system promptは含めず、fixture loaderでも禁止形状を拒否する。

artifactとactivity logへ保存するのはcase hash、attack family、boundary、reason code、
boolean metric、aggregate、latency、token、source fingerprintだけである。
raw question、chunk、tool result、answer、canary、factは保存しない。

promotion gateはattack success 0、clean utility非悪化、boundary coverage 100%、
pipeline failure 0を必須とする。0/14の片側exact 95%上限は約19.3%であり、
このfixtureを実運用全体の安全率として公開しない。

## 実測履歴の扱い

最終control run `20260803T070037Z-2cc65b26`ではbaseline 11/14、candidate 0/14、
clean utilityとboundary coverageは両方100%だった。
最終Qwen run `20260803T070126Z-bba01705`ではbaseline/candidateのp95は
1,557/1,526 ms、pipeline failure 0、resolved modelは `qwen/qwen3.5-9b` だった。
この2 runのartifactとactivity logでraw fixture文字列hitは0件だった。

model未loadのrun、文字列一致biasを検出したrun、再測定runを削除せずappend-onlyで保持する。
文字列一致biasはsynthetic factを安定した識別子へ変更し、攻撃検出ロジックとは分離した。
LM Studio実測はexact model `qwen/qwen3.5-9b`、reasoning off、temperature 0、
max output 512で行い、run後はこのタスクがloadしたinstanceだけをunloadする。

## Local run

```powershell
python -m app.scripts.run_prompt_injection_security_gate_phase2 --git-sha <current-sha>
python -m app.scripts.run_prompt_injection_security_qwen_phase2 `
  --confirm-local-runtime --git-sha <current-sha>
```

## 制約と次の対策

regexと有限decodeは既知難読化への回帰対策で、未知semantic injectionの完全防御ではない。
次はauthenticated public endpointの共有rate limit、日次budget、circuit breakerを実装し、
その後にingest provenance/quarantine、MCP toolのdeny-by-default権限を評価する。
通常精度の改善率とsecurity gateは混ぜない。

## 一次資料

- [OWASP LLM01:2025 Prompt Injection](https://genai.owasp.org/llmrisk/llm01-prompt-injection/)
- [OWASP LLM10:2025 Unbounded Consumption](https://genai.owasp.org/llmrisk/llm102025-unbounded-consumption/)
- [NIST AI 100-2e2025](https://doi.org/10.6028/NIST.AI.100-2e2025)
- [NIST AI Agent Hijacking Evaluations](https://www.nist.gov/news-events/news/2025/01/technical-blog-strengthening-ai-agent-hijacking-evaluations)
- [LM Studio REST API](https://lmstudio.ai/docs/developer/rest)
