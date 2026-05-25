if ([string]::IsNullOrWhiteSpace($env:OPENAI_API_KEY)) {
  Write-Error "OPENAI_API_KEY is required. This script does not use fake generation."
  exit 1
}

$modelName = $env:GENERATION_MODEL_NAME
if ([string]::IsNullOrWhiteSpace($modelName)) {
  $modelName = "gpt-5.5"
}

$baseUrl = $env:OPENAI_BASE_URL
if ([string]::IsNullOrWhiteSpace($baseUrl)) {
  $baseUrl = "https://api.openai.com/v1"
}

$timeoutSeconds = $env:OPENAI_TIMEOUT_SECONDS
if ([string]::IsNullOrWhiteSpace($timeoutSeconds)) {
  $timeoutSeconds = "30"
}

docker compose -f docker-compose.ci.yml build backend-test
if ($LASTEXITCODE -ne 0) {
  exit $LASTEXITCODE
}

docker run --rm `
  -e RUN_OPENAI_GENERATION_TEST=true `
  -e OPENAI_API_KEY `
  -e GENERATION_MODEL_NAME=$modelName `
  -e OPENAI_BASE_URL=$baseUrl `
  -e OPENAI_TIMEOUT_SECONDS=$timeoutSeconds `
  ragproject-ci-backend-test `
  pytest tests/test_openai_generation.py -k real_api -q

exit $LASTEXITCODE
