# Windows Build Script (PowerShell)

param(
    [string]$Version = "2.0.0",
    [string]$OutputDir = "dist\windows",
    [switch]$Clean  # Use -Clean to force rebuild venv
)

Write-Host "=========================================="
Write-Host "Building Test Worker for Windows"
Write-Host "Version: $Version"
Write-Host "Output: $OutputDir"
Write-Host "=========================================="

# Check Python
if (-not (Get-Command python -ErrorAction SilentlyContinue)) {
    Write-Error "Python not found!"
    exit 1
}

# Virtual environment path
$VenvPath = "build_env"

# Check if we need to recreate virtual environment
if ($Clean -or -not (Test-Path $VenvPath)) {
    if (Test-Path $VenvPath) {
        Write-Host "[1/6] Removing old virtual environment..."
        Remove-Item -Recurse -Force $VenvPath
    }
    Write-Host "[1/6] Creating virtual environment..."
    python -m venv $VenvPath
} else {
    Write-Host "[1/6] Using existing virtual environment..."
}

# Activate virtual environment
& ".\$VenvPath\Scripts\Activate.ps1"

# Check if pyinstaller exists in venv
$PyinstallerExists = Test-Path ".\$VenvPath\Scripts\pyinstaller.exe"

if (-not $PyinstallerExists) {
    Write-Host "[2/6] Installing dependencies (pyinstaller not found in venv)..."
    pip install --upgrade pip
    pip install -e ".[all]"
    pip install pyinstaller
} else {
    Write-Host "[2/6] Dependencies already installed, skipping..."
}

# Check if Playwright chromium is already installed
$ChromiumPath = "$env:LOCALAPPDATA\ms-playwright\chromium-*"
$ChromiumInstalled = Test-Path $ChromiumPath

if ($ChromiumInstalled) {
    Write-Host "[3/6] Playwright chromium already installed, skipping..."
} else {
    Write-Host "[3/6] Installing Playwright browsers..."
    playwright install chromium
    if ($LASTEXITCODE -ne 0) {
        Write-Warning "Playwright browser installation may have issues"
    }
}

# Build
Write-Host "[4/6] Building executable..."
pyinstaller scripts/pyinstaller.spec --clean --noconfirm
if ($LASTEXITCODE -ne 0) {
    Write-Error "PyInstaller build failed!"
    deactivate
    exit 1
}

# Check generated file
$ExePath = "dist\test-worker.exe"
if (-not (Test-Path $ExePath)) {
    Write-Error "Executable not found: $ExePath"
    deactivate
    exit 1
}

# Create release package
Write-Host "[5/6] Creating release package..."
$PackageDir = "$OutputDir\test-worker-$Version"

# Clean old release directory
if (Test-Path $PackageDir) {
    Remove-Item -Recurse -Force $PackageDir
}
New-Item -ItemType Directory -Force -Path $PackageDir | Out-Null

# Move files to release directory
Move-Item $ExePath $PackageDir
Copy-Item -Path "config" -Destination $PackageDir -Recurse

# Create start script
@"
@echo off
chcp 65001 >nul 2>&1
cd /d "%~dp0"
test-worker.exe
pause
"@ | Out-File "$PackageDir\start.bat" -Encoding ASCII

# Create README
@"
Test Worker v$Version - Windows

Usage:
  1. Edit config\worker.yaml to configure settings
  2. Double-click start.bat to start the worker
  3. Or run from command line: test-worker.exe

Configuration:
  All settings are read from config\worker.yaml, including:
  - Server port (default: 8080)
  - OCR service URL
  - Platform API URL
  - Platform-specific options

Requirements:
  - For Android/iOS: ADB and libimobiledevice must be installed
  - For OCR: OCR service must be running
"@ | Out-File "$PackageDir\README.txt" -Encoding UTF8

# Deactivate virtual environment
deactivate

Write-Host "[6/6] Build complete!"
Write-Host "=========================================="
Write-Host "Build successful!"
Write-Host "Package: $PackageDir"
Write-Host ""
Write-Host "Note: Virtual environment preserved at: $VenvPath"
Write-Host "Use -Clean flag to rebuild from scratch: .\build_windows.bat -Clean"
Write-Host "=========================================="
