# Build sendline-worker.exe (one-folder) for Sendline Desktop Phase 3.
# Requires: venv with requirements.txt + pip install pyinstaller
param(
    [string]$AutomateRoot = (Split-Path -Parent $PSScriptRoot)
)

$ErrorActionPreference = "Stop"
Set-Location $AutomateRoot

$venvPython = Join-Path $AutomateRoot "venv\Scripts\python.exe"
if (-not (Test-Path $venvPython)) {
    throw "Missing venv at $venvPython - create venv and pip install -r requirements.txt"
}

$gitSha = ""
try {
    $gitSha = (git -C $AutomateRoot rev-parse --short HEAD 2>$null).Trim()
} catch {
    $gitSha = ""
}
if (-not $gitSha) {
    $gitSha = "dev"
}
$env:WORKER_VERSION = $gitSha
Write-Host "WORKER_VERSION=$env:WORKER_VERSION"

& $venvPython -m pip install -q pyinstaller
& $venvPython -m PyInstaller --noconfirm --clean (Join-Path $AutomateRoot "sendline-worker.spec")

$distDir = Join-Path $AutomateRoot "dist\sendline-worker"
$versionFile = Join-Path $distDir "VERSION"
Set-Content -Path $versionFile -Value $env:WORKER_VERSION -NoNewline -Encoding utf8

Write-Host "Built: $distDir\sendline-worker.exe"
Write-Host "VERSION: $versionFile"
