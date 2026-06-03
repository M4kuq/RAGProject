# deploy/aws Handoff

PR-44 does not implement AWS. This document separates local Kubernetes baseline work from future deploy/aws integration work.

## Branch Policy

Use a dedicated deploy/aws integration branch for cloud work. Keep it separate from Phase2.5 docs and local Kubernetes hardening.

Candidate follow-up:

```text
deploy/aws integration branch with S3 / Bedrock / RDS / ECS or EKS / OIDC follow-up PRs
```

## Why PR-44 Does Not Move To AWS

- PR-44 is final Phase2.5 hardening and demo documentation.
- PR-43 Kubernetes baseline is local kind/minikube only.
- AWS introduces production security, cost, IAM, networking, secrets, and compliance decisions that should be reviewed separately.
- Phase2.5 acceptance must remain runnable without cloud credentials or paid services.

## Candidate AWS Workstreams

| Area | Candidate direction | Notes |
|---|---|---|
| S3 Storage Adapter | Replace/supplement local upload storage with object storage | Requires object key policy, encryption, lifecycle, and presigned URL decisions. |
| Bedrock Provider Adapter | Add optional AWS-hosted generation/embedding adapter | Requires model allowlist, timeout/cost controls, and prompt/context export policy. |
| RDS | Move Postgres to managed RDS | Requires migrations, backup, encryption, subnet, and access policy. |
| Qdrant | Evaluate self-managed Qdrant on ECS/EKS or managed alternative | Requires persistence, backup, scaling, and network isolation. |
| ECS deploy | Container runtime for backend/worker/frontend | Simpler than EKS for first AWS path. |
| EKS deploy | Production Kubernetes follow-up from `k8s/local` | Requires Ingress/TLS, autoscaling, secrets, RBAC, and rollout policy. |
| OIDC / RBAC hardening | External identity and authorization model | Must preserve viewer/admin boundaries. |
| Secrets Manager | Replace local placeholder secrets | Never commit generated secret manifests. |
| WAF / NAT / private subnet | Network hardening | Later production hardening, not Phase2.5. |
| Observability | Cloud logs/metrics/traces | Must preserve redaction and safe trace policy. |

## Difference From PR-43 Kubernetes Baseline

| Topic | PR-43 local Kubernetes | deploy/aws follow-up |
|---|---|---|
| Target | kind/minikube local cluster | AWS account and cloud runtime |
| Secrets | placeholder-only local Secret template | Secrets Manager or equivalent |
| Storage | local PVCs | S3/RDS/Qdrant production storage decisions |
| Access | port-forward/local services | Ingress/load balancer/TLS/OIDC decisions |
| Cost | local only | cloud cost controls required |
| Security | local demo boundary | production IAM/network/compliance boundary |
| Cleanup | local data deletion warning | infrastructure lifecycle and backups |

## Required Security Decisions Before AWS

- Which evidence can leave the local runtime for provider calls.
- Whether raw image/OCR text/graph paths may be sent to external providers.
- How Context Budget, Evidence Pack, and Tool Result Compression apply to cloud providers.
- How secrets are injected without committing `.env`, kubeconfig, tokens, or generated secret files.
- How viewer/admin debug boundaries are enforced behind external identity.
- How logs and traces are redacted before cloud export.

## Out Of Scope For PR-44

- Terraform apply
- EKS provisioning
- ECS rollout
- S3 bucket creation
- Bedrock calls
- RDS provisioning
- OIDC setup
- WAF/NAT/private subnet setup
- remote MCP publication
- public tunnels
