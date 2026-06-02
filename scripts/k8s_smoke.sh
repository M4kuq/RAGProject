#!/usr/bin/env sh
set -eu

namespace="${K8S_NAMESPACE:-ragproject-local}"
timeout="${K8S_SMOKE_TIMEOUT:-300s}"

kubectl -n "$namespace" rollout status statefulset/postgres --timeout="$timeout"
kubectl -n "$namespace" rollout status statefulset/qdrant --timeout="$timeout"
kubectl -n "$namespace" wait --for=condition=complete job/ragproject-migrate --timeout="$timeout"
kubectl -n "$namespace" wait --for=condition=complete job/ragproject-seed --timeout="$timeout"
kubectl -n "$namespace" rollout status deployment/backend --timeout="$timeout"
kubectl -n "$namespace" rollout status deployment/worker --timeout="$timeout"
kubectl -n "$namespace" rollout status deployment/frontend --timeout="$timeout"

kubectl -n "$namespace" run ragproject-k8s-smoke \
  --rm \
  --attach \
  --restart=Never \
  --image=ragproject-backend:local \
  --image-pull-policy=Never \
  --command -- sh -c "python -m app.scripts.healthcheck http://backend:8000/ready database && python -m app.scripts.healthcheck http://qdrant:6333/healthz && python -c \"from urllib.request import urlopen; raise SystemExit(0 if urlopen('http://frontend:5173', timeout=3).status < 400 else 1)\""
