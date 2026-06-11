param(
  [ValidateSet("kind", "minikube")]
  [string]$Runtime = $(if ($env:K8S_RUNTIME) { $env:K8S_RUNTIME } else { "kind" }),
  [string]$KindClusterName = $(if ($env:KIND_CLUSTER_NAME) { $env:KIND_CLUSTER_NAME } else { "kind" }),
  [string]$MinikubeProfile = $(if ($env:MINIKUBE_PROFILE) { $env:MINIKUBE_PROFILE } else { "minikube" })
)

$ErrorActionPreference = "Stop"

docker build -f backend/Dockerfile --target backend -t ragproject-backend:local .
docker build -f backend/Dockerfile --target worker -t ragproject-worker:local .
docker build -f frontend/Dockerfile --target dev -t ragproject-frontend:local .

if ($Runtime -eq "kind") {
  kind load docker-image ragproject-backend:local ragproject-worker:local ragproject-frontend:local --name $KindClusterName
} else {
  minikube -p $MinikubeProfile image load ragproject-backend:local
  minikube -p $MinikubeProfile image load ragproject-worker:local
  minikube -p $MinikubeProfile image load ragproject-frontend:local
}

$missing = @()
foreach ($image in @("ragproject-backend:local", "ragproject-worker:local", "ragproject-frontend:local")) {
  if (-not (docker images -q $image)) {
    $missing += $image
  }
}
if ($missing.Count -gt 0) {
  Write-Error "ERROR: expected local images are missing after build/load: $($missing -join ' '). Ensure 'docker build' succeeded for each ragproject-*:local target."
  exit 1
}

Write-Host "Loaded ragproject local images into $Runtime"
