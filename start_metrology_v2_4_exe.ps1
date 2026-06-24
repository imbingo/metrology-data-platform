param(
    [switch]$InteractiveInstall,
    [int]$Port = 8023,
    [string]$HostName = "127.0.0.1"
)

$ErrorActionPreference = "Stop"
Set-Location -LiteralPath $PSScriptRoot

function Resolve-TesseractExe {
    if ($env:MDCP_TESSERACT_CMD -and (Test-Path -LiteralPath $env:MDCP_TESSERACT_CMD)) {
        return (Resolve-Path -LiteralPath $env:MDCP_TESSERACT_CMD).Path
    }

    $cmd = Get-Command tesseract -ErrorAction SilentlyContinue
    if ($cmd) {
        return $cmd.Source
    }

    $candidates = @(
        "$env:ProgramFiles\Tesseract-OCR\tesseract.exe",
        "${env:ProgramFiles(x86)}\Tesseract-OCR\tesseract.exe",
        "$env:LOCALAPPDATA\Programs\Tesseract-OCR\tesseract.exe",
        "$env:LOCALAPPDATA\Tesseract-OCR\tesseract.exe"
    )

    foreach ($candidate in $candidates) {
        if ($candidate -and (Test-Path -LiteralPath $candidate)) {
            return (Resolve-Path -LiteralPath $candidate).Path
        }
    }
    return $null
}

function Install-BundledTesseract {
    param([bool]$Silent)

    $installerDir = Join-Path $PSScriptRoot "offline_ocr_bundle\tesseract_installer"
    if (-not (Test-Path -LiteralPath $installerDir)) {
        throw "Missing bundled Tesseract installer directory: $installerDir"
    }

    $installer = Get-ChildItem -LiteralPath $installerDir -Filter "*.exe" -File -ErrorAction SilentlyContinue |
        Sort-Object Length -Descending |
        Select-Object -First 1

    if (-not $installer) {
        throw "Tesseract installer not found in: $installerDir"
    }

    Write-Host "Installing Tesseract OCR from bundled offline installer..."
    if ($Silent) {
        $proc = Start-Process -FilePath $installer.FullName -ArgumentList "/S" -Wait -PassThru
    } else {
        $proc = Start-Process -FilePath $installer.FullName -Wait -PassThru
    }

    if ($proc.ExitCode -ne 0) {
        throw "Tesseract installer exited with code $($proc.ExitCode)."
    }
}

function Resolve-AppExe {
    $candidates = @(
        (Join-Path $PSScriptRoot "metrology_data_platform_v2_4\metrology_data_platform_v2_4.exe"),
        (Join-Path $PSScriptRoot "dist_exe\metrology_data_platform_v2_4\metrology_data_platform_v2_4.exe")
    )

    foreach ($candidate in $candidates) {
        if (Test-Path -LiteralPath $candidate) {
            return (Resolve-Path -LiteralPath $candidate).Path
        }
    }
    throw "Metrology exe was not found. Keep this script together with the metrology_data_platform_v2_4 folder."
}

Write-Host "============================================================"
Write-Host "Metrology Data Platform V2.4 - EXE offline startup"
Write-Host "Python is bundled inside the exe package; production PC does not need Python."
Write-Host "============================================================"

$tesseractExe = Resolve-TesseractExe
if (-not $tesseractExe) {
    Install-BundledTesseract -Silent (-not $InteractiveInstall)
    $tesseractExe = Resolve-TesseractExe
}

if (-not $tesseractExe) {
    throw "Tesseract OCR was not found after installer completed. Set MDCP_TESSERACT_CMD to the tesseract.exe path."
}

$env:MDCP_TESSERACT_CMD = $tesseractExe
$env:MDCP_HOST = $HostName
$env:MDCP_PORT = [string]$Port

$envLines = @()
$escapedTesseract = $tesseractExe.Replace("'", "''")
$envLines += "`$env:MDCP_TESSERACT_CMD='$escapedTesseract'"
$envLines += "`$env:MDCP_HOST='$HostName'"
$envLines += "`$env:MDCP_PORT='$Port'"
$envLines | Set-Content -LiteralPath (Join-Path $PSScriptRoot ".env.ocr.ps1") -Encoding UTF8

$appExe = Resolve-AppExe
$url = "http://${HostName}:${Port}"

Write-Host "Using Tesseract: $tesseractExe"
Write-Host "Starting app: $appExe"
Write-Host "URL: $url"

Start-Job -ScriptBlock {
    param($TargetUrl)
    Start-Sleep -Seconds 3
    Start-Process $TargetUrl
} -ArgumentList $url | Out-Null

& $appExe
