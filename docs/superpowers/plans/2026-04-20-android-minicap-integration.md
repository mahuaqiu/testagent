# Android Minicap 截图集成 实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 集成 minicap 截图能力，解决 Android FLAG_SECURE 防截屏限制问题

**Architecture:** 从 airtest 复制 stf_libs 资源文件，实现简化的 Minicap 类，在 AndroidPlatformManager 中集成 minicap 截图逻辑，替换原有 uiautomator2.screenshot()

**Tech Stack:** Python, ADB, minicap binary (stf_libs), PIL

---

## File Structure

| 文件 | 操作 | 职责 |
|------|------|------|
| `worker/platforms/minicap/__init__.py` | 创建 | 模块导出 Minicap 类 |
| `worker/platforms/minicap/minicap.py` | 创建 | Minicap 核心类：install(), get_frame(), get_display_info() |
| `worker/platforms/minicap/static/stf_libs/` | 复制 | minicap 二进制资源（完整目录） |
| `worker/platforms/android.py` | 修改 | 集成 minicap：_minicap_instances, ensure_device_service(), take_screenshot() |

---

### Task 1: 复制 stf_libs 资源文件

**Files:**
- 复制: `D:\code\Airtest-master\airtest\core\android\static\stf_libs` → `D:\code\autotest\worker\platforms\minicap\static\stf_libs`

- [ ] **Step 1: 创建 minicap 目录结构**

Run:
```bash
mkdir -p worker/platforms/minicap/static
```

- [ ] **Step 2: 复制 stf_libs 资源目录**

Run:
```bash
cp -r "D:/code/Airtest-master/airtest/core/android/static/stf_libs" "D:/code/autotest/worker/platforms/minicap/static/"
```

- [ ] **Step 3: 验证复制成功**

Run:
```bash
ls -la worker/platforms/minicap/static/stf_libs/
```
Expected: 显示 arm64-v8a, armeabi-v7a, x86, x86_64, minicap-shared 等目录

---

### Task 2: 创建 minicap 模块基础文件

**Files:**
- 创建: `worker/platforms/minicap/__init__.py`
- 创建: `worker/platforms/minicap/minicap.py`

- [ ] **Step 1: 创建 __init__.py**

Create `worker/platforms/minicap/__init__.py`:
```python
"""
Minicap 截图模块。

基于 openstf minicap 实现，支持绑过 FLAG_SECURE 防截屏限制。
"""

from worker.platforms.minicap.minicap import Minicap

__all__ = ["Minicap"]
```

- [ ] **Step 2: 创建 minicap.py 框架**

Create `worker/platforms/minicap/minicap.py`:
```python
"""
Minicap 截图工具实现。

基于 airtest.core.android.cap_methods.minicap 适配，
使用纯 ADB 命令操作设备。
"""

import logging
import os
import re
import subprocess
from io import BytesIO
from pathlib import Path
from typing import Optional

from PIL import Image

logger = logging.getLogger(__name__)

# stf_libs 资源目录路径
STFLIB_PATH = Path(__file__).parent / "static" / "stf_libs"


class MinicapError(Exception):
    """Minicap 截图异常"""
    pass


class Minicap:
    """Android minicap 截图工具"""
    
    VERSION = 5
    DEVICE_DIR = "/data/local/tmp"
    CMD = "LD_LIBRARY_PATH=/data/local/tmp /data/local/tmp/minicap"
    
    def __init__(self, udid: str):
        self.udid = udid
        self._installed = False
        self._abi: Optional[str] = None
        self._sdk: Optional[int] = None
        self._display_info: Optional[dict] = None
    
    def _adb_shell(self, cmd: str, timeout: int = 30) -> str:
        """执行 adb shell 命令"""
        full_cmd = ["adb", "-s", self.udid, "shell", cmd]
        result = subprocess.run(
            full_cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        if result.returncode != 0:
            raise MinicapError(f"ADB shell failed: {result.stderr}")
        return result.stdout.strip()
    
    def _adb_push(self, local_path: str, remote_path: str) -> None:
        """执行 adb push 命令"""
        full_cmd = ["adb", "-s", self.udid, "push", local_path, remote_path]
        result = subprocess.run(
            full_cmd,
            capture_output=True,
            text=True,
            timeout=60,
        )
        if result.returncode != 0:
            raise MinicapError(f"ADB push failed: {result.stderr}")
    
    def _get_device_info(self) -> tuple[str, int]:
        """获取设备 CPU ABI 和 SDK 版本"""
        if self._abi and self._sdk:
            return self._abi, self._sdk
        
        # 获取 CPU ABI
        abi = self._adb_shell("getprop ro.product.cpu.abi")
        self._abi = abi
        
        # 获取 SDK 版本
        sdk_str = self._adb_shell("getprop ro.build.version.sdk")
        self._sdk = int(sdk_str)
        
        logger.info(f"Device info: abi={abi}, sdk={self._sdk}")
        return self._abi, self._sdk
    
    def get_display_info(self) -> dict:
        """获取屏幕显示信息"""
        if self._display_info:
            return self._display_info
        
        # 使用 wm size 和 wm density 获取信息
        size_output = self._adb_shell("wm size")
        density_output = self._adb_shell("wm density")
        
        # 解析物理分辨率
        width, height = 1080, 1920  # 默认值
        if "Physical size:" in size_output:
            match = re.search(r"Physical size: (\d+)x(\d+)", size_output)
            if match:
                width, height = int(match.group(1)), int(match.group(2))
        
        # 解析旋转角度（从 dumpsys display）
        rotation = 0
        try:
            display_output = self._adb_shell("dumpsys display | grep 'mOrientation'")
            match = re.search(r"mOrientation=(\d+)", display_output)
            if match:
                rotation = int(match.group(1)) * 90
        except Exception:
            pass
        
        self._display_info = {
            "width": width,
            "height": height,
            "rotation": rotation,
        }
        logger.info(f"Display info: {self._display_info}")
        return self._display_info
    
    def install(self) -> None:
        """安装 minicap 到设备"""
        if self._installed:
            logger.info("Minicap already installed, skipping")
            return
        
        abi, sdk = self._get_device_info()
        
        # 选择 minicap 二进制文件
        if sdk >= 16:
            binfile = "minicap"
        else:
            binfile = "minicap-nopie"
        
        # 推送 minicap 二进制
        minicap_bin_path = STFLIB_PATH / abi / binfile
        if not minicap_bin_path.exists():
            raise MinicapError(f"Minicap binary not found: {minicap_bin_path}")
        
        logger.info(f"Pushing minicap: {minicap_bin_path}")
        self._adb_push(str(minicap_bin_path), f"{self.DEVICE_DIR}/minicap")
        
        # 推送 minicap.so
        # 尝试按 SDK 版本匹配，若不存在则按 Release 版本
        minicap_so_pattern = STFLIB_PATH / "minicap-shared" / "aosp" / "libs" / f"android-{sdk}" / abi / "minicap.so"
        if not minicap_so_pattern.exists():
            # 尝试使用 Release 版本匹配
            rel = self._adb_shell("getprop ro.build.version.release")
            minicap_so_pattern = STFLIB_PATH / "minicap-shared" / "aosp" / "libs" / f"android-{rel}" / abi / "minicap.so"
        
        if not minicap_so_pattern.exists():
            raise MinicapError(f"Minicap.so not found for sdk={sdk}, abi={abi}")
        
        logger.info(f"Pushing minicap.so: {minicap_so_pattern}")
        self._adb_push(str(minicap_so_pattern), f"{self.DEVICE_DIR}/minicap.so")
        
        # 设置执行权限
        self._adb_shell(f"chmod 755 {self.DEVICE_DIR}/minicap")
        self._adb_shell(f"chmod 755 {self.DEVICE_DIR}/minicap.so")
        
        self._installed = True
        logger.info("Minicap installation completed")
    
    def get_frame(self) -> bytes:
        """获取单帧截图（JPG格式）"""
        if not self._installed:
            raise MinicapError("Minicap not installed, call install() first")
        
        display_info = self.get_display_info()
        width = display_info["width"]
        height = display_info["height"]
        rotation = display_info["rotation"]
        
        # 构建 minicap 参数
        # -P {width}x{height}@{width}x{height}/{rotation} -s
        params = f"{width}x{height}@{width}x{height}/{rotation}"
        cmd = f"{self.CMD} -n 'worker_minicap' -P {params} -s 2>&1"
        
        # 执行命令获取截图
        full_cmd = ["adb", "-s", self.udid, "shell", cmd]
        result = subprocess.run(
            full_cmd,
            capture_output=True,
            timeout=30,
        )
        
        raw_data = result.stdout
        
        # 提取 JPG 数据（去除日志输出）
        # minicap 输出格式：日志信息 + JPG 数据
        jpg_marker = b"for JPG encoder"
        if jpg_marker in raw_data:
            jpg_data = raw_data.split(jpg_marker)[-1]
            # 去除换行符
            jpg_data = jpg_data.replace(b"\r\r\n", b"\n").replace(b"\r\n", b"\n")
        else:
            jpg_data = raw_data
        
        # 验证 JPG 格式
        if not jpg_data.startswith(b"\xff\xd8") or not jpg_data.endswith(b"\xff\xd9"):
            raise MinicapError(f"Invalid JPG format, got {len(jpg_data)} bytes")
        
        return jpg_data
    
    def get_screenshot_png(self) -> bytes:
        """获取 PNG 格式截图"""
        jpg_data = self.get_frame()
        img = Image.open(BytesIO(jpg_data))
        buffer = BytesIO()
        img.save(buffer, format="PNG")
        return buffer.getvalue()
```

- [ ] **Step 3: 提交 minicap 模块**

Run:
```bash
git add worker/platforms/minicap/
git commit -m "feat: add minicap screenshot module"
```

---

### Task 3: 修改 AndroidPlatformManager 集成 minicap

**Files:**
- 修改: `worker/platforms/android.py`

- [ ] **Step 1: 添加 minicap 导入和实例字典**

修改 `worker/platforms/android.py`，在文件顶部添加导入：
```python
from worker.platforms.minicap import Minicap
from worker.platforms.minicap.minicap import MinicapError
```

在 `__init__` 方法中添加 `_minicap_instances`：
```python
def __init__(self, config: PlatformConfig, ocr_client=None, unlock_config=None):
    super().__init__(config, ocr_client)
    self._device_clients: dict[str, u2.Device] = {}
    self._current_device: str | None = None
    self._unlock_config = unlock_config or {}
    self._minicap_instances: dict[str, Minicap] = {}  # 新增
```

- [ ] **Step 2: 在 ensure_device_service 中安装 minicap**

修改 `ensure_device_service` 方法，在 uiautomator2 连接成功后安装 minicap：
```python
def ensure_device_service(self, udid: str) -> tuple[str, str]:
    """确保设备服务可用（由 DeviceMonitor 调用）。"""
    try:
        device = self._device_clients.get(udid)
        if device:
            try:
                device.info
                return ("online", "OK")
            except Exception:
                pass

        device = u2.connect(udid)
        device.info
        self._device_clients[udid] = device
        logger.info(f"Android device service ready: {udid}")
        
        # 新增：安装 minicap
        try:
            minicap = Minicap(udid)
            minicap.install()
            self._minicap_instances[udid] = minicap
            logger.info(f"Minicap installed for device: {udid}")
        except MinicapError as e:
            logger.warning(f"Minicap installation failed: {e}, will use fallback")
        
        return ("online", "OK")
    except Exception as e:
        logger.error(f"Failed to ensure device service: {udid}, {e}")
        return ("faulty", str(e))
```

- [ ] **Step 3: 修改 take_screenshot 使用 minicap**

修改 `take_screenshot` 方法：
```python
def take_screenshot(self, context: Any = None) -> bytes:
    """获取截图。"""
    device_id = self._current_device
    
    # 优先使用 minicap
    minicap = self._minicap_instances.get(device_id)
    if minicap:
        try:
            return minicap.get_screenshot_png()
        except MinicapError as e:
            logger.warning(f"Minicap screenshot failed: {e}, falling back to uiautomator2")
    
    # 回退到 uiautomator2
    device = context or self._device_clients.get(self._current_device)
    if device:
        from io import BytesIO
        img = device.screenshot()
        buffer = BytesIO()
        img.save(buffer, format="PNG")
        return buffer.getvalue()
    return b""
```

- [ ] **Step 4: 在 mark_device_faulty 中清理 minicap 实例**

修改 `mark_device_faulty` 方法：
```python
def mark_device_faulty(self, udid: str) -> None:
    """标记设备为异常。"""
    if udid in self._device_clients:
        del self._device_clients[udid]
    if udid in self._minicap_instances:  # 新增
        del self._minicap_instances[udid]
    logger.info(f"Android device marked faulty: {udid}")
```

- [ ] **Step 5: 提交 android.py 修改**

Run:
```bash
git add worker/platforms/android.py
git commit -m "feat: integrate minicap screenshot in AndroidPlatformManager"
```

---

### Task 4: 测试验证

- [ ] **Step 1: 简单语法检查**

Run:
```bash
python -c "from worker.platforms.minicap import Minicap; print('OK')"
```
Expected: 输出 `OK`

- [ ] **Step 2: 检查 android.py 导入**

Run:
```bash
python -c "from worker.platforms.android import AndroidPlatformManager; print('OK')"
```
Expected: 输出 `OK`

- [ ] **Step 3: 启动 Worker 测试（需连接设备）**

Run:
```bash
python -m worker.main
```

Expected: Worker 正常启动，连接设备时日志显示 minicap 安装信息

---

### Task 5: 最终提交和清理

- [ ] **Step 1: 运行代码检查**

Run:
```bash
ruff check worker/platforms/minicap/ worker/platforms/android.py
black worker/platforms/minicap/ worker/platforms/android.py
```

- [ ] **Step 2: 更新 MANIFEST.in（打包配置）**

检查并确保 `worker/platforms/minicap/static/stf_libs` 目录会被打包带入：
```bash
# 查看 MANIFEST.in 内容
cat MANIFEST.in
```

如果需要，添加：
```
include worker/platforms/minicap/static/stf_libs/**/*
```

- [ ] **Step 3: 最终提交**

Run:
```bash
git add -A
git commit -m "feat: complete minicap integration for Android screenshot"
```

---

## 完成标准

1. `worker/platforms/minicap/` 模块创建完成
2. stf_libs 资源文件复制到位
3. AndroidPlatformManager 正确集成 minicap
4. Worker 启动时自动安装 minicap 到设备
5. 截图优先使用 minicap，失败时回退到 uiautomator2
6. 代码通过 ruff/black 检查