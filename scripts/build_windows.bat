@echo off
REM Windows 打包启动脚本 - 无需修改 PowerShell 执行策略
REM 此脚本会绕过执行策略限制运行 PowerShell 脚本
REM
REM 用法:
REM   build_windows.bat                          (使用默认参数)
REM   build_windows.bat -Version 3.0.0           (指定版本号)
REM   build_windows.bat -OutputDir dist\custom   (指定输出目录)

setlocal EnableDelayedExpansion

REM 获取脚本所在目录并切换到项目根目录
set "SCRIPT_DIR=%~dp0"
set "PROJECT_ROOT=%SCRIPT_DIR%.."
cd /d "%PROJECT_ROOT%"

REM PowerShell 脚本路径
set "PS_SCRIPT=%SCRIPT_DIR%build_windows.ps1"

REM 检查 PowerShell 脚本是否存在
if not exist "%PS_SCRIPT%" (
    echo Error: build_windows.ps1 not found!
    echo Expected location: %PS_SCRIPT%
    pause
    exit /b 1
)

REM 检查 pyproject.toml 是否存在（确认项目根目录）
if not exist "pyproject.toml" (
    echo Error: pyproject.toml not found!
    echo Please ensure you are in the project root directory.
    pause
    exit /b 1
)

REM 构建 PowerShell 参数（直接传递所有参数）
set "PS_ARGS=%*"

echo ==========================================
echo Test Worker Build Script
echo ==========================================
echo Project Root: %CD%
echo PowerShell Script: %PS_SCRIPT%
if not "%PS_ARGS%"=="" echo Arguments: %PS_ARGS%
echo ==========================================
echo.

REM 使用 Bypass 策略运行 PowerShell 脚本
powershell.exe -ExecutionPolicy Bypass -NoProfile -File "%PS_SCRIPT%" %PS_ARGS%

if %ERRORLEVEL% neq 0 (
    echo.
    echo ==========================================
    echo Build failed with error code: %ERRORLEVEL%
    echo ==========================================
    pause
    exit /b %ERRORLEVEL%
)

echo.
echo ==========================================
echo Build completed successfully!
echo ==========================================