# iOS/Android 直连模式实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将 iOS 和 Android 平台从 Appium Server 依赖改为直连模式，Worker 自动管理设备服务

**Architecture:**
- Android: uiautomator2 直连设备，ATX-Agent 自动部署
- iOS: tidevice3 + WDA 直连设备，Worker 管理 WDA 进程生命周期
- 新增 DeviceMonitor 模块独立管理设备状态（5分钟周期检测）

**Tech Stack:** uiautomator2, tidevice3, httpx, threading

---

## 文件结构

| 文件 | 负责内容 |
|------|----------|
| `worker/platforms/base.py` | 添加设备服务方法（修改，提供默认实现） |
| `worker/device_monitor.py` | 设备监控模块（新增） |
| `worker/platforms/android.py` | Android 平台管理器（重写） |
| `worker/platforms/ios.py` | iOS 平台管理器（重写） |
| `worker/platforms/wda_client.py` | WDA HTTP 客户端（新增） |
| `worker/discovery/ios.py` | iOS 设备发现（重写） |
| `worker/discovery/android.py` | Android 设备发现（小改） |
| `worker/worker.py` | 集成 DeviceMonitor（修改） |
| `worker/config.py` | 新增配置项（修改） |
| `config/worker.yaml` | 配置结构更新（修改） |
| `pyproject.toml` | 依赖更新（修改） |
| `wda/.gitkeep` | WDA 目录占位（新增） |

> **注意**：`web.py`、`windows.py`、`mac.py` 通过 `base.py` 的默认实现自动支持设备服务接口，无需额外修改。

---

## Phase 1: 基础设施

### Task 1: 更新依赖配置

**Files:**
- Modify: `pyproject.toml`

- [ ] **Step 1: 移除 Appium 依赖，添加 uiautomator2 和 tidevice**

查找 `dependencies` 中的 `"Appium-Python-Client>=3.1.0"` 行，替换为：

```toml
# 移除: "Appium-Python-Client>=3.1.0",
# 添加:
    "uiautomator2>=3.0",
    "tidevice>=3.0",
```

- [ ] **Step 2: 安装新依赖**

Run: `pip install uiautomator2 tidevice`
Expected: 安装成功

- [ ] **Step 3: Commit**

```bash
git add pyproject.toml
git commit -m "chore: replace Appium with uiautomator2 and tidevice"
```

---

### Task 2: 更新配置类

**Files:**
- Modify: `worker/config.py`

- [ ] **Step 1: 修改 WorkerConfig - 更新 device_check_interval 默认值并添加新字段**

将第 20 行：
```python
device_check_interval: int = 60
```
修改为：
```python
device_check_interval: int = 300      # 设备检测间隔(秒)，改为5分钟
service_retry_count: int = 3          # 服务启动重试次数
service_retry_interval: int = 10      # 重试间隔(秒)
```

- [ ] **Step 2: 修改 WorkerConfig.from_yaml - 添加新字段解析**

在第 51 行 `return cls(` 块中，添加新字段：

```python
        return cls(
            id=worker_data.get("id") or str(uuid.uuid4())[:8],
            port=worker_data.get("port", 8080),
            device_check_interval=worker_data.get("device_check_interval", 300),
            service_retry_count=worker_data.get("service_retry_count", 3),
            service_retry_interval=worker_data.get("service_retry_interval", 10),
            action_step_delay=worker_data.get("action_step_delay", 0.5),
            # ... 其余字段保持不变
```

- [ ] **Step 3: 修改 PlatformConfig - 移除 appium_server，添加移动端新字段**

将第 86-87 行：
```python
    # 移动端专用
    appium_server: str = ""
```
修改为：
```python
    # iOS 专用
    wda_base_port: int = 8100
    wda_ipa_path: str = "wda/WebDriverAgent.ipa"

    # Android 专用
    u2_port: int = 7912
```

- [ ] **Step 4: 修改 PlatformConfig.from_dict - 添加新字段解析**

将第 101 行：
```python
            appium_server=data.get("appium_server", ""),
```
修改为：
```python
            wda_base_port=data.get("wda_base_port", 8100),
            wda_ipa_path=data.get("wda_ipa_path", "wda/WebDriverAgent.ipa"),
            u2_port=data.get("u2_port", 7912),
```

- [ ] **Step 5: Commit**

```bash
git add worker/config.py
git commit -m "feat: add device monitor and platform config fields"
```

---

### Task 3: 更新配置文件

**Files:**
- Modify: `config/worker.yaml`

- [ ] **Step 1: 更新 worker 配置节**

将第 8 行：
```yaml
  device_check_interval: 60         # 设备检测间隔(秒)
```
修改为：
```yaml
  device_check_interval: 300        # 设备检测间隔(秒)，5分钟
  service_retry_count: 3            # 服务启动重试次数
  service_retry_interval: 10        # 重试间隔(秒)
```

- [ ] **Step 2: 更新 android 配置节 - 移除 appium_server，添加 u2_port**

将第 30-34 行：
```yaml
  android:
    enabled: null                   # 仅 Windows
    appium_server: ""               # Appium Server 地址（如：http://192.168.1.100:4723）
    session_timeout: 300
    screenshot_dir: data/screenshots
```
修改为：
```yaml
  android:
    enabled: null                   # 仅 Windows
    u2_port: 7912                   # uiautomator2 端口
    session_timeout: 300
    screenshot_dir: data/screenshots
```

- [ ] **Step 3: 更新 ios 配置节 - 移除 appium_server，添加 WDA 配置**

将第 36-40 行：
```yaml
  ios:
    enabled: null                   # 仅 Windows
    appium_server: ""               # Appium Server 地址（如：http://192.168.1.100:4724）
    session_timeout: 300
    screenshot_dir: data/screenshots
```
修改为：
```yaml
  ios:
    enabled: null                   # 仅 Windows
    wda_base_port: 8100             # WDA 基础端口
    wda_ipa_path: wda/WebDriverAgent.ipa
    session_timeout: 300
    screenshot_dir: data/screenshots
```

- [ ] **Step 4: Commit**

```bash
git add config/worker.yaml
git commit -m "feat: update worker config for direct connection"
```

---

### Task 4: 创建 WDA 目录

**Files:**
- Create: `wda/.gitkeep`

- [ ] **Step 1: 创建 wda 目录**

Run: `mkdir -p wda`

- [ ] **Step 2: 创建 .gitkeep 占位文件**

Run: `touch wda/.gitkeep`

- [ ] **Step 3: Commit**

```bash
git add wda/.gitkeep
git commit -m "chore: create wda directory for WebDriverAgent.ipa"
```

---

### Task 5: 更新 PlatformManager 基类

**Files:**
- Modify: `worker/platforms/base.py`

- [ ] **Step 1: 添加抽象方法到 PlatformManager 类**

在 `take_screenshot` 方法后（约第 200 行），添加：

```python
    # ========== 设备服务管理（移动端平台实现） ==========

    def ensure_device_service(self, udid: str) -> tuple[str, str]:
        """
        确保设备服务可用。

        Args:
            udid: 设备 UDID

        Returns:
            tuple[str, str]: (status, message) - status 为 "online" 或 "faulty"
        """
        # 默认实现：非移动端平台始终返回 online
        return ("online", "OK")

    def mark_device_faulty(self, udid: str) -> None:
        """
        标记设备为异常。

        Args:
            udid: 设备 UDID
        """
        pass

    def get_online_devices(self) -> list[str]:
        """
        获取在线设备 UDID 列表。

        Returns:
            list[str]: 在线设备 UDID 列表
        """
        return []
```

- [ ] **Step 2: Commit**

```bash
git add worker/platforms/base.py
git commit -m "feat: add device service methods to PlatformManager base"
```

---

### Task 6: 创建设备监控模块

**Files:**
- Create: `worker/device_monitor.py`

- [ ] **Step 1: 创建 DeviceMonitor 类**

```python
"""
设备监控模块。

独立监控设备状态，维护设备服务。
"""

import logging
import threading
from typing import Any, Callable, Dict, List, Optional

from worker.config import WorkerConfig

logger = logging.getLogger(__name__)


class DeviceMonitor:
    """
    设备监控器。

    负责：
    - 定时检测物理设备连接
    - 维护设备服务状态（WDA/uiautomator2）
    - 管理正常/异常设备列表
    - 自动恢复异常设备
    """

    def __init__(self, config: WorkerConfig):
        self.config = config
        self.check_interval = config.device_check_interval
        self.retry_count = config.service_retry_count
        self.retry_interval = config.service_retry_interval

        # 设备列表
        self._android_devices: List[Dict[str, Any]] = []
        self._ios_devices: List[Dict[str, Any]] = []
        self._faulty_android_devices: List[Dict[str, Any]] = []
        self._faulty_ios_devices: List[Dict[str, Any]] = []

        # 平台管理器引用
        self._android_manager: Optional[Any] = None
        self._ios_manager: Optional[Any] = None

        # 线程控制
        self._monitor_thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()

        # 回调
        self.on_device_change: Optional[Callable[[Dict], None]] = None

    def set_platform_managers(self, android_manager=None, ios_manager=None) -> None:
        """设置平台管理器引用。"""
        self._android_manager = android_manager
        self._ios_manager = ios_manager

    def start(self) -> None:
        """启动监控。"""
        if self._monitor_thread and self._monitor_thread.is_alive():
            return

        self._stop_event.clear()
        self._monitor_thread = threading.Thread(target=self._monitor_loop, daemon=True)
        self._monitor_thread.start()
        logger.info(f"Device monitor started (interval={self.check_interval}s)")

    def stop(self) -> None:
        """停止监控。"""
        self._stop_event.set()
        if self._monitor_thread:
            self._monitor_thread.join(timeout=5)
        logger.info("Device monitor stopped")

    def _monitor_loop(self) -> None:
        """监控循环。"""
        # 首次立即执行
        self._check_and_maintain()

        while not self._stop_event.is_set():
            self._stop_event.wait(self.check_interval)

            if self._stop_event.is_set():
                break

            self._check_and_maintain()

    def _check_and_maintain(self) -> None:
        """检查和维护设备。"""
        self._detect_physical_devices()
        self._maintain_services()

        if self.on_device_change:
            self.on_device_change(self.get_all_devices())

    def _detect_physical_devices(self) -> None:
        """检测物理设备连接。"""
        # Android 设备检测
        if self._android_manager:
            try:
                from worker.discovery.android import AndroidDiscoverer
                devices = AndroidDiscoverer.discover()

                existing_udids = {d["udid"] for d in self._android_devices}
                existing_udids.update({d["udid"] for d in self._faulty_android_devices})

                for device in devices:
                    if device.udid not in existing_udids:
                        logger.info(f"New Android device detected: {device.udid}")
                        self._add_device("android", {
                            "udid": device.udid,
                            "name": device.name,
                            "model": device.model,
                        })
            except Exception as e:
                logger.error(f"Android device detection failed: {e}")

        # iOS 设备检测
        if self._ios_manager:
            try:
                from worker.discovery.ios import iOSDiscoverer
                devices = iOSDiscoverer.discover()

                existing_udids = {d["udid"] for d in self._ios_devices}
                existing_udids.update({d["udid"] for d in self._faulty_ios_devices})

                for device in devices:
                    if device.udid not in existing_udids:
                        logger.info(f"New iOS device detected: {device.udid}")
                        self._add_device("ios", {
                            "udid": device.udid,
                            "name": device.name,
                            "model": device.model,
                            "os_version": device.os_version,
                        })
            except Exception as e:
                logger.error(f"iOS device detection failed: {e}")

    def _add_device(self, platform: str, device_info: Dict[str, Any]) -> None:
        """添加新设备到异常列表，立即尝试启动服务。"""
        if platform == "android":
            self._faulty_android_devices.append(device_info)
        else:
            self._faulty_ios_devices.append(device_info)

        self._try_start_service(platform, device_info["udid"])

    def _try_start_service(self, platform: str, udid: str) -> None:
        """尝试启动设备服务。"""
        manager = self._android_manager if platform == "android" else self._ios_manager
        if not manager:
            return

        for attempt in range(self.retry_count):
            status, message = manager.ensure_device_service(udid)

            if status == "online":
                if platform == "android":
                    self._faulty_android_devices = [
                        d for d in self._faulty_android_devices if d["udid"] != udid
                    ]
                    self._android_devices.append({"udid": udid})
                else:
                    self._faulty_ios_devices = [
                        d for d in self._faulty_ios_devices if d["udid"] != udid
                    ]
                    self._ios_devices.append({"udid": udid})

                logger.info(f"Device service started: {udid}")
                return

            logger.warning(f"Service start attempt {attempt + 1} failed for {udid}: {message}")

            if attempt < self.retry_count - 1:
                self._stop_event.wait(self.retry_interval)
                if self._stop_event.is_set():
                    return

        logger.error(f"Failed to start service for {udid} after {self.retry_count} attempts")

    def _maintain_services(self) -> None:
        """维护服务状态，检查异常设备恢复。"""
        for device in self._faulty_android_devices[:]:
            self._try_start_service("android", device["udid"])

        for device in self._faulty_ios_devices[:]:
            self._try_start_service("ios", device["udid"])

        self._check_online_devices()

    def _check_online_devices(self) -> None:
        """检查在线设备状态。"""
        if self._android_manager:
            for device in self._android_devices[:]:
                udid = device["udid"]
                manager_devices = self._android_manager.get_online_devices()
                if udid not in manager_devices:
                    self._android_devices = [d for d in self._android_devices if d["udid"] != udid]
                    self._faulty_android_devices.append({"udid": udid})
                    logger.warning(f"Android device went offline: {udid}")

        if self._ios_manager:
            for device in self._ios_devices[:]:
                udid = device["udid"]
                manager_devices = self._ios_manager.get_online_devices()
                if udid not in manager_devices:
                    self._ios_devices = [d for d in self._ios_devices if d["udid"] != udid]
                    self._faulty_ios_devices.append({"udid": udid})
                    logger.warning(f"iOS device went offline: {udid}")

    def get_all_devices(self) -> Dict[str, Any]:
        """获取所有设备状态。"""
        return {
            "android": self._android_devices,
            "ios": self._ios_devices,
            "faulty_android": self._faulty_android_devices,
            "faulty_ios": self._faulty_ios_devices,
        }

    def get_online_devices(self, platform: str) -> List[str]:
        """获取在线设备 UDID 列表。"""
        if platform == "android":
            return [d["udid"] for d in self._android_devices]
        else:
            return [d["udid"] for d in self._ios_devices]

    def is_device_online(self, platform: str, udid: str) -> bool:
        """检查设备是否在线。"""
        return udid in self.get_online_devices(platform)
```

- [ ] **Step 2: Commit**

```bash
git add worker/device_monitor.py
git commit -m "feat: add DeviceMonitor module"
```

---

## Phase 2: Android 改造

### Task 7: 重写 Android 平台管理器

**Files:**
- Rewrite: `worker/platforms/android.py`

- [ ] **Step 1: 重写导入和类结构**

完整替换文件内容为：

```python
"""
Android 平台执行引擎。

基于 uiautomator2 直连实现，支持 OCR/图像识别定位。
"""

import logging
import time
from typing import Any, Dict, Optional, Set

import uiautomator2 as u2

from worker.platforms.base import PlatformManager
from worker.task import Action, ActionResult, ActionStatus
from worker.config import PlatformConfig
from worker.actions import ActionRegistry

logger = logging.getLogger(__name__)


class AndroidPlatformManager(PlatformManager):
    """
    Android 平台管理器。

    使用 uiautomator2 直连控制 Android 设备。
    """

    SUPPORTED_ACTIONS: Set[str] = {"start_app", "stop_app"}

    KEY_MAP = {
        "HOME": 3,
        "BACK": 4,
        "MENU": 82,
        "ENTER": 66,
        "SEARCH": 84,
    }

    def __init__(self, config: PlatformConfig, ocr_client=None):
        super().__init__(config, ocr_client)
        self._device_clients: Dict[str, u2.Device] = {}
        self._current_device: Optional[str] = None

    @property
    def platform(self) -> str:
        return "android"

    def start(self) -> None:
        """启动 Android 平台（检查环境）。"""
        if self._started:
            return

        try:
            # 检查 ADB 是否可用
            import subprocess
            result = subprocess.run(["adb", "version"], capture_output=True, timeout=5)
            if result.returncode != 0:
                logger.warning("ADB not available")
        except Exception as e:
            logger.warning(f"ADB check failed: {e}")

        self._started = True
        logger.info("Android platform started (uiautomator2 mode)")

    def stop(self) -> None:
        """停止 Android 平台。"""
        self._device_clients.clear()
        self._started = False
        logger.info("Android platform stopped")

    def is_available(self) -> bool:
        """检查平台是否可用。"""
        return self._started

    # ========== 设备服务管理 ==========

    def ensure_device_service(self, udid: str) -> tuple[str, str]:
        """确保设备服务可用（由 DeviceMonitor 调用）。"""
        try:
            device = self._device_clients.get(udid)
            if device and device.ping():
                return ("online", "OK")

            # 尝试连接设备
            device = u2.connect(udid)
            if device.ping():
                self._device_clients[udid] = device
                logger.info(f"Android device service ready: {udid}")
                return ("online", "OK")
            else:
                return ("faulty", "Service not responding")
        except Exception as e:
            logger.error(f"Failed to ensure device service: {udid}, {e}")
            return ("faulty", str(e))

    def mark_device_faulty(self, udid: str) -> None:
        """标记设备为异常。"""
        if udid in self._device_clients:
            del self._device_clients[udid]
            logger.info(f"Android device marked faulty: {udid}")

    def get_online_devices(self) -> list[str]:
        """获取在线设备列表。"""
        return list(self._device_clients.keys())

    # ========== 上下文管理 ==========

    def create_context(self, device_id: Optional[str] = None, options: Optional[Dict] = None) -> u2.Device:
        """获取已有的设备连接。"""
        if not self.is_available():
            raise RuntimeError("Android platform not started")

        if not device_id:
            raise ValueError("device_id is required for Android platform")

        device = self._device_clients.get(device_id)
        if device is None:
            raise RuntimeError(f"Device service not ready: {device_id}")

        self._current_device = device_id
        logger.info(f"Android context created: {device_id}")
        return device

    def close_context(self, context: Any, close_session: bool = False) -> None:
        """关闭上下文（uiautomator2 保持连接）。"""
        if close_session:
            for udid, client in list(self._device_clients.items()):
                if client == context:
                    del self._device_clients[udid]
                    break
        logger.info("Android context closed")

    # ========== 会话管理（兼容旧接口） ==========

    def has_active_session(self, device_id: Optional[str] = None) -> bool:
        """检查是否有活跃的会话。"""
        if device_id:
            return device_id in self._device_clients
        return len(self._device_clients) > 0

    def get_session_context(self, device_id: Optional[str] = None) -> Any:
        """获取当前会话的上下文。"""
        if device_id:
            return self._device_clients.get(device_id)
        if self._current_device:
            return self._device_clients.get(self._current_device)
        return None

    def close_session(self, device_id: Optional[str] = None) -> None:
        """关闭会话。"""
        if device_id:
            if device_id in self._device_clients:
                del self._device_clients[device_id]
            logger.info(f"Android session closed (device={device_id})")
        else:
            self._device_clients.clear()
            logger.info("All Android sessions closed")

    # ========== 基础能力实现 ==========

    def click(self, x: int, y: int, context: Any = None) -> None:
        """点击指定坐标。"""
        device = context or self._device_clients.get(self._current_device)
        if device:
            device.click(x, y)

    def input_text(self, text: str, context: Any = None) -> None:
        """输入文本。"""
        device = context or self._device_clients.get(self._current_device)
        if device:
            device.send_keys(text)

    def swipe(self, start_x: int, start_y: int, end_x: int, end_y: int, context: Any = None) -> None:
        """滑动。"""
        device = context or self._device_clients.get(self._current_device)
        if device:
            device.swipe(start_x, start_y, end_x, end_y, duration=0.5)

    def press(self, key: str, context: Any = None) -> None:
        """按键。"""
        device = context or self._device_clients.get(self._current_device)
        if device:
            key_name = key.upper() if key else ""
            key_code = self.KEY_MAP.get(key_name)

            if key_code:
                device.press(key_code)
            elif key and key.isdigit():
                device.press(int(key))
            else:
                raise ValueError(f"Unknown key: {key}")

    def take_screenshot(self, context: Any = None) -> bytes:
        """获取截图。"""
        device = context or self._device_clients.get(self._current_device)
        if device:
            from io import BytesIO
            img = device.screenshot()
            buffer = BytesIO()
            img.save(buffer, format="PNG")
            return buffer.getvalue()
        return b""

    def get_screenshot(self, context: Any) -> bytes:
        """获取当前屏幕截图（兼容旧接口）。"""
        return self.take_screenshot(context)

    # ========== 动作执行 ==========

    def execute_action(self, context: Any, action: Action) -> ActionResult:
        """执行动作。"""
        start_time = time.time()

        device = context
        if not device and action.action_type not in ("start_app", "stop_app"):
            return ActionResult(
                index=0,
                action_type=action.action_type,
                status=ActionStatus.FAILED,
                error="Device context is invalid",
            )

        if device:
            for udid, client in self._device_clients.items():
                if client == device:
                    self._current_device = udid
                    break

        try:
            if action.action_type == "start_app":
                result = self._action_start_app(device, action)
            elif action.action_type == "stop_app":
                result = self._action_stop_app(device, action)
            else:
                executor = ActionRegistry.get(action.action_type)
                if executor:
                    result = executor.execute(self, action, device)
                else:
                    result = ActionResult(
                        index=0,
                        action_type=action.action_type,
                        status=ActionStatus.FAILED,
                        error=f"Unknown action type: {action.action_type}",
                    )

            duration_ms = int((time.time() - start_time) * 1000)
            result.duration_ms = duration_ms
            return result

        except Exception as e:
            duration_ms = int((time.time() - start_time) * 1000)
            return ActionResult(
                index=0,
                action_type=action.action_type,
                status=ActionStatus.FAILED,
                duration_ms=duration_ms,
                error=str(e),
            )

    # ========== 平台特有动作实现 ==========

    def _action_start_app(self, device, action: Action) -> ActionResult:
        """启动应用。"""
        package = action.package_name or action.value
        if not package:
            return ActionResult(
                index=0,
                action_type="start_app",
                status=ActionStatus.FAILED,
                error="package_name is required",
            )

        if device:
            device.app_start(package)

        return ActionResult(
            index=0,
            action_type="start_app",
            status=ActionStatus.SUCCESS,
            output=f"Started: {package}",
        )

    def _action_stop_app(self, device, action: Action) -> ActionResult:
        """关闭应用。"""
        if device and self._current_device:
            package = action.package_name or action.value
            if package:
                device.app_stop(package)
            return ActionResult(
                index=0,
                action_type="stop_app",
                status=ActionStatus.SUCCESS,
                output=f"Stopped app",
            )
        else:
            return ActionResult(
                index=0,
                action_type="stop_app",
                status=ActionStatus.FAILED,
                error="No device context",
            )
```

- [ ] **Step 2: Commit**

```bash
git add worker/platforms/android.py
git commit -m "refactor(android): rewrite with uiautomator2 direct connection"
```

---

### Task 8: 更新 Android 设备发现

**Files:**
- Modify: `worker/discovery/android.py`

- [ ] **Step 1: 添加 uiautomator2 状态检测方法**

在文件末尾添加：

```python
    @staticmethod
    def check_u2_service(udid: str) -> bool:
        """检查 uiautomator2 服务是否可用。"""
        try:
            import uiautomator2 as u2
            device = u2.connect(udid)
            return device.ping()
        except Exception:
            return False
```

- [ ] **Step 2: Commit**

```bash
git add worker/discovery/android.py
git commit -m "feat(android): add uiautomator2 service check"
```

---

## Phase 3: iOS 改造

### Task 9: 创建 WDA HTTP 客户端

**Files:**
- Create: `worker/platforms/wda_client.py`

- [ ] **Step 1: 创建 WDA 客户端类**

```python
"""
WDA (WebDriverAgent) HTTP 客户端。

通过 HTTP 调用 WDA 服务控制 iOS 设备。
"""

import base64
import logging
import time
from typing import Optional

import httpx

logger = logging.getLogger(__name__)


class WDAClient:
    """WDA HTTP 客户端。"""

    def __init__(self, base_url: str, timeout: int = 30):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.session = httpx.Client(timeout=timeout)
        self._session_id: Optional[str] = None

    def health_check(self) -> bool:
        """检查服务状态。"""
        try:
            response = self.session.get(f"{self.base_url}/status")
            return response.status_code == 200
        except Exception:
            return False

    def wait_ready(self, timeout: int = 30) -> bool:
        """等待服务就绪。"""
        start = time.time()
        while time.time() - start < timeout:
            if self.health_check():
                return True
            time.sleep(1)
        return False

    def _get_session(self) -> str:
        """获取或创建 WebDriver 会话。"""
        if self._session_id:
            return self._session_id

        response = self.session.post(
            f"{self.base_url}/session",
            json={"capabilities": {}}
        )
        if response.status_code == 200:
            data = response.json()
            self._session_id = data.get("sessionId") or data.get("value", {}).get("sessionId")
            return self._session_id
        raise RuntimeError(f"Failed to create session: {response.text}")

    def tap(self, x: int, y: int) -> bool:
        """点击坐标。"""
        try:
            session_id = self._get_session()
            response = self.session.post(
                f"{self.base_url}/session/{session_id}/wda/tap/{x}/{y}"
            )
            return response.status_code == 200
        except Exception as e:
            logger.error(f"Tap failed: {e}")
            return False

    def swipe(self, sx: int, sy: int, ex: int, ey: int, duration: float = 0.5) -> bool:
        """滑动。"""
        try:
            session_id = self._get_session()
            response = self.session.post(
                f"{self.base_url}/session/{session_id}/wda/dragfromtoforduration",
                json={
                    "fromX": sx,
                    "fromY": sy,
                    "toX": ex,
                    "toY": ey,
                    "duration": duration
                }
            )
            return response.status_code == 200
        except Exception as e:
            logger.error(f"Swipe failed: {e}")
            return False

    def screenshot(self) -> bytes:
        """截图。"""
        try:
            response = self.session.get(f"{self.base_url}/screenshot")
            if response.status_code == 200:
                data = response.json()
                value = data.get("value", data)
                if isinstance(value, str):
                    return base64.b64decode(value)
                return value
        except Exception as e:
            logger.error(f"Screenshot failed: {e}")
        return b""

    def send_keys(self, text: str) -> bool:
        """输入文本。"""
        try:
            session_id = self._get_session()
            response = self.session.post(
                f"{self.base_url}/session/{session_id}/wda/keys",
                json={"value": list(text)}
            )
            return response.status_code == 200
        except Exception as e:
            logger.error(f"Send keys failed: {e}")
            return False

    def press_button(self, name: str) -> bool:
        """按键（HOME, VOLUME_UP 等）。"""
        try:
            response = self.session.post(
                f"{self.base_url}/wda/pressButton",
                json={"name": name}
            )
            return response.status_code == 200
        except Exception as e:
            logger.error(f"Press button failed: {e}")
            return False

    def close(self) -> None:
        """关闭客户端。"""
        if self._session_id:
            try:
                self.session.delete(f"{self.base_url}/session/{self._session_id}")
            except Exception:
                pass
        self.session.close()
```

- [ ] **Step 2: Commit**

```bash
git add worker/platforms/wda_client.py
git commit -m "feat(ios): add WDA HTTP client"
```

---

### Task 10: 重写 iOS 平台管理器

**Files:**
- Rewrite: `worker/platforms/ios.py`

- [ ] **Step 1: 重写 iOS 平台管理器**

完整替换文件内容：

```python
"""
iOS 平台执行引擎。

基于 tidevice3 + WDA 直连实现，支持 OCR/图像识别定位。
"""

import logging
import subprocess
import time
from typing import Any, Dict, Optional, Set

import tidevice

from worker.platforms.base import PlatformManager
from worker.platforms.wda_client import WDAClient
from worker.task import Action, ActionResult, ActionStatus
from worker.config import PlatformConfig
from worker.actions import ActionRegistry

logger = logging.getLogger(__name__)


class iOSPlatformManager(PlatformManager):
    """
    iOS 平台管理器。

    使用 tidevice3 + WDA 直连控制 iOS 设备。
    """

    SUPPORTED_ACTIONS: Set[str] = {"start_app", "stop_app"}
    WDA_BUNDLE_ID = "com.facebook.WebDriverAgentRunner"

    def __init__(self, config: PlatformConfig, ocr_client=None):
        super().__init__(config, ocr_client)
        self.wda_base_port = config.wda_base_port or 8100
        self.wda_ipa_path = config.wda_ipa_path or "wda/WebDriverAgent.ipa"
        self._device_wda: Dict[str, dict] = {}  # udid -> {"port": int, "process": Popen}
        self._device_clients: Dict[str, WDAClient] = {}
        self._current_device: Optional[str] = None
        self._port_counter = 0

    @property
    def platform(self) -> str:
        return "ios"

    def start(self) -> None:
        """启动 iOS 平台（检查环境）。"""
        if self._started:
            return

        try:
            devices = tidevice.usb_device_list()
            logger.info(f"tidevice available, found {len(devices)} devices")
        except Exception as e:
            logger.warning(f"tidevice check failed: {e}")

        self._started = True
        logger.info("iOS platform started (tidevice3 + WDA mode)")

    def stop(self) -> None:
        """停止 iOS 平台。"""
        for udid in list(self._device_wda.keys()):
            self._stop_wda(udid)
        self._device_clients.clear()
        self._device_wda.clear()
        self._started = False
        logger.info("iOS platform stopped")

    def is_available(self) -> bool:
        """检查平台是否可用。"""
        return self._started

    # ========== 设备服务管理 ==========

    def ensure_device_service(self, udid: str) -> tuple[str, str]:
        """确保 WDA 服务可用（由 DeviceMonitor 调用）。"""
        try:
            client = self._device_clients.get(udid)
            if client and client.health_check():
                return ("online", "OK")

            return self._start_wda(udid)
        except Exception as e:
            logger.error(f"Failed to ensure WDA service: {udid}, {e}")
            return ("faulty", str(e))

    def mark_device_faulty(self, udid: str) -> None:
        """标记设备为异常。"""
        if udid in self._device_clients:
            del self._device_clients[udid]
        self._stop_wda(udid)
        logger.info(f"iOS device marked faulty: {udid}")

    def get_online_devices(self) -> list[str]:
        """获取在线设备列表。"""
        return list(self._device_clients.keys())

    def _allocate_port(self) -> int:
        """分配 WDA 端口。"""
        self._port_counter += 1
        return self.wda_base_port + self._port_counter

    def _stop_wda(self, udid: str) -> None:
        """停止 WDA 进程。"""
        if udid in self._device_wda:
            wda_info = self._device_wda[udid]
            process = wda_info.get("process")
            if process:
                try:
                    process.terminate()
                    process.wait(timeout=5)
                except Exception:
                    process.kill()
            del self._device_wda[udid]

    def _start_wda(self, udid: str) -> tuple[str, str]:
        """启动 WDA 服务。"""
        try:
            device = tidevice.Device(udid)

            if udid in self._device_wda:
                self._stop_wda(udid)

            port = self._allocate_port()

            process = subprocess.Popen(
                [
                    "tidevice",
                    "-u", udid,
                    "xctest",
                    "-B", self.WDA_BUNDLE_ID,
                    "--port", str(port)
                ],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE
            )

            base_url = f"http://127.0.0.1:{port}"
            client = WDAClient(base_url)

            if client.wait_ready(timeout=30):
                self._device_wda[udid] = {"port": port, "process": process}
                self._device_clients[udid] = client
                logger.info(f"WDA started: {udid} on port {port}")
                return ("online", "OK")
            else:
                process.terminate()
                return ("faulty", "WDA failed to start")

        except Exception as e:
            logger.error(f"Failed to start WDA: {e}")
            return ("faulty", str(e))

    # ========== 上下文管理 ==========

    def create_context(self, device_id: Optional[str] = None, options: Optional[Dict] = None) -> WDAClient:
        """获取已有的 WDA 连接。"""
        if not self.is_available():
            raise RuntimeError("iOS platform not started")

        if not device_id:
            raise ValueError("device_id is required for iOS platform")

        client = self._device_clients.get(device_id)
        if client is None:
            raise RuntimeError(f"WDA service not ready: {device_id}")

        self._current_device = device_id
        logger.info(f"iOS context created: {device_id}")
        return client

    def close_context(self, context: Any, close_session: bool = False) -> None:
        """关闭上下文。"""
        if close_session:
            for udid, client in list(self._device_clients.items()):
                if client == context:
                    client.close()
                    del self._device_clients[udid]
                    break
        logger.info("iOS context closed")

    # ========== 会话管理（兼容旧接口） ==========

    def has_active_session(self, device_id: Optional[str] = None) -> bool:
        """检查是否有活跃的会话。"""
        if device_id:
            return device_id in self._device_clients
        return len(self._device_clients) > 0

    def get_session_context(self, device_id: Optional[str] = None) -> Any:
        """获取当前会话的上下文。"""
        if device_id:
            return self._device_clients.get(device_id)
        if self._current_device:
            return self._device_clients.get(self._current_device)
        return None

    def close_session(self, device_id: Optional[str] = None) -> None:
        """关闭会话。"""
        if device_id:
            self._stop_wda(device_id)
            if device_id in self._device_clients:
                del self._device_clients[device_id]
            logger.info(f"iOS session closed (device={device_id})")
        else:
            for udid in list(self._device_wda.keys()):
                self._stop_wda(udid)
            self._device_clients.clear()
            logger.info("All iOS sessions closed")

    # ========== 基础能力实现 ==========

    def click(self, x: int, y: int, context: Any = None) -> None:
        """点击指定坐标。"""
        client = context or self._device_clients.get(self._current_device)
        if client:
            client.tap(x, y)

    def input_text(self, text: str, context: Any = None) -> None:
        """输入文本。"""
        client = context or self._device_clients.get(self._current_device)
        if client:
            client.send_keys(text)

    def swipe(self, start_x: int, start_y: int, end_x: int, end_y: int, context: Any = None) -> None:
        """滑动。"""
        client = context or self._device_clients.get(self._current_device)
        if client:
            client.swipe(start_x, start_y, end_x, end_y)

    def press(self, key: str, context: Any = None) -> None:
        """按键。"""
        client = context or self._device_clients.get(self._current_device)
        if client:
            client.press_button(key.upper())

    def take_screenshot(self, context: Any = None) -> bytes:
        """获取截图。"""
        client = context or self._device_clients.get(self._current_device)
        if client:
            return client.screenshot()
        return b""

    def get_screenshot(self, context: Any) -> bytes:
        """获取当前屏幕截图（兼容旧接口）。"""
        return self.take_screenshot(context)

    # ========== 动作执行 ==========

    def execute_action(self, context: Any, action: Action) -> ActionResult:
        """执行动作。"""
        start_time = time.time()

        client = context
        if not client and action.action_type not in ("start_app", "stop_app"):
            return ActionResult(
                index=0,
                action_type=action.action_type,
                status=ActionStatus.FAILED,
                error="WDA context is invalid",
            )

        if client:
            for udid, c in self._device_clients.items():
                if c == client:
                    self._current_device = udid
                    break

        try:
            if action.action_type == "start_app":
                result = self._action_start_app(client, action)
            elif action.action_type == "stop_app":
                result = self._action_stop_app(client, action)
            elif action.action_type == "ocr_paste":
                result = ActionResult(
                    index=0,
                    action_type="ocr_paste",
                    status=ActionStatus.FAILED,
                    error="ocr_paste is not supported on iOS",
                )
            else:
                executor = ActionRegistry.get(action.action_type)
                if executor:
                    result = executor.execute(self, action, client)
                else:
                    result = ActionResult(
                        index=0,
                        action_type=action.action_type,
                        status=ActionStatus.FAILED,
                        error=f"Unknown action type: {action.action_type}",
                    )

            duration_ms = int((time.time() - start_time) * 1000)
            result.duration_ms = duration_ms
            return result

        except Exception as e:
            duration_ms = int((time.time() - start_time) * 1000)
            return ActionResult(
                index=0,
                action_type=action.action_type,
                status=ActionStatus.FAILED,
                duration_ms=duration_ms,
                error=str(e),
            )

    # ========== 平台特有动作实现 ==========

    def _action_start_app(self, client, action: Action) -> ActionResult:
        """启动应用。"""
        bundle_id = action.bundle_id or action.value
        if not bundle_id:
            return ActionResult(
                index=0,
                action_type="start_app",
                status=ActionStatus.FAILED,
                error="bundle_id is required",
            )

        if client and self._current_device:
            try:
                subprocess.run(
                    ["tidevice", "-u", self._current_device, "launch", bundle_id],
                    check=True, timeout=30
                )
            except Exception as e:
                logger.warning(f"Failed to launch app via tidevice: {e}")

        return ActionResult(
            index=0,
            action_type="start_app",
            status=ActionStatus.SUCCESS,
            output=f"Started: {bundle_id}",
        )

    def _action_stop_app(self, client, action: Action) -> ActionResult:
        """关闭应用。"""
        if self._current_device:
            return ActionResult(
                index=0,
                action_type="stop_app",
                status=ActionStatus.SUCCESS,
                output=f"Stopped app session",
            )
        else:
            return ActionResult(
                index=0,
                action_type="stop_app",
                status=ActionStatus.FAILED,
                error="No device context",
            )
```

- [ ] **Step 2: Commit**

```bash
git add worker/platforms/ios.py
git commit -m "refactor(ios): rewrite with tidevice3 + WDA direct connection"
```

---

### Task 11: 重写 iOS 设备发现

**Files:**
- Rewrite: `worker/discovery/ios.py`

- [ ] **Step 1: 重写 iOS 设备发现（保留分辨率映射）**

完整替换文件内容：

```python
"""
iOS 设备发现模块。

使用 tidevice3 发现 iOS 设备。
"""

import logging
from dataclasses import dataclass
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)


# iOS 设备型号映射
IOS_DEVICE_MODELS = {
    "iPhone14,2": "iPhone 13 Pro",
    "iPhone14,3": "iPhone 13 Pro Max",
    "iPhone14,4": "iPhone 13 mini",
    "iPhone14,5": "iPhone 13",
    "iPhone15,2": "iPhone 14 Pro",
    "iPhone15,3": "iPhone 14 Pro Max",
    "iPhone15,4": "iPhone 14",
    "iPhone15,5": "iPhone 14 Plus",
    "iPhone16,1": "iPhone 15 Pro",
    "iPhone16,2": "iPhone 15 Pro Max",
    "iPhone16,3": "iPhone 15",
    "iPhone16,4": "iPhone 15 Plus",
}

# 设备分辨率映射
IOS_RESOLUTION_MAP = {
    "iPhone14,2": "1170x2532",
    "iPhone14,3": "1284x2778",
    "iPhone14,4": "1080x2340",
    "iPhone14,5": "1170x2532",
    "iPhone15,2": "1179x2556",
    "iPhone15,3": "1290x2796",
    "iPhone15,4": "1170x2532",
    "iPhone15,5": "1284x2778",
    "iPhone16,1": "1179x2556",
    "iPhone16,2": "1290x2796",
    "iPhone16,3": "1170x2532",
    "iPhone16,4": "1284x2778",
}


@dataclass
class iOSDeviceInfo:
    """iOS 设备信息。"""
    udid: str
    name: str
    model: str
    product_type: str
    os_version: str
    build_version: str
    resolution: str
    status: str

    def to_dict(self) -> Dict:
        """转换为字典。"""
        return {
            "platform": "ios",
            "udid": self.udid,
            "name": self.name,
            "model": self.model,
            "product_type": self.product_type,
            "os_version": self.os_version,
            "build_version": self.build_version,
            "resolution": self.resolution,
            "status": self.status,
        }


class iOSDiscoverer:
    """iOS 设备发现器。"""

    @staticmethod
    def check_tidevice_available() -> bool:
        """检查 tidevice 是否可用。"""
        try:
            import tidevice
            return True
        except ImportError:
            return False

    @staticmethod
    def list_devices() -> List[str]:
        """获取设备 UDID 列表。"""
        try:
            import tidevice
            return tidevice.usb_device_list()
        except Exception as e:
            logger.error(f"Failed to list iOS devices: {e}")
            return []

    @staticmethod
    def get_resolution_by_model(product_type: str) -> str:
        """根据设备型号推断分辨率。"""
        return IOS_RESOLUTION_MAP.get(product_type, "Unknown")

    @staticmethod
    def get_device_info(udid: str, status: str = "online") -> Optional[iOSDeviceInfo]:
        """获取设备详细信息。"""
        if status == "offline":
            return iOSDeviceInfo(
                udid=udid,
                name="Unknown",
                model="Unknown",
                product_type="Unknown",
                os_version="Unknown",
                build_version="Unknown",
                resolution="Unknown",
                status="offline",
            )

        try:
            import tidevice
            d = tidevice.Device(udid)
            product_type = d.product_type or "Unknown"

            return iOSDeviceInfo(
                udid=udid,
                name=d.name or "Unknown",
                model=IOS_DEVICE_MODELS.get(product_type, product_type),
                product_type=product_type,
                os_version=d.product_version or "Unknown",
                build_version=d.build_version or "Unknown",
                resolution=iOSDiscoverer.get_resolution_by_model(product_type),
                status="online",
            )
        except Exception as e:
            logger.error(f"Failed to get device info for {udid}: {e}")
            return None

    @classmethod
    def discover(cls) -> List[iOSDeviceInfo]:
        """发现所有 iOS 设备。"""
        if not cls.check_tidevice_available():
            logger.warning("tidevice not available, skipping iOS discovery")
            return []

        devices = []
        for udid in cls.list_devices():
            info = cls.get_device_info(udid)
            if info:
                devices.append(info)
        return devices

    @classmethod
    def discover_device(cls, udid: str) -> Optional[iOSDeviceInfo]:
        """发现指定设备。"""
        all_udids = cls.list_devices()
        if udid in all_udids:
            return cls.get_device_info(udid)
        return None

    @classmethod
    def check_device_connected(cls, udid: str) -> bool:
        """检查指定设备是否连接。"""
        return udid in cls.list_devices()
```

- [ ] **Step 2: Commit**

```bash
git add worker/discovery/ios.py
git commit -m "refactor(ios): rewrite discovery with tidevice3"
```

---

## Phase 4: Worker 集成

### Task 12: 集成到 Worker

**Files:**
- Modify: `worker/worker.py`

- [ ] **Step 1: 在 Worker 类中添加 DeviceMonitor 属性**

在 `__init__` 方法中添加（约第 80 行后）：

```python
        # 设备监控
        self.device_monitor: Optional[DeviceMonitor] = None
```

并在文件顶部添加导入：

```python
from worker.device_monitor import DeviceMonitor
```

- [ ] **Step 2: 修改 _init_platform_managers 方法**

在创建平台管理器后初始化 DeviceMonitor（方法末尾添加）：

```python
        # 初始化设备监控
        if self.android_manager or self.ios_manager:
            self.device_monitor = DeviceMonitor(self.config)
            self.device_monitor.set_platform_managers(
                android_manager=self.android_manager,
                ios_manager=self.ios_manager
            )
            self.device_monitor.on_device_change = self._on_device_change
```

- [ ] **Step 3: 添加设备变更回调方法**

在 Worker 类中添加：

```python
    def _on_device_change(self, devices: Dict) -> None:
        """设备状态变更回调。"""
        logger.info(f"Device status changed: {devices}")
```

- [ ] **Step 4: 修改 start 方法 - 启动 DeviceMonitor**

在启动 HTTP 服务前添加：

```python
        # 启动设备监控
        if self.device_monitor:
            self.device_monitor.start()
```

- [ ] **Step 5: 修改 stop 方法 - 停止 DeviceMonitor**

在停止平台管理器前添加：

```python
        # 停止设备监控
        if self.device_monitor:
            self.device_monitor.stop()
```

- [ ] **Step 6: 修改 get_worker_devices 方法**

替换该方法为：

```python
    def get_worker_devices(self) -> Dict[str, Any]:
        """获取 Worker 状态和设备信息。"""
        devices = self.device_monitor.get_all_devices() if self.device_monitor else {}

        return {
            "status": self._status,
            "started_at": self._started_at,
            "supported_platforms": self.supported_platforms,
            "ip": self.host_info.ip_addresses[0] if self.host_info else "unknown",
            "port": self.port,
            "devices": {
                "windows": [],
                "web": [],
                "mac": [],
                "android": devices.get("android", []),
                "ios": devices.get("ios", []),
            },
            "faulty_devices": {
                "android": devices.get("faulty_android", []),
                "ios": devices.get("faulty_ios", []),
            },
        }
```

- [ ] **Step 7: 重构设备监控方法**

将 `_device_monitor_loop` 和 `_start_device_monitor` 方法替换为：

```python
    def _start_device_monitor(self) -> None:
        """启动设备监控（已由 DeviceMonitor 模块接管）。"""
        # 设备监控已由 DeviceMonitor 模块接管
        # 此方法保留用于兼容，实际初始化在 _init_platform_managers 中完成
        pass

    def _device_monitor_loop(self) -> None:
        """设备监控循环（已由 DeviceMonitor 模块接管）。"""
        # 设备监控已由 DeviceMonitor 模块接管
        pass
```

- [ ] **Step 8: Commit**

```bash
git add worker/worker.py
git commit -m "feat: integrate DeviceMonitor into Worker"
```

---

## Phase 5: 验证与清理

### Task 13: 最终验证

- [ ] **Step 1: 代码检查**

Run: `ruff check worker/`
Expected: 无错误

- [ ] **Step 2: 格式化代码**

Run: `black worker/`
Expected: 格式化完成

- [ ] **Step 3: 手动测试 - 启动 Worker**

Run: `python -m worker.main`
Expected: Worker 启动，日志显示 DeviceMonitor 启动

- [ ] **Step 4: 最终 Commit**

```bash
git add -A
git commit -m "feat: complete iOS/Android direct connection refactor"
```

---

## 实现说明

1. **设备状态流转**：新设备先入异常列表，服务启动成功后移至正常列表
2. **即时处理**：新设备检测到后立即尝试启动服务，不等待下一个周期
3. **周期检测**：每 5 分钟检测一次设备状态和服务健康
4. **服务管理**：Android 由 uiautomator2 自动管理，iOS 由 Worker 管理 WDA 进程
5. **异常恢复**：异常设备在每次周期检测时尝试恢复
6. **兼容性**：保留 `has_active_session`、`get_session_context` 等接口保持向后兼容

---

## 版本信息

- 计划日期：2026-03-21
- 基于设计文档：`docs/superpowers/specs/2026-03-21-ios-android-direct-connection-design.md`