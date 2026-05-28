param(
    [string]$Dataset = "phase2_strategy_smoke",
    [string]$Strategies = "dense,hybrid,agentic_router",
    [ValidateSet("local")]
    [string]$Mode = "local",
    [ValidateSet("warn", "fail")]
    [string]$ThresholdMode = "warn",
    [int]$CaseLimit = 5,
    [int]$TimeoutSeconds = 300,
    [string]$OutputJson = "..\artifacts\retrieval_eval_smoke.json",
    [string]$OutputMd = "..\artifacts\retrieval_eval_smoke.md"
)

$ErrorActionPreference = "Stop"
$RepoRoot = Resolve-Path (Join-Path $PSScriptRoot "..")
$BackendDir = Join-Path $RepoRoot "backend"

Push-Location $BackendDir
try {
    uv run --with "sentence-transformers>=2.7.0,<4" python -m app.scripts.retrieval_eval_smoke `
        --dataset $Dataset `
        --strategies $Strategies `
        --mode $Mode `
        --threshold-mode $ThresholdMode `
        --case-limit $CaseLimit `
        --timeout-seconds $TimeoutSeconds `
        --output-json $OutputJson `
        --output-md $OutputMd
}
finally {
    Pop-Location
}
