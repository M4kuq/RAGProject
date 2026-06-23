# Generation Usage Metadata

`/api/v1/rag/ask` can include an optional `generation` block for a newly generated
answer:

```json
{
  "provider": "openai",
  "model": "gpt-5.5",
  "input_tokens": 1234,
  "output_tokens": 321,
  "total_tokens": 1555,
  "estimated_cost_usd": 0.0047525,
  "latency_ms": 842
}
```

The block is response-only metadata. It is not written to the database in B1.
Replay responses set `generation` to `null` because no new LLM call is made.

The block must contain only numeric usage/cost/latency values and provider/model
labels. It must not include raw prompts, raw answers, retrieved context, chunk
text, request headers, API keys, or other secrets. The answer text remains only in
the existing `assistant_message.content` field.

`estimated_cost_usd` is an estimate, not a billing record. Rates are USD per 1M
tokens and can be changed with `GENERATION_PRICING_OVERRIDES`:

```env
GENERATION_PRICING_OVERRIDES={"openai:gpt-5.5":{"input_per_1m":1.25,"output_per_1m":10.0}}
```

Unknown `(provider, model)` pairs, missing usage, or invalid override entries
return `estimated_cost_usd: null` and do not fail the RAG response.
