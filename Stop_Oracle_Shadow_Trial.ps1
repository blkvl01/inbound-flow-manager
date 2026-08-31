$ErrorActionPreference = "Stop"
$trialRoot = Join-Path $PSScriptRoot "_oracle_shadow_trial"
$pidFile = Join-Path $trialRoot "flow_manager.pid"
$statusFile = Join-Path $trialRoot "trial_status.txt"

if (-not (Test-Path -LiteralPath $pidFile)) {
    Write-Host "Nincs rogzitett Oracle proba-folyamat."
    exit 0
}

$trialPid = 0
[int]::TryParse((Get-Content -LiteralPath $pidFile -Raw).Trim(), [ref]$trialPid) | Out-Null
if ($trialPid -le 0) {
    throw "Ervenytelen trial PID."
}

$process = Get-Process -Id $trialPid -ErrorAction SilentlyContinue
if ($process) {
    Stop-Process -Id $trialPid -Force
    $process.WaitForExit()
}
Set-Content -LiteralPath $statusFile -Value "STOPPED" -Encoding ASCII
Remove-Item -LiteralPath $pidFile -Force -ErrorAction SilentlyContinue
Write-Host "Az izolalt Oracle proba leallt."
