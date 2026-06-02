param(
  [string]$Namespace = $(if ($env:K8S_NAMESPACE) { $env:K8S_NAMESPACE } else { "ragproject-local" }),
  [string]$Timeout = $(if ($env:K8S_SMOKE_TIMEOUT) { $env:K8S_SMOKE_TIMEOUT } else { "300s" })
)

$ErrorActionPreference = "Stop"
$SmokePod = $(if ($env:K8S_SMOKE_POD) { $env:K8S_SMOKE_POD } else { "ragproject-k8s-smoke" })

kubectl -n $Namespace rollout status statefulset/postgres --timeout=$Timeout
kubectl -n $Namespace rollout status statefulset/qdrant --timeout=$Timeout
kubectl -n $Namespace wait --for=condition=complete job/ragproject-migrate --timeout=$Timeout
kubectl -n $Namespace wait --for=condition=complete job/ragproject-seed --timeout=$Timeout
kubectl -n $Namespace rollout status deployment/backend --timeout=$Timeout
kubectl -n $Namespace rollout status deployment/worker --timeout=$Timeout
kubectl -n $Namespace rollout status deployment/frontend --timeout=$Timeout

kubectl -n $Namespace delete pod $SmokePod --ignore-not-found=true --wait=true

try {
  kubectl -n $Namespace run $SmokePod `
    --restart=Never `
    --image=ragproject-backend:local `
    --image-pull-policy=Never `
    --command -- sh -c "python -m app.scripts.healthcheck http://backend:8000/ready database && python -m app.scripts.healthcheck http://qdrant:6333/healthz && python -m app.scripts.healthcheck http://frontend:5173"
  kubectl -n $Namespace wait "--for=jsonpath={.status.phase}=Succeeded" "pod/$SmokePod" --timeout=$Timeout
  kubectl -n $Namespace logs $SmokePod
  Write-Host "K8s smoke passed"
} catch {
  kubectl -n $Namespace logs $SmokePod
  kubectl -n $Namespace describe pod $SmokePod
  throw
} finally {
  kubectl -n $Namespace delete pod $SmokePod --ignore-not-found=true --wait=true
}
