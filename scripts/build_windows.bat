@echo off
REM Windows Build Script - Runs PowerShell with Bypass policy
REM Usage:
REM   build_windows.bat              (default parameters)
REM   build_windows.bat -Version 3.0.0
REM   build_windows.bat -Clean       (rebuild venv from scratch)

setlocal EnableDelayedExpansion

set "SCRIPT_DIR=%~dp0"
set "PROJECT_ROOT=%SCRIPT_DIR%.."
cd /d "%PROJECT_ROOT%"

set "PS_SCRIPT=%SCRIPT_DIR%build_windows.ps1"

if not exist "%PS_SCRIPT%" (
    echo Error: build_windows.ps1 not found!
    echo Expected location: %PS_SCRIPT%
    pause
    exit /b 1
)

if not exist "pyproject.toml" (
    echo Error: pyproject.toml not found!
    echo Please run this script from the project root directory.
    pause
    exit /b 1
)

set "PS_ARGS=%*"

echo ==========================================
echo Test Worker Build Script
echo ==========================================
echo Project Root: %CD%
echo PowerShell Script: %PS_SCRIPT%
if not "%PS_ARGS%"=="" echo Arguments: %PS_ARGS%
echo ==========================================
echo.

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
