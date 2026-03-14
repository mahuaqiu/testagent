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

# 创建发布包
Write-Host "[5/6] Creating release package..."
$PackageDir = "$OutputDir\test-worker-$Version"
New-Item -ItemType Directory -Force -Path $PackageDir | Out-Null

# 复制文件
Copy-Item "dist\test-worker.exe" $PackageDir
Copy-Item -Path "config" -Destination $PackageDir -Recurse

# 创建启动脚本
@"
@echo off
cd /d "%~dp0"
test-worker.exe --port 8080
pause
"@ | Out-File "$PackageDir\start.bat" -Encoding ASCII

# 创建 README
@"
Test Worker v$Version - Windows

Usage:
  1. Double-click start.bat
  2. Or run from command line: test-worker.exe --port 8080

Options:
  --port PORT           HTTP server port (default: 8080)
  --ocr-service URL     OCR service URL
  --platform-api URL    Platform API URL
  --help                Show help

Configuration:
  Edit config\worker.yaml to customize settings

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