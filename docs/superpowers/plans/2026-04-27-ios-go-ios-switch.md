# iOS 连接方案切换：tidevice3 → go-ios 实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将 iOS 平台的设备连接、WDA 启动、端口转发等功能从 tidevice3 完全切换到 go-ios。

**Architecture:** 新增 GoIOSClient 类封装 go-ios CLI 命令调用，重构 iOSPlatformManager 使用 go-ios agent + HTTP API 管理 tunnel 和设备服务，移除 tidevice3 依赖。

**Tech Stack:** Python 3.10+、go-ios CLI（tools/go-ios/ios.exe）、HTTP API（localhost:28100）、subprocess、httpx

---

## 文件结构

| 文件 | 变更类型 | 责任 |
|------|----------|------|
| `worker/platforms/go_ios_client.py` | 新增 | GoIOSClient 类：封装 go-ios CLI 命令 |
| `worker/platforms/ios.py` | 重写 | iOSPlatformManager：使用 GoIOSClient 管理设备 |
| `worker/config.py` | 修改 | PlatformConfig 新增 go_ios_path、agent_port、mjpeg_base_port |
| `config/worker.yaml` | 修改 | iOS 配置结构变更 |
| `worker/discovery/ios.py` | 修改 | iOS 设备发现模块：使用 go-ios 替代 tidevice3 |
| `pyproject.toml` | 修改 | 移除 tidevice3 依赖 |

---

### Task 1: 创建 GoIOSClient 类

**Files:**
- Create: `worker/platforms/go_ios_client.py`

- [ ] **Step 1: 创建 GoIOSClient 类框架**

```python
"""
go-ios CLI 客户端封装。

封装 go-ios 命令调用，提供设备发现、WDA 启动、端口转发等功能。
"""

import json
import logging
import os
import subprocess
import time
from typing import Any, Optional

import httpx

logger = logging.getLogger(__name__)


class GoIOSClient:
    """go-ios CLI 客户端。"""

    def __init__(
        self,
        go_ios_path: str,
        agent_port: int = 28100,
        timeout: int = 30,
    ):
        """
        初始化 GoIOSClient。

        Args:
            go_ios_path: go-ios 可执行文件路径（相对于 exe 目录或绝对路径）
            agent_port: go-ios agent HTTP API 端口
            timeout: 命令执行超时时间（秒）
        """
        self._go_ios_path = self._resolve_path(go_ios_path)
        self.agent_port = agent_port
        self.agent_host = "127.0.0.1"
        self.timeout = timeout
        self._http_client: Optional[httpx.Client] = None

    def _resolve_path(self, path: str) -> str:
        """解析 go-ios 路径（支持相对路径和绝对路径）。"""
        import sys
        if os.path.isabs(path):
            return path
        # 打包模式下相对于 exe 目录
        base_dir = os.path.dirname(sys.executable) if getattr(sys, 'frozen', False) else os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
        return os.path.join(base_dir, path)

    def _run_cmd(
        self,
        args: list[str],
        timeout: Optional[int] = None,
        check: bool = True,
    ) -> subprocess.CompletedProcess:
        """
        执行 go-ios 命令。

        Args:
            args: 命令参数（不含 ios.exe 本身）
            timeout: 超时时间
            check: 是否检查返回码

        Returns:
            CompletedProcess: 命令执行结果
        """
        cmd = [self._go_ios_path] + args
        logger.debug(f"Running go-ios command: {cmd}")
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout or self.timeout,
            check=check,
        )
        return result

    def _run_cmd_json(self, args: list[str], timeout: Optional[int] = None) -> Any:
        """执行 go-ios 命令并解析 JSON 输出。"""
        result = self._run_cmd(args, timeout=timeout, check=False)
        if result.returncode != 0:
            logger.warning(f"go-ios command failed: {result.stderr}")
            return None
        try:
            return json.loads(result.stdout)
        except json.JSONDecodeError as e:
            logger.error(f"Failed to parse go-ios JSON output: {e}")
            return None

    # ========== Agent 管理 ==========

    def start_agent(self) -> subprocess.Popen:
        """启动 go-ios agent（后台进程）。"""
        cmd = [self._go_ios_path, "tunnel", "start"]
        # Windows: 隐藏窗口，独立进程
        creationflags = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
        startupinfo = subprocess.STARTUPINFO()
        startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        startupinfo.wShowWindow = subprocess.SW_HIDE
        process = subprocess.Popen(
            cmd,
            stdin=None,
            stdout=None,
            stderr=subprocess.DEVNULL,
            creationflags=creationflags,
            startupinfo=startupinfo,
        )
        logger.info(f"go-ios agent started (PID: {process.pid})")
        return process

    def check_agent_health(self) -> bool:
        """检查 agent 健康状态。"""
        try:
            if not self._http_client:
                self._http_client = httpx.Client(timeout=5)
            resp = self._http_client.get(f"http://{self.agent_host}:{self.agent_port}/health")
            return resp.status_code == 200
        except Exception as e:
            logger.debug(f"Agent health check failed: {e}")
            return False

    def wait_agent_ready(self, timeout: int = 30) -> bool:
        """等待 agent 就绪。"""
        start = time.time()
        while time.time() - start < timeout:
            if self.check_agent_health():
                return True
            time.sleep(1)
        return False

    def get_tunnel_info(self, udid: str) -> Optional[dict]:
        """
        获取 iOS 17+ 设备的 tunnel 信息。

        Args:
            udid: 设备 UDID

        Returns:
            dict: tunnel 信息 {"address": "...", "rsdPort": ..., "udid": "..."} 或 None
        """
        try:
            if not self._http_client:
                self._http_client = httpx.Client(timeout=5)
            resp = self._http_client.get(f"http://{self.agent_host}:{self.agent_port}/tunnel/{udid}")
            if resp.status_code == 200:
                return resp.json()
            return None
        except Exception as e:
            logger.debug(f"Failed to get tunnel info for {udid}: {e}")
            return None

    def list_tunnels(self) -> list[dict]:
        """列出所有已建立的 tunnel。"""
        try:
            if not self._http_client:
                self._http_client = httpx.Client(timeout=5)
            resp = self._http_client.get(f"http://{self.agent_host}:{self.agent_port}/tunnels")
            if resp.status_code == 200:
                return resp.json()
            return []
        except Exception as e:
            logger.debug(f"Failed to list tunnels: {e}")
            return []

    def close(self) -> None:
        """关闭 HTTP 客户端。"""
        if self._http_client:
            self._http_client.close()
            self._http_client = None

    # ========== 设备发现 ==========

    def list_devices(self) -> list[dict]:
        """
        获取设备列表（含详细信息）。

        Returns:
            list[dict]: 设备列表，每个设备包含 udid, name, version, model 等
        """
        data = self._run_cmd_json(["list", "--details"])
        if not data:
            return []
        devices = data.get("deviceList", [])
        result = []
        for d in devices:
            # go-ios 的设备信息结构
            props = d.get("Properties", {})
            result.append({
                "udid": props.get("SerialNumber", ""),
                "name": "",  # 需要通过 info 命令获取
                "version": "",  # 需要通过 info 命令获取
                "model": props.get("ProductType", ""),
                "device_id": d.get("DeviceID", 0),
            })
        return result

    def get_device_info(self, udid: str) -> Optional[dict]:
        """
        获取设备详细信息。

        Args:
            udid: 设备 UDID

        Returns:
            dict: 设备信息
        """
        data = self._run_cmd_json(["--udid", udid, "info"])
        if not data:
            return None
        return {
            "udid": udid,
            "name": data.get("DeviceName", "Unknown"),
            "version": data.get("ProductVersion", "Unknown"),
            "model": data.get("ProductType", "Unknown"),
            "build_version": data.get("BuildVersion", "Unknown"),
        }

    def get_device_version(self, udid: str) -> str:
        """获取设备 iOS 版本。"""
        info = self.get_device_info(udid)
        return info.get("version", "") if info else ""

    # ========== WDA 启动 ==========

    def start_wda(
        self,
        udid: str,
        bundle_id: str,
        address: Optional[str] = None,
        rsd_port: Optional[int] = None,
    ) -> subprocess.Popen:
        """
        启动 WDA（后台进程）。

        Args:
            udid: 设备 UDID
            bundle_id: WDA bundle ID
            address: iOS 17+ tunnel 地址
            rsd_port: iOS 17+ tunnel RSD 端口

        Returns:
            subprocess.Popen: WDA 进程
        """
        args = ["--udid", udid, "runwda", "--bundleid", bundle_id]
        if address and rsd_port:
            args.extend(["--address", address, "--rsd-port", str(rsd_port)])
        cmd = [self._go_ios_path] + args
        # Windows: 隐藏窗口，独立进程
        creationflags = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
        startupinfo = subprocess.STARTUPINFO()
        startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        startupinfo.wShowWindow = subprocess.SW_HIDE
        process = subprocess.Popen(
            cmd,
            stdin=None,
            stdout=None,
            stderr=subprocess.DEVNULL,
            creationflags=creationflags,
            startupinfo=startupinfo,
        )
        logger.info(f"WDA started for {udid} (PID: {process.pid})")
        return process

    # ========== 端口转发 ==========

    def forward_port(
        self,
        udid: str,
        local_port: int,
        device_port: int,
        address: Optional[str] = None,
        rsd_port: Optional[int] = None,
    ) -> subprocess.Popen:
        """
        端口转发（后台进程）。

        Args:
            udid: 设备 UDID
            local_port: 本地端口
            device_port: 设备端口
            address: iOS 17+ tunnel 地址
            rsd_port: iOS 17+ tunnel RSD 端口

        Returns:
            subprocess.Popen: 端口转发进程
        """
        args = ["--udid", udid, "forward", str(local_port), str(device_port)]
        if address and rsd_port:
            args.extend(["--address", address, "--rsd-port", str(rsd_port)])
        cmd = [self._go_ios_path] + args
        # Windows: 隐藏窗口，独立进程
        creationflags = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
        startupinfo = subprocess.STARTUPINFO()
        startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        startupinfo.wShowWindow = subprocess.SW_HIDE
        process = subprocess.Popen(
            cmd,
            stdin=None,
            stdout=None,
            stderr=subprocess.DEVNULL,
            creationflags=creationflags,
            startupinfo=startupinfo,
        )
        logger.info(f"Port forward started: {local_port} -> {device_port} (PID: {process.pid})")
        return process

    # ========== 应用管理 ==========

    def launch_app(
        self,
        udid: str,
        bundle_id: str,
        address: Optional[str] = None,
        rsd_port: Optional[int] = None,
    ) -> bool:
        """启动应用。"""
        args = ["--udid", udid, "launch", bundle_id]
        if address and rsd_port:
            args.extend(["--address", address, "--rsd-port", str(rsd_port)])
        result = self._run_cmd(args, check=False)
        return result.returncode == 0

    def kill_app(
        self,
        udid: str,
        bundle_id: str,
        address: Optional[str] = None,
        rsd_port: Optional[int] = None,
    ) -> bool:
        """关闭应用。"""
        args = ["--udid", udid, "kill", bundle_id]
        if address and rsd_port:
            args.extend(["--address", address, "--rsd-port", str(rsd_port)])
        result = self._run_cmd(args, check=False)
        return result.returncode == 0

    def get_processes(
        self,
        udid: str,
        address: Optional[str] = None,
        rsd_port: Optional[int] = None,
    ) -> list[dict]:
        """获取运行的应用进程。"""
        args = ["--udid", udid, "ps", "--apps"]
        if address and rsd_port:
            args.extend(["--address", address, "--rsd-port", str(rsd_port)])
        data = self._run_cmd_json(args)
        if not data:
            return []
        return data
```

- [ ] **Step 2: 创建文件**

使用 Write 工具创建 `worker/platforms/go_ios_client.py`，内容为上述代码。

- [ ] **Step 3: Commit**

```bash
git add worker/platforms/go_ios_client.py
git commit -m "feat: 新增 GoIOSClient 类封装 go-ios CLI 命令"
```

---

### Task 2: 修改 PlatformConfig 配置

**Files:**
- Modify: `worker/config.py:158-212`

- [ ] **Step 1: 修改 PlatformConfig 数据类**

在 `PlatformConfig` 类中新增字段：

```python
# iOS 专用
wda_base_port: int = 8100
wda_ipa_path: str = "wda/WebDriverAgent.ipa"
wda_bundle_id: str = "com.facebook.WebDriverAgentRunner"
go_ios_path: str = "tools/go-ios/ios.exe"  # go-ios 可执行文件路径
agent_port: int = 28100                    # go-ios agent HTTP API 端口
mjpeg_base_port: int = 9100               # MJPEG 基础端口
```

修改 `from_dict` 方法添加新字段解析：

```python
wda_base_port=data.get("wda_base_port", 8100),
wda_ipa_path=data.get("wda_ipa_path", "wda/WebDriverAgent.ipa"),
wda_bundle_id=data.get("wda_bundle_id", "com.facebook.WebDriverAgentRunner"),
go_ios_path=data.get("go_ios_path", "tools/go-ios/ios.exe"),
agent_port=data.get("agent_port", 28100),
mjpeg_base_port=data.get("mjpeg_base_port", 9100),
```

- [ ] **Step 2: Commit**

```bash
git add worker/config.py
git commit -m "feat: PlatformConfig 新增 go_ios_path、agent_port、mjpeg_base_port 配置项"
```

---

### Task 3: 修改 worker.yaml 配置

**Files:**
- Modify: `config/worker.yaml:57-65`

- [ ] **Step 1: 修改 iOS 配置部分**

将现有 iOS 配置替换为：

```yaml
ios:
  enabled: null                   # Only on Windows
  go_ios_path: tools/go-ios/ios.exe  # go-ios 可执行文件路径
  agent_port: 28100               # go-ios agent HTTP API 端口
  wda_base_port: 8100             # WDA 基础端口
  mjpeg_base_port: 9100           # MJPEG 基础端口
  wda_bundle_id: com.facebook.WebDriverAgentRunner.majy.xctrunner
  session_timeout: 300
  screenshot_dir: data/screenshots
```

移除 `tunneld_port` 和 `tunneld_enabled` 配置项。

- [ ] **Step 2: Commit**

```bash
git add config/worker.yaml
git commit -m "config: iOS 配置切换到 go-ios，移除 tunneld 配置项"
```

---

### Task 4: 修改 iOS 设备发现模块

**Files:**
- Modify: `worker/discovery/ios.py`

- [ ] **Step 1: 修改 iOSDiscoverer 类**

移除 tidevice3 导入，使用 GoIOSClient：

```python
"""
iOS 设备发现模块。

使用 go-ios 发现 iOS 设备。
"""

import logging
from dataclasses import dataclass
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)


# iOS 设备型号映射（保持不变）
IOS_DEVICE_MODELS = {
    ...
}

# 设备分辨率映射（保持不变）
IOS_RESOLUTION_MAP = {
    ...
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

    _go_ios_client: Optional["GoIOSClient"] = None

    @classmethod
    def set_go_ios_client(cls, client: "GoIOSClient") -> None:
        """设置 GoIOSClient 实例。"""
        cls._go_ios_client = client

    @staticmethod
    def check_go_ios_available() -> bool:
        """检查 go-ios 是否可用。"""
        try:
            from worker.platforms.go_ios_client import GoIOSClient
            return True
        except ImportError:
            return False

    @staticmethod
    def list_devices() -> List[str]:
        """获取设备 UDID 列表。"""
        if not iOSDiscoverer._go_ios_client:
            logger.warning("GoIOSClient not initialized")
            return []
        try:
            devices = iOSDiscoverer._go_ios_client.list_devices()
            return [d["udid"] for d in devices]
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

        if not iOSDiscoverer._go_ios_client:
            return None

        try:
            info = iOSDiscoverer._go_ios_client.get_device_info(udid)
            if not info:
                return None
            product_type = info.get("model", "Unknown")
            return iOSDeviceInfo(
                udid=udid,
                name=info.get("name", "Unknown"),
                model=IOS_DEVICE_MODELS.get(product_type, product_type),
                product_type=product_type,
                os_version=info.get("version", "Unknown"),
                build_version=info.get("build_version", "Unknown"),
                resolution=iOSDiscoverer.get_resolution_by_model(product_type),
                status="online",
            )
        except Exception as e:
            logger.error(f"Failed to get device info for {udid}: {e}")
            return None

    @classmethod
    def discover(cls) -> List[iOSDeviceInfo]:
        """发现所有 iOS 设备。"""
        if not cls._go_ios_client:
            logger.warning("GoIOSClient not initialized, skipping iOS discovery")
            return []

        try:
            devices = cls._go_ios_client.list_devices()
            result = []
            for d in devices:
                udid = d["udid"]
                info = cls.get_device_info(udid)
                if info:
                    result.append(info)
            return result
        except Exception as e:
            logger.error(f"Failed to discover iOS devices: {e}")
            return []

    @classmethod
    def discover_device(cls, udid: str) -> Optional[iOSDeviceInfo]:
        """发现指定设备。"""
        return cls.get_device_info(udid)

    @classmethod
    def check_device_connected(cls, udid: str) -> bool:
        """检查指定设备是否连接。"""
        return udid in cls.list_devices()
```

- [ ] **Step 2: Commit**

```bash
git add worker/discovery/ios.py
git commit -m "refactor: iOS 设备发现模块切换到 go-ios"
```

---

### Task 5: 重写 iOSPlatformManager

**Files:**
- Rewrite: `worker/platforms/ios.py`

- [ ] **Step 1: 重写 iOSPlatformManager**

完整重写文件，使用 GoIOSClient 管理 iOS 设备：

```python
"""
iOS 平台执行引擎。

基于 go-ios + WDA 直连实现，支持 OCR/图像识别定位。
"""

import logging
import time
import subprocess
from typing import Any, Optional

from common.utils import run_cmd
from worker.actions import ActionRegistry
from worker.config import PlatformConfig
from worker.platforms.base import PlatformManager
from worker.platforms.go_ios_client import GoIOSClient
from worker.platforms.wda_client import WDAClient
from worker.task import Action, ActionResult, ActionStatus

logger = logging.getLogger(__name__)


class iOSPlatformManager(PlatformManager):
    """
    iOS 平台管理器。

    使用 go-ios + WDA 直连控制 iOS 设备。
    """

    SUPPORTED_ACTIONS: set[str] = {"start_app", "stop_app", "unlock_screen", "pinch"}

    # iOS 按键映射（保持不变）
    KEY_MAP = {
        "HOME": "home",
        "VOLUME_UP": "volumeup",
        "VOLUMEUP": "volumeup",
        "VOLUME_DOWN": "volumedown",
        "VOLUMEDOWN": "volumedown",
    }

    # WDA 不支持的按键（保持不变）
    UNSUPPORTED_KEYS = {
        "BACK": "iOS 无物理返回键，请使用 OCR 点击导航栏返回按钮",
        "ENTER": "iOS 无物理回车键，请使用 OCR 点击键盘上的完成/搜索按钮",
        "LOCK": "iPhone 8 不支持 LOCK 按键，Face ID 机型可能支持",
        "POWER": "iPhone 8 不支持 POWER 按键，Face ID 机型可能支持",
        "ESCAPE": "iOS 无 ESC 键",
        "TAB": "iOS 无 Tab 键",
        "ARROWUP": "iOS 无方向键",
        "ARROWDOWN": "iOS 无方向键",
        "ARROWLEFT": "iOS 无方向键",
        "ARROWRIGHT": "iOS 无方向键",
    }

    def __init__(self, config: PlatformConfig, ocr_client=None, unlock_config=None):
        super().__init__(config, ocr_client)
        # go-ios 配置
        self.go_ios_path = config.go_ios_path or "tools/go-ios/ios.exe"
        self.agent_port = config.agent_port or 28100
        self.wda_base_port = config.wda_base_port or 8100
        self.mjpeg_base_port = config.mjpeg_base_port or 9100
        self.wda_bundle_id = config.wda_bundle_id or "com.facebook.WebDriverAgentRunner"

        # GoIOSClient 实例
        self._go_ios: Optional[GoIOSClient] = None

        # 设备状态管理
        self._device_wda: dict[str, dict] = {}  # udid -> {port, mjpeg_port, process, forward_process}
        self._device_clients: dict[str, WDAClient] = {}  # udid -> WDAClient
        self._device_tunnel_info: dict[str, dict] = {}  # udid -> tunnel info
        self._current_device: str | None = None
        self._unlock_config = unlock_config or {}

        # Agent 进程引用（用于异常时重启）
        self._agent_process: subprocess.Popen | None = None

    @property
    def platform(self) -> str:
        return "ios"

    # ========== 生命周期管理 ==========

    def start(self) -> None:
        """启动 iOS 平台（检查并启动 go-ios agent）。"""
        if self._started:
            return

        # 1. 创建 GoIOSClient
        self._go_ios = GoIOSClient(
            go_ios_path=self.go_ios_path,
            agent_port=self.agent_port,
        )

        # 2. 确保 agent 运行
        self._ensure_agent_running()

        # 3. 设置设备发现模块的 GoIOSClient
        from worker.discovery.ios import iOSDiscoverer
        iOSDiscoverer.set_go_ios_client(self._go_ios)

        self._started = True
        logger.info("iOS platform started (go-ios + WDA mode)")

    def stop(self) -> None:
        """停止 iOS 平台（不关闭进程，保持复用）。"""
        # 只清理内存引用，不关闭 agent、runwda、forward 进程
        self._device_clients.clear()
        self._device_wda.clear()
        self._device_tunnel_info.clear()

        if self._go_ios:
            self._go_ios.close()
            self._go_ios = None

        # 不关闭 agent 进程，保持运行以便下次复用
        # self._agent_process = None

        self._started = False
        logger.info("iOS platform stopped (processes preserved for reuse)")

    def is_available(self) -> bool:
        """检查平台是否可用。"""
        return self._started and self._go_ios is not None

    # ========== Agent 管理 ==========

    def _ensure_agent_running(self) -> None:
        """确保 go-ios agent 正在运行，异常则重启。"""
        if self._go_ios.check_agent_health():
            logger.info("go-ios agent already running, reusing")
            return

        # Agent 未运行或健康检查失败，尝试启动
        logger.info("go-ios agent not running, starting...")
        self._agent_process = self._go_ios.start_agent()

        # 等待就绪
        if not self._go_ios.wait_agent_ready(timeout=30):
            logger.error("go-ios agent failed to start within 30s")
            # 尝试杀掉可能残留的进程
            self._kill_agent_processes()
            raise RuntimeError("go-ios agent failed to start")

        logger.info("go-ios agent started successfully")

    def _kill_agent_processes(self) -> None:
        """杀掉 go-ios agent 相关进程。"""
        try:
            # 通过端口查找并杀掉进程
            result = run_cmd(["netstat", "-ano"], check=True, timeout=10)
            for line in result.stdout.splitlines():
                if f":{self.agent_port}" in line and "LISTENING" in line:
                    parts = line.split()
                    if len(parts) >= 5:
                        pid = parts[-1]
                        logger.info(f"Killing agent process {pid}")
                        run_cmd(["taskkill", "/F", "/PID", pid], check=True, timeout=10)
        except Exception as e:
            logger.warning(f"Failed to kill agent processes: {e}")

    # ========== 设备服务管理 ==========

    def _is_ios17_plus(self, version: str) -> bool:
        """检测 iOS 版本是否 >= 17。"""
        try:
            major = int(version.split('.')[0])
            return major >= 17
        except (ValueError, IndexError):
            return False

    def _get_device_version(self, udid: str) -> str:
        """获取设备 iOS 版本。"""
        return self._go_ios.get_device_version(udid)

    def _get_device_index(self, udid: str) -> int:
        """根据设备列表位置计算索引（用于端口分配）。"""
        devices = self._go_ios.list_devices()
        for i, d in enumerate(devices):
            if d["udid"] == udid:
                return i
        return 0

    def _allocate_ports(self, udid: str) -> tuple[int, int]:
        """分配 WDA 和 MJPEG 端口。"""
        index = self._get_device_index(udid)
        wda_port = self.wda_base_port + index
        mjpeg_port = self.mjpeg_base_port + index
        return wda_port, mjpeg_port

    def _get_tunnel_info(self, udid: str, timeout: int = 30) -> Optional[dict]:
        """获取 iOS 17+ 设备的 tunnel 信息（等待建立）。"""
        # 先检查是否已缓存
        if udid in self._device_tunnel_info:
            return self._device_tunnel_info[udid]

        # 等待 agent 建立 tunnel
        start = time.time()
        while time.time() - start < timeout:
            info = self._go_ios.get_tunnel_info(udid)
            if info and info.get("address"):
                self._device_tunnel_info[udid] = info
                return info
            time.sleep(1)

        logger.warning(f"Tunnel not established for {udid} within {timeout}s")
        return None

    def ensure_device_service(self, udid: str) -> tuple[str, str]:
        """确保 WDA 服务可用。"""
        try:
            # 0. 获取设备版本
            device_version = self._get_device_version(udid)

            # 1. iOS 17+ 设备获取 tunnel 信息
            if device_version and self._is_ios17_plus(device_version):
                tunnel_info = self._get_tunnel_info(udid)
                if not tunnel_info:
                    return ("faulty", f"iOS 17+ device {udid} tunnel not established")

            # 2. 检查已有的 client 是否可用
            client = self._device_clients.get(udid)
            if client and client.health_check():
                logger.info(f"WDA already running: {udid}")
                return ("online", "OK")

            # 3. 分配端口
            wda_port, mjpeg_port = self._allocate_ports(udid)

            # 4. 检查端口是否被占用
            port_occupied = self._check_port_occupied(wda_port)

            if port_occupied:
                # 端口被占用，探测是否有可用的 WDA
                probe_client = WDAClient(f"http://127.0.0.1:{wda_port}")
                retry_count = 5
                for i in range(retry_count):
                    if probe_client.health_check():
                        logger.info(f"Found existing WDA on port {wda_port}, reusing")
                        self._device_clients[udid] = probe_client
                        # 补充 MJPEG 端口转发（如未启动）
                        mjpeg_process = self._start_mjpeg_forward(udid, mjpeg_port)
                        self._device_wda[udid] = {
                            "port": wda_port,
                            "mjpeg_port": mjpeg_port,
                            "process": None,  # WDA 进程不是我们启动的
                            "forward_process": mjpeg_process,
                        }
                        return ("online", "OK")
                    time.sleep(1)

                # 重试后仍失败，杀掉占用端口的进程
                logger.warning(f"Port {wda_port} occupied but WDA not responding, killing process")
                self._kill_port_process(wda_port)

            # 5. 启动新的 WDA
            return self._start_wda(udid, wda_port, mjpeg_port)

        except Exception as e:
            logger.error(f"Failed to ensure WDA service: {udid}, {e}")
            return ("faulty", str(e))

    def _start_wda(self, udid: str, wda_port: int, mjpeg_port: int) -> tuple[str, str]:
        """启动 WDA 服务（含端口转发）。"""
        try:
            # 清理已有进程
            if udid in self._device_wda:
                self._stop_wda(udid)

            # 获取 tunnel 信息（iOS 17+）
            tunnel_info = self._device_tunnel_info.get(udid)
            address = tunnel_info.get("address") if tunnel_info else None
            rsd_port = tunnel_info.get("rsdPort") if tunnel_info else None

            # 启动 WDA
            wda_process = self._go_ios.start_wda(
                udid=udid,
                bundle_id=self.wda_bundle_id,
                address=address,
                rsd_port=rsd_port,
            )

            # 启动 WDA 端口转发（设备 8100 -> 本地 wda_port）
            forward_process = self._go_ios.forward_port(
                udid=udid,
                local_port=wda_port,
                device_port=8100,
                address=address,
                rsd_port=rsd_port,
            )

            # 启动 MJPEG 端口转发（设备 9100 -> 本地 mjpeg_port）
            mjpeg_process = self._start_mjpeg_forward(udid, mjpeg_port)

            base_url = f"http://127.0.0.1:{wda_port}"
            client = WDAClient(base_url)

            logger.info(f"WDA process started on port {wda_port}, waiting for ready...")
            if client.wait_ready(timeout=30):
                self._device_wda[udid] = {
                    "port": wda_port,
                    "mjpeg_port": mjpeg_port,
                    "process": wda_process,
                    "forward_process": forward_process,
                    "mjpeg_process": mjpeg_process,
                }
                self._device_clients[udid] = client
                logger.info(f"WDA started: {udid} on port {wda_port}, MJPEG on port {mjpeg_port}")
                return ("online", "OK")
            else:
                logger.warning(f"WDA failed to become ready on port {wda_port}")
                # 不主动杀进程，让其保持运行以便下次复用或手动清理
                return ("faulty", "WDA failed to start")

        except Exception as e:
            logger.error(f"Failed to start WDA: {e}")
            return ("faulty", str(e))

    def _start_mjpeg_forward(self, udid: str, mjpeg_port: int) -> subprocess.Popen:
        """启动 MJPEG 端口转发。"""
        tunnel_info = self._device_tunnel_info.get(udid)
        address = tunnel_info.get("address") if tunnel_info else None
        rsd_port = tunnel_info.get("rsdPort") if tunnel_info else None

        return self._go_ios.forward_port(
            udid=udid,
            local_port=mjpeg_port,
            device_port=9100,
            address=address,
            rsd_port=rsd_port,
        )

    def _stop_wda(self, udid: str) -> None:
        """停止 WDA 相关进程（不主动停止，保留引用清理）。"""
        if udid in self._device_wda:
            # 不主动停止进程，只清理引用
            # 进程使用 DETACHED_PROCESS 独立运行，会在设备断开时自动退出
            del self._device_wda[udid]

    def _check_port_occupied(self, port: int) -> bool:
        """检查端口是否被占用。"""
        try:
            result = run_cmd(["netstat", "-ano"], check=True, timeout=10)
            for line in result.stdout.splitlines():
                if f":{port}" in line and "LISTENING" in line:
                    return True
            return False
        except Exception as e:
            logger.warning(f"Failed to check port: {e}")
            return False

    def _kill_port_process(self, port: int) -> None:
        """杀掉占用指定端口的进程（Windows）。"""
        try:
            result = run_cmd(["netstat", "-ano"], check=True, timeout=10)
            for line in result.stdout.splitlines():
                if f":{port}" in line and "LISTENING" in line:
                    parts = line.split()
                    if len(parts) >= 5:
                        pid = parts[-1]
                        logger.info(f"Killing process {pid} occupying port {port}")
                        run_cmd(["taskkill", "/F", "/PID", pid], check=True, timeout=10)
                        time.sleep(1)
                        return
            logger.info(f"No process found occupying port {port}")
        except Exception as e:
            logger.warning(f"Failed to kill port process: {e}")

    def mark_device_faulty(self, udid: str) -> None:
        """标记设备为异常。"""
        if udid in self._device_clients:
            del self._device_clients[udid]
        self._stop_wda(udid)
        logger.info(f"iOS device marked faulty: {udid}")

    def get_online_devices(self) -> list[str]:
        """获取在线设备列表。"""
        return list(self._device_clients.keys())

    # ========== 上下文管理（保持不变） ==========

    def create_context(self, device_id: str | None = None, options: dict | None = None) -> WDAClient:
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

    # ========== 会话管理（保持不变） ==========

    def has_active_session(self, device_id: str | None = None) -> bool:
        """检查是否有活跃的会话。"""
        if device_id:
            return device_id in self._device_clients
        return len(self._device_clients) > 0

    def get_session_context(self, device_id: str | None = None) -> Any:
        """获取当前会话的上下文。"""
        if device_id:
            return self._device_clients.get(device_id)
        if self._current_device:
            return self._device_clients.get(self._current_device)
        return None

    def close_session(self, device_id: str | None = None) -> None:
        """关闭会话（不关闭 WDA 进程，保持复用）。"""
        if device_id:
            if device_id in self._device_wda:
                del self._device_wda[device_id]
            if device_id in self._device_clients:
                del self._device_clients[device_id]
            logger.info(f"iOS session closed (device={device_id}, WDA preserved)")
        else:
            self._device_wda.clear()
            self._device_clients.clear()
            logger.info("All iOS sessions closed (WDA preserved)")

    # ========== 基础能力实现（保持不变） ==========

    def _convert_coords(self, x: int, y: int) -> tuple[int, int]:
        """转换物理像素坐标到 WDA 逻辑坐标。"""
        if x > 400 or y > 700:
            return (x // 2, y // 2)
        return (x, y)

    def click(self, x: int, y: int, duration: int = 0, context: Any = None) -> None:
        """点击指定坐标，支持长按。"""
        client = context or self._device_clients.get(self._current_device)
        if client:
            wx, wy = self._convert_coords(x, y)
            if duration > 0:
                duration_sec = duration / 1000.0
                logger.debug(f"Long click at ({wx}, {wy}) for {duration}ms")
                success = client.touch_and_hold(wx, wy, duration=duration_sec)
            else:
                logger.debug(f"Click at ({wx}, {wy})")
                success = client.tap(wx, wy)
            if not success:
                raise RuntimeError(f"Click failed at ({wx}, {wy})")

    def double_click(self, x: int, y: int, context: Any = None) -> None:
        """双击指定坐标。"""
        client = context or self._device_clients.get(self._current_device)
        if client:
            wx, wy = self._convert_coords(x, y)
            success = client.tap(wx, wy)
            if not success:
                raise RuntimeError(f"First tap failed at ({wx}, {wy})")
            time.sleep(0.1)
            success = client.tap(wx, wy)
            if not success:
                raise RuntimeError(f"Second tap failed at ({wx}, {wy})")

    def move(self, x: int, y: int, context: Any = None) -> None:
        """移动鼠标（移动端不支持）。"""
        raise NotImplementedError("move action is not supported on mobile platforms")

    def input_text(self, text: str, context: Any = None) -> None:
        """输入文本。"""
        client = context or self._device_clients.get(self._current_device)
        if client:
            success = client.send_keys(text)
            if not success:
                raise RuntimeError(f"Send keys failed: {text}")

    def swipe(self, start_x: int, start_y: int, end_x: int, end_y: int,
              duration: int = 500, steps: Optional[int] = None, context: Any = None) -> None:
        """滑动。"""
        client = context or self._device_clients.get(self._current_device)
        if client:
            wx1, wy1 = self._convert_coords(start_x, start_y)
            wx2, wy2 = self._convert_coords(end_x, end_y)
            duration_sec = duration / 1000.0
            logger.debug(f"Swipe from ({wx1}, {wy1}) to ({wx2}, {wy2}) with duration={duration}ms")
            success = client.swipe(wx1, wy1, wx2, wy2, duration=duration_sec)
            if not success:
                raise RuntimeError(f"Swipe failed from ({wx1}, {wy1}) to ({wx2}, {wy2})")

    # ========== 手势操作（保持不变） ==========

    def pinch(self, direction: str, scale: float = 0.5,
              duration: int = 500, context: Any = None) -> None:
        """双指缩放手势。"""
        client = context or self._device_clients.get(self._current_device)
        if not client:
            raise RuntimeError("No device context")

        duration_sec = duration / 1000.0

        if direction == "in":
            client.pinch(scale=scale, duration=duration_sec)
        else:
            client.pinch(scale=1.0 / scale, duration=duration_sec)

        logger.debug(f"pinch {direction} executed: scale={scale}, duration={duration}ms")

    def press(self, key: str, context: Any = None) -> None:
        """按键。"""
        client = context or self._device_clients.get(self._current_device)
        if client:
            key_upper = key.upper()

            if key_upper in self.UNSUPPORTED_KEYS:
                raise ValueError(f"Unsupported key '{key}' for iOS. {self.UNSUPPORTED_KEYS[key_upper]}")

            wda_key = self.KEY_MAP.get(key_upper)
            if wda_key:
                success = client.press_button(wda_key)
                if not success:
                    raise RuntimeError(f"Press button failed: {key}")
            else:
                supported = ", ".join(sorted(self.KEY_MAP.keys()))
                raise ValueError(f"Unsupported key '{key}' for iOS. Supported keys: {supported}")

    def take_screenshot(self, context: Any = None) -> bytes:
        """获取截图。"""
        client = context or self._device_clients.get(self._current_device)
        if client:
            data = client.screenshot()
            if not data:
                raise RuntimeError("Screenshot failed")
            return data
        return b""

    def get_screenshot(self, context: Any) -> bytes:
        """获取当前屏幕截图（兼容旧接口）。"""
        return self.take_screenshot(context)

    # ========== 动作执行（保持不变） ==========

    def execute_action(self, context: Any, action: Action) -> ActionResult:
        """执行动作。"""
        start_time = time.time()

        client = context
        if not client and action.action_type not in ("start_app", "stop_app"):
            return ActionResult(
                number=0,
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
            elif action.action_type == "unlock_screen":
                executor = ActionRegistry.get(action.action_type)
                if executor:
                    result = executor.execute(self, action, client)
                else:
                    result = ActionResult(
                        number=0,
                        action_type=action.action_type,
                        status=ActionStatus.FAILED,
                        error=f"Unknown action type: {action.action_type}",
                    )
            elif action.action_type == "ocr_paste":
                result = ActionResult(
                    number=0,
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
                        number=0,
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
                number=0,
                action_type=action.action_type,
                status=ActionStatus.FAILED,
                duration_ms=duration_ms,
                error=str(e),
            )

    # ========== 平台特有动作实现 ==========

    def _action_start_app(self, client, action: Action) -> ActionResult:
        """启动应用（含锁屏检测）。"""
        bundle_id = action.bundle_id or action.value
        if not bundle_id:
            return ActionResult(
                number=0,
                action_type="start_app",
                status=ActionStatus.FAILED,
                error="bundle_id is required",
            )

        if not client or not self._current_device:
            return ActionResult(
                number=0,
                action_type="start_app",
                status=ActionStatus.FAILED,
                error="No device context",
            )

        # 检测锁屏状态
        if hasattr(client, "is_locked"):
            try:
                is_locked = client.is_locked()
                if is_locked:
                    logger.info("Screen is locked, performing auto unlock before start_app")
                    unlock_result = self._auto_unlock(client)
                    if unlock_result.status != ActionStatus.SUCCESS:
                        return ActionResult(
                            number=0,
                            action_type="start_app",
                            status=ActionStatus.FAILED,
                            error=f"Auto unlock failed: {unlock_result.error}",
                        )
            except Exception as e:
                logger.warning(f"Failed to check lock status: {e}")

        try:
            # 使用 go-ios launch 命令
            tunnel_info = self._device_tunnel_info.get(self._current_device)
            address = tunnel_info.get("address") if tunnel_info else None
            rsd_port = tunnel_info.get("rsdPort") if tunnel_info else None

            success = self._go_ios.launch_app(
                udid=self._current_device,
                bundle_id=bundle_id,
                address=address,
                rsd_port=rsd_port,
            )

            if success:
                return ActionResult(
                    number=0,
                    action_type="start_app",
                    status=ActionStatus.SUCCESS,
                    output=f"Started: {bundle_id}",
                )
            else:
                return ActionResult(
                    number=0,
                    action_type="start_app",
                    status=ActionStatus.FAILED,
                    error=f"Failed to launch: {bundle_id}",
                )
        except Exception as e:
            logger.error(f"Failed to launch app: {e}")
            return ActionResult(
                number=0,
                action_type="start_app",
                status=ActionStatus.FAILED,
                error=str(e),
            )

    def _auto_unlock(self, client) -> ActionResult:
        """自动解锁屏幕（使用配置密码）。"""
        from worker.actions import ActionRegistry
        from worker.task import Action

        password = self._unlock_config.get("password", "123456")

        unlock_action = Action(
            action_type="unlock_screen",
            value=password,
        )

        executor = ActionRegistry.get("unlock_screen")
        if executor:
            return executor.execute(self, unlock_action, client)
        else:
            return ActionResult(
                number=0,
                action_type="unlock_screen",
                status=ActionStatus.FAILED,
                error="unlock_screen executor not found",
            )

    def _action_stop_app(self, client, action: Action) -> ActionResult:
        """关闭应用。"""
        bundle_id = action.bundle_id or action.value

        if not client or not self._current_device:
            return ActionResult(
                number=0,
                action_type="stop_app",
                status=ActionStatus.FAILED,
                error="No device context",
            )

        try:
            if bundle_id:
                # 使用 go-ios kill 命令
                tunnel_info = self._device_tunnel_info.get(self._current_device)
                address = tunnel_info.get("address") if tunnel_info else None
                rsd_port = tunnel_info.get("rsdPort") if tunnel_info else None

                success = self._go_ios.kill_app(
                    udid=self._current_device,
                    bundle_id=bundle_id,
                    address=address,
                    rsd_port=rsd_port,
                )

                if success:
                    return ActionResult(
                        number=0,
                        action_type="stop_app",
                        status=ActionStatus.SUCCESS,
                        output=f"Stopped: {bundle_id}",
                    )
                else:
                    return ActionResult(
                        number=0,
                        action_type="stop_app",
                        status=ActionStatus.FAILED,
                        error=f"Failed to kill: {bundle_id}",
                    )
            else:
                # 未指定 bundle_id，按 HOME 键回到主屏幕
                if hasattr(client, "press_button"):
                    client.press_button("home")
                return ActionResult(
                    number=0,
                    action_type="stop_app",
                    status=ActionStatus.SUCCESS,
                    output="Pressed HOME key",
                )
        except Exception as e:
            logger.error(f"Failed to stop app: {e}")
            return ActionResult(
                number=0,
                action_type="stop_app",
                status=ActionStatus.FAILED,
                error=str(e),
            )
```

- [ ] **Step 2: Commit**

```bash
git add worker/platforms/ios.py
git commit -m "refactor: iOSPlatformManager 重写，使用 go-ios 替代 tidevice3"
```

---

### Task 6: 移除 tidevice3 依赖

**Files:**
- Modify: `pyproject.toml:17`

- [ ] **Step 1: 移除 tidevice3 依赖**

删除 `pyproject.toml` 中的 `tidevice3` 依赖行：

```toml
# 修改前
"tidevice3",

# 修改后（删除该行）
```

保留 `wda>=0.3.0`（这是 WDA Python 客户端，用于调试，不是必须的）。

- [ ] **Step 2: Commit**

```bash
git add pyproject.toml
git commit -m "chore: 移除 tidevice3 依赖"
```

---

### Task 7: 验证和测试

- [ ] **Step 1: 安装依赖**

```bash
pip install -e .
```

- [ ] **Step 2: 启动 Worker 测试**

```bash
python -m worker.main
```

验证：
1. go-ios agent 是否成功启动
2. iOS 设备是否能被发现
3. WDA 是否能成功启动
4. 动作执行是否正常

- [ ] **Step 3: 多设备测试**

连接多台 iOS 设备，验证：
1. 端口分配是否正确（设备1: 8100/9100, 设备2: 8101/9101）
2. 并发任务是否正常执行

- [ ] **Step 4: 重启复用测试**

重启 Worker，验证：
1. agent 进程是否被复用（不重复启动）
2. WDA 进程是否被复用

---

### Task 8: 最终 Commit

- [ ] **Step 1: 检查所有变更**

```bash
git status
git diff
```

- [ ] **Step 2: 确认所有文件已提交**

- [ ] **Step 3: 最终提交消息**

```bash
git log --oneline -5
```

预期输出：
```
chore: 移除 tidevice3 依赖
refactor: iOSPlatformManager 重写，使用 go-ios 替代 tidevice3
refactor: iOS 设备发现模块切换到 go-ios
config: iOS 配置切换到 go-ios，移除 tunneld 配置项
feat: PlatformConfig 新增 go_ios_path、agent_port、mjpeg_base_port 配置项
feat: 新增 GoIOSClient 类封装 go-ios CLI 命令
```