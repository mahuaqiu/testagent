@echo off
REM Build wrapper - calls PowerShell build script
REM Usage:
REM   build.bat                          (default)
REM   build.bat -PythonPath "C:\python\python.exe"
REM   build.bat -Clean                   (rebuild venv)
REM   build.bat -BuildInstaller          (build installer directly)

setlocal EnableDelayedExpansion

set "SCRIPT_DIR=%~dp0"
set "PROJECT_ROOT=%SCRIPT_DIR%.."
cd /d "%PROJECT_ROOT%"

set "PS_SCRIPT=%SCRIPT_DIR%build_windows.ps1"

if not exist "%PS_SCRIPT%" (
    echo Error: build_windows.ps1 not found!
    pause
    exit /b 1
)

powershell.exe -ExecutionPolicy Bypass -NoProfile -File "%PS_SCRIPT%" %*

if %ERRORLEVEL% neq 0 (
    echo Build failed with error code: %ERRORLEVEL%
    pause
    exit /b %ERRORLEVEL%
)