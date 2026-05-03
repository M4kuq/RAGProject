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
Invoke-Compose @("-f", "docker-compose.ci.yml", "build", "backend", "worker", "frontend-build", "backend-test", "frontend-test", "smoke")
Invoke-Compose @("-f", "docker-compose.ci.yml", "run", "--rm", "backend-test")
Invoke-Compose @("-f", "docker-compose.ci.yml", "run", "--rm", "frontend-test")
if ($Smoke) {
  Invoke-Compose @("-f", "docker-compose.ci.yml", "run", "--rm", "frontend-build")
  Invoke-Compose @("-f", "docker-compose.ci.yml", "up", "-d", "backend", "worker")
  $workerOk = $false
  for ($attempt = 1; $attempt -le 24; $attempt++) {
    $workerId = docker compose -f docker-compose.ci.yml ps -q worker
    if ($LASTEXITCODE -eq 0 -and -not [string]::IsNullOrWhiteSpace($workerId)) {
      $workerRunning = docker inspect -f "{{.State.Running}}" $workerId
      $workerHealth = docker inspect -f "{{.State.Health.Status}}" $workerId
      if ($LASTEXITCODE -eq 0 -and $workerRunning.Trim() -eq "true" -and $workerHealth.Trim() -eq "healthy") {
        $workerOk = $true
        break
      }
    }
    Write-Host "worker not healthy yet; retry $attempt/24"
    Start-Sleep -Seconds 5
  }
  if (-not $workerOk) {
    exit 1
  }
  $smokeOk = $false
  for ($attempt = 1; $attempt -le 24; $attempt++) {
    docker compose -f docker-compose.ci.yml run --rm --no-deps smoke
    if ($LASTEXITCODE -eq 0) {
      $smokeOk = $true
      break
    }
    Write-Host "compose smoke not ready yet; retry $attempt/24"
    Start-Sleep -Seconds 5
  }
  if (-not $smokeOk) {
    exit 1
  }
}
