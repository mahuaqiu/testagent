# Worker 外部脚本执行机制实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 实现 Worker 外部脚本执行机制，支持通过 cmd_exec action 执行 PowerShell/Shell 脚本，并提供远程下发接口。

**Architecture:** 新增 `tools/` 目录存放脚本，`worker/tools.py` 提供路径解析和脚本管理函数，`cmd_exec.py` 支持 `@tools/` 占位符替换，`server.py` 新增 `/worker/scripts` 接口支持远程下发。

**Tech Stack:** Python 3.14, FastAPI, PowerShell, Shell

---

## 文件结构

| 文件 | 操作 | 说明 |
|------|------|------|
| `worker/tools.py` | 创建 | 路径解析 + 脚本管理函数 |
| `worker/actions/cmd_exec.py` | 修改 | @tools/ 占位符替换 + 日志增强 |
| `worker/server.py` | 修改 | 新增 /worker/scripts 接口 |
| `scripts/pyinstaller.spec` | 修改 | 添加 tools 目录到打包数据 |
| `tools/play_ppt.ps1` | 创建 | 示例脚本：播放 PPT |
| `tools/download_install.ps1` | 创建 | 示例脚本：下载解压安装 |
| `tests/test_tools.py` | 创建 | 单元测试 |

---

### Task 1: 创建 worker/tools.py 基础函数

**Files:**
- Create: `worker/tools.py`

- [ ] **Step 1: 创建 tools.py 文件，实现 get_tools_dir 函数**

```python
"""
脚本管理工具模块。

提供 tools 目录路径解析、脚本保存、版本管理等功能。
"""

import json
import os
import sys

from typing import Optional


def get_tools_dir() -> str:
    """
    获取 tools 目录的完整路径。

    打包后：exe 所在目录下的 tools
    开发时：项目根目录下的 tools

    Returns:
        str: tools 目录完整路径
    """
    if getattr(sys, 'frozen', False):
        # 打包后：exe 所在目录
        base_dir = os.path.dirname(sys.executable)
    else:
        # 开发时：项目根目录（worker 的上级目录）
        base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base_dir, 'tools')


def validate_script_name(name: str) -> bool:
    """
    校验脚本名称合法性。

    规则：
    - 只允许 .ps1、.sh、.bat 扩展名
    - 禁止路径穿越（../、/、\\）

    Args:
        name: 脚本名称

    Returns:
        bool: 是否合法
    """
    # 只允许合法扩展名
    allowed_exts = {'.ps1', '.sh', '.bat'}
    ext = os.path.splitext(name)[1].lower()
    if ext not in allowed_exts:
        return False

    # 禁止路径穿越
    if '..' in name or '/' in name or '\\' in name:
        return False

    return True


def get_versions_file() -> str:
    """获取版本记录文件路径。"""
    return os.path.join(get_tools_dir(), '.versions.json')


def get_script_version(name: str) -> Optional[str]:
    """
    获取脚本版本号。

    Args:
        name: 脚本名称

    Returns:
        str | None: 版本号，不存在则返回 None
    """
    versions_file = get_versions_file()
    if not os.path.exists(versions_file):
        return None

    try:
        with open(versions_file, 'r', encoding='utf-8') as f:
            versions = json.load(f)
        return versions.get(name)
    except (json.JSONDecodeError, IOError):
        return None


def update_script_version(name: str, version: str) -> None:
    """
    更新脚本版本记录。

    Args:
        name: 脚本名称
        version: 版本号
    """
    tools_dir = get_tools_dir()
    versions_file = get_versions_file()
    os.makedirs(tools_dir, exist_ok=True)

    # 读取现有版本记录
    versions = {}
    if os.path.exists(versions_file):
        try:
            with open(versions_file, 'r', encoding='utf-8') as f:
                versions = json.load(f)
        except json.JSONDecodeError:
            versions = {}

    # 更新版本
    versions[name] = version

    # 保存版本记录
    with open(versions_file, 'w', encoding='utf-8') as f:
        json.dump(versions, f, indent=2)


def save_script(name: str, content: str) -> str:
    """
    保存脚本到 tools 目录。

    Args:
        name: 脚本名称
        content: 脚本内容

    Returns:
        str: 脚本完整路径
    """
    tools_dir = get_tools_dir()
    os.makedirs(tools_dir, exist_ok=True)
    script_path = os.path.join(tools_dir, name)

    with open(script_path, 'w', encoding='utf-8') as f:
        f.write(content)

    return script_path


def script_exists(name: str) -> bool:
    """
    检查脚本是否已存在。

    Args:
        name: 脚本名称

    Returns:
        bool: 是否存在
    """
    script_path = os.path.join(get_tools_dir(), name)
    return os.path.exists(script_path)
```

- [ ] **Step 2: 提交代码**

```bash
git add worker/tools.py
git commit -m "feat: add tools.py for script management"
```

---

### Task 2: 编写 worker/tools.py 单元测试

**Files:**
- Create: `tests/test_tools.py`

- [ ] **Step 1: 创建测试文件**

```python
"""
worker/tools.py 单元测试。
"""

import json
import os
import tempfile

import pytest

from worker.tools import (
    get_tools_dir,
    validate_script_name,
    get_script_version,
    update_script_version,
    save_script,
    script_exists,
)


class TestValidateScriptName:
    """测试脚本名称校验。"""

    def test_valid_ps1_script(self):
        """合法的 .ps1 脚本名称。"""
        assert validate_script_name("play_ppt.ps1") is True

    def test_valid_sh_script(self):
        """合法的 .sh 脚本名称。"""
        assert validate_script_name("play_video.sh") is True

    def test_valid_bat_script(self):
        """合法的 .bat 脚本名称。"""
        assert validate_script_name("install.bat") is True

    def test_invalid_extension(self):
        """非法扩展名。"""
        assert validate_script_name("script.py") is False
        assert validate_script_name("script.exe") is False
        assert validate_script_name("script.txt") is False

    def test_path_traversal(self):
        """路径穿越攻击。"""
        assert validate_script_name("../evil.ps1") is False
        assert validate_script_name("subdir/script.ps1") is False
        assert validate_script_name("..\\evil.ps1") is False

    def test_empty_name(self):
        """空名称。"""
        assert validate_script_name("") is False


class TestScriptVersion:
    """测试脚本版本管理。"""

    def test_get_version_not_exists(self, tmp_path):
        """版本文件不存在时返回 None。"""
        # 临时修改 tools_dir
        import worker.tools
        original_frozen = getattr(sys, 'frozen', False)
        worker.tools.sys.frozen = False
        worker.tools.__file__ = str(tmp_path / "worker" / "tools.py")

        result = get_script_version("test.ps1")
        assert result is None

        # 恢复
        worker.tools.sys.frozen = original_frozen

    def test_update_and_get_version(self, tmp_path):
        """更新版本后可以获取。"""
        import worker.tools
        worker.tools.sys.frozen = False
        worker.tools.__file__ = str(tmp_path / "worker" / "tools.py")

        # 创建 tools 目录
        tools_dir = tmp_path / "tools"
        tools_dir.mkdir()

        # 更新版本
        update_script_version("test.ps1", "20260418-120000")

        # 获取版本
        result = get_script_version("test.ps1")
        assert result == "20260418-120000"


class TestSaveScript:
    """测试脚本保存。"""

    def test_save_script(self, tmp_path):
        """保存脚本到文件。"""
        import worker.tools
        worker.tools.sys.frozen = False
        worker.tools.__file__ = str(tmp_path / "worker" / "tools.py")

        content = "param([string]$Path)\nWrite-Output $Path"
        result = save_script("test.ps1", content)

        # 验证文件存在
        assert os.path.exists(result)
        assert script_exists("test.ps1")

        # 验证内容
        with open(result, 'r', encoding='utf-8') as f:
            assert f.read() == content
```

- [ ] **Step 2: 运行测试**

```bash
pytest tests/test_tools.py -v
```

Expected: PASS（可能需要调整测试中的路径模拟方式）

- [ ] **Step 3: 提交测试代码**

```bash
git add tests/test_tools.py
git commit -m "test: add unit tests for worker/tools.py"
```

---

### Task 3: 修改 cmd_exec.py 支持占位符替换和日志增强

**Files:**
- Modify: `worker/actions/cmd_exec.py`

- [ ] **Step 1: 添加占位符替换和日志增强**

修改 `worker/actions/cmd_exec.py`，在 execute 方法中添加：

```python
"""
命令执行 Action。

在宿主机执行 shell/cmd 命令，所有平台均支持。
"""

import subprocess  # 用于 TimeoutExpired 异常类型
import logging
from typing import Optional, TYPE_CHECKING

from common.utils import run_cmd
from worker.tools import get_tools_dir  # 新增导入
from worker.task import Action, ActionResult, ActionStatus
from worker.actions.base import BaseActionExecutor

if TYPE_CHECKING:
    from worker.platforms.base import PlatformManager

logger = logging.getLogger(__name__)


class CmdExecAction(BaseActionExecutor):
    """命令执行动作。在宿主机上执行 shell/cmd 命令。"""

    name = "cmd_exec"
    requires_context = False  # 不需要浏览器/设备上下文
    requires_ocr = False

    def execute(self, platform: "PlatformManager", action: Action, context: Optional[object] = None) -> ActionResult:
        cmd = action.value
        if not cmd:
            return ActionResult(
                number=0,
                action_type=self.name,
                status=ActionStatus.FAILED,
                error="command is required (use 'value' field)",
            )

        # 替换 @tools/ 占位符为完整路径
        tools_dir = get_tools_dir()
        cmd = cmd.replace('@tools/', tools_dir + '/')

        # 超时时间，默认 30 秒
        timeout_ms = action.timeout or 30000
        timeout_sec = timeout_ms / 1000

        logger.info(f"Executing command: {cmd[:100]}...")

        try:
            result = run_cmd(
                cmd,
                shell=True,
                timeout=timeout_sec,
            )

            status = ActionStatus.SUCCESS if result.returncode == 0 else ActionStatus.FAILED

            logger.info(f"Command completed: exit_code={result.returncode}")

            # 日志增强：输出 stdout/stderr 后 500 字符
            if result.stdout:
                stdout_preview = result.stdout[-500:] if len(result.stdout) > 500 else result.stdout
                logger.info(f"Script output: {stdout_preview}")

            if result.stderr:
                stderr_preview = result.stderr[-500:] if len(result.stderr) > 500 else result.stderr
                if result.returncode != 0:
                    logger.error(f"Script error: {stderr_preview}")

            # 输出信息截断（避免过长）
            output_preview = cmd[:50] if len(cmd) > 50 else cmd

            return ActionResult(
                number=0,
                action_type=self.name,
                status=status,
                exit_code=result.returncode,
                stdout=result.stdout,
                stderr=result.stderr,
                output=f"Command executed: {output_preview}",
                error=result.stderr if result.returncode != 0 else None,
            )

        except subprocess.TimeoutExpired:
            logger.warning(f"Command timeout after {timeout_ms}ms")
            return ActionResult(
                number=0,
                action_type=self.name,
                status=ActionStatus.FAILED,
                error=f"Command timeout after {timeout_ms}ms",
            )
        except Exception as e:
            logger.error(f"Command execution failed: {e}")
            return ActionResult(
                number=0,
                action_type=self.name,
                status=ActionStatus.FAILED,
                error=str(e),
            )
```

- [ ] **Step 2: 提交改动**

```bash
git add worker/actions/cmd_exec.py
git commit -m "feat: add @tools/ placeholder replacement and enhanced logging in cmd_exec"
```

---

### Task 4: 创建 tools 目录和示例脚本

**Files:**
- Create: `tools/play_ppt.ps1`
- Create: `tools/download_install.ps1`
- Create: `tools/.gitkeep`

- [ ] **Step 1: 创建 tools 目录**

```bash
mkdir -p tools
```

- [ ] **Step 2: 创建 play_ppt.ps1 脚本**

```powershell
<#
.SYNOPSIS
    播放 PowerPoint 文件。

.PARAMETER FilePath
    PPT 文件路径。

.PARAMETER Duration
    播放时长（秒），默认 60。
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
```

- [ ] **Step 3: 创建 download_install.ps1 脚本**

```powershell
<#
.SYNOPSIS
    下载文件并安装。

.PARAMETER Url
    下载地址。

.PARAMETER TargetDir
    目标目录。

.PARAMETER SilentArgs
    静默安装参数，默认 /S。
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

# 如果是 zip 文件，解压
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
# 如果是 exe 文件，直接静默安装
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
```

- [ ] **Step 4: 提交脚本**

```bash
git add tools/
git commit -m "feat: add example scripts (play_ppt.ps1, download_install.ps1)"
```

---

### Task 5: 新增 /worker/scripts 接口

**Files:**
- Modify: `worker/server.py`

- [ ] **Step 1: 在 server.py 中添加导入和模型定义**

在文件开头的导入部分添加：

```python
from worker.tools import (
    get_tools_dir,
    validate_script_name,
    get_script_version,
    update_script_version,
    save_script,
    script_exists,
)
```

在 `ConfigUpdateRequest` 类定义后添加：

```python
class ScriptUpdateRequest(BaseModel):
    """脚本更新请求。"""
    name: str = Field(..., description="脚本名称，如 play_ppt.ps1")
    content: str = Field(..., description="脚本内容")
    version: str = Field(..., description="脚本版本号，格式：YYYYMMDD-HHMMSS")
    overwrite: bool = Field(True, description="是否覆盖已有脚本")
```

在 `_config_update_lock` 定义后添加：

```python
# 脚本更新并发锁
_script_update_lock = threading.Lock()
```

- [ ] **Step 2: 添加 /worker/scripts 接口**

在 `update_worker_config` 函数后添加：

```python
@app.post("/worker/scripts")
async def update_worker_script(request: ScriptUpdateRequest):
    """
    更新 Worker 脚本。

    流程：
    1. 版本格式校验
    2. 脚本名称校验（扩展名 + 路径穿越）
    3. 并发保护
    4. 版本比较（相同则跳过）
    5. 覆盖检查
    6. 保存脚本
    7. 更新版本记录
    8. 返回响应（不重启）

    Returns:
        Dict: 更新结果
    """
    if not worker:
        raise HTTPException(status_code=503, detail="Worker not initialized")

    logger.info(f"Script update request: name={request.name}, version={request.version}")

    # 1. 版本格式校验
    if not re.match(r"^\d{8}-\d{6}$", request.version):
        return JSONResponse(
            status_code=400,
            content={"status": "error", "message": "版本号格式无效，应为 YYYYMMDD-HHMMSS"}
        )

    # 2. 脚本名称校验
    if not validate_script_name(request.name):
        return JSONResponse(
            status_code=400,
            content={"status": "error", "message": "脚本名称不合法，只允许 .ps1/.sh/.bat 扩展名，禁止路径穿越"}
        )

    # 3. 并发保护（非阻塞）
    if not _script_update_lock.acquire(blocking=False):
        return JSONResponse(
            status_code=409,
            content={"status": "error", "message": "脚本更新正在进行中，请稍后重试"}
        )

    try:
        # 4. 版本比较
        local_version = get_script_version(request.name)
        if local_version == request.version:
            logger.info(f"Script version unchanged: {request.name} -> {request.version}")
            return {
                "status": "success",
                "message": "脚本版本相同，无需更新",
                "name": request.name,
                "version": request.version,
                "updated": False,
            }

        # 5. 覆盖检查
        if not request.overwrite and script_exists(request.name):
            return JSONResponse(
                status_code=409,
                content={"status": "error", "message": f"脚本已存在且 overwrite=false: {request.name}"}
            )

        # 6. 保存脚本
        script_path = save_script(request.name, request.content)
        logger.info(f"Script saved: {script_path}")

        # 7. 更新版本记录
        update_script_version(request.name, request.version)

        # 8. 返回响应（不重启）
        logger.info(f"Script updated successfully: {request.name} -> {request.version}")

        return {
            "status": "success",
            "message": "脚本更新成功",
            "name": request.name,
            "version": request.version,
            "path": script_path,
            "updated": True,
        }

    finally:
        _script_update_lock.release()
```

- [ ] **Step 3: 提交改动**

```bash
git add worker/server.py
git commit -m "feat: add /worker/scripts API for remote script deployment"
```

---

### Task 6: 修改 pyinstaller.spec 添加 tools 目录

**Files:**
- Modify: `scripts/pyinstaller.spec`

- [ ] **Step 1: 在 datas 列表中添加 tools 目录**

修改 `scripts/pyinstaller.spec` 的 datas 部分：

```python
# 收集数据文件
datas = [
    (os.path.join(PROJECT_ROOT, 'config'), 'config'),
    (os.path.join(PROJECT_ROOT, 'assets'), 'assets'),  # 图标文件
    (os.path.join(PROJECT_ROOT, 'tools'), 'tools'),  # 新增：脚本目录
]
```

- [ ] **Step 2: 提交改动**

```bash
git add scripts/pyinstaller.spec
git commit -m "feat: add tools directory to pyinstaller packaging"
```

---

### Task 7: 更新 CLAUDE.md 文档

**Files:**
- Modify: `CLAUDE.md`

- [ ] **Step 1: 在「动作类型」章节添加 cmd_exec 说明**

在「动作类型」表格中补充 cmd_exec 的说明：

```markdown
- **命令执行**：`cmd_exec` - 执行宿主机命令，支持 `@tools/脚本名` 占位符
```

在「动作参数」表格中补充 cmd_exec 相关参数：

```markdown
| `value` | 命令字符串，`@tools/脚本名` 自动替换为完整脚本路径 | cmd_exec |
```

- [ ] **Step 2: 添加「脚本执行」章节**

在文档末尾添加：

```markdown
## 脚本执行机制

Worker 支持通过 `cmd_exec` action 执行外部脚本（PowerShell/Shell），用于复杂任务如播放媒体、软件安装等。

### tools 目录

脚本存放在 `tools/` 目录，打包时带入 exe 目录：
- `play_ppt.ps1` - 播放 PowerPoint
- `download_install.ps1` - 下载解压安装

### 调用方式

使用 `@tools/` 占位符，自动替换为完整路径：

```json
{
  "action_type": "cmd_exec",
  "value": "powershell -ExecutionPolicy Bypass -File \"@tools/play_ppt.ps1\" -FilePath \"C:\\demo.pptx\" -Duration 60",
  "timeout": 120000
}
```

### 远程下发接口

POST `/worker/scripts` 可远程下发脚本，无需重启 Worker：

```json
{
  "name": "play_ppt.ps1",
  "content": "param([string]$FilePath)...",
  "version": "20260418-120000",
  "overwrite": true
}
```
```

- [ ] **Step 3: 提交文档更新**

```bash
git add CLAUDE.md
git commit -m "docs: update CLAUDE.md with script execution mechanism"
```

---

### Task 8: 最终验证

- [ ] **Step 1: 运行所有测试**

```bash
pytest tests/test_tools.py -v
```

Expected: PASS

- [ ] **Step 2: 启动 Worker 验证接口**

```bash
python -m worker.main
```

然后手动测试：
- GET `/worker_devices` - 确认 Worker 正常启动
- POST `/worker/scripts` - 下发一个测试脚本

- [ ] **Step 3: 验证打包（可选）**

```bash
powershell scripts/build_windows.ps1
```

确认打包后的 `dist/test-worker/tools/` 目录包含脚本文件。

---

## 完成标志

- 所有测试通过
- `/worker/scripts` 接口可用
- `cmd_exec` 支持 `@tools/` 占位符
- 打包后 tools 目录正确包含