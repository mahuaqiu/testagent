@echo off
chcp 65001 >nul 2>&1
set PYTHONUTF8=1
set PYTHONIOENCODING=utf-8

cd /d "%~dp0"
cd ..

echo ==========================================
echo Building Test Worker with Nuitka (MSVC)
echo ==========================================

if not exist "python.exe" (
    echo Error: Python not found!
    pause
    exit /b 1
)

:: 检查 Nuitka
python -c "import nuitka" 2>nul
if errorlevel 1 (
    echo Installing Nuitka...
    pip install nuitka ordered-set zstandard
)

:: 检查 Visual Studio Build Tools
set "VS_WHERE=%ProgramFiles(x86)%\Microsoft Visual Studio\Installer\vswhere.exe"
if not exist "%VS_WHERE%" (
    echo Error: Visual Studio Build Tools not found!
    echo Download from: https://visualstudio.microsoft.com/visual-cpp-build-tools/
    pause
    exit /b 1
)

:: 创建虚拟环境
set VENV_PATH=build_env_nuitka
if exist "%VENV_PATH%" (
    echo [1/6] Using existing virtual environment...
) else (
    echo [1/6] Creating virtual environment...
    python -m venv "%VENV_PATH%"
)

call "%VENV_PATH%\Scripts\activate.bat"

echo [2/6] Installing dependencies...
pip install --upgrade pip -q
pip install nuitka ordered-set zstandard -q
pip install -e ".[all]" -q

echo [3/6] Generating version file...
for /f "tokens=*" %%i in ('powershell -Command "Get-Date -Format yyyyMMddHHmm"') do set BUILD_VERSION=%%i
echo VERSION = "%BUILD_VERSION%" > worker\_version.py

echo [4/6] Checking Playwright...
if not exist "%LOCALAPPDATA%\ms-playwright\chromium-*" (
    playwright install chromium
)

echo [5/6] Building with Nuitka...
python -m nuitka ^
    --mode=standalone ^
    worker\gui_main.py ^
    --output-filename=test-worker.exe ^
    --windows-console-mode=disable ^
    --windows-uac-admin ^
    --windows-icon-from-ico=assets\icon.ico ^
    --include-data-dir=config=config ^
    --include-data-dir=assets=assets ^
    --include-data-dir=tools=tools ^
    --enable-plugin=pyqt5 ^
    --include-package=uvicorn ^
    --include-package=fastapi ^
    --include-package=starlette ^
    --include-package=httpx ^
    --include-package=playwright ^
    --include-package=pyautogui ^
    --include-package=mss ^
    --include-package=cv2 ^
    --include-package=PIL ^
    --include-package=numpy ^
    --include-package=pydantic ^
    --include-package=pystray ^
    --include-package=uiautomator2 ^
    --include-package=tidevice3 ^
    --include-module=uvicorn.logging ^
    --include-module=uvicorn.loops ^
    --include-module=uvicorn.loops.auto ^
    --include-module=uvicorn.protocols ^
    --include-module=uvicorn.protocols.http ^
    --include-module=uvicorn.protocols.http.auto ^
    --include-module=uvicorn.protocols.websockets ^
    --include-module=uvicorn.protocols.websockets.auto ^
    --include-module=uvicorn.lifespan ^
    --include-module=uvicorn.lifespan.on ^
    --nofollow-import-to=pytest ^
    --nofollow-import-to=allure ^
    --nofollow-import-to=faker ^
    --nofollow-import-to=numpy._core.tests ^
    --nofollow-import-to=numpy.tests ^
    --nofollow-import-to=numpy.typing.tests ^
    --nofollow-import-to=numpy.lib.tests ^
    --nofollow-import-to=numpy.fft.tests ^
    --nofollow-import-to=numpy.linalg.tests ^
    --nofollow-import-to=numpy.ma.tests ^
    --nofollow-import-to=numpy.polynomial.tests ^
    --nofollow-import-to=numpy.random.tests ^
    --nofollow-import-to=numpy.matrixlib.tests ^
    --nofollow-import-to=PIL.tests ^
    --nofollow-import-to=cv2.tests ^
    --nofollow-import-to=cryptography.tests ^
    --nofollow-import-to=jinja2.tests ^
    --nofollow-import-to=pydantic.v1.tests ^
    --nofollow-import-to=sentry_sdk.integrations.openai_agents.tests ^
    --output-dir=dist\nuitka_build ^
    --show-progress ^
    --msvc=latest

if errorlevel 1 (
    echo Error: Nuitka build failed!
    del worker\_version.py 2>nul
    call deactivate
    pause
    exit /b 1
)

del worker\_version.py 2>nul

echo [6/6] Creating release package...
set PACKAGE_DIR=dist\windows\test-worker
if exist "%PACKAGE_DIR%" rd /s /q "%PACKAGE_DIR%"
mkdir "%PACKAGE_DIR%"

set BUILD_DIR=dist\nuitka_build\gui_main.dist
if not exist "%BUILD_DIR%" (
    echo Error: Build directory not found: %BUILD_DIR%
    call deactivate
    pause
    exit /b 1
)

xcopy "%BUILD_DIR%\*" "%PACKAGE_DIR%\" /E /Q /Y

echo %BUILD_VERSION% > "%PACKAGE_DIR%\VERSION"

echo Copying Playwright chromium...
for /d %%i in ("%LOCALAPPDATA%\ms-playwright\chromium-*") do (
    xcopy "%%i" "%PACKAGE_DIR%\playwright\%%~ni\" /E /Q /I /Y
)

(
echo @echo off
echo chcp 65001 ^>nul 2^>^&1
echo cd /d %%~dp0
echo test-worker.exe
echo pause
) > "%PACKAGE_DIR%\start.bat"

echo Test Worker - Windows (Nuitka Build) > "%PACKAGE_DIR%\README.txt"
echo Build Version: %BUILD_VERSION% >> "%PACKAGE_DIR%\README.txt"

call deactivate

echo ==========================================
echo Build complete!
echo Package: %PACKAGE_DIR%
echo ==========================================
pause