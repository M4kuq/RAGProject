# Local Kubernetes Baseline

PR-43 adds a local Kubernetes baseline for running the existing Compose-shaped
RAGProject stack on kind or minikube without changing `docker-compose.yml`.

The baseline is intentionally local and minimal. It covers:

- `frontend`
- `backend`
- `worker`
- `postgres`
- `qdrant`
- migration and seed Jobs
- ConfigMap and Secret template
- PVCs for Postgres, Qdrant, and uploaded files
- ClusterIP Services
- Deployment / StatefulSet manifests
- readiness and liveness probes
- resource requests and limits
- local image loading and smoke commands

It does not implement EKS, AWS, Terraform, S3, Bedrock, RDS, OIDC, production
Ingress, WAF, NAT, private subnet design, or production secret management.

## Files

```text
k8s/local/
  namespace.yaml
  kustomization.yaml
  secret.template.yaml
  configmap.yaml
  upload-pvc.yaml
  postgres.yaml
  qdrant.yaml
  migration-jobs.yaml
  backend.yaml
  worker.yaml
  frontend.yaml
```

`secret.template.yaml` is committed only with placeholder local values. Do not
commit real passwords, API keys, session secrets, database dumps, Qdrant data,
logs, or debug artifacts.

## Defaults

The local K8s profile uses fake embedding, fake rerank, and fake generation by
default. This keeps the baseline runnable without external API keys, GPU, model
downloads, LM Studio, Ollama, or cloud services.

Important defaults:

| Setting | Value |
|---|---|
| Namespace | `ragproject-local` |
| Backend image | `ragproject-backend:local` |
| Worker image | `ragproject-worker:local` |
| Frontend image | `ragproject-frontend:local` |
| Postgres service | `postgres:5432` |
| Qdrant service | `qdrant:6333` |
| Backend service | `backend:8000` |
| Frontend service | `frontend:5173` |
| Generation | `fake` |
| Embedding | `fake` |
| Rerank | `fake` |

## Build And Load Images

Build the local Docker images and load them into kind:

```powershell
scripts\k8s_load_images.ps1 -Runtime kind -KindClusterName kind
```

```sh
K8S_RUNTIME=kind KIND_CLUSTER_NAME=kind sh scripts/k8s_load_images.sh
```

For minikube:

```powershell
scripts\k8s_load_images.ps1 -Runtime minikube -MinikubeProfile minikube
```

```sh
K8S_RUNTIME=minikube MINIKUBE_PROFILE=minikube sh scripts/k8s_load_images.sh
```

The manifests use `imagePullPolicy: Never` for local app images, so image
loading is required before deployment.

## Deploy

Validate the manifest set first:

```powershell
python scripts\validate_k8s_manifests.py
kubectl kustomize k8s/local
```

```sh
python scripts/validate_k8s_manifests.py
kubectl kustomize k8s/local
```

Apply the local stack:

```powershell
kubectl apply -k k8s/local
```

```sh
kubectl apply -k k8s/local
```

Wait for core resources:

```powershell
kubectl -n ragproject-local rollout status statefulset/postgres --timeout=300s
kubectl -n ragproject-local rollout status statefulset/qdrant --timeout=300s
kubectl -n ragproject-local wait --for=condition=complete job/ragproject-migrate --timeout=300s
kubectl -n ragproject-local wait --for=condition=complete job/ragproject-seed --timeout=300s
kubectl -n ragproject-local rollout status deployment/backend --timeout=300s
kubectl -n ragproject-local rollout status deployment/worker --timeout=300s
kubectl -n ragproject-local rollout status deployment/frontend --timeout=300s
```

The helper smoke script runs the same waits plus internal backend, Qdrant, and
frontend HTTP checks:

```powershell
scripts\k8s_smoke.ps1
```

```sh
sh scripts/k8s_smoke.sh
```

## Open The App

Forward the frontend service:

```powershell
kubectl -n ragproject-local port-forward svc/frontend 5173:5173
```

```sh
kubectl -n ragproject-local port-forward svc/frontend 5173:5173
```

Open `http://localhost:5173`. The Vite dev server proxies `/api/v1`, `/health`,
and `/ready` to the `backend` service inside the cluster.

To inspect backend directly:

```powershell
kubectl -n ragproject-local port-forward svc/backend 8000:8000
Invoke-RestMethod http://localhost:8000/health
Invoke-RestMethod http://localhost:8000/ready
```

```sh
kubectl -n ragproject-local port-forward svc/backend 8000:8000
curl -fsS http://localhost:8000/health
curl -fsS http://localhost:8000/ready
```

## Secret Handling

`k8s/local/secret.template.yaml` is an example Secret with local placeholders.
It exists so `kubectl apply -k k8s/local` works in an isolated local cluster.

Rules:

- Do not commit real Kubernetes Secrets.
- Do not copy `.env` values into docs, manifests, logs, or PR comments.
- Do not put production credentials into `secret.template.yaml`.
- For any shared cluster, create a separate untracked Secret manifest or use a
  real secret manager outside this PR scope.

The PR-43 baseline does not add production secret management.

## Cleanup

Deleting this kustomization deletes the local StatefulSets, Deployments, Jobs,
Services, ConfigMap, Secret, and PVCs in the namespace. That removes local
Postgres/Qdrant/upload data created by this K8s baseline.

Use only when you accept local K8s data deletion:

```powershell
kubectl delete -k k8s/local
```

```sh
kubectl delete -k k8s/local
```

## Validation

PR-43 adds:

- `scripts/validate_k8s_manifests.py`
- `.github/workflows/k8s-manifest-ci.yml`

The validation checks that local manifests include the required components,
probes, resources, Services, PVCs, local image policy, and safe Secret template
shape. It also checks that the local K8s baseline does not introduce Ingress,
EKS, AWS, Terraform, RDS, OIDC, or obvious secret-like values.
