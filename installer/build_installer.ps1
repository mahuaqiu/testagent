# installer/build_installer.ps1
# Windows Installer Build Script

param(
    [string]$Version = "",
    [string]$PyInstallerOutput = "..\dist\windows\test-worker"
)

Write-Host "=========================================="
Write-Host "Building Test Worker Installer"
Write-Host "=========================================="

# Check Inno Setup
$InnoPath = "C:\Program Files (x86)\Inno Setup 6\ISCC.exe"
if (-not (Test-Path $InnoPath)) {
    $InnoPath = "C:\Program Files\Inno Setup 6\ISCC.exe"
}
if (-not (Test-Path $InnoPath)) {
    $InnoPath = "D:\Program Files\Inno Setup 6\ISCC.exe"
}
if (-not (Test-Path $InnoPath)) {
    $InnoPath = (Get-Command ISCC -ErrorAction SilentlyContinue).Source
}
if (-not $InnoPath -or -not (Test-Path $InnoPath)) {
    Write-Error "Inno Setup 6 not found!"
    Write-Host "Please download from: https://jrsoftware.org/isdl.php"
    exit 1
}

# Check PyInstaller output
$OutputDir = Join-Path $PSScriptRoot $PyInstallerOutput
if (-not (Test-Path $OutputDir)) {
    Write-Error "PyInstaller output not found: $OutputDir"
    Write-Host "Please run scripts/build_windows.ps1 first"
    exit 1
}

# Auto-read version
if ($Version -eq "") {
    $VersionFile = Join-Path $OutputDir "VERSION"
    if (Test-Path $VersionFile) {
        $Version = Get-Content $VersionFile -Raw
        $Version = $Version.Trim()
        Write-Host "Auto-detected version: $Version"
    } else {
        $Version = Get-Date -Format "yyyyMMddHHmm"
        Write-Host "No VERSION file found, using timestamp: $Version"
    }
}

Write-Host "Version: $Version"

# Check dist directory
$DistDir = Join-Path $PSScriptRoot "..\dist"
if (-not (Test-Path $DistDir)) {
    New-Item -ItemType Directory -Force -Path $DistDir | Out-Null
}

# Compile installer script
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