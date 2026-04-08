param(
    [string]$BackupRoot = "backups"
)

$ErrorActionPreference = "Stop"
$ts = Get-Date -Format "yyyyMMdd_HHmmss"
$backupDir = Join-Path $BackupRoot $ts

New-Item -ItemType Directory -Path $backupDir -Force | Out-Null

if (Test-Path ".env") {
    Copy-Item ".env" (Join-Path $backupDir ".env") -Force
}

if (Test-Path "users") {
    Copy-Item "users" (Join-Path $backupDir "users") -Recurse -Force
}

if (Test-Path "watcher\data") {
    Copy-Item "watcher\data" (Join-Path $backupDir "watcher_data") -Recurse -Force
}

Write-Host "Backup created at: $backupDir"
