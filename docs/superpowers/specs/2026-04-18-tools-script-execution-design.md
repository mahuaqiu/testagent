---
name: tools-script-execution
description: Worker 外部脚本执行机制设计
type: project
---

# Worker 外部脚本执行机制设计

## 背景

Worker 打包为 exe 后，不再依赖 Python 环境。需要一种机制支持执行复杂任务：
- 播放媒体（PPT、视频）
- 软件安装（下载、解压、安装）
- 文件操作（下载、解压、复制）

现有 `cmd_exec` action 可执行命令，但需要：
1. 脚本打包后能被找到
2. 调用方式简洁易用
3. 执行日志可追溯

## 设计目标

- 不依赖机器上的 Python 环境
- 脚本打包时更新，支持远程下发
- 调用时传参方便
- 执行输出记录到日志

## 方案

使用 PowerShell（Windows）和 Shell（Mac）脚本，通过 `cmd_exec` action 执行。

### 核心机制

1. **tools 目录**：存放脚本文件，打包时带入 exe 目录
2. **路径占位符**：`@tools/脚本名` 自动替换为完整路径
3. **日志增强**：stdout/stderr 截取后 500 字符输出

## 目录结构

```
项目根目录/
├── tools/                  # 新增
│   ├── play_ppt.ps1        # Windows PowerShell 脚本
│   ├── download_install.ps1
│   ├── play_video.sh       # Mac Shell 脚本
│   └── ...
├── config/
├── assets/
├── worker/
└── scripts/
    └── pyinstaller.spec    # 添加 tools 到打包数据
```

打包后：

```
dist/test-worker/
├── test-worker.exe
├── config/
├── assets/
├── tools/                  # 脚本目录
│   ├── play_ppt.ps1
│   └── ...
├── _internal/
└── VERSION
```

## 调用格式

使用 `@tools/脚本名` 占位符，action 内部自动替换为完整路径：

```json
// 播放 PPT（Windows）
{
  "action_type": "cmd_exec",
  "value": "powershell -ExecutionPolicy Bypass -File \"@tools/play_ppt.ps1\" -FilePath \"C:\\demo.pptx\" -Duration 60",
  "timeout": 120000
}

// 下载并安装（Windows）
{
  "action_type": "cmd_exec",
  "value": "powershell -ExecutionPolicy Bypass -File \"@tools/download_install.ps1\" -Url \"https://xxx/app.zip\" -TargetDir \"C:\\Apps\"",
  "timeout": 300000
}

// 播放视频（Mac）
{
  "action_type": "cmd_exec",
  "value": "bash @tools/play_video.sh /path/to/video.mp4 30",
  "timeout": 60000
}
```

## 实现改动

### 1. 路径工具函数

新增 `worker/tools.py`，获取 tools 目录路径：

```python
import os
import sys

def get_tools_dir() -> str:
    """获取 tools 目录的完整路径。"""
    if getattr(sys, 'frozen', False):
        # 打包后：exe 所在目录
        base_dir = os.path.dirname(sys.executable)
    else:
        # 开发时：项目根目录
        base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base_dir, 'tools')
```

### 2. cmd_exec 改动

`worker/actions/cmd_exec.py`：

```python
from worker.tools import get_tools_dir

def execute(...):
    cmd = action.value
    
    # 替换 @tools/ 占位符
    tools_dir = get_tools_dir()
    cmd = cmd.replace('@tools/', tools_dir + '/')
    
    # 执行命令...
    result = run_cmd(cmd, shell=True, timeout=timeout_sec)
    
    # 日志增强
    if result.stdout:
        stdout_preview = result.stdout[-500:] if len(result.stdout) > 500 else result.stdout
        logger.info(f"Script output: {stdout_preview}")
    if result.stderr:
        stderr_preview = result.stderr[-500:] if len(result.stderr) > 500 else result.stderr
        if result.returncode != 0:
            logger.error(f"Script error: {stderr_preview}")
    
    return ActionResult(...)
```

### 3. pyinstaller.spec 改动

`scripts/pyinstaller.spec` 添加 tools 目录：

```python
datas = [
    (os.path.join(PROJECT_ROOT, 'config'), 'config'),
    (os.path.join(PROJECT_ROOT, 'assets'), 'assets'),
    (os.path.join(PROJECT_ROOT, 'tools'), 'tools'),  # 新增
]
```

### 4. tools 目录

创建 `tools/` 目录，放入示例脚本：

- `play_ppt.ps1` - 播放 PPT（使用 PowerPoint COM 对象）
- `play_video.ps1` - 播放视频（使用 Windows Media Player）
- `download_install.ps1` - 下载解压安装
- 其他脚本按需添加

## 日志输出示例

```
[INFO] Executing command: powershell -ExecutionPolicy Bypass -File "D:\test-worker\tools\play_ppt.ps1"...
[INFO] Command completed: exit_code=0
[INFO] Script output: ...播放完成，已自动关闭 PowerPoint

[ERROR] Command failed: exit_code=1
[ERROR] Script error: ...无法找到文件 C:\demo.pptx
```

## 远程下发接口

新增 `/worker/scripts` 接口，支持远程下发脚本到 `tools/` 目录，无需重启 Worker。

### 接口定义

```python
class ScriptUpdateRequest(BaseModel):
    """脚本更新请求。"""
    name: str = Field(..., description="脚本名称，如 play_ppt.ps1")
    content: str = Field(..., description="脚本内容")
    version: str = Field(..., description="脚本版本号，格式：YYYYMMDD-HHMMSS")
    overwrite: bool = Field(True, description="是否覆盖已有脚本")
```

### 请求示例

```json
POST /worker/scripts
{
  "name": "play_ppt.ps1",
  "content": "param([string]$FilePath...) ...",
  "version": "20260418-120000",
  "overwrite": true
}
```

### 响应示例

**成功**：
```json
{
  "status": "success",
  "message": "脚本更新成功",
  "name": "play_ppt.ps1",
  "version": "20260418-120000",
  "path": "D:\\test-worker\\tools\\play_ppt.ps1"
}
```

**版本相同，无需更新**：
```json
{
  "status": "success",
  "message": "脚本版本相同，无需更新",
  "name": "play_ppt.ps1",
  "version": "20260418-120000",
  "updated": false
}
```

**失败（非法脚本名称）**：
```json
{
  "status": "error",
  "message": "脚本名称不合法，只允许 .ps1/.sh/.bat 扩展名"
}
```

**失败（禁止覆盖）**：
```json
{
  "status": "error",
  "message": "脚本已存在且 overwrite=false"
}
```

### 处理流程

1. **版本格式校验**：`YYYYMMDD-HHMMSS`
2. **脚本名称校验**：只允许 `.ps1`、`.sh`、`.bat` 扩展名，禁止路径穿越（如 `../xxx.ps1`）
3. **版本比较**：读取 `tools/.versions.json`，相同版本跳过
4. **覆盖检查**：`overwrite=false` 且脚本已存在时拒绝
5. **保存脚本**：写入 `tools/{name}`
6. **更新版本记录**：更新 `tools/.versions.json`
7. **返回响应**：不触发重启

### 版本管理

`tools/.versions.json` 记录每个脚本的版本：

```json
{
  "play_ppt.ps1": "20260418-120000",
  "download_install.ps1": "20260415-100000",
  "play_video.sh": "20260410-080000"
}
```

每个脚本独立版本管理，可单独更新。

### 实现改动

#### server.py 新增接口

```python
class ScriptUpdateRequest(BaseModel):
    """脚本更新请求。"""
    name: str = Field(..., description="脚本名称，如 play_ppt.ps1")
    content: str = Field(..., description="脚本内容")
    version: str = Field(..., description="脚本版本号，格式：YYYYMMDD-HHMMSS")
    overwrite: bool = Field(True, description="是否覆盖已有脚本")

_script_update_lock = threading.Lock()

@app.post("/worker/scripts")
async def update_worker_script(request: ScriptUpdateRequest):
    """更新 Worker 脚本。"""
    # 1. 版本格式校验
    # 2. 脚本名称校验（扩展名 + 路径穿越）
    # 3. 并发保护
    # 4. 版本比较
    # 5. 覆盖检查
    # 6. 保存脚本
    # 7. 更新版本记录
    # 8. 返回响应（不重启）
```

#### worker/tools.py 新增函数

```python
def save_script(name: str, content: str) -> str:
    """保存脚本到 tools 目录，返回完整路径。"""
    tools_dir = get_tools_dir()
    os.makedirs(tools_dir, exist_ok=True)
    script_path = os.path.join(tools_dir, name)
    with open(script_path, 'w', encoding='utf-8') as f:
        f.write(content)
    return script_path

def get_script_version(name: str) -> str | None:
    """获取脚本版本号。"""
    versions_file = os.path.join(get_tools_dir(), '.versions.json')
    if not os.path.exists(versions_file):
        return None
    with open(versions_file, 'r', encoding='utf-8') as f:
        versions = json.load(f)
    return versions.get(name)

def update_script_version(name: str, version: str) -> None:
    """更新脚本版本记录。"""
    tools_dir = get_tools_dir()
    versions_file = os.path.join(tools_dir, '.versions.json')
    os.makedirs(tools_dir, exist_ok=True)

    # 读取现有版本记录
    versions = {}
    if os.path.exists(versions_file):
        with open(versions_file, 'r', encoding='utf-8') as f:
            versions = json.load(f)

    # 更新版本
    versions[name] = version

    # 保存版本记录
    with open(versions_file, 'w', encoding='utf-8') as f:
        json.dump(versions, f, indent=2)

def validate_script_name(name: str) -> bool:
    """校验脚本名称合法性。"""
    # 只允许合法扩展名
    allowed_exts = {'.ps1', '.sh', '.bat'}
    ext = os.path.splitext(name)[1].lower()
    if ext not in allowed_exts:
        return False

    # 禁止路径穿越
    if '..' in name or '/' in name or '\\' in name:
        return False

    return True
```

## 脚本示例

### play_ppt.ps1（播放 PPT）

```powershell
param(
    [string]$FilePath,    # PPT 文件路径
    [int]$Duration = 60   # 播放时长（秒）
)

# 检查文件存在
if (-not (Test-Path $FilePath)) {
    Write-Error "文件不存在: $FilePath"
    exit 1
}

# 使用 PowerPoint COM 对象播放
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
```

### download_install.ps1（下载解压安装）

```powershell
param(
    [string]$Url,           # 下载地址
    [string]$TargetDir,     # 目标目录
    [string]$InstallerName  # 安装程序名称（可选）
)

# 创建目标目录
New-Item -ItemType Directory -Force -Path $TargetDir | Out-Null

# 下载文件
$FileName = Split-Path $Url -Leaf
$DownloadPath = Join-Path $TargetDir $FileName

Write-Output "下载: $Url -> $DownloadPath"
Invoke-WebRequest -Uri $Url -OutFile $DownloadPath

# 如果是 zip 文件，解压
if ($FileName -like "*.zip") {
    Write-Output "解压: $DownloadPath"
    Expand-Archive -Path $DownloadPath -DestinationPath $TargetDir -Force
    
    # 寻找安装程序
    $Installer = Get-ChildItem -Path $TargetDir -Filter "*.exe" -Recurse | Select-Object -First 1
    if ($Installer) {
        Write-Output "安装: $Installer.FullName"
        Start-Process -FilePath $Installer.FullName -ArgumentList "/S" -Wait
    }
}
# 如果是 exe 文件，直接静默安装
elseif ($FileName -like "*.exe") {
    Write-Output "安装: $DownloadPath"
    Start-Process -FilePath $DownloadPath -ArgumentList "/S" -Wait
}

Write-Output "完成: $Url"
```

## 风险与注意事项

1. **PowerShell 执行策略**：使用 `-ExecutionPolicy Bypass` 绕过限制
2. **路径中的空格**：脚本路径用双引号包裹
3. **超时设置**：长时间任务（下载安装）需要设置足够 timeout
4. **管理员权限**：Worker 已配置 `uac_admin=True`，安装类脚本有权限