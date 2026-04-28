# Nuitka Build Script for Windows
param(
    [string]$Version = "2.0.0",
    [string]$OutputDir = "dist\windows",
    [string]$PythonPath = "",      # Specify Python executable path
    [switch]$Clean,
    [switch]$UseMingw,  # Use MinGW (needs separate install), default is MSVC
    [switch]$BuildInstaller  # Build installer directly
)

Write-Host "=========================================="
Write-Host "Building Test Worker with Nuitka"
Write-Host "Version: $Version"
Write-Host "Output: $OutputDir"
if ($UseMingw) { Write-Host "Compiler: MinGW-w64" } else { Write-Host "Compiler: MSVC (default)" }
Write-Host "=========================================="

# Python path handling
if ($PythonPath -ne "") {
    $PythonExe = $PythonPath
    if (-not (Test-Path $PythonExe)) {
        Write-Error "Python not found at: $PythonPath"
        exit 1
    }
    Write-Host "Python path: $PythonPath"
} else {
    $PythonExe = "python"
    if (-not (Get-Command python -ErrorAction SilentlyContinue)) {
        Write-Error "Python not found in PATH!"
        exit 1
    }
}

$nuitkaInstalled = & $PythonExe -c "import nuitka; print('ok')" 2>$null
if ($nuitkaInstalled -ne "ok") {
    Write-Host "Installing Nuitka..."
    pip install nuitka ordered-set zstandard
}

# MSVC compiler heap optimization (fix C1002 heap memory error)
if (-not $UseMingw) {
    Write-Host "Setting MSVC compiler heap limit (2x default)..."
    $env:_CL_ = "/Zm8000"
}

$mingwBinPath = ""
if ($UseMingw) {
    # Use MinGW
    $mingwPaths = @("C:\mingw64\bin\gcc.exe", "C:\msys64\ucrt64\bin\gcc.exe")
    $mingwPath = $mingwPaths | Where-Object { Test-Path $_ } | Select-Object -First 1
    if (-not $mingwPath) {
        Write-Error "MinGW-w64 GCC not found at C:\mingw64\bin\gcc.exe"
        Write-Host "Download from: https://github.com/niXman/mingw-builds-binaries/releases"
        exit 1
    }
    $mingwBinPath = Split-Path $mingwPath -Parent
    Write-Host "MinGW-w64 GCC found: $mingwPath"
    $env:PATH = "$mingwBinPath;$env:PATH"
} else {
    # Default use MSVC, check Visual Studio
    $vsWhere = "${env:ProgramFiles(x86)}\Microsoft Visual Studio\Installer\vswhere.exe"
    if (-not (Test-Path $vsWhere)) {
        Write-Warning "Visual Studio Installer not found, but Nuitka will auto-detect MSVC"
    }
}

$VenvPath = "build_env_nuitka"
if ($Clean -or -not (Test-Path $VenvPath)) {
    if (Test-Path $VenvPath) { Remove-Item -Recurse -Force $VenvPath }
    Write-Host "[1/6] Creating virtual environment..."
    & $PythonExe -m venv $VenvPath
} else {
    Write-Host "[1/6] Using existing virtual environment..."
}

& ".\$VenvPath\Scripts\Activate.ps1"

Write-Host "[2/6] Installing dependencies..."
pip install --upgrade pip
pip install nuitka ordered-set zstandard
pip install -e ".[all]"

Write-Host "[3/6] Generating version file..."
$BuildVersion = Get-Date -Format "yyyyMMddHHmm"
Set-Content -Path "worker\_version.py" -Value "VERSION = `"$BuildVersion`"" -Encoding UTF8

Write-Host "[4/6] Checking Playwright..."
$ChromiumPath = "$env:LOCALAPPDATA\ms-playwright\chromium-*"
if (-not (Test-Path $ChromiumPath)) { playwright install chromium }

Write-Host "[5/6] Building with Nuitka..."

$nuitkaArgs = @(
    "--mode=standalone"
    "worker\gui_main.py"
    "--output-filename=test-worker.exe"
    "--windows-console-mode=disable"
    "--windows-uac-admin"
    "--windows-icon-from-ico=assets\icon.ico"
    "--include-data-dir=config=config"
    "--include-data-dir=assets=assets"
    "--include-data-dir=tools=tools"
    "--enable-plugin=pyqt5"
    "--include-package=uvicorn"
    "--include-package=fastapi"
    "--include-package=starlette"
    "--include-package=httpx"
    "--include-package=playwright"
    "--include-package=pyautogui"
    "--include-package=mss"
    "--include-package=cv2"
    "--include-package=PIL"
    "--include-package=numpy"
    "--include-package=pydantic"
    "--include-package=pystray"
    "--include-package=uiautomator2"
    # go-ios switched, removed tidevice3
    "--include-module=uvicorn.logging"
    "--include-module=uvicorn.loops"
    "--include-module=uvicorn.loops.auto"
    "--include-module=uvicorn.protocols"
    "--include-module=uvicorn.protocols.http"
    "--include-module=uvicorn.protocols.http.auto"
    "--include-module=uvicorn.protocols.websockets"
    "--include-module=uvicorn.protocols.websockets.auto"
    "--include-module=uvicorn.lifespan"
    "--include-module=uvicorn.lifespan.on"
    "--nofollow-import-to=pytest"
    "--nofollow-import-to=allure"
    "--nofollow-import-to=faker"
    # Exclude Playwright large generated modules (fix MSVC C1002 heap error)
    "--nofollow-import-to=playwright.sync_api._generated"
    "--nofollow-import-to=playwright.async_api._generated"
    "--nofollow-import-to=playwright._impl._generated"
    # Exclude large package test modules to reduce compile time
    "--nofollow-import-to=numpy._core.tests"
    "--nofollow-import-to=numpy.tests"
    "--nofollow-import-to=numpy.typing.tests"
    "--nofollow-import-to=numpy.lib.tests"
    "--nofollow-import-to=numpy.fft.tests"
    "--nofollow-import-to=numpy.linalg.tests"
    "--nofollow-import-to=numpy.ma.tests"
    "--nofollow-import-to=numpy.polynomial.tests"
    "--nofollow-import-to=numpy.random.tests"
    "--nofollow-import-to=numpy.matrixlib.tests"
    "--nofollow-import-to=PIL.tests"
    "--nofollow-import-to=cv2.tests"
    "--nofollow-import-to=cryptography.tests"
    "--nofollow-import-to=jinja2.tests"
    "--nofollow-import-to=pydantic.v1.tests"
    "--nofollow-import-to=sentry_sdk.integrations.openai_agents.tests"
    "--output-dir=dist\nuitka_build"
    "--show-progress"
)

if ($UseMingw) { $nuitkaArgs += "--mingw64" }

& python -m nuitka $nuitkaArgs

if ($LASTEXITCODE -ne 0) {
    Write-Error "Nuitka build failed!"
    Remove-Item "worker\_version.py" -ErrorAction SilentlyContinue
    deactivate
    exit 1
}

Remove-Item "worker\_version.py" -ErrorAction SilentlyContinue

Write-Host "[6/6] Creating release package..."
$PackageDir = "$OutputDir\test-worker"
if (Test-Path $PackageDir) { Remove-Item -Recurse -Force $PackageDir }
New-Item -ItemType Directory -Force -Path $PackageDir | Out-Null

$BuildDir = "dist\nuitka_build\gui_main.dist"
if (Test-Path $BuildDir) {
    Move-Item "$BuildDir\*" $PackageDir
} else {
    Write-Error "Build directory not found: $BuildDir"
    deactivate
    exit 1
}

# Nuitka --include-data-dir may miss binary files in subdirs, copy tools manually
Write-Host "Copying tools directory (full)..."
if (Test-Path "$PackageDir\tools") { Remove-Item -Recurse -Force "$PackageDir\tools" }
Copy-Item -Path "tools" -Destination "$PackageDir\tools" -Recurse -Force

# Also ensure assets and config are complete
Write-Host "Copying assets directory..."
if (Test-Path "$PackageDir\assets") { Remove-Item -Recurse -Force "$PackageDir\assets" }
Copy-Item -Path "assets" -Destination "$PackageDir\assets" -Recurse -Force

Write-Host "Copying config directory..."
if (Test-Path "$PackageDir\config") { Remove-Item -Recurse -Force "$PackageDir\config" }
Copy-Item -Path "config" -Destination "$PackageDir\config" -Recurse -Force

Set-Content -Path "$PackageDir\VERSION" -Value $BuildVersion -Encoding UTF8

Write-Host "Copying Playwright chromium..."
$ChromiumDir = Get-ChildItem -Path "$env:LOCALAPPDATA\ms-playwright" -Filter "chromium-*" -Directory | Select-Object -First 1
if ($ChromiumDir) {
    Copy-Item -Path $ChromiumDir.FullName -Destination "$PackageDir\playwright\$($ChromiumDir.Name)" -Recurse
}

Set-Content -Path "$PackageDir\start.bat" -Value "@echo off`nchcp 65001 >nul 2>&1`ncd /d `%~dp0`ntest-worker.exe`npause" -Encoding ASCII
Set-Content -Path "$PackageDir\README.txt" -Value "Test Worker - Windows (Nuitka Build)`nBuild Version: $BuildVersion" -Encoding UTF8

deactivate

Write-Host "=========================================="
Write-Host "Build complete!"
Write-Host "Package: $PackageDir"
Write-Host "=========================================="

# Build installer (optional)
if ($BuildInstaller) {
    Write-Host "Building installer (via -BuildInstaller flag)..."
    & ".\installer\build_installer.ps1" -Version $BuildVersion
    if ($LASTEXITCODE -ne 0) {
        Write-Warning "Installer build failed, but EXE package is available"
    }
} else {
    Write-Host ""
    Write-Host "Build installer? (for distribution)"
    $BuildInstallerChoice = Read-Host "Enter 'y' to build, or skip"

    if ($BuildInstallerChoice -eq 'y') {
        Write-Host "Building installer..."
        & ".\installer\build_installer.ps1" -Version $BuildVersion
        if ($LASTEXITCODE -ne 0) {
            Write-Warning "Installer build failed, but EXE package is available"
        }
    }
}

Write-Host "=========================================="
Write-Host "All builds complete!"
Write-Host "EXE package: $PackageDir"
Write-Host "Installer: $OutputDir\test-worker-installer.exe (if built)"
Write-Host "=========================================="