# Build Windows installer. Use -Signed to load .env.signing and optional electron-builder.local.yml.
param(
    [switch]$Signed
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Push-Location $root
try {
    & pnpm run copy-worker
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

    if ($Signed) {
        . (Join-Path $PSScriptRoot "load-signing-env.ps1")
        $hasPfx = [bool]$env:CSC_LINK -or [bool]$env:WIN_CSC_LINK
        $localYml = Join-Path $root "electron-builder.local.yml"
        $hasStore = Test-Path $localYml

        if (-not $hasPfx -and -not $hasStore) {
            Write-Host @"
Signed build requires one of:
  - .env.signing with CSC_LINK (+ CSC_KEY_PASSWORD), or
  - electron-builder.local.yml with win.certificateSubjectName
Copy from .env.signing.example / electron-builder.local.example.yml
"@ -ForegroundColor Red
            exit 1
        }
    }

    $builderArgs = @("electron-builder", "--win", "--publish", "never")
    if ($Signed -and (Test-Path (Join-Path $root "electron-builder.local.yml"))) {
        $builderArgs = @(
            "electron-builder",
            "-c", "electron-builder.yml",
            "-c", "electron-builder.local.yml",
            "--win",
            "--publish", "never"
        )
    }

    & pnpm exec @builderArgs
    exit $LASTEXITCODE
}
finally {
    Pop-Location
}
