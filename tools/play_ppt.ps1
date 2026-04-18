<#
.SYNOPSIS
    启动 PowerPoint 文件。

.PARAMETER FilePath
    PPT 文件路径。

.PARAMETER AutoPlay
    是否开启幻灯片放映模式，默认 true。
    - true: 启动后进入放映模式
    - false: 仅打开PPT编辑模式
#>

param(
    [string]$FilePath,
    [bool]$AutoPlay = $true
)

# 检查文件存在
if (-not (Test-Path $FilePath)) {
    Write-Error "文件不存在: $FilePath"
    exit 1
}

Write-Output "开始打开: $FilePath, AutoPlay: $AutoPlay"

# 使用 PowerPoint COM 对象
try {
    $ppt = New-Object -ComObject PowerPoint.Application
    $presentation = $ppt.Presentations.Open($FilePath)

    if ($AutoPlay) {
        # 开始幻灯片放映，启动后立即返回
        $presentation.SlideShowSettings.Run()
        Write-Output "幻灯片放映已启动: $FilePath"
    } else {
        Write-Output "PPT 已打开（编辑模式）: $FilePath"
    }

    exit 0
}
catch {
    Write-Error "操作失败: $_"
    exit 1
}