# installer/build_installer.ps1
# Windows Installer Build Script (NSIS)

param(
    [string]$Version = "",
    [string]$BuildOutput = "..\dist\windows\test-worker"
)

Write-Host "=========================================="
Write-Host "Building Test Worker Installer (NSIS)"
Write-Host "=========================================="

# Check NSIS
$NsisPath = "C:\Program Files (x86)\NSIS\makensis.exe"
if (-not (Test-Path $NsisPath)) {
    $NsisPath = "C:\Program Files\NSIS\makensis.exe"
}
if (-not (Test-Path $NsisPath)) {
    $NsisPath = (Get-Command makensis -ErrorAction SilentlyContinue).Source
}
if (-not $NsisPath -or -not (Test-Path $NsisPath)) {
    Write-Error "NSIS not found!"
    Write-Host "Please download from: https://nsis.sourceforge.io/Download"
    exit 1
}

Write-Host "NSIS found: $NsisPath"

# Check build output
$OutputDir = Join-Path $PSScriptRoot $BuildOutput
if (-not (Test-Path $OutputDir)) {
    Write-Error "Build output not found: $OutputDir"
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

# Read default values from worker.yaml
$WorkerYaml = Join-Path $PSScriptRoot "..\config\worker.yaml"
if (Test-Path $WorkerYaml) {
    $YamlContent = Get-Content $WorkerYaml

    # Extract platform_api
    $PlatformApiMatch = $YamlContent | Select-String 'platform_api:\s*"([^"]+)"'
    if ($PlatformApiMatch) {
        $PlatformApi = $PlatformApiMatch.Matches.Groups[1].Value
        Write-Host "Platform API from config: $PlatformApi"
    } else {
        $PlatformApi = "http://192.168.0.102:8000"
        Write-Host "Platform API default: $PlatformApi"
    }

    # Extract ocr_service
    $OcrServiceMatch = $YamlContent | Select-String 'ocr_service:\s*"([^"]+)"'
    if ($OcrServiceMatch) {
        $OcrService = $OcrServiceMatch.Matches.Groups[1].Value
        Write-Host "OCR Service from config: $OcrService"
    } else {
        $OcrService = "http://192.168.0.102:9021"
        Write-Host "OCR Service default: $OcrService"
    }
} else {
    Write-Warning "worker.yaml not found, using hardcoded defaults"
    $PlatformApi = "http://192.168.0.102:8000"
    $OcrService = "http://192.168.0.102:9021"
}

# Check dist directory
$DistDir = Join-Path $PSScriptRoot "..\dist"
if (-not (Test-Path $DistDir)) {
    New-Item -ItemType Directory -Force -Path $DistDir | Out-Null
}

# Compile installer script
Write-Host "Compiling installer script..."
$ScriptPath = Join-Path $PSScriptRoot "installer.nsi"

& $NsisPath "/DVERSION=$Version" "/DPLATFORM_API=$PlatformApi" "/DOCR_SERVICE=$OcrService" $ScriptPath

if ($LASTEXITCODE -ne 0) {
    Write-Error "NSIS compilation failed!"
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