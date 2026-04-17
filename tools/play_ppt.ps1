<#
.SYNOPSIS
    播放 PowerPoint 文件。

.PARAMETER FilePath
    PPT 文件路径。

.PARAMETER Duration
    播放时长(秒),默认 60。
#>

param(
    [string]$FilePath,
    [int]$Duration = 60
)

# 检查文件存在
if (-not (Test-Path $FilePath)) {
    Write-Error "文件不存在: $FilePath"
    exit 1
}

Write-Output "开始播放: $FilePath"

# 使用 PowerPoint COM 对象播放
try {
    $ppt = New-Object -ComObject PowerPoint.Application
    $presentation = $ppt.Presentations.Open($FilePath)

    # 开始幻灯片放映
    $presentation.SlideShowSettings.Run()

    # 等待指定时长
    Start-Sleep -Seconds $Duration

    # 关闭
    $presentation.Close()
    $ppt.Quit()

    Write-Output "播放完成: $FilePath, 时长: $Duration秒"
    exit 0
}
catch {
    Write-Error "播放失败: $_"
    exit 1
}