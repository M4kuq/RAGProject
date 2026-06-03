# Kubernetes Baseline For Phase2.5

This file is the Phase2.5 entrypoint for the PR-43 local Kubernetes baseline. The detailed manifest reference remains [kubernetes_local_baseline.md](kubernetes_local_baseline.md).

## Scope

Implemented:

- local kind/minikube manifests under `k8s/local`
- frontend, backend, worker, Postgres, Qdrant
- migration and seed Jobs
- ConfigMap and placeholder-only Secret template
- PVCs for local Postgres, Qdrant, and upload storage
- ClusterIP Services
- readiness/liveness probes and resource requests/limits
- local image loading scripts
- local K8s smoke scripts
- manifest validator and CI workflow

Not implemented:

- EKS or production AWS
- Terraform
- Helm production charting
- Ingress/TLS
- production autoscaling
- S3, Bedrock, RDS, OIDC, WAF, NAT, private subnets
- production Secrets Manager integration
- remote MCP or network tunnels

## Path Note

The current repository uses:

```text
k8s/local
```

There is no committed `deploy/k8s` directory at the time of PR-44 inspection. AWS deployment work should be documented under deploy/aws follow-up planning, not added to this PR.

## Local Demo Commands

Validate without applying:

```powershell
python scripts\validate_k8s_manifests.py
kubectl kustomize k8s/local
kubectl apply --dry-run=client -k k8s/local
```

```sh
python scripts/validate_k8s_manifests.py
kubectl kustomize k8s/local
kubectl apply --dry-run=client -k k8s/local
```

Apply to a local cluster only:

```powershell
scripts\k8s_load_images.ps1 -Runtime kind
kubectl apply -k k8s/local
scripts\k8s_smoke.ps1
kubectl -n ragproject-local port-forward svc/frontend 5173:5173
```

```sh
K8S_RUNTIME=kind sh scripts/k8s_load_images.sh
kubectl apply -k k8s/local
sh scripts/k8s_smoke.sh
kubectl -n ragproject-local port-forward svc/frontend 5173:5173
```

Open `http://localhost:5173` after the port-forward starts.

## Destructive Command Warning

Do not run these commands unless you explicitly accept local data deletion:

```text
docker compose down -v
kubectl delete namespace ragproject-local
kubectl delete -k k8s/local
```

Phase2.5 smoke wrappers do not run these commands by default.

## Secret Handling

- Do not commit real Kubernetes Secrets.
- Do not commit kubeconfig.
- Do not copy `.env` values into manifests, docs, logs, PR comments, or artifacts.
- Keep `secret.template.yaml` placeholder-only.
- Use a separate untracked Secret or a real secret manager for non-local clusters.

## Relationship To Docker Compose

Docker Compose remains the primary local development path. `k8s/local` is a local-cluster demo and manifest hardening path. Both should remain fake/local-capable and should not require external API keys by default.
