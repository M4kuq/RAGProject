param(
  [string]$Model = "lmstudio-community/Qwen3.5-9B-GGUF:Q4_K_M",
  [string]$BaseUrl = "http://host.docker.internal:1234/v1"
)

docker compose -f docker-compose.ci.yml build backend-test
if ($LASTEXITCODE -ne 0) {
  exit $LASTEXITCODE
}

docker run --rm `
  --add-host=host.docker.internal:host-gateway `
  -e RUN_LMSTUDIO_GENERATION_TEST=true `
  -e LMSTUDIO_API_KEY=lm-studio `
  -e LMSTUDIO_BASE_URL=$BaseUrl `
  -e LMSTUDIO_TIMEOUT_SECONDS=60 `
  -e GENERATION_MODEL_NAME=$Model `
  ragproject-ci-backend-test `
  pytest tests/test_lmstudio_generation.py -k local_server -q

exit $LASTEXITCODE
