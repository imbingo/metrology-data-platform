param(
    [switch]$SkipPython,
    [switch]$SkipTesseract,
    [switch]$SilentPython,
    [switch]$SilentTesseract
)

$ErrorActionPreference = "Stop"
Set-Location -LiteralPath $PSScriptRoot

function Test-PythonCompatible {
    param([string]$PythonPath)
    if (-not $PythonPath -or -not (Test-Path -LiteralPath $PythonPath)) {
        return $false
    }
    try {
        $version = & $PythonPath -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')"
        return ($version -eq "3.14")
    } catch {
        return $false
    }
}

function Resolve-PythonExe {
    if ($env:MDCP_PYTHON_EXE -and (Test-PythonCompatible $env:MDCP_PYTHON_EXE)) {
        return (Resolve-Path -LiteralPath $env:MDCP_PYTHON_EXE).Path
    }

    $candidates = @(
        "$env:LOCALAPPDATA\Programs\Python\Python314\python.exe",
        "$env:ProgramFiles\Python314\python.exe",
        "${env:ProgramFiles(x86)}\Python314\python.exe"
    )
    foreach ($candidate in $candidates) {
        if ($candidate -and (Test-PythonCompatible $candidate)) {
            return (Resolve-Path -LiteralPath $candidate).Path
        }
    }

    $cmd = Get-Command python -ErrorAction SilentlyContinue
    if ($cmd -and (Test-PythonCompatible $cmd.Source)) {
        return $cmd.Source
    }
    return $null
}

function Install-BundledPython {
    param([bool]$Silent)

    $installerDir = Join-Path $PSScriptRoot "offline_ocr_bundle\python_installer"
    $installer = Get-ChildItem -LiteralPath $installerDir -Filter "*.exe" -File -ErrorAction SilentlyContinue |
        Sort-Object Length -Descending |
        Select-Object -First 1
    if (-not $installer) {
        throw "Python 3.14 offline installer not found in: $installerDir"
    }

    Write-Host "Installing bundled Python 3.14 from local installer..."
    if ($Silent) {
        $args = "/quiet InstallAllUsers=0 PrependPath=1 Include_pip=1 Include_launcher=1 Include_test=0 Include_doc=0 SimpleInstall=1"
    } else {
        $args = "InstallAllUsers=0 PrependPath=1 Include_pip=1 Include_launcher=1 Include_test=0 Include_doc=0"
    }
    $proc = Start-Process -FilePath $installer.FullName -ArgumentList $args -Wait -PassThru
    if ($proc.ExitCode -ne 0) {
        throw "Python installer exited with code $($proc.ExitCode)."
    }
}

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

$bundleRoot = Join-Path $PSScriptRoot "offline_ocr_bundle"
$wheelDir = Join-Path $bundleRoot "python_wheels"
$requirements = Join-Path $PSScriptRoot "requirements_ocr.txt"

if (-not (Test-Path -LiteralPath $wheelDir)) {
    throw "Missing offline Python wheel directory: $wheelDir"
}
if (-not (Test-Path -LiteralPath $requirements)) {
    throw "Missing requirements file: $requirements"
}

$python = Resolve-PythonExe
if (-not $python) {
    if ($SkipPython) {
        throw "Compatible Python 3.14 was not found and -SkipPython was specified."
    }
    Install-BundledPython -Silent ([bool]$SilentPython)
    $python = Resolve-PythonExe
}
if (-not $python) {
    throw "Compatible Python 3.14 was not found after bundled installer completed."
}

$env:MDCP_PYTHON_EXE = $python
Write-Host "Using Python: $python"

Write-Host "Installing OCR Python packages from local wheel bundle..."
& $python -m pip install --no-index --find-links $wheelDir -r $requirements
if ($LASTEXITCODE -ne 0) {
    throw "pip offline install failed."
}

if (-not $SkipTesseract) {
    $tesseractExe = Resolve-TesseractExe
    if (-not $tesseractExe) {
        $installerDir = Join-Path $bundleRoot "tesseract_installer"
        $installer = Get-ChildItem -LiteralPath $installerDir -Filter "*.exe" -File -ErrorAction SilentlyContinue |
            Sort-Object Length -Descending |
            Select-Object -First 1

        if (-not $installer) {
            throw "Tesseract installer not found in: $installerDir"
        }

        Write-Host "Installing Tesseract OCR from local installer..."
        if ($SilentTesseract) {
            $proc = Start-Process -FilePath $installer.FullName -ArgumentList "/S" -Wait -PassThru
        } else {
            $proc = Start-Process -FilePath $installer.FullName -Wait -PassThru
        }
        if ($proc.ExitCode -ne 0) {
            throw "Tesseract installer exited with code $($proc.ExitCode)."
        }
        $tesseractExe = Resolve-TesseractExe
    }

    if ($tesseractExe) {
        $env:MDCP_TESSERACT_CMD = $tesseractExe
        Write-Host "Tesseract OCR found: $tesseractExe"
    } else {
        Write-Warning "Tesseract OCR was not found. Set MDCP_TESSERACT_CMD manually if the installer used a custom path."
    }
}

$envLines = @()
$escapedPython = $python.Replace("'", "''")
$envLines += "`$env:MDCP_PYTHON_EXE='$escapedPython'"
if ($env:MDCP_TESSERACT_CMD) {
    $escapedTesseract = $env:MDCP_TESSERACT_CMD.Replace("'", "''")
    $envLines += "`$env:MDCP_TESSERACT_CMD='$escapedTesseract'"
}
$envLines | Set-Content -LiteralPath (Join-Path $PSScriptRoot ".env.ocr.ps1") -Encoding UTF8

Write-Host "Verifying OCR Python imports..."
& $python -c "import PIL, cv2, pytesseract, numpy; print('OCR Python packages OK')"
if ($LASTEXITCODE -ne 0) {
    throw "OCR Python import verification failed."
}

Write-Host "Offline OCR dependency installation complete."
