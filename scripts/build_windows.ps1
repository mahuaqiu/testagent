# Nuitka Build Script for Windows
param(
    [string]$Version = "2.0.0",
    [string]$OutputDir = "dist\windows",
    [string]$PythonPath = "",      # Specify Python executable path
    [string]$PerfwinWheel = "D:\code\perfwin\target\wheels\perfwin-0.3.2-cp312-cp312-win_amd64.whl",  # perfwin wheel 路径
    [string]$WinControlWheel = "D:\code\win-control\target\wheels\win_control-0.1.5-cp312-cp312-win_amd64.whl",  # win-control wheel 路径
    [switch]$Clean,
    [switch]$BuildInstaller  # Build installer directly
)

# 定义工程根目录（使用绝对路径，避免相对路径问题）
# $PSScriptRoot 是脚本所在目录（scripts），需要获取其父目录作为项目根目录
$ProjectRoot = Split-Path $PSScriptRoot -Parent
if ($ProjectRoot -eq "") {
    $ProjectRoot = Get-Location
}
Write-Host "Project root: $ProjectRoot"

Write-Host "=========================================="
Write-Host "Building Test Worker with Nuitka"
Write-Host "Version: $Version"
Write-Host "Output: $OutputDir"
Write-Host "Compiler: MSVC"
Write-Host "=========================================="

# 切换到工程根目录
Set-Location $ProjectRoot
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

# Check Visual Studio
$vsWhere = "${env:ProgramFiles(x86)}\Microsoft Visual Studio\Installer\vswhere.exe"
if (-not (Test-Path $vsWhere)) {
    Write-Warning "Visual Studio Installer not found, but Nuitka will auto-detect MSVC"
}

$VenvPath = "$ProjectRoot\build_env_nuitka"
if ($Clean -or -not (Test-Path $VenvPath)) {
    if (Test-Path $VenvPath) { Remove-Item -Recurse -Force $VenvPath }
    Write-Host "[1/6] Creating virtual environment..."
    & $PythonExe -m venv $VenvPath
} else {
    Write-Host "[1/6] Using existing virtual environment..."
}

& "$VenvPath\Scripts\Activate.ps1"

Write-Host "[2/6] Installing dependencies..."
pip install --upgrade pip
pip install nuitka ordered-set zstandard
pip install -e ".[all]"

# 安装 perfwin wheel
if ($PerfwinWheel -ne "" -and (Test-Path $PerfwinWheel)) {
    Write-Host "  Installing perfwin wheel: $PerfwinWheel"
    pip install $PerfwinWheel
} else {
    Write-Warning "perfwin wheel not found at: $PerfwinWheel"
    Write-Warning "Performance monitoring may not work!"
}

# 安装 win-control wheel
if ($WinControlWheel -ne "" -and (Test-Path $WinControlWheel)) {
    Write-Host "  Installing win-control wheel: $WinControlWheel"
    pip install $WinControlWheel
} else {
    Write-Warning "win-control wheel not found at: $WinControlWheel"
    Write-Warning "System control actions (set_resolution, set_volume, audio_device) may not work!"
}

Write-Host "[3/6] Generating version file..."
$BuildVersion = Get-Date -Format "yyyyMMddHHmm"
Set-Content -Path "$ProjectRoot\worker\_version.py" -Value "VERSION = `"$BuildVersion`"" -Encoding UTF8

Write-Host "[4/6] Checking Playwright..."
$ChromiumPath = "$env:LOCALAPPDATA\ms-playwright\chromium-*"
if (-not (Test-Path $ChromiumPath)) { playwright install chromium }

Write-Host "[5/6] Cleaning old build artifacts..."
$NuitkaBuildDir = "$ProjectRoot\dist\nuitka_build"
if (Test-Path $NuitkaBuildDir) {
    Remove-Item -Recurse -Force $NuitkaBuildDir
    Write-Host "  Cleaned: $NuitkaBuildDir"
}

Write-Host "[6/6] Building with Nuitka..."
Write-Host "  Memory optimization: --low-memory --jobs=10"

$nuitkaArgs = @(
    "--mode=standalone"
    "$ProjectRoot\worker\gui_main.py"
    "--output-filename=test-worker.exe"
    "--windows-console-mode=disable"
    "--windows-uac-admin"
    "--windows-icon-from-ico=$ProjectRoot\assets\icon.ico"
    "--include-data-dir=config=config"
    "--include-data-dir=assets=assets"
    "--include-data-dir=tools=tools"
    "--enable-plugin=pyqt5"
    "--include-package-data=perfwin"
    "--include-package-data=win_control"
    # uiautomator2 assets (u2.jar, app-uiautomator.apk)
    "--include-package-data=uiautomator2"
    "--low-memory"
    "--jobs=10"
    # Disable clcache to avoid D8000 errors (cache corruption issues)
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
    "--include-module=pystray._win32"
    "--include-package=six"
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
    # Note: Do NOT exclude playwright._generated modules - they are required at runtime
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
    "--output-dir=$ProjectRoot\dist\nuitka_build"
    "--show-progress"
)

& python -m nuitka $nuitkaArgs

if ($LASTEXITCODE -ne 0) {
    Write-Error "Nuitka build failed!"
    Remove-Item "$ProjectRoot\worker\_version.py" -ErrorAction SilentlyContinue
    exit 1
}

Remove-Item "$ProjectRoot\worker\_version.py" -ErrorAction SilentlyContinue

Write-Host "[7/6] Creating release package..."
# OutputDir 可能是相对路径或绝对路径，统一转换为绝对路径
if (-not [System.IO.Path]::IsPathRooted($OutputDir)) {
    $OutputDir = "$ProjectRoot\$OutputDir"
}
$PackageDir = "$OutputDir\test-worker"
if (Test-Path $PackageDir) { Remove-Item -Recurse -Force $PackageDir }
New-Item -ItemType Directory -Force -Path $PackageDir | Out-Null

$BuildDir = "$ProjectRoot\dist\nuitka_build\gui_main.dist"
if (Test-Path $BuildDir) {
    Move-Item "$BuildDir\*" $PackageDir
} else {
    Write-Error "Build directory not found: $BuildDir"
    exit 1
}

# Nuitka --include-data-dir may miss binary files in subdirs, copy tools manually
Write-Host "Copying tools directory (full)..."
if (Test-Path "$PackageDir\tools") { Remove-Item -Recurse -Force "$PackageDir\tools" }
Copy-Item -Path "$ProjectRoot\tools" -Destination "$PackageDir\tools" -Recurse -Force

# Also ensure assets and config are complete
Write-Host "Copying assets directory..."
if (Test-Path "$PackageDir\assets") { Remove-Item -Recurse -Force "$PackageDir\assets" }
Copy-Item -Path "$ProjectRoot\assets" -Destination "$PackageDir\assets" -Recurse -Force

Write-Host "Copying config directory..."
if (Test-Path "$PackageDir\config") { Remove-Item -Recurse -Force "$PackageDir\config" }
Copy-Item -Path "$ProjectRoot\config" -Destination "$PackageDir\config" -Recurse -Force

# 复制 minicap 二进制文件（Nuitka --include-data-dir 可能遗漏）
Write-Host "Copying minicap static files..."
# Nuitka --include-data-dir 会创建根目录下的目录，但可能不完整
# 手动复制确保 minicap-shared 目录（包含 .so 文件）也被包含
$MinicapSrcDir = "$ProjectRoot\worker\platforms\minicap\static"
$MinicapTargetDir = "$PackageDir\worker\platforms\minicap\static"
if (-not (Test-Path $MinicapTargetDir)) {
    New-Item -ItemType Directory -Force -Path $MinicapTargetDir | Out-Null
}
Copy-Item -Path "$MinicapSrcDir\*" -Destination $MinicapTargetDir -Recurse -Force
Write-Host "  minicap files copied successfully"

# Create _internal\config as template backup (for config merging during upgrade)
Write-Host "Creating _internal\config template..."
$InternalConfigDir = "$PackageDir\_internal\config"
New-Item -ItemType Directory -Force -Path $InternalConfigDir | Out-Null
Copy-Item -Path "$ProjectRoot\config\*" -Destination $InternalConfigDir -Recurse -Force

# 复制 HWiNFO64.EXE (Nuitka --include-package-data 不包含 .exe 文件)
Write-Host "Copying HWiNFO64.EXE for perfwin..."
$HwinfoExe = "$VenvPath\Lib\site-packages\perfwin\HWiNFO64\HWiNFO64.EXE"
if (Test-Path $HwinfoExe) {
    $HwinfoTargetDir = "$PackageDir\perfwin\HWiNFO64"
    if (-not (Test-Path $HwinfoTargetDir)) {
        New-Item -ItemType Directory -Force -Path $HwinfoTargetDir | Out-Null
    }
    Copy-Item -Path $HwinfoExe -Destination "$HwinfoTargetDir\HWiNFO64.EXE" -Force
    Write-Host "  HWiNFO64.EXE copied successfully"
} else {
    Write-Warning "HWiNFO64.EXE not found at: $HwinfoExe"
    Write-Warning "Performance monitoring may not work!"
}

Set-Content -Path "$PackageDir\VERSION" -Value $BuildVersion -Encoding UTF8

Write-Host "Copying Playwright chromium..."
$ChromiumDir = Get-ChildItem -Path "$env:LOCALAPPDATA\ms-playwright" -Filter "chromium-*" -Directory | Select-Object -First 1
if ($ChromiumDir) {
    Copy-Item -Path $ChromiumDir.FullName -Destination "$PackageDir\playwright\$($ChromiumDir.Name)" -Recurse
}

Set-Content -Path "$PackageDir\start.bat" -Value "@echo off`nchcp 65001 >nul 2>&1`ncd /d `%~dp0`ntest-worker.exe`npause" -Encoding ASCII
Set-Content -Path "$PackageDir\README.txt" -Value "Test Worker - Windows (Nuitka Build)`nBuild Version: $BuildVersion" -Encoding UTF8

Write-Host "=========================================="
Write-Host "Build complete!"
Write-Host "Package: $PackageDir"
Write-Host "=========================================="

# Build installer (optional)
if ($BuildInstaller) {
    Write-Host "Building installer (via -BuildInstaller flag)..."
    & "$ProjectRoot\installer\build_installer.ps1" -Version $BuildVersion
    if ($LASTEXITCODE -ne 0) {
        Write-Warning "Installer build failed, but EXE package is available"
    }
} else {
    Write-Host ""
    Write-Host "Build installer? (for distribution)"
    $BuildInstallerChoice = Read-Host "Enter 'y' to build, or skip"

    if ($BuildInstallerChoice -eq 'y') {
        Write-Host "Building installer..."
        & "$ProjectRoot\installer\build_installer.ps1" -Version $BuildVersion
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