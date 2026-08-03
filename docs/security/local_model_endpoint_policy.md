# Local model endpoint policy

## Purpose

RAGProject treats LM Studio and Ollama as local providers. Before this policy, their configured
URLs were used after only trimming a trailing slash. A server misconfiguration could therefore
send a question, retrieved evidence, embedding input, or planner payload to an external host while
the request was still classified as local and outside the external-provider PII gate.

RAG-65 adds a startup-time trust boundary. It does not alter prompts, retrieval profiles, Gold v2,
the Qwen3.5 9B model choice, databases, Qdrant collections, or Docker volumes.

## Policy

`LOCAL_MODEL_ENDPOINT_POLICY_ENABLED` defaults to `true`. When enabled, both `OLLAMA_URL` and
`LMSTUDIO_BASE_URL` must:

- use `http` or `https`;
- contain a valid host and port;
- contain no URL userinfo, query, or fragment;
- match an entry in `LOCAL_MODEL_ALLOWED_HOSTS` exactly after safe host normalization.

The default host set is `localhost`, IPv4/IPv6 loopback, `host.docker.internal`, `lmstudio`, and
`ollama`. Wildcards, suffix matches, URLs, and `host:port` values are rejected in the allowlist.
A separately managed LAN endpoint must be added as one exact host. The policy deliberately does
not perform DNS resolution or learn hosts from request data, retrieved chunks, model output, or
tool results.

Validation errors identify only the setting and policy requirement. They do not echo the URL,
userinfo, host, prompt, model input, PII, or secret.

## Coverage

The shared LM Studio URL is used by answer generation, LM Studio embeddings, the Agentic planner,
and the LLM tool planner. The Ollama URL is used by Ollama generation. Validating settings before
service startup protects every one of those transports without duplicating checks in each client.

External OpenAI, Anthropic, Gemini, NVIDIA, and Bedrock endpoints are intentionally outside this
local-host policy; Draft PR #134 applies the separate provider allowlist and PII egress gate to
those transports.

## Rollback and operations

In `local`, `ci`, and `test`, operators can temporarily set
`LOCAL_MODEL_ENDPOINT_POLICY_ENABLED=false` to restore legacy endpoint behavior. Outside those
environments the disable switch fails startup closed. Production recovery is to add only the
required exact host to `LOCAL_MODEL_ALLOWED_HOSTS`, not to disable the boundary.

There is no schema migration and rollback never requires deleting or resetting a database,
Qdrant collection, Docker volume, or model installation.

## Verification log

- base: GitHub `main` `96e5fc82ad2c253d000633ff9e9431e88998f74a`
- branch: `feature/local-model-endpoint-policy`
- focused endpoint and generation tests: `28 passed`
- backend full tests: `984 passed, 19 skipped, 3 known warnings`
- Ruff format and lint: pass
- mypy: `Success: no issues found in 272 source files`
- added-line secret, sensitive logger, shell execution, and DNS-resolution scan: no findings
- `git diff --check`: pass
- live LM Studio, Ollama, external provider, DB, Qdrant, and Docker volume mutation: not used

## Residual risk

- An operator can explicitly trust the wrong hostname; configuration access and review remain a
  privileged boundary.
- Exact hostname matching does not pin a DNS response. Network egress controls and DNS policy are
  still required for defense in depth.
- This policy prevents accidental local-provider egress classification. It does not replace the
  external-provider PII gate, prompt-injection gate, or shared request budget controls.
