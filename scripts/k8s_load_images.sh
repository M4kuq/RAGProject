#!/usr/bin/env sh
set -eu

runtime="${K8S_RUNTIME:-kind}"
kind_cluster="${KIND_CLUSTER_NAME:-kind}"
minikube_profile="${MINIKUBE_PROFILE:-minikube}"

docker build -f backend/Dockerfile --target backend -t ragproject-backend:local .
docker build -f backend/Dockerfile --target worker -t ragproject-worker:local .
docker build -f frontend/Dockerfile --target dev -t ragproject-frontend:local .

case "$runtime" in
  kind)
    kind load docker-image ragproject-backend:local ragproject-worker:local ragproject-frontend:local --name "$kind_cluster"
    ;;
  minikube)
    minikube -p "$minikube_profile" image load ragproject-backend:local
    minikube -p "$minikube_profile" image load ragproject-worker:local
    minikube -p "$minikube_profile" image load ragproject-frontend:local
    ;;
  *)
    echo "K8S_RUNTIME must be kind or minikube" >&2
    exit 2
    ;;
esac

echo "Loaded ragproject local images into $runtime"
