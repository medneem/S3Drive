# Bundled third-party dependencies pinned to official releases and SHA256 digests.
$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"

$rcloneVersion = "1.75.2"
$rcloneZipName = "rclone-v$rcloneVersion-windows-amd64.zip"
$rcloneExpectedSha256 = "8A9DB2BAD1DC79A65B3348FD857069598E2AAB7CF4D118B77B1DF23055AFF818"
$winfspTag = "v2.2B4"
$winfspInstallerName = "winfsp-2.2.26215.msi"
$winfspExpectedSha256 = "2ECB5C89405488A95BBD8A01875E02C48534FD37BBDFD84488F7590464D65944"

$root = Split-Path -Parent $PSScriptRoot
$tools = Join-Path $root "tools"
New-Item -ItemType Directory -Force $tools | Out-Null
$archive = Join-Path $tools $rcloneZipName
$msi = Join-Path $tools $winfspInstallerName

function Download-And-Verify([string]$url, [string]$target, [string]$expected) {
    Invoke-WebRequest -Uri $url -OutFile $target
    if (-not (Test-Path -LiteralPath $target -PathType Leaf)) {
        throw "Missing dependency: $target"
    }
    $digest = (Get-FileHash -LiteralPath $target -Algorithm SHA256).Hash
    if ($digest -ne $expected) {
        Remove-Item -LiteralPath $target -Force -ErrorAction SilentlyContinue
        throw "SHA256 verification failed: $target"
    }
    Write-Host "Verified SHA256: $target"
}

Download-And-Verify `
    "https://github.com/rclone/rclone/releases/download/v$rcloneVersion/$rcloneZipName" `
    $archive $rcloneExpectedSha256
Download-And-Verify `
    "https://github.com/winfsp/winfsp/releases/download/$winfspTag/$winfspInstallerName" `
    $msi $winfspExpectedSha256

$extract = Join-Path $tools "rclone-unpacked"
if (Test-Path $extract) { Remove-Item $extract -Recurse -Force }
Expand-Archive -LiteralPath $archive -DestinationPath $extract -Force
$rclone = Get-ChildItem -LiteralPath $extract -Filter "rclone.exe" -Recurse -File |
    Select-Object -First 1
if (-not $rclone) { throw "rclone.exe missing from verified ZIP" }
Copy-Item -LiteralPath $rclone.FullName -Destination (Join-Path $tools "rclone.exe") -Force
& (Join-Path $tools "rclone.exe") version
if ($LASTEXITCODE -ne 0) { throw "Bundled rclone.exe cannot start" }
Remove-Item $archive -Force
Remove-Item $extract -Recurse -Force
Write-Host "Bundled dependencies: rclone $rcloneVersion and WinFsp $winfspTag"
