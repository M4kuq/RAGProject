# Context Engineering Known Limitations

These limitations are intentional for Phase2.5 and should not be treated as PR-44 defects unless a requirement says otherwise.

## Auto / Orchestrator

- Auto is retrieval-only tool calling.
- Upload, archive, approve, retry, admin mutation, write, deployment, and external operation tools are intentionally not exposed to the orchestrator.
- Auto depends on the configured local/fake/provider generation path. Provider outages should be handled as environment issues, not by adding provider-specific code in PR-44.
- Auto strategy summaries are safe summaries and do not expose raw planner payloads.

## Context Budget

- Token estimates are heuristic: `ceil(char_count / 4)`.
- The estimate is not tokenizer-accurate and does not reserve per-provider tokenizer behavior.
- Context Budget selects or drops item refs. It does not rewrite the retrieval algorithm.
- Dropped items are represented by safe refs and reason counts only.

## Evidence Pack

- Evidence Pack is deterministic compression first.
- LLM summarization for final evidence compression is not required.
- Evidence Pack compresses final retrieved context after Context Budget.
- Evidence Pack is separate from Tool Result Compression.
- Persisted traces do not include generated evidence text, raw chunk text, or full context.

## Tool Result Compression

- Tool Result Compression is for intermediate orchestrator tool outputs.
- It is not the final evidence compression layer.
- Planner-visible snippets are bounded/redacted, but persisted traces still avoid snippets.
- Repeated, duplicate, same-chunk, low-score, or oversized results may be dropped before planner visibility.

## External Context Engineering Libraries

- Headroom is not integrated.
- RTK is not integrated.
- LeanCTX is not integrated.
- PR-44 documents the local deterministic baseline and handoff points only.

## Kubernetes

- Kubernetes is a local baseline for kind/minikube.
- It is not production EKS.
- Helm, production Ingress, TLS, autoscaling, production load balancers, and production secret management are not implemented.
- NodePort and port-forward examples are local-only.
- Cleanup commands can delete local K8s data and are not run automatically by Phase2.5 smoke wrappers.

## AWS / Production Deploy

- AWS, S3, Bedrock, RDS, OIDC, Secrets Manager, WAF, NAT, private subnets, Terraform, and production EKS/ECS rollout are separated into deploy/aws follow-up work.
- PR-44 does not provision cloud resources.
- PR-44 does not create remote MCP, tunnels, public endpoints, or external operation agents.

## Phase3+

- Graph-RAG is Phase3 or later.
- Entity and relation extraction are Phase3 or later.
- Graph-aware routing, graph citations, and graph-vector hybrid evidence are Phase3 or later.
- OCR and PaddleOCR are Phase3 or later.
- Image upload and multimodal citation UI are Phase3 or later.
- Online evaluation, A/B testing, and alerting are Phase3 or later.

## Security Boundary

The limitation is strict by design: docs, logs, artifacts, UI, MCP output, and safe trace JSON must not contain raw prompt, full context, raw chunk text, PII, token values, secrets, `.env` values, kubeconfig, raw tool payloads, or local data dumps.
