param(
    [switch]$SkipTests,
    [string]$Python
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

$python = $Python
if (-not $python) {
    $python = Join-Path $root ".venv\Scripts\python.exe"
}
if (-not (Test-Path -LiteralPath $python) -and $python -ne "python") {
    $python = "python"
}

& $python --version
$compileTargets = @(
    "apps",
    "config",
    "core",
    "database",
    "dev_bootstrap",
    "runtime",
    "scripts",
    "skills",
    "tests",
    "trading_core",
    "utils",
    "main.py",
    "dev_up.py",
    "merge.py"
) | Where-Object { Test-Path -LiteralPath (Join-Path $root $_) }
& $python -m compileall -q $compileTargets
& $python scripts/check_complexity_governance.py --fail-on-critical
& $python scripts/check_cognitive_coherence.py --fail-on-critical
& $python scripts/check_core_contracts.py --fail-on-critical
& $python scripts/check_subsystem_constitution.py --fail-on-critical
& $python scripts/check_year34_readiness.py --fail-below 90

if (-not $SkipTests) {
    & $python -m unittest discover -s tests -p "test_*.py"
}

if (Get-Command ruff -ErrorAction SilentlyContinue) {
    & ruff check .
}

Write-Host "Quality gate passed."
