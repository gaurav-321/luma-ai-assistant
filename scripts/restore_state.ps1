param(
    [Parameter(Mandatory = $true)]
    [string]$BackupDir
)

$ErrorActionPreference = "Stop"

if (-not (Test-Path $BackupDir)) {
    throw "Backup directory not found: $BackupDir"
}

$envFile = Join-Path $BackupDir ".env"
if (Test-Path $envFile) {
    Copy-Item $envFile ".env" -Force
}

$usersDir = Join-Path $BackupDir "users"
if (Test-Path $usersDir) {
    New-Item -ItemType Directory -Path "users" -Force | Out-Null
    Copy-Item (Join-Path $usersDir "*") "users" -Recurse -Force
}

$watcherDataDir = Join-Path $BackupDir "watcher_data"
if (Test-Path $watcherDataDir) {
    New-Item -ItemType Directory -Path "watcher\data" -Force | Out-Null
    Copy-Item (Join-Path $watcherDataDir "*") "watcher\data" -Recurse -Force
}

Write-Host "Restore completed from: $BackupDir"
