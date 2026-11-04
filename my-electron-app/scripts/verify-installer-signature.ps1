# Verify Authenticode signature on installer and/or unpacked Sendline Desktop.exe
param(
    [string[]]$Paths = @(),
    [switch]$FailOnUnsigned
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot

if ($Paths.Count -eq 0) {
    $dist = Join-Path $root "dist"
    $setup = Get-ChildItem -Path $dist -Filter "Sendline Desktop Setup *.exe" -ErrorAction SilentlyContinue |
        Sort-Object LastWriteTime -Descending |
        Select-Object -First 1
    $unpacked = Join-Path $dist "win-unpacked\Sendline Desktop.exe"

    if ($setup) { $Paths += $setup.FullName }
    if (Test-Path $unpacked) { $Paths += $unpacked }

    if ($Paths.Count -eq 0) {
        Write-Host "No artifacts found under dist/. Run pnpm run dist:win first." -ForegroundColor Yellow
        exit 2
    }
}

$anyValid = $false
$anyUnsigned = $false

foreach ($path in $Paths) {
    if (-not (Test-Path -LiteralPath $path)) {
        Write-Host "Missing: $path" -ForegroundColor Red
        exit 1
    }

    $sig = Get-AuthenticodeSignature -LiteralPath $path
    Write-Host "`n--- $path ---"
    Write-Host "Status: $($sig.Status)"
    if ($sig.SignerCertificate) {
        Write-Host "Subject: $($sig.SignerCertificate.Subject)"
        Write-Host "Thumbprint: $($sig.SignerCertificate.Thumbprint)"
        Write-Host "NotAfter: $($sig.SignerCertificate.NotAfter)"
    }
    if ($sig.TimeStamperCertificate) {
        Write-Host "Timestamp: $($sig.TimeStamperCertificate.Subject)"
    }

    if ($sig.Status -eq "Valid") {
        $anyValid = $true
    }
    if ($sig.Status -eq "NotSigned") {
        $anyUnsigned = $true
    }
}

if ($FailOnUnsigned -and $anyUnsigned) {
    Write-Host "`nExpected signed binaries but found NotSigned." -ForegroundColor Red
    exit 1
}

if ($anyValid) {
    Write-Host "`nAt least one artifact has a valid Authenticode signature." -ForegroundColor Green
    exit 0
}

Write-Host "`nNo valid signature (typical for unsigned dev builds)." -ForegroundColor Yellow
exit 0
