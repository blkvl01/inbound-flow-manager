$ErrorActionPreference = "Stop"
$securePassword = Read-Host "WebbyCom Oracle jelszo" -AsSecureString
$bstr = [IntPtr]::Zero
$outputDir = Join-Path $PSScriptRoot "outputs\webbycom_oracle_adapter"
$statusFile = Join-Path $outputDir "run_status.txt"
$resultFile = Join-Path $outputDir "adapter_check.txt"
New-Item -ItemType Directory -Force -Path $outputDir | Out-Null
Set-Content -LiteralPath $statusFile -Value "RUNNING" -Encoding UTF8

try {
    $bstr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($securePassword)
    $env:WEBBYCOM_ORACLE_PASSWORD = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($bstr)
    Set-Location -LiteralPath $PSScriptRoot
    # Python logging writes warnings to stderr. Capture both streams without
    # letting PowerShell 7 promote a legitimate data-quality warning to a
    # terminating NativeCommandError.
    $previousPreference = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    if (Test-Path variable:PSNativeCommandUseErrorActionPreference) {
        $previousNativePreference = $PSNativeCommandUseErrorActionPreference
        $PSNativeCommandUseErrorActionPreference = $false
    }
    python -m oracle_ecomm *> $resultFile
    $pythonExitCode = $LASTEXITCODE
    $ErrorActionPreference = $previousPreference
    if (Test-Path variable:previousNativePreference) {
        $PSNativeCommandUseErrorActionPreference = $previousNativePreference
    }
    Get-Content -LiteralPath $resultFile -Encoding UTF8
    if ($pythonExitCode -ne 0) {
        throw "Az Oracle adapter ellenorzese sikertelen."
    }
    Set-Content -LiteralPath $statusFile -Value "SUCCESS" -Encoding UTF8
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
