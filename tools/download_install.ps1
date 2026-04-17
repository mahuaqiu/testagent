<#
.SYNOPSIS
    下载文件并安装。

.PARAMETER Url
    下载地址。

.PARAMETER TargetDir
    目标目录。

.PARAMETER SilentArgs
    静默安装参数,默认 /S。
#>

param(
    [string]$Url,
    [string]$TargetDir,
    [string]$SilentArgs = "/S"
)

if (-not $Url) {
    Write-Error "Url 参数必填"
    exit 1
}

if (-not $TargetDir) {
    Write-Error "TargetDir 参数必填"
    exit 1
}

# 创建目标目录
New-Item -ItemType Directory -Force -Path $TargetDir | Out-Null

# 下载文件
$FileName = Split-Path $Url -Leaf
$DownloadPath = Join-Path $TargetDir $FileName

Write-Output "下载: $Url -> $DownloadPath"
try {
    Invoke-WebRequest -Uri $Url -OutFile $DownloadPath -UseBasicParsing
}
catch {
    Write-Error "下载失败: $_"
    exit 1
}

# 如果是 zip 文件,解压
if ($FileName -like "*.zip") {
    Write-Output "解压: $DownloadPath"
    try {
        Expand-Archive -Path $DownloadPath -DestinationPath $TargetDir -Force
    }
    catch {
        Write-Error "解压失败: $_"
        exit 1
    }

    # 寻找安装程序
    $Installer = Get-ChildItem -Path $TargetDir -Filter "*.exe" -Recurse | Select-Object -First 1
    if ($Installer) {
        Write-Output "安装: $($Installer.FullName)"
        try {
            Start-Process -FilePath $Installer.FullName -ArgumentList $SilentArgs -Wait
        }
        catch {
            Write-Error "安装失败: $_"
            exit 1
        }
    }
}
# 如果是 exe 文件,直接静默安装
elseif ($FileName -like "*.exe") {
    Write-Output "安装: $DownloadPath"
    try {
        Start-Process -FilePath $DownloadPath -ArgumentList $SilentArgs -Wait
    }
    catch {
        Write-Error "安装失败: $_"
        exit 1
    }
}

Write-Output "完成: $Url"
exit 0