param([switch]$MigrateOnly)

$ErrorActionPreference = 'Stop'
$mobileDir = $PSScriptRoot
$backendDir = Join-Path (Split-Path $mobileDir -Parent) 'backend'
$python = Join-Path $backendDir '.venv/Scripts/python.exe'
if (-not (Test-Path -LiteralPath $python)) {
    throw 'Backend .venv is missing. Set up the existing backend environment first.'
}
$dbDir = Join-Path $mobileDir '.local-demo'
New-Item -ItemType Directory -Force -Path $dbDir | Out-Null
$dbPath = (Join-Path $dbDir 'uf1_uf2.db').Replace('\', '/')
$settings = @{
    DATABASE_URL = "sqlite+aiosqlite:///$dbPath"
    ENVIRONMENT = 'development'
    ENABLE_CALENDAR_SYNC_WORKER = 'false'
    CALENDAR_PROVIDER_MODE = 'fake'
}
$previous = @{}
foreach ($name in $settings.Keys) {
    $previous[$name] = [Environment]::GetEnvironmentVariable($name, 'Process')
    [Environment]::SetEnvironmentVariable($name, $settings[$name], 'Process')
}
Push-Location $backendDir
try {
    & $python -m alembic upgrade head
    if ($LASTEXITCODE -ne 0) { throw 'Database migration failed.' }
    & $python -m alembic current
    if ($LASTEXITCODE -ne 0) { throw 'Cannot read database revision.' }
    if (-not $MigrateOnly) {
        & $python -m uvicorn app.main:app --host 127.0.0.1 --port 8011
        if ($LASTEXITCODE -ne 0) { throw 'Local API stopped with an error.' }
    }
} finally {
    Pop-Location
    foreach ($name in $previous.Keys) {
        [Environment]::SetEnvironmentVariable($name, $previous[$name], 'Process')
    }
}
