param(
    [switch]$Force
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$envPath = Join-Path $root ".env"

if ((Test-Path -LiteralPath $envPath) -and -not $Force) {
    throw ".env already exists. Re-run with -Force to overwrite it."
}

$rng = [System.Security.Cryptography.RandomNumberGenerator]::Create()

function New-HexSecret {
    param([int]$Bytes = 32)
    $buffer = New-Object byte[] $Bytes
    $rng.GetBytes($buffer)
    return -join ($buffer | ForEach-Object { $_.ToString("x2") })
}

$secret = New-HexSecret 32
$jwtSecret = New-HexSecret 32

$content = @(
    "# Local development overrides.",
    "CONFIG_PROFILE=development",
    "FLASK_ENV=development",
    "APP_ENV=development",
    "DEBUG=0",
    "SECRET_KEY=$secret",
    "JWT_SECRET_KEY=$jwtSecret",
    "ALLOW_DEV_BACKDOOR=0",
    "FAIL_FAST_STARTUP=0",
    "",
    "# Lightweight local defaults: SQLite + file fallback, no Redis requirement.",
    "DATABASE_URL=",
    "DB_AUTO_CREATE_TABLES=1",
    "STATE_BACKEND=file",
    "STATE_ALLOW_MEMORY_FALLBACK=1",
    "STATE_BACKEND_FAIL_FAST=0",
    "REDIS_URL=",
    "REDIS_EVENT_BUS_REQUIRED=0",
    "TRADING_FILE_IPC_FALLBACK_ENABLED=1",
    "TRADING_STATUS_FILE_FALLBACK_ENABLED=1",
    "CORE_HEARTBEAT_FILE_FALLBACK_ENABLED=1",
    "",
    "# Experimental/remote integrations stay opt-in.",
    "ENABLE_TRADING_SKILL=1",
    "ENABLE_KNOWLEDGE_SKILL=0",
    "ENABLE_TRADING_ORCHESTRATOR=0",
    "ENABLE_VOICE=0",
    "ENABLE_STREAM=0",
    "LLM_ALLOW_NO_API_KEY_IN_DEV=1",
    "LLM_REQUIRE_PRIMARY_API_KEY=0",
    "WEB_FORCE_HTTPS=0",
    "WEB_REVERSE_PROXY_ENABLED=0",
    "WEB_ALLOWED_HOSTS=localhost,127.0.0.1",
    "CORS_ORIGINS=http://localhost:5000",
    "",
    "# Self-deploy installs are intentionally non-hashed for local dev.",
    "SELF_DEPLOY_REQUIREMENTS_FILE=requirements.in",
    "SELF_DEPLOY_REQUIRE_HASHES=0"
)

[System.IO.File]::WriteAllText(
    $envPath,
    ($content -join [Environment]::NewLine) + [Environment]::NewLine,
    [System.Text.UTF8Encoding]::new($false)
)

Write-Host "Wrote $envPath"
