$ErrorActionPreference = "Stop"

$trialRoot = Join-Path $PSScriptRoot "_oracle_shadow_trial"
$trialLocalAppData = Join-Path $trialRoot "localappdata"
$trialConfigDir = Join-Path $trialLocalAppData ("InboundFlowManager\{0}\{1}" -f $env:COMPUTERNAME, $env:USERNAME)
$trialSharedState = Join-Path $trialRoot "shared_state"
$trialLogs = Join-Path $trialRoot "logs"
$pidFile = Join-Path $trialRoot "flow_manager.pid"
$statusFile = Join-Path $trialRoot "trial_status.txt"
$stdoutFile = Join-Path $trialLogs "stdout.log"
$stderrFile = Join-Path $trialLogs "stderr.log"
$exe = Join-Path $PSScriptRoot "_oracle_build_verify_dist\FlowManagerOracleVerify\FlowManagerOracleVerify.exe"

if (-not (Test-Path -LiteralPath $exe)) {
    throw "Az izolalt Oracle-shadow ellenorzo build nem talalhato: $exe"
}

if (Test-Path -LiteralPath $pidFile) {
    $oldPid = 0
    [int]::TryParse((Get-Content -LiteralPath $pidFile -Raw).Trim(), [ref]$oldPid) | Out-Null
    if ($oldPid -gt 0 -and (Get-Process -Id $oldPid -ErrorAction SilentlyContinue)) {
        throw "Az Oracle proba mar fut (PID: $oldPid)."
    }
}

New-Item -ItemType Directory -Force -Path $trialConfigDir, $trialSharedState, $trialLogs | Out-Null

$sourceConfigPath = Join-Path $PSScriptRoot "config.json"
if (-not (Test-Path -LiteralPath $sourceConfigPath)) {
    throw "The production config.json was not found: $sourceConfigPath"
}

$sourceConfig = Get-Content -LiteralPath $sourceConfigPath -Raw -Encoding UTF8 | ConvertFrom-Json
if (-not $sourceConfig.ecomm_file -or -not (Test-Path -LiteralPath $sourceConfig.ecomm_file)) {
    throw "The configured E_COMM source file was not found."
}
if (-not $sourceConfig.pallets_file -or -not (Test-Path -LiteralPath $sourceConfig.pallets_file)) {
    throw "The configured pallets source file was not found."
}

$trialConfig = [ordered]@{
    ecomm_file = [string]$sourceConfig.ecomm_file
    pallets_file = [string]$sourceConfig.pallets_file
    refresh_interval_minutes = 10
    port = 8591
}
$configJson = $trialConfig | ConvertTo-Json -Depth 3
[IO.File]::WriteAllText((Join-Path $trialConfigDir "config.json"), $configJson, [Text.UTF8Encoding]::new($false))

$securePassword = Read-Host "WebbyCom Oracle jelszo (izolalt shadow proba)" -AsSecureString
$bstr = [IntPtr]::Zero

try {
    $bstr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($securePassword)
    $env:WEBBYCOM_ORACLE_PASSWORD = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($bstr)
    $env:FLOW_ECOMM_SOURCE = "oracle"
    $env:FLOW_SHARED_STATE_DIR = $trialSharedState
    $env:LOCALAPPDATA = $trialLocalAppData
    $env:WEBBYCOM_ORACLE_HOST = "192.168.45.180"
    $env:WEBBYCOM_ORACLE_PORT = "1521"
    $env:WEBBYCOM_ORACLE_SERVICE = "HGL"
    $env:WEBBYCOM_ORACLE_USER = "ECOMM_READ"

    Remove-Item -LiteralPath $stdoutFile, $stderrFile -Force -ErrorAction SilentlyContinue
    Set-Content -LiteralPath $statusFile -Value "STARTING" -Encoding ASCII
    $process = Start-Process -FilePath $exe -WorkingDirectory (Split-Path $exe) -RedirectStandardOutput $stdoutFile -RedirectStandardError $stderrFile -PassThru
    Set-Content -LiteralPath $pidFile -Value $process.Id -Encoding ASCII

    $ready = $false
    for ($attempt = 0; $attempt -lt 90; $attempt++) {
        Start-Sleep -Seconds 1
        if ($process.HasExited) { break }
        try {
            $response = Invoke-WebRequest -Uri "http://127.0.0.1:8591/" -UseBasicParsing -TimeoutSec 2
            if ($response.StatusCode -eq 200) {
                $ready = $true
                break
            }
        }
        catch { }
    }

    if (-not $ready) {
        if ($process.HasExited) {
            Set-Content -LiteralPath $statusFile -Value ("FAILED_EXIT_" + $process.ExitCode) -Encoding ASCII
            throw "Az Oracle Flow Manager indulas kozben kilepett."
        }
        Set-Content -LiteralPath $statusFile -Value "FAILED_TIMEOUT" -Encoding ASCII
        throw "Az Oracle Flow Manager 90 masodpercen belul nem lett elerheto."
    }

    Set-Content -LiteralPath $statusFile -Value "RUNNING_ORACLE_ACTIVE" -Encoding ASCII
    Start-Process "http://127.0.0.1:8591/"
    Write-Host ""
    Write-Host "[OK] Izolalt Oracle proba fut: http://127.0.0.1:8591/"
    Write-Host "[OK] Oracle az aktiv E_COMM adatforras. PID: $($process.Id)"
}
finally {
    Remove-Item Env:WEBBYCOM_ORACLE_PASSWORD -ErrorAction SilentlyContinue
    Remove-Item Env:FLOW_ECOMM_SOURCE -ErrorAction SilentlyContinue
    Remove-Item Env:FLOW_SHARED_STATE_DIR -ErrorAction SilentlyContinue
    Remove-Item Env:WEBBYCOM_ORACLE_HOST -ErrorAction SilentlyContinue
    Remove-Item Env:WEBBYCOM_ORACLE_PORT -ErrorAction SilentlyContinue
    Remove-Item Env:WEBBYCOM_ORACLE_SERVICE -ErrorAction SilentlyContinue
    Remove-Item Env:WEBBYCOM_ORACLE_USER -ErrorAction SilentlyContinue
    if ($bstr -ne [IntPtr]::Zero) {
        [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($bstr)
    }
}
