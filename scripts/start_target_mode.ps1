param(
  [string]$Python = "python",
  [string]$HostName = "127.0.0.1",
  [int]$HelperPort = 8192,
  [int]$TargetPort = 8193,
  [double]$ReadyDeadlineS = 20.0,
  [switch]$RunSmokeFirst
)

$ErrorActionPreference = "Stop"
$root = Resolve-Path (Join-Path $PSScriptRoot "..")
$targetUrl = "http://${HostName}:${TargetPort}"
$helperUrl = "http://${HostName}:${HelperPort}"

function Wait-JsonReady([string]$Uri, [double]$DeadlineS) {
  $deadline = [DateTime]::UtcNow.AddSeconds([Math]::Max(1.0, $DeadlineS))
  $lastError = ""
  while ([DateTime]::UtcNow -lt $deadline) {
    try {
      $payload = Invoke-RestMethod -Uri $Uri -TimeoutSec 2
      if ($payload.ready -eq $true) {
        return $payload
      }
      $lastError = ($payload | ConvertTo-Json -Depth 8)
    } catch {
      $lastError = $_.Exception.Message
      Start-Sleep -Milliseconds 250
    }
  }
  throw "$Uri did not become ready before deadline: $lastError"
}

if ($RunSmokeFirst) {
  & $Python (Join-Path $root "scripts\run_target_mode_smoke.py") `
    --host $HostName `
    --helper-port $HelperPort `
    --target-port $TargetPort `
    --ready-deadline-s $ReadyDeadlineS
  if ($LASTEXITCODE -ne 0) {
    exit $LASTEXITCODE
  }
}

$env:GUANXIANG_ACTION_AUDIO_TARGET_URL = $targetUrl
Remove-Item Env:\GUANXIANG_ACTION_AUDIO_TARGET_COMMAND -ErrorAction SilentlyContinue
Remove-Item Env:\GUANXIANG_ACTION_AUDIO_ALLOW_TEST_TARGET -ErrorAction SilentlyContinue

Write-Host "Start product action-audio target: $targetUrl"
Start-Process `
  -FilePath $Python `
  -WorkingDirectory $root `
  -ArgumentList @((Join-Path $root "product_action_audio_target.py"), "--host", $HostName, "--port", [string]$TargetPort) `
  -WindowStyle Hidden | Out-Null

Wait-JsonReady "$targetUrl/ready" $ReadyDeadlineS | Out-Null

Write-Host "Start product action-audio helper: $helperUrl/render"
Start-Process `
  -FilePath $Python `
  -WorkingDirectory $root `
  -ArgumentList @((Join-Path $root "product_action_audio_helper.py"), "--host", $HostName, "--port", [string]$HelperPort) `
  -WindowStyle Hidden | Out-Null

Wait-JsonReady "$helperUrl/ready" $ReadyDeadlineS | Out-Null

Write-Host "Ready."
Write-Host "Set this in the product process:"
Write-Host "  `$env:GUANXIANG_PRODUCT_ACTION_AUDIO_HELPER_URL = `"$helperUrl/render`""
