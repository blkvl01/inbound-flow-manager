$ErrorActionPreference = "Stop"
$statusDir = Join-Path $PSScriptRoot "outputs\oracle_location_fields"
$statusFile = Join-Path $statusDir "run_status.txt"
$logFile = Join-Path $statusDir "extended_check.log"
New-Item -ItemType Directory -Force -Path $statusDir | Out-Null
Set-Content -LiteralPath $statusFile -Value "WAITING_FOR_PASSWORD" -Encoding ASCII
Remove-Item -LiteralPath $logFile -Force -ErrorAction SilentlyContinue
$securePassword = Read-Host "WebbyCom Oracle jelszo (csak olvasasi ellenorzes)" -AsSecureString
$bstr = [IntPtr]::Zero
try {
    $bstr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($securePassword)
    $env:WEBBYCOM_ORACLE_PASSWORD = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($bstr)
    Set-Location -LiteralPath $PSScriptRoot
    Set-Content -LiteralPath $statusFile -Value "RUNNING_EXTENDED_CHECK" -Encoding ASCII
    python .\inspect_oracle_location_fields.py *> $logFile
    if ($LASTEXITCODE -ne 0) { throw "Az Oracle mezovizsgalat sikertelen." }
    Get-Content -LiteralPath $logFile -Encoding UTF8
    Set-Content -LiteralPath $statusFile -Value "SUCCESS_EXTENDED_CHECK" -Encoding ASCII
}
catch {
    Set-Content -LiteralPath $statusFile -Value ("ERROR: " + $_.Exception.Message) -Encoding UTF8
    throw
}
finally {
    Remove-Item Env:WEBBYCOM_ORACLE_PASSWORD -ErrorAction SilentlyContinue
    if ($bstr -ne [IntPtr]::Zero) {
        [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($bstr)
    }
}
