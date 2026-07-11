param(
  [string]$Python = "python",
  [string]$HostName = "127.0.0.1",
  [int]$Port = 8194,
  [string]$TargetFile = "data\tmp\product-geometry-helper-target.txt",
  [double]$ReadyDeadlineS = 10.0,
  [switch]$RunSmokeFirst,
  [string]$ProductRepo = ""
)

$ErrorActionPreference = "Stop"
$root = Resolve-Path (Join-Path $PSScriptRoot "..")
$wsUrl = "ws://${HostName}:${Port}"
$targetFilePath = Join-Path $root $TargetFile

if ($RunSmokeFirst) {
  $args = @((Join-Path $root "scripts\run_geometry_helper_smoke.py"), "--host", $HostName, "--port", [string]$Port, "--ready-deadline-s", [string]$ReadyDeadlineS)
  if ($ProductRepo -ne "") {
    $args += @("--product-repo", $ProductRepo)
  }
  & $Python $args
  if ($LASTEXITCODE -ne 0) {
    exit $LASTEXITCODE
  }
}

New-Item -ItemType Directory -Force -Path (Split-Path $targetFilePath) | Out-Null

Write-Host "Start product geometry helper: $wsUrl"
Start-Process `
  -FilePath $Python `
  -WorkingDirectory $root `
  -ArgumentList @((Join-Path $root "product_geometry_helper.py"), "--host", $HostName, "--port", [string]$Port, "--target-file", $targetFilePath) `
  -WindowStyle Hidden | Out-Null

$deadline = [DateTime]::UtcNow.AddSeconds([Math]::Max(1.0, $ReadyDeadlineS))
while ([DateTime]::UtcNow -lt $deadline) {
  if ((Test-Path $targetFilePath) -and ((Get-Content $targetFilePath -Raw).Trim() -eq $wsUrl)) {
    Write-Host "Ready."
    Write-Host "Set this in the product process:"
    Write-Host "  `$env:GUANXIANG_PRODUCT_GEOMETRY_HELPER_WS_URL = `"$wsUrl`""
    Write-Host "or:"
    Write-Host "  `$env:GUANXIANG_PRODUCT_GEOMETRY_HELPER_WS_TARGET_FILE = `"$targetFilePath`""
    exit 0
  }
  Start-Sleep -Milliseconds 200
}

throw "geometry helper did not write target file before deadline: $targetFilePath"
