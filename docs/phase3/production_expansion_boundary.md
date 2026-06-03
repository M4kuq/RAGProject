# Production Expansion Boundary

PR-45 does not implement production deployment. It keeps production expansion separate from local Phase2.5 and Graph-RAG design work.

## Boundary

Local runtime remains:

- Docker Compose for development and CI-adjacent checks
- `k8s/local` for kind/minikube local Kubernetes demo

Production expansion is future work for PR-54+ or a dedicated deploy/aws branch.

## Candidate Workstreams

| Area | Candidate direction | Boundary |
|---|---|---|
| External LLM Provider | Optional adapter for provider switching | Not required for local Graph-RAG. |
| Bedrock Provider | Candidate AWS-hosted provider | Requires explicit export policy. |
| S3 Storage Adapter | Optional object storage for uploads/artifacts | Local storage remains default. |
| RDS / managed DB | Managed PostgreSQL | Migration and backup policy needed. |
| Qdrant operations | Managed or self-hosted vector service | Persistence/backup/scaling decision needed. |
| ECS/EKS | Cloud container runtime | Separate from `k8s/local`. |
| OIDC / OAuth | External auth | Must preserve viewer/admin role boundary. |
| Secrets Manager | Runtime secret injection | No generated secret files committed. |
| WAF / NAT / private subnet | Network hardening | Later production hardening. |

## Local Kubernetes Difference

`k8s/local` is not a production EKS baseline. It uses local images, local PVCs, local ClusterIP services, and port-forward-first access. It does not define production ingress, TLS, autoscaling, cloud IAM, or cloud secret management.

## Deploy/AWS Separation

AWS work should use deploy/aws planning and follow-up PRs. Graph-RAG docs may describe provider/storage boundaries, but should not provision cloud resources.

## Provider Export Policy

Before external provider use, decide:

- whether graph path summaries can leave the local runtime
- whether OCR text or image regions can leave the local runtime
- how Context Budget, Evidence Pack, and Tool Result Compression minimize exports
- how logs and traces remain redacted
- how cost and timeout budgets are enforced

## Out Of Scope For PR-45

- Terraform apply
- AWS account resource creation
- S3 bucket creation
- Bedrock calls
- RDS provisioning
- ECS/EKS rollout
- OIDC setup
- public tunnels
- remote MCP publication
