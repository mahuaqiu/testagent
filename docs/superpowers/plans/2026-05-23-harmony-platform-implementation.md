# 鸿蒙平台集成实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 为 Worker 自动化测试执行基建新增鸿蒙设备支持，遵循 OCR/图像识别定位原则。

**Architecture:** 参考 hmnextauto 项目，实现 HarmonyHdcWrapper 封装 HDC 命令，HarmonyPlatformManager 继承 PlatformManager 基类，与现有 Android/iOS 实现模式保持一致。

**Tech Stack:** Python 3.x, subprocess (HDC 命令执行), dataclasses (配置), PyYAML (配置加载)

---

## 文件结构

### 新增文件

| 文件 | 责责 |
|------|------|
| `worker/platforms/harmony_hdc.py` | HDC 命令封装（shell、截图、点击、滑动、按键、应用管理） |
| `worker/platforms/harmony.py` | HarmonyPlatformManager 实现（继承 PlatformManager） |
| `worker/discovery/harmony.py` | HarmonyDiscoverer 设备发现 |
| `tools/hdc/hdc.exe` | HDC 工具（从 SDK 复制） |
| `tools/hdc/README.md` | HDC 工具说明文档 |

### 修改文件

| 文件 | 修改内容 |
|------|---------|
| `worker/config.py` | 增加 `discover_harmony_devices: bool = False` |
| `worker/worker.py` | 注册 HarmonyPlatformManager，增加鸿蒙设备发现 |
| `worker/device_monitor.py` | 鸿蒙设备监控逻辑 |
| `worker/settings_window.py` | Harmony checkbox UI |
| `worker/actions/unlock.py` | Harmony unlock_screen 支持 |
| `config/worker.yaml` | 鸿蒙配置项 |

---

## Phase 1: 核心框架

### Task 1: 复制 HDC 工具

**Files:**
- Create: `tools/hdc/hdc.exe`
- Create: `tools/hdc/README.md`

- [ ] **Step 1: 创建 tools/hdc 目录**

```bash
mkdir -p tools/hdc
```

- [ ] **Step 2: 复制 HDC 工具从 SDK**

```bash
copy "D:\code\commandline-tools-windows-x64-6.1.0.850\command-line-tools\sdk\default\openharmony\toolchains\hdc.exe" "tools\hdc\hdc.exe"
```

- [ ] **Step 3: 创建 README.md**

```markdown
# HDC 工具说明

## 来源
鸿蒙 Command Line Tools SDK (version 6.1.0.850)

## 版本
当前版本: Ver 2.0.0a

## 常用命令
- hdc list targets        # 列出设备
- hdc shell ...           # 执行 shell 命令
- hdc install app.hap     # 安装应用
```

- [ ] **Step 4: 验证 HDC 工具可用**

```bash
tools\hdc\hdc.exe -v
```
Expected: 输出版本号 "Ver: 2.0.0a"

- [ ] **Step 5: Commit**

```bash
git add tools/hdc/
git commit -m "feat: 添加鸿蒙 HDC 工具"
```

---

### Task 2: 实现 HarmonyHdcWrapper 基础结构

**Files:**
- Create: `worker/platforms/harmony_hdc.py`

- [ ] **Step 1: 创建文件头部和导入**

```python
"""
鸿蒙 HDC 命令封装模块。

参考 hmnextauto hdc.py 实现，封装 HDC 命令执行。
"""

import logging
import os
import subprocess
import tempfile
import uuid
import re
import json
import shutil
from dataclasses import dataclass
from typing import Optional, Tuple, List, Dict, Union

from common.packaging import get_base_dir

logger = logging.getLogger(__name__)


@dataclass
class CommandResult:
    """HDC 命令执行结果。"""
    output: str
    error: str
    exit_code: int


class HarmonyError(Exception):
    """鸿蒙平台基础异常。"""


class DeviceNotFoundError(HarmonyError):
    """设备未找到。"""


class HdcCommandError(HarmonyError):
    """HDC 命令执行失败。"""
    def __init__(self, cmd: str, output: str, exit_code: int):
        self.cmd = cmd
        self.output = output
        self.exit_code = exit_code
        super().__init__(f"HDC command failed: {cmd}, exit_code={exit_code}")
```

- [ ] **Step 2: 实现命令执行基础方法**

```python
def _execute_hdc_command(
    hdc_path: str,
    args: str,
    timeout: int = 30
) -> CommandResult:
    """
    执行 HDC 命令。

    Args:
        hdc_path: HDC 可执行文件路径
        args: 命令参数
        timeout: 超时时间（秒）

    Returns:
        CommandResult: 命令执行结果
    """
    cmdline = f"{hdc_path} {args}"
    logger.debug(f"Executing HDC: {cmdline}")

    try:
        process = subprocess.Popen(
            cmdline,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            shell=True
        )
        output, error = process.communicate(timeout=timeout)
        output = output.decode('utf-8', errors='replace')
        error = error.decode('utf-8', errors='replace')
        exit_code = process.returncode

        return CommandResult(output, error, exit_code)
    except subprocess.TimeoutExpired:
        process.kill()
        return CommandResult("", "Command timeout", -1)
    except Exception as e:
        return CommandResult("", str(e), -1)


def _find_hdc_path() -> Optional[str]:
    """
    查找 HDC 可执行文件路径。

    优先级：
    1. tools/hdc 目录下的 hdc.exe
    2. 系统 PATH 中的 hdc

    Returns:
        str | None: HDC 可执行文件路径
    """
    # 1. 尝试 tools/hdc 目录
    base_dir = get_base_dir()
    tools_hdc = os.path.join(base_dir, "tools", "hdc", "hdc.exe")

    if os.path.exists(tools_hdc):
        return tools_hdc

    # 2. 尝试系统 PATH
    try:
        result = subprocess.run(
            ["hdc", "-v"],
            capture_output=True,
            timeout=5
        )
        if result.returncode == 0:
            return "hdc"
    except Exception:
        pass

    return None


def list_devices(hdc_path: Optional[str] = None) -> List[str]:
    """
    获取已连接的鸿蒙设备列表。

    Args:
        hdc_path: HDC 可执行文件路径（可选）

    Returns:
        List[str]: 设备 UDID 列表
    """
    path = hdc_path or _find_hdc_path()
    if not path:
        return []

    result = _execute_hdc_command(path, "list targets", timeout=10)

    if result.exit_code != 0 or not result.output:
        return []

    devices = []
    for line in result.output.strip().split('\n'):
        line = line.strip()
        if line and 'Empty' not in line and '[empty]' not in line.lower():
            devices.append(line)

    return devices
```

- [ ] **Step 3: 实现 HarmonyHdcWrapper 类基础结构**

```python
class HarmonyHdcWrapper:
    """HDC 命令封装器。"""

    # 按键映射（鸿蒙 KeyCode）
    KEY_MAP = {
        "HOME": 1,
        "BACK": 2,
        "POWER": 18,
        "VOLUME_UP": 16,
        "VOLUME_DOWN": 17,
        "VOLUME_MUTE": 22,
        "ENTER": 2054,
        "MENU": 2067,
        "DPAD_UP": 2012,
        "DPAD_DOWN": 2013,
        "DPAD_LEFT": 2014,
        "DPAD_RIGHT": 2015,
        "DPAD_CENTER": 2016,
    }

    def __init__(self, serial: str, hdc_path: Optional[str] = None):
        """
        初始化 HDC Wrapper。

        Args:
            serial: 设备 UDID
            hdc_path: HDC 可执行文件路径（可选，自动查找）
        """
        self.serial = serial
        self._hdc_path = hdc_path or _find_hdc_path()

        if not self._hdc_path:
            raise HarmonyError("HDC tool not found")

        if not self.is_online():
            raise DeviceNotFoundError(f"Device [{serial}] not found")

    def _execute(self, args: str, timeout: int = 30) -> CommandResult:
        """执行 HDC 命令（带设备 ID）。"""
        return _execute_hdc_command(
            self._hdc_path,
            f"-t {self.serial} {args}",
            timeout=timeout
        )

    def is_online(self) -> bool:
        """检查设备是否在线。"""
        devices = list_devices(self._hdc_path)
        return self.serial in devices

    def shell(self, cmd: str, timeout: int = 30) -> CommandResult:
        """
        执行 shell 命令。

        Args:
            cmd: Shell 命令
            timeout: 超时时间

        Returns:
            CommandResult: 命令结果
        """
        # 确保命令用双引号包裹
        if not cmd.startswith('"'):
            cmd = f'"{cmd}"'
        if not cmd.endswith('"'):
            cmd = f'{cmd}"'

        return self._execute(f"shell {cmd}", timeout=timeout)
```

- [ ] **Step 4: Commit**

```bash
git add worker/platforms/harmony_hdc.py
git commit -m "feat: 实现 HarmonyHdcWrapper 基础结构"
```

---

### Task 3: 实现 HarmonyHdcWrapper 操作方法

**Files:**
- Modify: `worker/platforms/harmony_hdc.py`

- [ ] **Step 1: 实现截图方法**

```python
def screenshot(self, local_path: str, method: str = "snapshot_display") -> str:
    """
    截取设备屏幕并保存到本地。

    Args:
        local_path: 本地保存路径
        method: 截图方式
            - "snapshot_display": 快速方式（推荐）
            - "screenCap": 高质量方式

    Returns:
        str: 本地文件路径
    """
    tmp_name = uuid.uuid4().hex

    if method == "snapshot_display":
        tmp_path = f"/data/local/tmp/_tmp_{tmp_name}.jpeg"
        self.shell(f"snapshot_display -f {tmp_path}")
    else:
        tmp_path = f"/data/local/tmp/{tmp_name}.png"
        self.shell(f"uitest screenCap -p {tmp_path}")

    # 拉取文件到本地
    self.pull_file(tmp_path, local_path)

    # 清理临时文件
    self.shell(f"rm -rf {tmp_path}")

    return local_path


def pull_file(self, remote_path: str, local_path: str) -> None:
    """从设备拉取文件到本地。"""
    result = self._execute(f"file recv {remote_path} {local_path}")
    if result.exit_code != 0:
        raise HdcCommandError(f"file recv {remote_path}", result.output, result.exit_code)


def push_file(self, local_path: str, remote_path: str) -> None:
    """推送本地文件到设备。"""
    result = self._execute(f"file send {local_path} {remote_path}")
    if result.exit_code != 0:
        raise HdcCommandError(f"file send {local_path}", result.output, result.exit_code)
```

- [ ] **Step 2: 实现点击和滑动方法**

```python
def tap(self, x: int, y: int) -> None:
    """点击指定坐标。"""
    result = self.shell(f"uitest uiInput click {x} {y}")
    if result.exit_code != 0:
        raise HdcCommandError(f"uitest uiInput click {x} {y}", result.output, result.exit_code)


def double_tap(self, x: int, y: int) -> None:
    """双击指定坐标。"""
    result = self.shell(f"uitest uiInput doubleClick {x} {y}")
    if result.exit_code != 0:
        raise HdcCommandError(f"uitest uiInput doubleClick", result.output, result.exit_code)


def long_tap(self, x: int, y: int) -> None:
    """长按指定坐标。"""
    result = self.shell(f"uitest uiInput longClick {x} {y}")
    if result.exit_code != 0:
        raise HdcCommandError(f"uitest uiInput longClick", result.output, result.exit_code)


def swipe(self, x1: int, y1: int, x2: int, y2: int, speed: int = 1000) -> None:
    """
    滑动操作。

    Args:
        x1, y1: 起点坐标
        x2, y2: 终点坐标
        speed: 滑动速度（px/s），范围 200-40000
    """
    speed = max(200, min(speed, 40000))  # 限制范围
    result = self.shell(f"uitest uiInput swipe {x1} {y1} {x2} {y2} {speed}")
    if result.exit_code != 0:
        raise HdcCommandError(f"uitest uiInput swipe", result.output, result.exit_code)


def input_text_at(self, x: int, y: int, text: str) -> None:
    """在指定坐标位置输入文本。"""
    result = self.shell(f"uitest uiInput inputText {x} {y} '{text}'")
    if result.exit_code != 0:
        raise HdcCommandError(f"uitest uiInput inputText", result.output, result.exit_code)
```

- [ ] **Step 3: 实现按键方法**

```python
def send_key(self, key_code: int) -> None:
    """发送按键事件。"""
    if key_code > 3200:
        raise HarmonyError(f"Invalid KeyCode: {key_code}")

    result = self.shell(f"uitest uiInput keyEvent {key_code}")
    if result.exit_code != 0:
        raise HdcCommandError(f"uitest uiInput keyEvent {key_code}", result.output, result.exit_code)


def press_key(self, key_name: str) -> None:
    """
    按键（使用按键名）。

    Args:
        key_name: 按键名（HOME, BACK, POWER 等）
    """
    key_upper = key_name.upper()
    key_code = self.KEY_MAP.get(key_upper)

    if key_code:
        self.send_key(key_code)
    elif key_name.isdigit():
        self.send_key(int(key_name))
    else:
        supported = ", ".join(sorted(self.KEY_MAP.keys()))
        raise HarmonyError(f"Unsupported key '{key_name}'. Supported: {supported}")
```

- [ ] **Step 4: 实现屏幕控制方法**

```python
def wakeup(self) -> None:
    """唤醒屏幕。"""
    self.shell("power-shell wakeup")


def screen_state(self) -> str:
    """
    获取屏幕状态。

    Returns:
        str: "AWAKE", "INACTIVE", "SLEEP"
    """
    result = self.shell("hidumper -s PowerManagerService -a -s")
    match = re.search(r"Current State:\s*(\w+)", result.output)
    return match.group(1) if match else "UNKNOWN"


def is_screen_on(self) -> bool:
    """检查屏幕是否亮起。"""
    return self.screen_state() == "AWAKE"
```

- [ ] **Step 5: 实现设备信息方法**

```python
def display_size(self) -> Tuple[int, int]:
    """获取屏幕分辨率。"""
    result = self.shell("hidumper -s RenderService -a screen")
    match = re.search(r'activeMode:\s*(\d+)x(\d+),\s*refreshrate=\d+', result.output)

    if match:
        return (int(match.group(1)), int(match.group(2)))
    return (0, 0)


def model(self) -> str:
    """获取设备型号。"""
    result = self.shell("param get const.product.model")
    return result.output.strip().split('\n')[0] if result.output else ""


def product_name(self) -> str:
    """获取产品名称。"""
    result = self.shell("param get const.product.name")
    return result.output.strip().split('\n')[0] if result.output else ""


def sdk_version(self) -> str:
    """获取 SDK 版本。"""
    result = self.shell("param get const.ohos.apiversion")
    return result.output.strip().split('\n')[0] if result.output else ""


def sys_version(self) -> str:
    """获取系统版本。"""
    result = self.shell("param get const.product.software.version")
    return result.output.strip().split('\n')[0] if result.output else ""


def device_info(self) -> Dict:
    """获取设备详细信息。"""
    return {
        "serial": self.serial,
        "model": self.model(),
        "name": self.product_name(),
        "sdk_version": self.sdk_version(),
        "sys_version": self.sys_version(),
        "display_size": self.display_size(),
    }
```

- [ ] **Step 6: Commit**

```bash
git add worker/platforms/harmony_hdc.py
git commit -m "feat: 实现 HarmonyHdcWrapper 操作方法（截图、点击、滑动、按键）"
```

---

### Task 4: 实现 HarmonyHdcWrapper 应用管理方法

**Files:**
- Modify: `worker/platforms/harmony_hdc.py`

- [ ] **Step 1: 实现应用安装卸载**

```python
def install(self, hap_path: str) -> None:
    """安装应用。"""
    result = self._execute(f'install "{hap_path}"')
    if result.exit_code != 0 or 'fail' in result.output.lower():
        raise HdcCommandError(f"install {hap_path}", result.output, result.exit_code)


def uninstall(self, package: str) -> None:
    """卸载应用。"""
    result = self._execute(f"uninstall {package}")
    if result.exit_code != 0:
        raise HdcCommandError(f"uninstall {package}", result.output, result.exit_code)
```

- [ ] **Step 2: 实现应用启动停止**

```python
def start_app(self, package: str, ability: str) -> None:
    """启动应用。"""
    result = self.shell(f"aa start -a {ability} -b {package}")
    if result.exit_code != 0 or 'fail' in result.output.lower():
        raise HdcCommandError(f"aa start", result.output, result.exit_code)


def stop_app(self, package: str) -> None:
    """强制停止应用。"""
    result = self.shell(f"aa force-stop {package}")
    if result.exit_code != 0:
        raise HdcCommandError(f"aa force-stop", result.output, result.exit_code)


def clear_app(self, package: str) -> None:
    """清除应用数据。"""
    self.shell(f"bm clean -n {package} -c")  # 清缓存
    self.shell(f"bm clean -n {package} -d")  # 清数据
```

- [ ] **Step 3: 实现应用信息查询**

```python
def list_apps(self, include_system: bool = False) -> List[str]:
    """获取已安装应用列表。"""
    if include_system:
        cmd = "bm dump -a"
    else:
        cmd = "bm dump -a | grep -v 'com.huawei'"

    result = self.shell(cmd)
    apps = []

    for line in result.output.strip().split('\n'):
        line = line.strip()
        if line and not re.match(r'^ID:', line):
            apps.append(line)

    return apps


def has_app(self, package: str) -> bool:
    """检查应用是否安装。"""
    result = self.shell("bm dump -a")
    return package in result.output


def current_app(self) -> Tuple[Optional[str], Optional[str]]:
    """
    获取当前前台应用。

    Returns:
        Tuple[package, ability]: 包名和能力名，未找到则返回 (None, None)
    """
    result = self.shell("aa dump -l")

    # 解析 FOREGROUND 状态的应用
    mission_pattern = r'Mission ID #\d+.*?state #FOREGROUND.*?bundle name \[([^\]]+)\].*?main name \[([^\]]+)\]'
    match = re.search(mission_pattern, result.output, re.DOTALL)

    if match:
        return (match.group(1), match.group(2))

    return (None, None)
```

- [ ] **Step 4: Commit**

```bash
git add worker/platforms/harmony_hdc.py
git commit -m "feat: 实现 HarmonyHdcWrapper 应用管理方法"
```

---

### Task 5: 实现 HarmonyPlatformManager 基础结构

**Files:**
- Create: `worker/platforms/harmony.py`

- [ ] **Step 1: 创建文件头部和导入**

```python
"""
鸿蒙平台执行引擎。

基于 HDC 直连实现，支持 OCR/图像识别定位。
"""

import logging
import time
import tempfile
import os
from typing import Any, Optional

from worker.actions import ActionRegistry
from worker.config import PlatformConfig
from worker.platforms.base import PlatformManager
from worker.platforms.harmony_hdc import (
    HarmonyHdcWrapper,
    HarmonyError,
    DeviceNotFoundError,
    list_devices,
    _find_hdc_path,
)
from worker.task import Action, ActionResult, ActionStatus

logger = logging.getLogger(__name__)
```

- [ ] **Step 2: 实现类定义和 KEY_MAP**

```python
class HarmonyPlatformManager(PlatformManager):
    """
    鸿蒙平台管理器。

    使用 HDC 直连控制鸿蒙设备。
    """

    SUPPORTED_ACTIONS: set[str] = {"start_app", "stop_app", "unlock_screen"}

    # 鸿蒙按键映射（KeyCode）
    KEY_MAP = {
        "HOME": 1,       # KeyCode.HOME
        "BACK": 2,       # KeyCode.BACK
        "POWER": 18,     # KeyCode.POWER
        "VOLUME_UP": 16,
        "VOLUME_DOWN": 17,
        "VOLUME_MUTE": 22,
        "ENTER": 2054,
        "MENU": 2067,
        "DPAD_UP": 2012,
        "DPAD_DOWN": 2013,
        "DPAD_LEFT": 2014,
        "DPAD_RIGHT": 2015,
        "DPAD_CENTER": 2016,
    }

    def __init__(self, config: PlatformConfig, ocr_client=None, unlock_config=None):
        super().__init__(config, ocr_client)
        self._device_clients: dict[str, HarmonyHdcWrapper] = {}
        self._current_device: str | None = None
        self._unlock_config = unlock_config or {}
        self._hdc_path: str | None = None
        self._screenshot_method = "snapshot_display"
```

- [ ] **Step 3: 实现属性和生命周期方法**

```python
@property
def platform(self) -> str:
    return "harmony"


def start(self) -> None:
    """启动鸿蒙平台（检查 HDC 工具）。"""
    if self._started:
        return

    self._hdc_path = _find_hdc_path()

    if not self._hdc_path:
        logger.warning("HDC tool not found, harmony platform unavailable")
        return

    # 验证 HDC 工具可用
    try:
        result = list_devices(self._hdc_path)
        logger.debug(f"HDC available, found {len(result)} devices")
    except Exception as e:
        logger.warning(f"HDC check failed: {e}")

    self._started = True
    logger.info("Harmony platform started")


def stop(self) -> None:
    """停止鸿蒙平台。"""
    self._device_clients.clear()
    self._started = False
    logger.info("Harmony platform stopped")


def is_available(self) -> bool:
    """检查平台是否可用。"""
    return self._started and self._hdc_path is not None
```

- [ ] **Step 4: 实现设备服务管理**

```python
def ensure_device_service(self, udid: str) -> tuple[str, str]:
    """确保设备服务可用（由 DeviceMonitor 调用）。"""
    try:
        # 检查缓存的客户端
        client = self._device_clients.get(udid)
        if client:
            if client.is_online():
                return ("online", "OK")
            else:
                # 设备离线，清理缓存
                del self._device_clients[udid]

        # 创建新客户端（会检查在线状态）
        client = HarmonyHdcWrapper(udid, self._hdc_path)
        self._device_clients[udid] = client
        logger.info(f"Harmony device service ready: {udid}")

        return ("online", "OK")

    except DeviceNotFoundError:
        return ("faulty", "Device not found")
    except Exception as e:
        logger.error(f"Failed to ensure device service: {udid}, {e}")
        return ("faulty", str(e))


def mark_device_faulty(self, udid: str) -> None:
    """标记设备为异常。"""
    if udid in self._device_clients:
        del self._device_clients[udid]
    logger.info(f"Harmony device marked faulty: {udid}")


def get_online_devices(self) -> list[str]:
    """获取在线设备列表。"""
    if not self._hdc_path:
        return []
    return list_devices(self._hdc_path)
```

- [ ] **Step 5: 实现上下文管理**

```python
def create_context(self, device_id: Optional[str] = None, options: Optional[dict] = None) -> Any:
    """创建执行上下文。"""
    if not device_id:
        devices = self.get_online_devices()
        if not devices:
            raise HarmonyError("No harmony devices available")
        device_id = devices[0]

    client = self._device_clients.get(device_id)
    if not client:
        client = HarmonyHdcWrapper(device_id, self._hdc_path)
        self._device_clients[device_id] = client

    self._current_device = device_id
    return client


def close_context(self, context: Any, close_session: bool = False) -> None:
    """关闭上下文。"""
    # 鸿蒙无需关闭会话，保持客户端缓存
    pass
```

- [ ] **Step 6: Commit**

```bash
git add worker/platforms/harmony.py
git commit -m "feat: 实现 HarmonyPlatformManager 基础结构"
```

---

### Task 6: 实现 HarmonyPlatformManager 基础操作方法

**Files:**
- Modify: `worker/platforms/harmony.py`

- [ ] **Step 1: 实现截图方法**

```python
def get_screenshot(self, context: Any) -> bytes:
    """获取截图。"""
    client: HarmonyHdcWrapper = context

    # 创建临时文件
    with tempfile.NamedTemporaryFile(suffix=".jpeg", delete=False) as f:
        temp_path = f.name

    try:
        client.screenshot(temp_path, method=self._screenshot_method)

        with open(temp_path, "rb") as f:
            return f.read()
    finally:
        if os.path.exists(temp_path):
            os.remove(temp_path)


def take_screenshot(self, context: Any = None) -> bytes:
    """获取截图（基类方法）。"""
    client = context or self._device_clients.get(self._current_device)
    if not client:
        raise HarmonyError("No device context")
    return self.get_screenshot(client)
```

- [ ] **Step 2: 实现点击和滑动方法**

```python
def click(self, x: int, y: int, duration: int = 0, context: Any = None) -> None:
    """点击。"""
    client = context or self._device_clients.get(self._current_device)
    if not client:
        raise HarmonyError("No device context")
    client.tap(x, y)


def double_click(self, x: int, y: int, context: Any = None) -> None:
    """双击。"""
    client = context or self._device_clients.get(self._current_device)
    if not client:
        raise HarmonyError("No device context")
    client.double_tap(x, y)


def swipe(
    self,
    start_x: int,
    start_y: int,
    end_x: int,
    end_y: int,
    duration: int = 500,
    steps: Optional[int] = None,
    context: Any = None
) -> None:
    """滑动。"""
    client = context or self._device_clients.get(self._current_device)
    if not client:
        raise HarmonyError("No device context")

    # duration 转换为 speed (px/s)
    distance = abs(end_x - start_x) + abs(end_y - start_y)
    speed = int(distance * 1000 / duration) if duration > 0 else 1000
    speed = max(200, min(speed, 40000))

    client.swipe(start_x, start_y, end_x, end_y, speed)


def move(self, x: int, y: int, context: Any = None) -> None:
    """移动（鸿蒙无单独移动，用点击实现）。"""
    # 鸿蒙没有 hover 操作，忽略
    pass


def input_text(self, text: str, context: Any = None) -> None:
    """输入文本。"""
    client = context or self._device_clients.get(self._current_device)
    if not client:
        raise HarmonyError("No device context")

    # 使用 HDC uitest inputText 命令
    # 需要先点击输入框获取焦点
    client.shell(f"uitest uiInput inputText 0 0 '{text}'")
```

- [ ] **Step 3: 实现按键方法**

```python
def press(self, key: str, context: Any = None) -> None:
    """按键。"""
    client = context or self._device_clients.get(self._current_device)
    if not client:
        raise HarmonyError("No device context")

    key_upper = key.upper() if key else ""
    key_code = self.KEY_MAP.get(key_upper)

    if key_code:
        client.send_key(key_code)
    elif key and key.isdigit():
        client.send_key(int(key))
    else:
        supported = ", ".join(sorted(self.KEY_MAP.keys()))
        raise ValueError(f"Unsupported key '{key}' for Harmony. Supported: {supported}")
```

- [ ] **Step 4: Commit**

```bash
git add worker/platforms/harmony.py
git commit -m "feat: 实现 HarmonyPlatformManager 基础操作方法"
```

---

### Task 7: 实现 HarmonyPlatformManager 动作执行方法

**Files:**
- Modify: `worker/platforms/harmony.py`

- [ ] **Step 1: 实现 execute_action 方法**

```python
def execute_action(self, context: Any, action: Action) -> ActionResult:
    """执行动作。"""
    client: HarmonyHdcWrapper = context

    # 平台特有动作
    if action.action_type == "start_app":
        return self._action_start_app(client, action)
    elif action.action_type == "stop_app":
        return self._action_stop_app(client, action)
    elif action.action_type == "unlock_screen":
        # 使用 ActionRegistry 执行（需要在 unlock.py 中增加 harmony 分支）
        executor = ActionRegistry.get(action.action_type)
        if executor:
            return executor.execute(self, action, context)
        else:
            return ActionResult(
                number=action.number,
                action_type=action.action_type,
                status=ActionStatus.FAILED,
                message="unlock_screen executor not found"
            )

    # 通用动作（通过 ActionRegistry）
    executor = ActionRegistry.get(action.action_type)
    if executor:
        return executor.execute(self, action, context)

    return ActionResult(
        number=action.number,
        action_type=action.action_type,
        status=ActionStatus.FAILED,
        message=f"Unsupported action: {action.action_type}"
    )
```

- [ ] **Step 2: 实现 start_app 动作**

```python
def _action_start_app(self, client: HarmonyHdcWrapper, action: Action) -> ActionResult:
    """启动应用。"""
    package = action.value

    if not package:
        return ActionResult(
            number=action.number,
            action_type="start_app",
            status=ActionStatus.FAILED,
            message="Missing package name"
        )

    try:
        # 获取主 Ability（如果未指定）
        # 简化实现：默认使用 EntryAbility
        ability = "EntryAbility"

        # 检查屏幕状态，必要时唤醒
        if not client.is_screen_on():
            client.wakeup()
            time.sleep(0.5)

        client.start_app(package, ability)

        return ActionResult(
            number=action.number,
            action_type="start_app",
            status=ActionStatus.SUCCESS,
            message=f"App started: {package}"
        )

    except Exception as e:
        logger.error(f"start_app failed: {e}")
        return ActionResult(
            number=action.number,
            action_type="start_app",
            status=ActionStatus.FAILED,
            message=str(e)
        )
```

- [ ] **Step 3: 实现 stop_app 动作**

```python
def _action_stop_app(self, client: HarmonyHdcWrapper, action: Action) -> ActionResult:
    """停止应用。"""
    package = action.value

    if not package:
        return ActionResult(
            number=action.number,
            action_type="stop_app",
            status=ActionStatus.FAILED,
            message="Missing package name"
        )

    try:
        client.stop_app(package)

        return ActionResult(
            number=action.number,
            action_type="stop_app",
            status=ActionStatus.SUCCESS,
            message=f"App stopped: {package}"
        )

    except Exception as e:
        logger.error(f"stop_app failed: {e}")
        return ActionResult(
            number=action.number,
            action_type="stop_app",
            status=ActionStatus.FAILED,
            message=str(e)
        )
```

- [ ] **Step 4: Commit**

```bash
git add worker/platforms/harmony.py
git commit -m "feat: 实现 HarmonyPlatformManager 动作执行方法"
```

---

## Phase 2: 设备管理

### Task 8: 实现 HarmonyDiscoverer

**Files:**
- Create: `worker/discovery/harmony.py`

- [ ] **Step 1: 创建文件头部和导入**

```python
"""
鸿蒙设备发现模块。

通过 HDC 发现连接到本机的鸿蒙设备。
"""

import logging
import re
from dataclasses import dataclass
from typing import List, Optional, Dict

from worker.platforms.harmony_hdc import list_devices, _find_hdc_path

logger = logging.getLogger(__name__)


@dataclass
class HarmonyDeviceInfo:
    """鸿蒙设备信息。"""

    udid: str
    name: str
    model: str
    sys_version: str
    sdk_version: str
    display_size: tuple
    status: str

    def to_dict(self) -> Dict:
        """转换为字典。"""
        return {
            "platform": "harmony",
            "udid": self.udid,
            "name": self.name,
            "model": self.model,
            "sys_version": self.sys_version,
            "sdk_version": self.sdk_version,
            "display_size": self.display_size,
            "status": self.status,
        }
```

- [ ] **Step 2: 实现 HarmonyDiscoverer 类**

```python
class HarmonyDiscoverer:
    """鸿蒙设备发现器。"""

    @staticmethod
    def check_hdc_available() -> bool:
        """检查 HDC 是否可用。"""
        return _find_hdc_path() is not None

    @staticmethod
    def list_devices() -> List[str]:
        """获取设备 UDID 列表。"""
        return list_devices()

    @staticmethod
    def get_device_info(udid: str) -> Optional[HarmonyDeviceInfo]:
        """
        获取设备详细信息。

        Args:
            udid: 设备 UDID

        Returns:
            HarmonyDeviceInfo | None: 设备信息
        """
        from worker.platforms.harmony_hdc import HarmonyHdcWrapper

        try:
            client = HarmonyHdcWrapper(udid)

            return HarmonyDeviceInfo(
                udid=udid,
                name=client.product_name(),
                model=client.model(),
                sys_version=client.sys_version(),
                sdk_version=client.sdk_version(),
                display_size=client.display_size(),
                status="online",
            )
        except Exception as e:
            logger.debug(f"Failed to get device info for {udid}: {e}")
            return None

    @classmethod
    def discover(cls) -> List[HarmonyDeviceInfo]:
        """
        发现所有鸿蒙设备。

        Returns:
            List[HarmonyDeviceInfo]: 设备信息列表
        """
        if not cls.check_hdc_available():
            return []

        devices = []
        udid_list = cls.list_devices()

        for udid in udid_list:
            info = cls.get_device_info(udid)
            if info:
                devices.append(info)

        return devices

    @classmethod
    def discover_device(cls, udid: str) -> Optional[HarmonyDeviceInfo]:
        """
        发现指定设备。

        Args:
            udid: 设备 UDID

        Returns:
            HarmonyDeviceInfo | None: 设备信息
        """
        udid_list = cls.list_devices()

        if udid in udid_list:
            return cls.get_device_info(udid)

        return None
```

- [ ] **Step 3: Commit**

```bash
git add worker/discovery/harmony.py
git commit -m "feat: 实现 HarmonyDiscoverer 设备发现模块"
```

---

### Task 9: 修改配置增加 discover_harmony_devices

**Files:**
- Modify: `worker/config.py:54-55`（增加配置字段）
- Modify: `worker/config.py:136-137`（增加配置加载）

- [ ] **Step 1: 增加 WorkerConfig 配置字段**

在 `worker/config.py` 第 55 行后增加：

```python
    discover_android_devices: bool = False  # 是否发现 Android 设备
    discover_ios_devices: bool = False      # 是否发现 iOS 设备
    discover_harmony_devices: bool = False  # 是否发现鸿蒙设备（新增）
```

- [ ] **Step 2: 增加 from_yaml 加载**

在 `worker/config.py` 第 137 行后增加：

```python
            discover_android_devices=worker_data.get("discover_android_devices", False),
            discover_ios_devices=worker_data.get("discover_ios_devices", False),
            discover_harmony_devices=worker_data.get("discover_harmony_devices", False),  # 新增
```

- [ ] **Step 3: Commit**

```bash
git add worker/config.py
git commit -m "feat: 增加 discover_harmony_devices 配置项"
```

---

### Task 10: 修改 DeviceMonitor 增加鸿蒙设备监控

**Files:**
- Modify: `worker/device_monitor.py:28-31`（增加字段）
- Modify: `worker/device_monitor.py:53-58`（增加 set_platform_managers）
- Modify: `worker/device_monitor.py:104-150`（增加检测逻辑）

- [ ] **Step 1: 增加 __init__ 字段**

```python
    def __init__(self, config: WorkerConfig):
        self.config = config
        self.discover_android = config.discover_android_devices
        self.discover_ios = config.discover_ios_devices
        self.discover_harmony = config.discover_harmony_devices  # 新增
        # ...
        self._harmony_devices: list[dict] = []  # 新增
        self._faulty_harmony_devices: list[dict] = []  # 新增
```

- [ ] **Step 2: 增加 set_platform_managers 参数**

```python
    def set_platform_managers(self, android_manager=None, ios_manager=None, harmony_manager=None) -> None:
        """设置平台管理器引用。"""
        if self.discover_android:
            self._android_manager = android_manager
        if self.discover_ios:
            self._ios_manager = ios_manager
        if self.discover_harmony:  # 新增
            self._harmony_manager = harmony_manager
```

- [ ] **Step 3: 增加 _detect_physical_devices 检测**

在 `_detect_physical_devices` 方法中增加：

```python
        # 鸿蒙设备检测
        if self._harmony_manager and self.discover_harmony:
            try:
                from worker.discovery.harmony import HarmonyDiscoverer
                harmony_devices = HarmonyDiscoverer.discover()
                for device in harmony_devices:
                    self._add_device("harmony", device.to_dict())
            except Exception as e:
                logger.error(f"Harmony device discovery failed: {e}")
```

- [ ] **Step 4: 增加 _maintain_services 维护**

在 `_maintain_services` 方法中增加：

```python
        if self.discover_harmony:
            for device in self._faulty_harmony_devices[:]:
                self._try_start_service("harmony", device["udid"])
```

- [ ] **Step 5: 增加 _try_start_service 检测**

在 `_try_start_service` 方法中增加 harmony 分支：

```python
        elif platform == "harmony":
            if self._harmony_manager:
                status, message = self._harmony_manager.ensure_device_service(udid)
                # ...
```

- [ ] **Step 6: Commit**

```bash
git add worker/device_monitor.py
git commit -m "feat: DeviceMonitor 增加鸿蒙设备监控"
```

---

### Task 11: 修改 Worker 注册 HarmonyPlatformManager

**Files:**
- Modify: `worker/worker.py:210-215`（增加平台检查）
- Modify: `worker/worker.py:291-305`（增加设备发现）
- Modify: `worker/worker.py:326-333`（增加平台初始化）

- [ ] **Step 1: 修改 _init_platform_managers 增加 harmony**

```python
        for platform in ("android", "ios", "harmony"):
            # 根据开关跳过
            if platform == "android" and not self.config.discover_android_devices:
                continue
            if platform == "ios" and not self.config.discover_ios_devices:
                continue
            if platform == "harmony" and not self.config.discover_harmony_devices:  # 新增
                continue
```

- [ ] **Step 2: 增加 HarmonyPlatformManager 初始化**

```python
        if platform == "harmony":
            from worker.platforms.harmony import HarmonyPlatformManager
            manager = HarmonyPlatformManager(platform_config, self.ocr_client, unlock_config)
            self.harmony_manager = manager
```

- [ ] **Step 3: 增加 _discover_devices 鸿蒙发现**

```python
        # 鸿蒙设备
        if self.config.discover_harmony_devices:
            from worker.discovery.harmony import HarmonyDiscoverer
            if HarmonyDiscoverer.check_hdc_available():
                self.harmony_devices = HarmonyDiscoverer.discover()
```

- [ ] **Step 4: 增加 supported_platforms**

```python
        self.supported_platforms = ["web", "windows"]
        if self.config.discover_android_devices:
            self.supported_platforms.append("android")
        if self.config.discover_ios_devices:
            self.supported_platforms.append("ios")
        if self.config.discover_harmony_devices:  # 新增
            self.supported_platforms.append("harmony")
```

- [ ] **Step 5: 增加 DeviceMonitor.set_platform_managers 调用**

```python
        self.device_monitor.set_platform_managers(
            android_manager=self.android_manager if self.config.discover_android_devices else None,
            ios_manager=self.ios_manager if self.config.discover_ios_devices else None,
            harmony_manager=self.harmony_manager if self.config.discover_harmony_devices else None,  # 新增
        )
```

- [ ] **Step 6: Commit**

```bash
git add worker/worker.py
git commit -m "feat: Worker 注册 HarmonyPlatformManager"
```

---

### Task 12: 修改设置窗口增加 Harmony checkbox

**Files:**
- Modify: `worker/settings_window.py:200-208`（增加 checkbox）
- Modify: `worker/settings_window.py:343-349`（增加加载）
- Modify: `worker/settings_window.py:434-435`（增加保存）
- Modify: `worker/settings_window.py:450-453`（增加配置更新）

- [ ] **Step 1: 增加 Harmony checkbox**

```python
        # 设备发现开关（同一行）
        self.discover_android_checkbox = QCheckBox("Android")
        self.discover_android_checkbox.setStyleSheet("font-size: 14px; color: #555555;")
        grid.addWidget(self.discover_android_checkbox, row, 0)

        self.discover_ios_checkbox = QCheckBox("iOS")
        self.discover_ios_checkbox.setStyleSheet("font-size: 14px; color: #555555;")
        grid.addWidget(self.discover_ios_checkbox, row, 1)

        # 新增
        self.discover_harmony_checkbox = QCheckBox("Harmony")
        self.discover_harmony_checkbox.setStyleSheet("font-size: 14px; color: #555555;")
        grid.addWidget(self.discover_harmony_checkbox, row, 2)
```

- [ ] **Step 2: 增加 _load_config 加载**

```python
        # 设备发现开关
        discover_android = worker.get("discover_android_devices", False)
        self.discover_android_checkbox.setChecked(discover_android)

        discover_ios = worker.get("discover_ios_devices", False)
        self.discover_ios_checkbox.setChecked(discover_ios)

        # 新增
        discover_harmony = worker.get("discover_harmony_devices", False)
        self.discover_harmony_checkbox.setChecked(discover_harmony)
```

- [ ] **Step 3: 增加 _save_config_yaml 保存**

```python
            original_content = self._update_yaml_value(original_content, "discover_android_devices", "true" if self.discover_android_checkbox.isChecked() else "false")
            original_content = self._update_yaml_value(original_content, "discover_ios_devices", "true" if self.discover_ios_checkbox.isChecked() else "false")
            # 新增
            original_content = self._update_yaml_value(original_content, "discover_harmony_devices", "true" if self.discover_harmony_checkbox.isChecked() else "false")
```

- [ ] **Step 4: 增加 _save_config_dict 配置更新**

```python
            self._config["worker"]["discover_android_devices"] = self.discover_android_checkbox.isChecked()
            self._config["worker"]["discover_ios_devices"] = self.discover_ios_checkbox.isChecked()
            # 新增
            self._config["worker"]["discover_harmony_devices"] = self.discover_harmony_checkbox.isChecked()
```

- [ ] **Step 5: Commit**

```bash
git add worker/settings_window.py
git commit -m "feat: 设置窗口增加 Harmony checkbox"
```

---

## Phase 3: 配置和测试

### Task 13: 更新 worker.yaml 配置文件

**Files:**
- Modify: `config/worker.yaml:10-11`（增加 discover_harmony_devices）
- Modify: `config/worker.yaml`（增加 platforms.harmony 配置）

- [ ] **Step 1: 增加 discover_harmony_devices 配置**

```yaml
worker:
  discover_android_devices: false   # 启用 Android 设备发现
  discover_ios_devices: false       # 启用 iOS 设备发现
  discover_harmony_devices: false   # 启用鸿蒙设备发现（新增）
```

- [ ] **Step 2: 增加 platforms.harmony 配置**

```yaml
platforms:
  # ... 现有配置 ...
  
  # 鸿蒙平台配置
  harmony:
    enabled: null                   # 仅 Windows 支持
    hdc_path: tools/hdc/hdc.exe     # HDC 工具路径
    screenshot_method: snapshot_display  # 截图方式
    session_timeout: 300
    screenshot_dir: data/screenshots
```

- [ ] **Step 3: Commit**

```bash
git add config/worker.yaml
git commit -m "feat: worker.yaml 增加鸿蒙配置项"
```

---

### Task 14: 实现集成测试验证

**Files:**
- Create: `tests/test_harmony_hdc.py`（可选，需要设备）

- [ ] **Step 1: 验证基本导入**

```bash
python -c "from worker.platforms.harmony_hdc import HarmonyHdcWrapper; print('OK')"
```
Expected: 输出 "OK"

- [ ] **Step 2: 验证平台导入**

```bash
python -c "from worker.platforms.harmony import HarmonyPlatformManager; print('OK')"
```
Expected: 输出 "OK"

- [ ] **Step 3: 验证配置加载**

```bash
python -c "from worker.config import WorkerConfig; c = WorkerConfig(); print(c.discover_harmony_devices)"
```
Expected: 输出 "False"

- [ ] **Step 4: 验证 Worker 注册（手动测试）**

需要连接鸿蒙设备后手动测试：
1. 启动 Worker：`python -m worker.main`
2. 检查日志是否有 "Harmony platform started"
3. 访问 `/worker_devices` 查看鸿蒙设备列表

- [ ] **Step 5: Final Commit**

```bash
git add -A
git commit -m "feat: 鸿蒙平台集成完成"
```

---

### Task 15: 修改 HostDiscoverer 增加 harmony 平台支持

**Files:**
- Modify: `worker/discovery/host.py:423`

- [ ] **Step 1: 修改 get_supported_platforms 方法**

```python
        if os_type == "windows":
            return ["web", "windows", "android", "ios", "harmony"]
```

- [ ] **Step 2: Commit**

```bash
git add worker/discovery/host.py
git commit -m "feat: HostDiscoverer 增加 harmony 平台支持"
```

---

### Task 16: 修改 platforms/__init__.py 增加导出

**Files:**
- Modify: `worker/platforms/__init__.py`

- [ ] **Step 1: 增加 HarmonyPlatformManager 导入和导出**

```python
"""
平台执行引擎模块。
"""

from worker.platforms.base import PlatformManager
from worker.platforms.web import WebPlatformManager
from worker.platforms.android import AndroidPlatformManager
from worker.platforms.ios import iOSPlatformManager
from worker.platforms.windows import WindowsPlatformManager
from worker.platforms.mac import MacPlatformManager
from worker.platforms.harmony import HarmonyPlatformManager  # 新增

__all__ = [
    "PlatformManager",
    "WebPlatformManager",
    "AndroidPlatformManager",
    "iOSPlatformManager",
    "WindowsPlatformManager",
    "MacPlatformManager",
    "HarmonyPlatformManager",  # 新增
]
```

- [ ] **Step 2: Commit**

```bash
git add worker/platforms/__init__.py
git commit -m "feat: 增加 HarmonyPlatformManager 导出"
```

---

### Task 17: 修改 discovery/__init__.py 增加导出

**Files:**
- Modify: `worker/discovery/__init__.py`

- [ ] **Step 1: 增加 HarmonyDiscoverer 导入和导出**

```python
"""
设备发现模块。
"""

from worker.discovery.host import HostDiscoverer, HostInfo
from worker.discovery.android import AndroidDiscoverer, AndroidDeviceInfo
from worker.discovery.ios import iOSDiscoverer, iOSDeviceInfo
from worker.discovery.harmony import HarmonyDiscoverer, HarmonyDeviceInfo  # 新增

__all__ = [
    "HostDiscoverer",
    "HostInfo",
    "AndroidDiscoverer",
    "AndroidDeviceInfo",
    "iOSDiscoverer",
    "iOSDeviceInfo",
    "HarmonyDiscoverer",  # 新增
    "HarmonyDeviceInfo",  # 新增
]
```

- [ ] **Step 2: Commit**

```bash
git add worker/discovery/__init__.py
git commit -m "feat: 增加 HarmonyDiscoverer 导出"
```

---

### Task 18: 完善 worker.yaml 配置

**Files:**
- Modify: `config/worker.yaml`

- [ ] **Step 1: 增加 platforms.harmony 配置块**

在 `platforms` 部分增加：

```yaml
  # 鸿蒙平台配置
  harmony:
    enabled: null                   # 仅 Windows 支持
    hdc_path: tools/hdc/hdc.exe     # HDC 工具路径
    screenshot_method: snapshot_display  # 截图方式
    session_timeout: 300
    screenshot_dir: data/screenshots
```

- [ ] **Step 2: 增加 unlock.harmony_keypad 配置块**

在 `unlock` 部分增加：

```yaml
  # 鸿蒙密码键盘坐标配置（物理分辨率）
  harmony_keypad:
    # 1080x2400 分辨率（常见鸿蒙设备，需根据实际设备验证）
    "1080x2400":
      "1": {x: 180, y: 850}
      "2": {x: 540, y: 850}
      "3": {x: 900, y: 850}
      "4": {x: 180, y: 950}
      "5": {x: 540, y: 950}
      "6": {x: 900, y: 950}
      "7": {x: 180, y: 1050}
      "8": {x: 540, y: 1050}
      "9": {x: 900, y: 1050}
      "0": {x: 540, y: 1150}

    # 后备默认配置
    default:
      "1": {x: 180, y: 850}
      "2": {x: 540, y: 850}
      "3": {x: 900, y: 850}
      "4": {x: 180, y: 950}
      "5": {x: 540, y: 950}
      "6": {x: 900, y: 950}
      "7": {x: 180, y: 1050}
      "8": {x: 540, y: 1050}
      "9": {x: 900, y: 1050}
      "0": {x: 540, y: 1150}
```

- [ ] **Step 3: Commit**

```bash
git add config/worker.yaml
git commit -m "feat: worker.yaml 增加完整鸿蒙配置块"
```

---

### Task 19: 修改 unlock.py 增加 harmony 分支

**Files:**
- Modify: `worker/actions/unlock.py`

- [ ] **Step 1: 增加 DEFAULT_HARMONY_KEYPAD**

在 DEFAULT_ANDROID_KEYPAD 定义后增加：

```python
    # 鸿蒙 1080x2400（默认，需根据实际设备验证）
    DEFAULT_HARMONY_KEYPAD = {
        "1": {"x": 180, "y": 850},
        "2": {"x": 540, "y": 850},
        "3": {"x": 900, "y": 850},
        "4": {"x": 180, "y": 950},
        "5": {"x": 540, "y": 950},
        "6": {"x": 900, "y": 950},
        "7": {"x": 180, "y": 1050},
        "8": {"x": 540, "y": 1050},
        "9": {"x": 900, "y": 1050},
        "0": {"x": 540, "y": 1150},
    }
```

- [ ] **Step 2: 修改 _get_keypad_coords 方法增加 harmony 分支**

在 `_get_keypad_coords` 方法的 `elif platform_type == "android"` 分支后增加：

```python
        elif platform_type == "harmony":
            keypad = unlock_config.get("harmony_keypad", {})
            coords = keypad.get(resolution_key, keypad.get("default", self.DEFAULT_HARMONY_KEYPAD))
            logger.info(f"Using keypad config for: {resolution_key if resolution_key in keypad else 'default'}")
            return coords
```

- [ ] **Step 3: 修改 _get_device_resolution 方法增加 harmony 分支**

在 `_get_device_resolution` 方法的 `elif platform_type == "android"` 分支后增加：

```python
        elif platform_type == "harmony":
            client = context or platform._device_clients.get(platform._current_device)
            if client:
                try:
                    return client.display_size()
                except Exception as e:
                    logger.warning(f"Failed to get Harmony screen size: {e}")
```

- [ ] **Step 4: 修改 _get_scale_factor 方法增加 harmony 分支**

在 `_get_scale_factor` 方法的 `elif platform_type == "android"` 分支后增加：

```python
        elif platform_type == "harmony":
            # 鸿蒙不需要缩放
            return 1
```

- [ ] **Step 5: 修改 _check_locked 方法增加 harmony 分支**

在 `_check_locked` 方法的 `elif platform_type == "android"` 分支后增加：

```python
        elif platform_type == "harmony":
            client = context or platform._device_clients.get(platform._current_device)
            if client:
                try:
                    # 鸿蒙：通过 screen_state 判断（AWAKE 表示亮屏）
                    state = client.screen_state()
                    return state != "AWAKE"
                except Exception as e:
                    logger.warning(f"Failed to check Harmony locked state: {e}")
            return True
```

- [ ] **Step 6: 修改 _check_screen_brightness 方法增加 harmony 分支**

在 `_check_screen_brightness` 方法的 `if platform_type == "android"` 分支后增加：

```python
        elif platform_type == "harmony":
            client = context or platform._device_clients.get(platform._current_device)
            if client:
                try:
                    return client.is_screen_on()
                except Exception as e:
                    logger.warning(f"Failed to check Harmony screen state: {e}")
            return True
```

- [ ] **Step 7: 修改 _wake_screen 方法增加 harmony 分支**

在 `_wake_screen` 方法的 `elif platform_type == "android"` 分支后增加：

```python
        elif platform_type == "harmony":
            client = context or platform._device_clients.get(platform._current_device)
            if client:
                client.wakeup()
                logger.info("Harmony screen awakened")
```

- [ ] **Step 8: 修改 _get_unlock_method 方法增加 harmony 分支**

在 `_get_unlock_method` 方法的 `# Android 默认使用 swipe` 注释前增加：

```python
        # 鸿蒙默认使用 swipe_up
        elif platform.platform == "harmony":
            return "swipe_up"
```

- [ ] **Step 9: 修改 _trigger_password_screen 方法增加 harmony 分支**

在 `_trigger_password_screen` 方法中，在 `if platform_type == "ios"` 分支后增加：

```python
        elif platform_type == "harmony":
            client = context or platform._device_clients.get(platform._current_device)
            if client:
                # 鸿蒙：向上滑动触发解锁界面
                w, h = client.display_size()
                if w > 0 and h > 0:
                    start_y = int(h * 0.8)
                    end_y = int(h * 0.2)
                    center_x = int(w * 0.5)
                    client.swipe(center_x, start_y, center_x, end_y, speed=6000)
                    logger.info("Harmony swipe up for unlock")
```

- [ ] **Step 10: Commit**

```bash
git add worker/actions/unlock.py
git commit -m "feat: unlock.py 增加 harmony 分支支持"
```

---

## 实现完成检查清单

- [ ] HDC 工具已复制到 `tools/hdc/`
- [ ] `HarmonyHdcWrapper` 实现完整（shell、截图、点击、滑动、按键、应用管理）
- [ ] `HarmonyPlatformManager` 实现完整（生命周期、设备服务、动作执行）
- [ ] `HarmonyDiscoverer` 实现完整（设备发现、信息获取）
- [ ] `WorkerConfig` 增加 `discover_harmony_devices`
- [ ] `Worker` 注册 HarmonyPlatformManager
- [ ] `DeviceMonitor` 增加鸿蒙设备监控
- [ ] 设置窗口增加 Harmony checkbox
- [ ] `worker.yaml` 增加鸿蒙配置项（含 platforms.harmony 配置块）
- [ ] `worker/discovery/host.py` 增加 harmony 平台支持
- [ ] `worker/platforms/__init__.py` 增加 HarmonyPlatformManager 导出
- [ ] `worker/discovery/__init__.py` 增加 HarmonyDiscoverer 导出
- [ ] `worker/actions/unlock.py` 增加 harmony 分支
- [ ] 基本导入测试通过