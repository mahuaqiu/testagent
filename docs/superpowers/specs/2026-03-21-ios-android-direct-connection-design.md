# iOS/Android 底层连接方式重构设计

## 概述

将 iOS 和 Android 平台的底层连接方式从 Appium Server 改为直连模式：
- **iOS**：使用 tidevice3 + WDA (WebDriverAgent) 直连
- **Android**：使用 uiautomator2 直连

## 目标

1. 移除对外部 Appium Server 的依赖
2. Worker 自动启动和维护设备服务（WDA/uiautomator2）
3. 支持多设备并发执行
4. 异常设备自动检测和恢复

## 架构变更

### 当前架构

```
Worker -> Appium Server -> 设备
         (外部服务)
```

### 新架构

```
Worker -> uiautomator2 (Android) -> 设备
       -> tidevice3 + WDA (iOS) -> 设备
       (无外部服务依赖)
```

## 模块变更清单

| 模块 | 文件 | 变更类型 | 说明 |
|------|------|----------|------|
| 设备监控 | `worker/device_monitor.py` | 新增 | 独立的设备监控模块 |
| Android 平台 | `worker/platforms/android.py` | 重写 | 使用 uiautomator2 |
| iOS 平台 | `worker/platforms/ios.py` | 重写 | 使用 tidevice3 + WDA |
| Android 发现 | `worker/discovery/android.py` | 小改 | 增加 uiautomator2 状态检测 |
| iOS 发现 | `worker/discovery/ios.py` | 重写 | 使用 tidevice3 发现设备 |
| Worker | `worker/worker.py` | 修改 | 集成 DeviceMonitor |
| 配置 | `worker/config.py` | 修改 | 新增配置项 |
| 配置文件 | `config/worker.yaml` | 修改 | 更新配置结构 |
| 依赖 | `pyproject.toml` | 修改 | 更新依赖 |

---

## 详细设计

### 1. DeviceMonitor 模块

独立的设备监控模块，负责：
- 定时检测物理设备连接
- 维护设备服务状态（WDA/uiautomator2）
- 管理正常/异常设备列表
- 自动恢复异常设备

#### 设备状态流转

```
设备连接 → 检测到设备 → 加入异常列表 → 尝试启动服务
                                    ↓
                          ┌─────────┴─────────┐
                          ↓                   ↓
                    服务启动成功          服务启动失败
                          ↓                   ↓
                    移至正常列表         保留在异常列表
                          ↓                   ↓
                    可接受任务          不可接受任务
                                              ↓
                                        定时检测恢复
                                              ↓
                                        恢复成功 → 移至正常列表
```

#### 核心接口

```python
class DeviceMonitor:
    def start() -> None:
        """启动监控"""

    def stop() -> None:
        """停止监控"""

    def get_all_devices() -> Dict[str, Any]:
        """获取所有设备状态"""

    def get_online_devices(platform: str) -> List[str]:
        """获取在线设备列表"""

    def is_device_online(platform: str, udid: str) -> bool:
        """检查设备是否在线"""
```

#### 定时任务逻辑

```python
def _monitor_loop(self) -> None:
    # 首次立即执行
    self._check_and_maintain()

    while not self._stop_event.is_set():
        self._stop_event.wait(self.check_interval)  # 5分钟

        if self._stop_event.is_set():
            break

        self._check_and_maintain()

def _check_and_maintain(self) -> None:
    # 1. 检测物理设备
    self._detect_physical_devices()

    # 2. 维护服务状态
    self._maintain_services()

    # 3. 回调通知
    if self.on_device_change:
        self.on_device_change(self.get_all_devices())
```

#### 新设备即时处理

当检测到新设备连接时，立即尝试启动服务（不等待下一个周期）：

```python
def _add_device(self, platform: str, device_info: Any) -> None:
    # 加入异常列表
    if platform == "android":
        self.faulty_android_devices.append(device_info)
    else:
        self.faulty_ios_devices.append(device_info)

    # 立即尝试启动服务
    self._try_start_service(platform, device_info.udid)
```

---

### 2. Android 平台改造

#### uiautomator2 架构

```
Worker
  │
  ├── uiautomator2 库
  │     ├── ADB 连接设备
  │     ├── 自动安装 ATX-Agent (minicap + minitouch)
  │     └── 启动 HTTP 服务 (端口 7912)
  │
  └── HTTP 调用设备服务
        ├── /screenshot
        ├── /tap
        ├── /swipe
        └── ...
```

#### AndroidPlatformManager 核心改动

```python
import uiautomator2 as u2

class AndroidPlatformManager(PlatformManager):
    DEFAULT_PORT = 7912

    def __init__(self, config: PlatformConfig, ocr_client=None):
        self._device_clients: Dict[str, u2.Device] = {}

    def start(self) -> None:
        """仅环境检查：ADB 可用、uiautomator2 已安装"""

    def create_context(self, device_id: str, options: Optional[Dict] = None) -> u2.Device:
        """获取已有的设备连接"""
        device = self._device_clients.get(device_id)
        if device is None:
            raise RuntimeError(f"Device service not ready: {device_id}")
        return device

    def ensure_device_service(self, udid: str) -> tuple[str, str]:
        """确保设备服务可用（由 DeviceMonitor 调用）"""
        try:
            device = self._device_clients.get(udid)
            if device and device.ping():
                return ("online", "OK")

            device = u2.connect(udid)
            if device.ping():
                self._device_clients[udid] = device
                return ("online", "OK")
            else:
                return ("faulty", "Service not responding")
        except Exception as e:
            return ("faulty", str(e))

    def mark_device_faulty(self, udid: str) -> None:
        """标记设备为异常"""
        if udid in self._device_clients:
            del self._device_clients[udid]
```

#### 基础能力实现

| 方法 | uiautomator2 调用 |
|------|-------------------|
| `click(x, y)` | `device.click(x, y)` |
| `swipe(sx, sy, ex, ey)` | `device.swipe(sx, sy, ex, ey)` |
| `take_screenshot()` | `device.screenshot()` → PNG bytes |
| `input_text(text)` | `device.send_keys(text)` |
| `press(key)` | `device.press(key)` |

---

### 3. iOS 平台改造

#### tidevice3 + WDA 架构

```
Worker
  │
  ├── tidevice3 库
  │     ├── 检测设备连接
  │     ├── 安装 WDA (首次)
  │     └── 启动 WDA 服务 (端口映射)
  │
  └── HTTP 调用 WDA 服务
        ├── /wda/tap/{x}/{y}
        ├── /wda/dragfromtoforduration
        ├── /screenshot
        └── ...
```

#### iOSPlatformManager 核心改动

```python
import tidevice

class iOSPlatformManager(PlatformManager):
    WDA_BUNDLE_ID = "com.facebook.WebDriverAgentRunner"
    WDA_IPA_PATH = "wda/WebDriverAgent.ipa"

    def __init__(self, config: PlatformConfig, ocr_client=None):
        self.wda_base_port = config.wda_base_port or 8100
        self._device_wda: Dict[str, dict] = {}  # udid -> {"port": int, "process": Popen}
        self._device_clients: Dict[str, WDAClient] = {}

    def start(self) -> None:
        """仅环境检查：tidevice 可用、WDA.ipa 存在"""

    def create_context(self, device_id: str, options: Optional[Dict] = None) -> WDAClient:
        """获取已有的 WDA 连接"""
        client = self._device_clients.get(device_id)
        if client is None:
            raise RuntimeError(f"WDA service not ready: {device_id}")
        return client

    def ensure_device_service(self, udid: str) -> tuple[str, str]:
        """确保 WDA 服务可用（由 DeviceMonitor 调用）"""
        try:
            client = self._device_clients.get(udid)
            if client and client.health_check():
                return ("online", "OK")

            return self._start_wda(udid)
        except Exception as e:
            return ("faulty", str(e))

    def _start_wda(self, udid: str) -> tuple[str, str]:
        """启动 WDA 服务"""
        # 1. 检查/安装 WDA
        # 2. 分配端口
        # 3. 启动 WDA 进程
        # 4. 等待服务就绪

    def mark_device_faulty(self, udid: str) -> None:
        """标记设备为异常"""
        if udid in self._device_clients:
            del self._device_clients[udid]
        if udid in self._device_wda:
            # 停止 WDA 进程
            pass
```

#### WDA HTTP 客户端

```python
class WDAClient:
    def __init__(self, base_url: str):
        self.base_url = base_url
        self.session = httpx.Client(timeout=30)

    def health_check(self) -> bool:
        """检查服务状态"""

    def wait_ready(self, timeout: int = 30) -> bool:
        """等待服务就绪"""

    def tap(self, x: int, y: int) -> bool:
        """点击"""

    def swipe(self, sx: int, sy: int, ex: int, ey: int, duration: float = 0.5) -> bool:
        """滑动"""

    def screenshot(self) -> bytes:
        """截图"""
```

---

### 4. iOS 设备发现改造

使用 tidevice3 替代 libimobiledevice：

```python
# worker/discovery/ios.py

import tidevice

class iOSDiscoverer:
    @staticmethod
    def check_tidevice_available() -> bool:
        """检查 tidevice 是否可用"""
        try:
            import tidevice
            return True
        except ImportError:
            return False

    @staticmethod
    def list_devices() -> List[str]:
        """获取设备 UDID 列表"""
        return tidevice.usb_device_list()

    @staticmethod
    def get_device_info(udid: str) -> Optional[iOSDeviceInfo]:
        """获取设备详细信息"""
        d = tidevice.Device(udid)
        return iOSDeviceInfo(
            udid=udid,
            name=d.name,
            model=d.device_name,
            product_type=d.product_type,
            os_version=d.product_version,
            # ...
        )

    @classmethod
    def discover(cls) -> List[iOSDeviceInfo]:
        """发现所有 iOS 设备"""
        if not cls.check_tidevice_available():
            return []

        devices = []
        for udid in cls.list_devices():
            info = cls.get_device_info(udid)
            if info:
                devices.append(info)
        return devices
```

---

### 5. 配置变更

#### worker.yaml

```yaml
worker:
  id: null
  port: 8080
  device_check_interval: 300        # 5分钟
  service_retry_count: 3            # 服务启动重试次数
  service_retry_interval: 10        # 重试间隔(秒)
  action_step_delay: 0.5

external_services:
  platform_api: ""
  ocr_service: "http://192.168.0.102:9021"

platforms:
  web:
    enabled: null
    headless: false
    browser_type: chromium
    timeout: 30000
    session_timeout: 300
    screenshot_dir: data/screenshots
    ignore_https_errors: true
    permissions:
      - camera
      - microphone

  android:
    enabled: null
    wda_base_port: 7912             # uiautomator2 端口
    session_timeout: 300
    screenshot_dir: data/screenshots

  ios:
    enabled: null
    wda_base_port: 8100             # WDA 基础端口
    wda_ipa_path: wda/WebDriverAgent.ipa
    session_timeout: 300
    screenshot_dir: data/screenshots

  windows:
    enabled: null
    session_timeout: 300
    screenshot_dir: data/screenshots

  mac:
    enabled: null
    session_timeout: 300
    screenshot_dir: data/screenshots
```

#### pyproject.toml

```toml
dependencies = [
    # HTTP 服务
    "fastapi>=0.100.0",
    "uvicorn[standard]>=0.23.0",
    "httpx>=0.24.0",

    # Web 平台
    "playwright>=1.40",

    # 移动端平台（变更）
    "uiautomator2>=3.0",
    "tidevice>=3.0",

    # 桌面平台
    "pyautogui>=0.9.54",
    "psutil>=5.9.0",
    "Pillow>=10.0.0",

    # 图像匹配
    "opencv-python-headless>=4.8.0",

    # 工具
    "pyyaml>=6.0",
    "python-dotenv>=1.0",
    "pyperclip>=1.8.0",
]
```

---

### 6. 文件结构

```
worker/
├── device_monitor.py    # 新增：设备监控模块
├── platforms/
│   ├── base.py          # 无变更
│   ├── web.py           # 无变更
│   ├── android.py       # 重写：uiautomator2
│   ├── ios.py           # 重写：tidevice3 + WDA
│   ├── windows.py       # 无变更
│   └── mac.py           # 无变更
├── discovery/
│   ├── android.py       # 小改
│   └── ios.py           # 重写：tidevice3
├── worker.py            # 修改：集成 DeviceMonitor
└── config.py            # 修改：新增配置项

wda/                      # 新增目录
└── WebDriverAgent.ipa   # WDA 安装包（用户提供）

config/
└── worker.yaml          # 修改：更新配置结构
```

---

### 7. Worker 状态上报增强

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
            "windows": [] if self.host_info.os_type == "windows" else [],
            "web": [] if self.host_info.os_type == "windows" else [],
            "mac": [] if self.host_info.os_type == "macos" else [],
            "android": devices.get("android", []),
            "ios": devices.get("ios", []),
        },
        "faulty_devices": {
            "android": devices.get("faulty_android", []),
            "ios": devices.get("faulty_ios", []),
        },
    }
```

---

## 实现优先级

1. **Phase 1**：基础设施
   - 新增 device_monitor.py
   - 更新配置和依赖

2. **Phase 2**：Android 改造
   - 重写 android.py
   - 更新 android discovery

3. **Phase 3**：iOS 改造
   - 重写 ios.py
   - 更新 ios discovery

4. **Phase 4**：集成测试
   - Worker 集成
   - 端到端测试

---

## 风险与注意事项

1. **WDA 安装包**：需要用户提供 WebDriverAgent.ipa 文件
2. **tidevice 依赖**：Windows 需要 Apple Device Driver 或 iTunes
3. **端口冲突**：多设备时需要合理分配端口
4. **服务稳定性**：WDA 和 uiautomator2 偶尔需要重启

---

## 版本信息

- 设计日期：2026-03-21
- 设计版本：v1.0