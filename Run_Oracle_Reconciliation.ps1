param(
    [string]$OutputDir = "outputs\webbycom_oracle_reconciliation"
)

$ErrorActionPreference = "Stop"
$projectDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$driverDir = Join-Path $env:TEMP "codex_webbycom_oracledb_py314"
$statusPath = Join-Path $projectDir $OutputDir
$statusFile = Join-Path $statusPath "run_status.txt"
New-Item -ItemType Directory -Path $statusPath -Force | Out-Null

$securePassword = Read-Host "WebbyCom Oracle jelszo (nem kerul fajlba)" -AsSecureString
$bstr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($securePassword)
try {
    $env:WEBBYCOM_ORACLE_PASSWORD = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($bstr)
    $env:WEBBYCOM_ORACLE_DRIVER_DIR = $driverDir
    Set-Content -LiteralPath $statusFile -Value "RUNNING" -Encoding UTF8
    Push-Location $projectDir
    try {
        & python oracle_reconciliation.py --output-dir $OutputDir
        if ($LASTEXITCODE -ne 0) {
            throw "Az osszehasonlitas hibakoddal allt le: $LASTEXITCODE"
        }
        Set-Content -LiteralPath $statusFile -Value "SUCCESS" -Encoding UTF8
        Write-Host ""
        Write-Host "Az osszehasonlitas sikeresen befejezodott." -ForegroundColor Green
    } finally {
        Pop-Location
    }
} catch {
    Set-Content -LiteralPath $statusFile -Value ("ERROR: " + $_.Exception.Message) -Encoding UTF8
    Write-Host ""
    Write-Host ("Hiba: " + $_.Exception.Message) -ForegroundColor Red
    exit 1
} finally {
    Remove-Item Env:WEBBYCOM_ORACLE_PASSWORD -ErrorAction SilentlyContinue
    Remove-Item Env:WEBBYCOM_ORACLE_DRIVER_DIR -ErrorAction SilentlyContinue
    if ($bstr -ne [IntPtr]::Zero) {
        [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($bstr)
    }
}
