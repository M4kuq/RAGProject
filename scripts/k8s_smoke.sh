#!/usr/bin/env sh
set -eu

namespace="${K8S_NAMESPACE:-ragproject-local}"
timeout="${K8S_SMOKE_TIMEOUT:-300s}"
smoke_pod="${K8S_SMOKE_POD:-ragproject-k8s-smoke}"

kubectl -n "$namespace" rollout status statefulset/postgres --timeout="$timeout"
kubectl -n "$namespace" rollout status statefulset/qdrant --timeout="$timeout"
kubectl -n "$namespace" wait --for=condition=complete job/ragproject-migrate --timeout="$timeout"
kubectl -n "$namespace" wait --for=condition=complete job/ragproject-seed --timeout="$timeout"
kubectl -n "$namespace" rollout status deployment/backend --timeout="$timeout"
kubectl -n "$namespace" rollout status deployment/worker --timeout="$timeout"
kubectl -n "$namespace" rollout status deployment/frontend --timeout="$timeout"

cleanup() {
  kubectl -n "$namespace" delete pod "$smoke_pod" --ignore-not-found=true --wait=true >/dev/null 2>&1 || true
}
trap cleanup EXIT

cleanup
kubectl -n "$namespace" run "$smoke_pod" \
  --restart=Never \
  --image=ragproject-backend:local \
  --image-pull-policy=Never \
  --command -- sh -c "python -m app.scripts.healthcheck http://backend:8000/ready database && python -m app.scripts.healthcheck http://qdrant:6333/healthz && python -m app.scripts.healthcheck http://frontend:5173"

if ! kubectl -n "$namespace" wait --for=jsonpath='{.status.phase}'=Succeeded pod/"$smoke_pod" --timeout="$timeout"; then
  kubectl -n "$namespace" logs "$smoke_pod" || true
  kubectl -n "$namespace" describe pod "$smoke_pod" || true
  exit 1
fi
kubectl -n "$namespace" logs "$smoke_pod" || true
echo "K8s smoke passed"
