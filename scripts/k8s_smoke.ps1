param(
  [string]$Namespace = $(if ($env:K8S_NAMESPACE) { $env:K8S_NAMESPACE } else { "ragproject-local" }),
  [string]$Timeout = $(if ($env:K8S_SMOKE_TIMEOUT) { $env:K8S_SMOKE_TIMEOUT } else { "300s" })
)

$ErrorActionPreference = "Stop"

kubectl -n $Namespace rollout status statefulset/postgres --timeout=$Timeout
kubectl -n $Namespace rollout status statefulset/qdrant --timeout=$Timeout
kubectl -n $Namespace wait --for=condition=complete job/ragproject-migrate --timeout=$Timeout
kubectl -n $Namespace wait --for=condition=complete job/ragproject-seed --timeout=$Timeout
kubectl -n $Namespace rollout status deployment/backend --timeout=$Timeout
kubectl -n $Namespace rollout status deployment/worker --timeout=$Timeout
kubectl -n $Namespace rollout status deployment/frontend --timeout=$Timeout

kubectl -n $Namespace run ragproject-k8s-smoke `
  --rm `
  --attach `
  --restart=Never `
  --image=ragproject-backend:local `
  --image-pull-policy=Never `
  --command -- sh -c "python -m app.scripts.healthcheck http://backend:8000/ready database && python -m app.scripts.healthcheck http://qdrant:6333/healthz && python -c `"from urllib.request import urlopen; raise SystemExit(0 if urlopen('http://frontend:5173', timeout=3).status < 400 else 1)`""
