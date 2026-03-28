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
        Write-Host "[1/7] Removing old virtual environment..."
        Remove-Item -Recurse -Force $VenvPath
    }
    Write-Host "[1/7] Creating virtual environment..."
    python -m venv $VenvPath
} else {
    Write-Host "[1/7] Using existing virtual environment..."
}

# Activate virtual environment
& ".\$VenvPath\Scripts\Activate.ps1"

# Check if pyinstaller exists in venv
$PyinstallerExists = Test-Path ".\$VenvPath\Scripts\pyinstaller.exe"

if (-not $PyinstallerExists) {
    Write-Host "[2/7] Installing dependencies (pyinstaller not found in venv)..."
    pip install --upgrade pip
    pip install -e ".[all]"
    pip install pyinstaller
} else {
    Write-Host "[2/7] Dependencies already installed, skipping..."
}

# Check if Playwright chromium is already installed
$ChromiumPath = "$env:LOCALAPPDATA\ms-playwright\chromium-*"
$ChromiumInstalled = Test-Path $ChromiumPath

if ($ChromiumInstalled) {
    Write-Host "[3/7] Playwright chromium already installed, skipping..."
} else {
    Write-Host "[3/7] Installing Playwright browsers..."
    playwright install chromium
    if ($LASTEXITCODE -ne 0) {
        Write-Warning "Playwright browser installation may have issues"
    }
}

# Generate version file
Write-Host "[4/7] Generating version file..."
$BuildVersion = Get-Date -Format "yyyyMMddHHmm"
$VersionFile = "worker\_version.py"
$VersionContent = "VERSION = `"$BuildVersion`""
Set-Content -Path $VersionFile -Value $VersionContent -Encoding UTF8
Write-Host "Build version: $BuildVersion"

# Build
Write-Host "[5/7] Building executable..."
pyinstaller scripts/pyinstaller.spec --clean --noconfirm
if ($LASTEXITCODE -ne 0) {
    Write-Error "PyInstaller build failed!"
    Remove-Item $VersionFile -ErrorAction SilentlyContinue
    deactivate
    exit 1
}

# Clean up version file
Remove-Item $VersionFile -ErrorAction SilentlyContinue

# Check generated directory
$BuildDir = "dist\test-worker"
if (-not (Test-Path $BuildDir)) {
    Write-Error "Build directory not found: $BuildDir"
    deactivate
    exit 1
}

# Create release package (no version suffix)
Write-Host "[6/7] Creating release package..."
$PackageDir = "$OutputDir\test-worker"

# Clean old release directory
if (Test-Path $PackageDir) {
    Remove-Item -Recurse -Force $PackageDir
}
New-Item -ItemType Directory -Force -Path $PackageDir | Out-Null

# Move build directory to package
Move-Item "$BuildDir\*" $PackageDir

# Clean up empty build directory
if (Test-Path $BuildDir) {
    Remove-Item -Path $BuildDir -Force -Recurse -ErrorAction SilentlyContinue
}

# Copy Playwright chromium to package
Write-Host "Copying Playwright chromium..."
$SourcePlaywright = "$env:LOCALAPPDATA\ms-playwright"
$DestPlaywright = "$PackageDir\playwright"

$ChromiumDir = Get-ChildItem -Path $SourcePlaywright -Filter "chromium-*" -Directory | Select-Object -First 1
if ($ChromiumDir) {
    Copy-Item -Path $ChromiumDir.FullName -Destination "$DestPlaywright\$($ChromiumDir.Name)" -Recurse
    Write-Host "Copied chromium: $($ChromiumDir.Name)"
} else {
    Write-Warning "Playwright chromium not found at $SourcePlaywright"
}

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
Test Worker - Windows

Usage:
  1. Edit config\worker.yaml to configure settings
  2. Double-click start.bat to start the worker
  3. Or run from command line: test-worker.exe

Configuration:
  All settings are read from config\worker.yaml, including:
  - Server port (default: 8088)
  - IP address (optional, auto-detected if not specified)
  - OCR service URL
  - Platform API URL
  - Platform-specific options

Requirements:
  - For Android/iOS: ADB and libimobiledevice must be installed
  - For OCR: OCR service must be running

Build Version: $BuildVersion
"@ | Out-File "$PackageDir\README.txt" -Encoding UTF8

# Deactivate virtual environment
deactivate

Write-Host "[7/7] Build complete!"
Write-Host "=========================================="
Write-Host "Build successful!"
Write-Host "Package: $PackageDir"
Write-Host "Build Version: $BuildVersion"
Write-Host ""
Write-Host "Note: Virtual environment preserved at: $VenvPath"
Write-Host "Use -Clean flag to rebuild from scratch: .\build_windows.ps1 -Clean"
Write-Host "=========================================="