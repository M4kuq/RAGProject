param(
  [switch]$Smoke
)

function Invoke-Compose {
  docker compose @args
  if ($LASTEXITCODE -ne 0) {
    exit $LASTEXITCODE
  }
}

Invoke-Compose @("-f", "docker-compose.ci.yml", "config")
Invoke-Compose @("-f", "docker-compose.ci.yml", "build", "backend", "worker", "frontend", "backend-test", "frontend-test", "smoke")
Invoke-Compose @("-f", "docker-compose.ci.yml", "run", "--rm", "backend-test")
Invoke-Compose @("-f", "docker-compose.ci.yml", "run", "--rm", "frontend-test")
if ($Smoke) {
  Invoke-Compose @("-f", "docker-compose.ci.yml", "up", "--abort-on-container-exit", "--exit-code-from", "smoke", "smoke")
}
