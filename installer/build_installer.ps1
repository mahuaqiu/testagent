# installer/build_installer.ps1
# Windows 安装包构建脚本

param(
    [string]$Version = "2.0.0",
    [string]$PyInstallerOutput = "..\dist\test-worker"
)

Write-Host "=========================================="
Write-Host "Building Test Worker Installer"
Write-Host "Version: $Version"
Write-Host "=========================================="

# 检查 Inno Setup
$InnoPath = "C:\Program Files (x86)\Inno Setup 6\ISCC.exe"
if (-not (Test-Path $InnoPath)) {
    $InnoPath = "C:\Program Files\Inno Setup 6\ISCC.exe"
}
if (-not (Test-Path $InnoPath)) {
    Write-Error "Inno Setup 6 not found!"
    Write-Host "Please download from: https://jrsoftware.org/isdl.php"
    exit 1
}

# 检查 PyInstaller 输出
$OutputDir = Join-Path $PSScriptRoot $PyInstallerOutput
if (-not (Test-Path $OutputDir)) {
    Write-Error "PyInstaller output not found: $OutputDir"
    Write-Host "Please run scripts/build_windows.ps1 first"
    exit 1
}

# 检查 dist 目录
$DistDir = Join-Path $PSScriptRoot "..\dist"
if (-not (Test-Path $DistDir)) {
    New-Item -ItemType Directory -Force -Path $DistDir | Out-Null
}

# 编译安装脚本
Write-Host "Compiling installer script..."
$ScriptPath = Join-Path $PSScriptRoot "installer.iss"

& $InnoPath "/DVersion=$Version" $ScriptPath

if ($LASTEXITCODE -ne 0) {
    Write-Error "Inno Setup compilation failed!"
    exit 1
}

$InstallerPath = Join-Path $DistDir "test-worker-installer.exe"
if (-not (Test-Path $InstallerPath)) {
    Write-Error "Installer not generated: $InstallerPath"
    exit 1
}

Write-Host "=========================================="
Write-Host "Installer build complete!"
Write-Host "Output: $InstallerPath"
Write-Host "Size: $([math]::Round((Get-Item $InstallerPath).Length / 1MB, 2)) MB"
Write-Host "=========================================="