<#
.SYNOPSIS
    启动应用程序，支持重启和窗口激活

.DESCRIPTION
    根据参数启动或激活应用程序：
    - restart=true: 先关闭进程，等待1秒，再启动
    - restart=false 或不传:
      - 进程已存在: 激活窗口
      - 进程不存在: 启动进程

.PARAMETER AppPath
    应用程序的完整路径

.PARAMETER Restart
    是否强制重启。如果为 true，启动前会先关闭现有进程

.EXAMPLE
    .\start_app.ps1 -AppPath "C:\Program Files\MyApp\app.exe"
    .\start_app.ps1 -AppPath "C:\Program Files\MyApp\app.exe" -Restart $true
#>

param(
    [Parameter(Mandatory=$true)]
    [string]$AppPath,

    [Parameter(Mandatory=$false)]
    [bool]$Restart = $false
)

# 从路径提取进程名（不含扩展名）
$ProcessName = [System.IO.Path]::GetFileNameWithoutExtension($AppPath)

# 检查进程是否存在
$existingProcess = Get-Process -Name $ProcessName -ErrorAction SilentlyContinue

if ($Restart) {
    # 模式1: 强制重启
    if ($existingProcess) {
        Write-Host "正在关闭进程: $ProcessName"
        Stop-Process -Name $ProcessName -Force -ErrorAction SilentlyContinue
        Start-Sleep -Seconds 1
    }

    Write-Host "正在启动: $AppPath"
    Start-Process -FilePath $AppPath
    Write-Host "启动成功: $ProcessName"
}
else {
    # 模式2: 不强制重启
    if ($existingProcess) {
        # 进程已存在，尝试激活窗口
        Write-Host "进程已存在，尝试激活窗口: $ProcessName"

        # 使用 WScript.Shell 的 AppActivate 激活窗口
        $wshell = New-Object -ComObject WScript.Shell
        $activated = $wshell.AppActivate($ProcessName)

        if ($activated) {
            Write-Host "窗口激活成功: $ProcessName"
        } else {
            Write-Host "窗口激活失败（可能没有可见窗口）: $ProcessName"
        }
    } else {
        # 进程不存在，启动
        Write-Host "正在启动: $AppPath"
        Start-Process -FilePath $AppPath
        Write-Host "启动成功: $ProcessName"
    }
}