param(
    [ValidateSet("shadow", "oracle")]
    [string]$Mode = "oracle"
)

$ErrorActionPreference = "Stop"
$securePassword = Read-Host "WebbyCom Oracle jelszo" -AsSecureString
$bstr = [IntPtr]::Zero

try {
    $bstr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($securePassword)
    $env:WEBBYCOM_ORACLE_PASSWORD = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($bstr)
    $env:FLOW_ECOMM_SOURCE = $Mode

    # Non-secret connection settings may be overridden before launch. These
    # defaults match the approved WebbyCom reporting connection.
    if (-not $env:WEBBYCOM_ORACLE_HOST) { $env:WEBBYCOM_ORACLE_HOST = "192.168.45.180" }
    if (-not $env:WEBBYCOM_ORACLE_PORT) { $env:WEBBYCOM_ORACLE_PORT = "1521" }
    if (-not $env:WEBBYCOM_ORACLE_SERVICE) { $env:WEBBYCOM_ORACLE_SERVICE = "HGL" }
    if (-not $env:WEBBYCOM_ORACLE_USER) { $env:WEBBYCOM_ORACLE_USER = "ECOMM_READ" }

    $localExe = Join-Path $PSScriptRoot "FlowManager.exe"
    $syncLauncher = Join-Path $PSScriptRoot "Inditas.bat"
    if (Test-Path -LiteralPath $localExe) {
        Start-Process -FilePath $localExe
    }
    elseif (Test-Path -LiteralPath $syncLauncher) {
        & $syncLauncher
        if ($LASTEXITCODE -ne 0) {
            throw "A Flow Manager inditasa sikertelen (kod: $LASTEXITCODE)."
        }
    }
    else {
        throw "Sem FlowManager.exe, sem Inditas.bat nem talalhato az indito mellett."
    }
}
finally {
    Remove-Item Env:WEBBYCOM_ORACLE_PASSWORD -ErrorAction SilentlyContinue
    Remove-Item Env:FLOW_ECOMM_SOURCE -ErrorAction SilentlyContinue
    if ($bstr -ne [IntPtr]::Zero) {
        [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($bstr)
    }
}
