# LLM Paper Corpus Demo Data

RAG demo 用の LLM 論文 corpus は次の seed data file に置く。

```text
backend/app/seed_data/llm_paper_corpus.md
```

この corpus は 2026-05-22 時点の検索結果と著名論文リストをもとに作成した。Transformer、GPT、BERT、RAG、RLHF、instruction tuning、reasoning、agents、multimodal LLM、code LLM、efficient inference、2025〜2026 の新しめの technical report / survey を含む。各 entry は公開論文への source URL、概要、技術内容、理念を短くまとめる。

`docker compose run --rm seed` を実行すると、`LLM Paper Corpus for RAG Demo` という logical document として投入される。seed は論文ごとに chunk を分けるため、RAG search / ask で個別論文を拾いやすい。

## Suggested Questions

1. What is the core idea of Attention Is All You Need?
2. How did GPT-3 change few-shot learning?
3. What is the difference between GPT-3 and InstructGPT?
4. Which papers introduced RAG, Self-RAG, and GraphRAG?
5. How do DeepSeek-R1 and Kimi k1.5 use reinforcement learning for reasoning?
6. What does Qwen2.5-VL focus on?
7. Which papers are useful for understanding RAG evaluation?
8. Which papers are important for code generation benchmarks?
9. Why are FlashAttention and vLLM important for LLM deployment?
10. What is the difference between Chain-of-Thought, ReAct, and Tree of Thoughts?

## Notes

- 論文本文や abstract の長い引用は含めない。
- source URL と短い独自要約だけを含める。
- credential、token、private data、`.env` の値は含めない。
- 最新性が重要な確認では、追加の検索で論文リストを更新する。
