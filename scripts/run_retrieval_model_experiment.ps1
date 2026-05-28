param(
    [string]$Manifest = "app\experiments\manifests\phase2_retrieval_models.example.json",
    [ValidateSet("validate", "dry-run", "local")]
    [string]$Mode = "dry-run",
    [ValidateSet("never", "if-cached", "opt-in-download")]
    [string]$DownloadPolicy = "if-cached",
    [int]$TimeoutSeconds = 600,
    [string]$OutputJson = "..\artifacts\experiments\retrieval_model_comparison.json",
    [string]$OutputMd = "..\artifacts\experiments\retrieval_model_comparison.md",
    [switch]$SkipSeedIndexing
)

$ErrorActionPreference = "Stop"
$RepoRoot = Resolve-Path (Join-Path $PSScriptRoot "..")
$BackendDir = Join-Path $RepoRoot "backend"

Push-Location $BackendDir
try {
    $argsList = @(
        "run",
        "--extra",
        "experiments",
        "python",
        "-m",
        "app.experiments.run_retrieval_model_experiment",
        "--manifest",
        $Manifest,
        "--mode",
        $Mode,
        "--download-policy",
        $DownloadPolicy,
        "--timeout-seconds",
        $TimeoutSeconds,
        "--output-json",
        $OutputJson,
        "--output-md",
        $OutputMd
    )
    if ($SkipSeedIndexing) {
        $argsList += "--skip-seed-indexing"
    }
    uv @argsList
}
finally {
    Pop-Location
}
