# Nuitka Build Script for Windows
param(
    [string]$Version = "2.0.0",
    [string]$OutputDir = "dist\windows",
    [string]$PythonPath = "",      # Specify Python executable path
    [string]$PerfwinWheel = "D:\code\perfwin\target\wheels\perfwin-0.4.0-cp312-cp312-win_amd64.whl",  # perfwin wheel path
    [string]$PerfharmonyWheel = "D:\code\perfharmony\target\wheels\perfharmony-0.2.2-cp312-cp312-win_amd64.whl",  # perfharmony wheel path
    [string]$WinControlWheel = "D:\code\win-control\target\wheels\win_control-0.1.5-cp312-cp312-win_amd64.whl",  # win-control wheel path
    [string]$JavaRuntimePath = "",  # JRE 17+ 根目录；将复制到 tools\jre 供鸿蒙官方 Java Bridge 使用
    [string]$JavaCompilerPath = "",  # 可选：JDK 中 javac.exe 的路径；未指定时从 PATH 查找
    [switch]$Clean,
    [switch]$BuildInstaller
)

# Project root directory (absolute path to avoid relative path issues)
# $PSScriptRoot is the script directory (scripts), get its parent as project root
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

# Change to project root directory
Set-Location $ProjectRoot

# 编译鸿蒙官方 Java Bridge。目标机器运行时只需要 JRE，javac 仅在打包机上使用。
$HarmonyJar = "$ProjectRoot\tools\harmony\hosScrcpy-1.0.15-beta.jar"
$HarmonyBridgeSource = "$ProjectRoot\worker\platforms\harmony_official\java\StreamBridge.java"
$HarmonyBridgeOutput = "$ProjectRoot\tools\harmony\bridge"
$JavacExe = $null

if ($JavaCompilerPath -ne "") {
    $JavaCompilerCandidate = $JavaCompilerPath
    if (Test-Path $JavaCompilerCandidate -PathType Container) {
        $JavaCompilerCandidate = Join-Path $JavaCompilerCandidate "bin\javac.exe"
    }
    if (-not (Test-Path $JavaCompilerCandidate -PathType Leaf)) {
        Write-Error "javac not found at: $JavaCompilerPath"
        exit 1
    }
    $JavacExe = (Resolve-Path $JavaCompilerCandidate).Path
} else {
    $JavacCommand = Get-Command javac -ErrorAction SilentlyContinue
    if ($JavacCommand) {
        $JavacExe = $JavacCommand.Source
    }
}

if (-not $JavacExe) {
    Write-Error "未找到 javac。构建鸿蒙官方 Bridge 需要安装 JDK，运行目标机仍只需要 JRE。可通过 -JavaCompilerPath 指定 javac.exe。"
    exit 1
}
if (-not (Test-Path $HarmonyJar -PathType Leaf)) {
    Write-Error "HOScrcpy JAR 不存在: $HarmonyJar"
    exit 1
}
if (-not (Test-Path $HarmonyBridgeSource -PathType Leaf)) {
    Write-Error "StreamBridge Java 源码不存在: $HarmonyBridgeSource"
    exit 1
}

Write-Host "[0/6] Compiling Harmony official Java Bridge..."
New-Item -ItemType Directory -Force -Path $HarmonyBridgeOutput | Out-Null
# 清理旧的内部类，防止源码删除内部类后旧 class 仍被打进发布包。
Get-ChildItem -Path $HarmonyBridgeOutput -Filter "StreamBridge*.class" -File -ErrorAction SilentlyContinue |
    Remove-Item -Force

& $JavacExe -encoding UTF-8 -cp $HarmonyJar -d $HarmonyBridgeOutput $HarmonyBridgeSource
if ($LASTEXITCODE -ne 0) {
    Write-Error "Harmony StreamBridge 编译失败"
    exit 1
}
if (-not (Test-Path "$HarmonyBridgeOutput\StreamBridge.class" -PathType Leaf)) {
    Write-Error "Harmony StreamBridge 编译完成但未生成 StreamBridge.class"
    exit 1
}
Write-Host "  Java Bridge compiled with: $JavacExe"

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
$VenvPython = "$VenvPath\Scripts\python.exe"
$VenvPip = "$VenvPath\Scripts\pip.exe"

$nuitkaInstalled = & $VenvPython -c "import nuitka; print('ok')" 2>$null
if ($nuitkaInstalled -ne "ok") {
    Write-Host "Installing Nuitka in the build virtual environment..."
    & $VenvPip install nuitka ordered-set zstandard
}

Write-Host "[2/6] Installing dependencies..."
& $VenvPip install --upgrade pip
& $VenvPip install nuitka ordered-set zstandard
& $VenvPip install -e "."

# Install perfwin wheel
if ($PerfwinWheel -ne "" -and (Test-Path $PerfwinWheel)) {
    Write-Host "  Installing perfwin wheel: $PerfwinWheel"
    & $VenvPip install $PerfwinWheel
} else {
    Write-Warning "perfwin wheel not found at: $PerfwinWheel"
    Write-Warning "Performance monitoring may not work!"
}

# 安装 perfharmony wheel；构建环境包含 Harmony 性能采集能力。
if ($PerfharmonyWheel -ne "" -and (Test-Path $PerfharmonyWheel)) {
    Write-Host "  Installing perfharmony wheel: $PerfharmonyWheel"
    & $VenvPip install $PerfharmonyWheel
    if ($LASTEXITCODE -ne 0) {
        Write-Error "perfharmony wheel install failed"
        exit 1
    }
} else {
    Write-Error "perfharmony wheel not found at: $PerfharmonyWheel"
    Write-Error "Build the matching CPython wheel before packaging the Worker"
    exit 1
}

# Install win-control wheel
if ($WinControlWheel -ne "" -and (Test-Path $WinControlWheel)) {
    Write-Host "  Installing win-control wheel: $WinControlWheel"
    & $VenvPip install $WinControlWheel
} else {
    Write-Warning "win-control wheel not found at: $WinControlWheel"
    Write-Warning "System control actions (set_resolution, set_volume, audio_device) may not work!"
}

# Build Rust sidecar (if cargo is available)
$CargoExe = Get-Command cargo -ErrorAction SilentlyContinue
$SidecarDir = "$ProjectRoot\rust\windows-screen-sidecar"
$SidecarExe = "$ProjectRoot\tools\windows-screen-sidecar.exe"
if ($CargoExe -and (Test-Path "$SidecarDir\Cargo.toml")) {
    Write-Host "[2.5/6] Building Rust sidecar..."
    Set-Location $SidecarDir
    cargo build --release 2>&1 | Out-Null
    if ($LASTEXITCODE -eq 0) {
        $ReleaseExe = "$SidecarDir\target\release\windows-screen-sidecar.exe"
        if (Test-Path $ReleaseExe) {
            Copy-Item -Path $ReleaseExe -Destination $SidecarExe -Force
            Write-Host "  Rust sidecar built and copied to tools/"
        }
    } else {
        Write-Warning "Rust sidecar build failed, Windows screen features may not work"
    }
    Set-Location $ProjectRoot
} else {
    Write-Host "[2.5/6] Skipping Rust sidecar (cargo not found or dir not exists)"
    if (-not (Test-Path $SidecarExe)) {
        Write-Warning "windows-screen-sidecar.exe not found in tools/, Windows screen features may not work"
    }
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
    "--include-package=perfharmony"
    "--include-package-data=perfharmony"
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
    "--include-package=av"
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

& $VenvPython -m nuitka $nuitkaArgs

if ($LASTEXITCODE -ne 0) {
    Write-Error "Nuitka build failed!"
    Remove-Item "$ProjectRoot\worker\_version.py" -ErrorAction SilentlyContinue
    exit 1
}

Remove-Item "$ProjectRoot\worker\_version.py" -ErrorAction SilentlyContinue

Write-Host "[7/6] Creating release package..."
# OutputDir may be relative or absolute, convert to absolute path
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

# 官方鸿蒙投屏 Bridge 运行时只需要 JRE 17+，不要在目标机器上依赖 javac/JDK。
if ($JavaRuntimePath -ne "") {
    $JavaExe = Join-Path $JavaRuntimePath "bin\java.exe"
    if (-not (Test-Path $JavaExe)) {
        Write-Error "Java runtime not found: $JavaExe"
        exit 1
    }
    Write-Host "Copying Java runtime for Harmony official bridge..."
    $JreTarget = "$PackageDir\tools\jre"
    if (Test-Path $JreTarget) { Remove-Item -Recurse -Force $JreTarget }
    New-Item -ItemType Directory -Force -Path $JreTarget | Out-Null
    Copy-Item -Path "$JavaRuntimePath\*" -Destination $JreTarget -Recurse -Force
    Write-Host "  Java runtime copied to tools/jre"
} else {
    Write-Warning "JavaRuntimePath is empty; the package requires an external JRE 17+ configured in harmony_official.java_path"
}

# Verify the bundled Harmony module can be imported with the matching CPython ABI.
$oldPythonPath = $env:PYTHONPATH
$env:PYTHONPATH = "$PackageDir;$PackageDir\perfharmony"
& "$VenvPath\Scripts\python.exe" -c "import perfharmony; assert perfharmony.__version__ == '0.2.2'; print('perfharmony import smoke test passed')"
$smokeExit = $LASTEXITCODE
$env:PYTHONPATH = $oldPythonPath
if ($smokeExit -ne 0) {
    Write-Error "Bundled perfharmony import smoke test failed"
    exit 1
}

# Also ensure assets and config are complete
Write-Host "Copying assets directory..."
if (Test-Path "$PackageDir\assets") { Remove-Item -Recurse -Force "$PackageDir\assets" }
Copy-Item -Path "$ProjectRoot\assets" -Destination "$PackageDir\assets" -Recurse -Force

Write-Host "Copying config directory..."
if (Test-Path "$PackageDir\config") { Remove-Item -Recurse -Force "$PackageDir\config" }
Copy-Item -Path "$ProjectRoot\config" -Destination "$PackageDir\config" -Recurse -Force

if ($JavaRuntimePath -ne "") {
    # 发布包一律指向随包 JRE，开发机配置中的绝对 JDK 路径不能带到用户机器。
    $PackagedConfig = "$PackageDir\config\worker.yaml"
    $ConfigText = Get-Content -Path $PackagedConfig -Raw -Encoding UTF8
    $ConfigText = [regex]::Replace(
        $ConfigText,
        '(?m)^(\s*java_path:)\s*.*$',
        '$1 tools/jre/bin/java.exe'
    )
    Set-Content -Path $PackagedConfig -Value $ConfigText -Encoding UTF8
}

# Copy minicap binary files (Nuitka --include-data-dir may miss)
Write-Host "Copying minicap static files..."
# Nuitka --include-data-dir creates directories under root, but may be incomplete
# Manually copy to ensure minicap-shared directory (with .so files) is also included
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

# Copy HWiNFO64.EXE (Nuitka --include-package-data does not include .exe files)
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
Write-Host "=========================================="\r\r
