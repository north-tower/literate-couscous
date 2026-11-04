# Copy PyInstaller output into Electron resources for local/packaged runs.
# Prefer: pnpm run copy-worker (uses copy-worker.mjs on all platforms).
param(
    [string]$ElectronRoot = (Split-Path -Parent $PSScriptRoot)
)

$ErrorActionPreference = "Stop"
& node (Join-Path $PSScriptRoot "copy-worker.mjs")
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
