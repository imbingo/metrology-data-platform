param(
    [switch]$InteractiveInstall
)

$ErrorActionPreference = "Stop"
Set-Location -LiteralPath $PSScriptRoot

$installScript = Join-Path $PSScriptRoot "install_ocr_offline.ps1"
$envScript = Join-Path $PSScriptRoot ".env.ocr.ps1"

if (-not (Test-Path -LiteralPath $installScript)) {
    throw "Missing OCR offline install script: $installScript"
}

Write-Host "============================================================"
Write-Host "Metrology Data Platform V2.4 - OCR offline startup"
Write-Host "URL: http://127.0.0.1:8023"
Write-Host "============================================================"

$silentInstall = -not $InteractiveInstall
& $installScript -SilentPython:$silentInstall -SilentTesseract:$silentInstall

if (Test-Path -LiteralPath $envScript) {
    . $envScript
}

$python = $env:MDCP_PYTHON_EXE
if (-not $python -or -not (Test-Path -LiteralPath $python)) {
    $cmd = Get-Command python -ErrorAction Stop
    $python = $cmd.Source
}

Start-Job -ScriptBlock {
    Start-Sleep -Seconds 3
    Start-Process "http://127.0.0.1:8023"
} | Out-Null

& $python "$PSScriptRoot\metrology_config_app_v2_3_pie_delete_process_guard.py"
