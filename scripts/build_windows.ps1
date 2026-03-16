# Windows 打包脚本 (PowerShell)

param(
    [string]$Version = "2.0.0",
    [string]$OutputDir = "dist\windows"
)

Write-Host "=========================================="
Write-Host "Building Test Worker for Windows"
Write-Host "Version: $Version"
Write-Host "Output: $OutputDir"
Write-Host "=========================================="

# 检查 Python
if (-not (Get-Command python -ErrorAction SilentlyContinue)) {
    Write-Error "Python not found!"
    exit 1
}

# 创建虚拟环境
Write-Host "[1/6] Creating virtual environment..."
python -m venv build_env
.\build_env\Scripts\Activate.ps1

# 安装依赖
Write-Host "[2/6] Installing dependencies..."
pip install --upgrade pip
pip install -e ".[all]"
pip install pyinstaller

# 安装 Playwright 浏览器
Write-Host "[3/6] Installing Playwright browsers..."
playwright install chromium
if ($LASTEXITCODE -ne 0) {
    Write-Warning "Playwright browser installation may have issues"
}

# 打包
Write-Host "[4/6] Building executable..."
pyinstaller scripts/pyinstaller.spec --clean --noconfirm
if ($LASTEXITCODE -ne 0) {
    Write-Error "PyInstaller build failed!"
    deactivate
    exit 1
}

# 检查生成的文件
$ExePath = "dist\test-worker.exe"
if (-not (Test-Path $ExePath)) {
    Write-Error "Executable not found: $ExePath"
    deactivate
    exit 1
}

# 创建发布包
Write-Host "[5/6] Creating release package..."
$PackageDir = "$OutputDir\test-worker-$Version"

# 清理旧的发布目录
if (Test-Path $PackageDir) {
    Remove-Item -Recurse -Force $PackageDir
}
New-Item -ItemType Directory -Force -Path $PackageDir | Out-Null

# 移动文件到发布目录（而不是复制）
Move-Item $ExePath $PackageDir
Copy-Item -Path "config" -Destination $PackageDir -Recurse

# 创建启动脚本
@"
@echo off
cd /d "%~dp0"
test-worker.exe
pause
"@ | Out-File "$PackageDir\start.bat" -Encoding ASCII

# 创建 README
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

# 清理
Write-Host "[6/6] Cleaning up..."
deactivate
Remove-Item -Recurse -Force build_env -ErrorAction SilentlyContinue
Remove-Item -Recurse -Force build -ErrorAction SilentlyContinue

Write-Host "=========================================="
Write-Host "Build complete!"
Write-Host "Package: $PackageDir"
Write-Host "=========================================="