# 鸿蒙平台集成设计

## 概述

为 Worker 自动化测试执行基建新增鸿蒙设备支持，遵循项目核心设计原则：所有平台统一使用 OCR 和图像识别定位元素，不依赖传统元素选择器。

## 背景

### 现有架构

Worker 目前支持以下平台：
- **Web**: Playwright 浏览器自动化
- **Windows**: pyautogui 系统级操作
- **Mac**: pyautogui 系统级操作
- **Android**: uiautomator2 直连 + minicap 截图
- **iOS**: go-ios + WDA (WebDriverAgent) 直连

每个平台实现 `PlatformManager` 基类，通过 `DeviceMonitor` 管理设备状态，使用 `ActionRegistry` 执行动作。

### 参考项目

- **hmnextauto**: 鸿蒙自动化驱动库，提供完整的 HDC 命令封装和设备操作 API
- **awesome-hdc**: HDC 命令详细文档
- **鸿蒙 SDK**: Command Line Tools (version 6.1.0.850)，包含 hdc.exe 工具

## 设计目标

1. 支持 USB 连接物理鸿蒙设备（主要场景）
2. 支持鸿蒙模拟器和无线连接（次要场景）
3. 完全遵循 OCR/图像识别定位原则
4. 与现有 Android/iOS 实现模式保持一致
5. 设备发现默认关闭，用户手动开启

## 架构设计

### 组件关系图

```
HTTP Request
     │
     ▼
Worker.execute_task()
     │
     ├── TaskScheduler.acquire(platform="harmony", device_id)
     │
     ▼
HarmonyPlatformManager
     │
     ├── ensure_device_service() → HarmonyHdcWrapper.is_online()
     │
     ├── create_context() → HarmonyHdcWrapper
     │
     └── execute_action()
     │         │
     │         ├── 平台特有: start_app/stop_app/unlock_screen
     │         │
     │         └── OCR/Image 动作: ActionRegistry
     │                   │
     │                   ├── get_screenshot() → HDC screenshot
     │                   │
     │                   └── click/swipe/input → HDC uitest uiInput
     │
     ▼
HarmonyHdcWrapper
     │
     ├── shell() → hdc -t {serial} shell {cmd}
     │
     ├── screenshot() → snapshot_display / screenCap
     │
     ├── tap/swipe/keyEvent → uitest uiInput
     │
     └── start_app/stop_app → aa start/force-stop
     │
     ▼
HDC Process (subprocess)
     │
     ▼
tools/hdc/hdc.exe
```

### 与现有架构的集成点

| 组件 | 集成方式 |
|------|---------|
| `worker.py` | `_init_platform_managers()` 增加 Harmony 初始化，根据 `discover_harmony_devices` 开关 |
| `device_monitor.py` | 增加 `_harmony_devices`、`_harmony_manager`，检测和维护鸿蒙设备状态 |
| `TaskScheduler` | 设备锁机制（同 Android/iOS，按 device_id 加锁） |
| `ActionRegistry` | 无需修改，OCR/Image 动作已支持所有平台 |

## 组件设计

### HarmonyPlatformManager

```python
class HarmonyPlatformManager(PlatformManager):
    platform: str = "harmony"
    
    SUPPORTED_ACTIONS: set[str] = {"start_app", "stop_app", "unlock_screen"}
    
    KEY_MAP = {
        "HOME": 1,      # KeyCode.HOME
        "BACK": 2,      # KeyCode.BACK
        "POWER": 18,    # KeyCode.POWER
        "VOLUME_UP": 16,
        "VOLUME_DOWN": 17,
        "VOLUME_MUTE": 22,
        "ENTER": 2054,
        "MENU": 2067,
    }
    
    _device_clients: dict[str, HarmonyHdcWrapper] = {}
```

**核心方法实现**：

| 方法 | 实现方式 |
|------|---------|
| `start()` | 验证 HDC 工具可用性 |
| `ensure_device_service()` | 验证设备在线（无额外服务启动） |
| `create_context()` | 返回 HarmonyHdcWrapper 实例 |
| `get_screenshot()` | 调用 HDC screenshot 命令 |
| `click()` | `uitest uiInput click {x} {y}` |
| `swipe()` | `uitest uiInput swipe {x1} {y1} {x2} {y2} {speed}` |
| `press()` | `uitest uiInput keyEvent {keyCode}` |
| `input_text()` | Hypium 协议 `Driver.inputText` |
| `_action_start_app()` | `aa start -a {ability} -b {package}` |
| `_action_stop_app()` | `aa force-stop {package}` |
| `_action_unlock_screen()` | `power-shell wakeup` + 上滑 |

### HarmonyHdcWrapper

```python
class HarmonyHdcWrapper:
    def __init__(self, serial: str, hdc_path: str):
        self.serial = serial
        self._hdc_prefix = f"{hdc_path} -t {serial}"
```

**命令执行机制**：
- 使用 `subprocess.Popen` 执行 HDC 命令
- 超时设置：默认 30 秒，截图等长操作可配置更长
- 返回 `CommandResult(output, error, exit_code)`

**截图实现**：

```python
def screenshot(self, local_path: str, method: str = "snapshot_display") -> str:
    if method == "snapshot_display":
        # 快速方式（推荐）
        tmp_path = f"/data/local/tmp/_tmp_{uuid}.jpeg"
        self.shell(f"snapshot_display -f {tmp_path}")
        self.pull_file(tmp_path, local_path)
        self.shell(f"rm -rf {tmp_path}")
    elif method == "screenCap":
        # 高质量方式
        tmp_path = f"/data/local/tmp/{uuid}.png"
        self.shell(f"uitest screenCap -p {tmp_path}")
        self.pull_file(tmp_path, local_path)
        self.shell(f"rm -rf {tmp_path}")
```

### HarmonyDiscoverer

```python
class HarmonyDiscoverer:
    @staticmethod
    def discover() -> list[dict]:
        # hdc list targets -v
        # 解析输出：
        # FMR0223C13000649    USB    Connected    unknown...
        # 返回 [{"udid": "FMR...", "connection": "USB", "status": "Connected"}]
        
    @staticmethod
    def get_device_info(udid: str) -> dict:
        # param get const.product.model
        # hidumper -s RenderService -a screen
        # 返回型号、分辨率等信息
```

### DeviceMonitor 集成

```python
class DeviceMonitor:
    def __init__(self, config):
        self.discover_harmony = config.discover_harmony_devices
        self._harmony_devices: list[dict] = []
        self._faulty_harmony_devices: list[dict] = []
        self._harmony_manager = None
        
    def _detect_physical_devices(self):
        if self.discover_harmony:
            harmony_devices = HarmonyDiscoverer.discover()
            
    def _maintain_services(self):
        # 鸿蒙无需启动额外服务，只需检查在线状态
```

## 配置设计

### worker.yaml

```yaml
worker:
  discover_android_devices: false
  discover_ios_devices: false
  discover_harmony_devices: false   # 新增，默认关闭

platforms:
  harmony:
    enabled: null                   # 仅 Windows 支持
    hdc_path: tools/hdc/hdc.exe
    screenshot_method: snapshot_display
    session_timeout: 300
    screenshot_dir: data/screenshots

unlock:
  harmony:
    swipe_start_y: 0.8
    swipe_end_y: 0.2
    swipe_speed: 6000
```

### WorkerConfig

```python
discover_harmony_devices: bool = False
```

## 动作支持

### 平台特有动作

| 动作 | HDC 命令 | 说明 |
|------|---------|------|
| `start_app` | `aa start -a {ability} -b {package}` | 启动应用 |
| `stop_app` | `aa force-stop {package}` | 强制停止应用 |
| `unlock_screen` | `power-shell wakeup` + swipe | 解锁屏幕（上滑） |

### 基础动作（通过 ActionRegistry）

所有 OCR/Image 动作自动支持，通过：
- `get_screenshot()` 获取截图
- `click()` / `swipe()` / `input_text()` 执行操作

### 按键映射

| 按键名 | KeyCode | 说明 |
|--------|---------|------|
| HOME | 1 | 回到桌面 |
| BACK | 2 | 返回 |
| POWER | 18 | 电源键 |
| VOLUME_UP | 16 | 音量加 |
| VOLUME_DOWN | 17 | 音量减 |
| ENTER | 2054 | 回车 |

## 错误处理

### 异常类

```python
class HarmonyError(Exception):
    """鸿蒙平台基础异常"""

class DeviceNotFoundError(HarmonyError):
    """设备未找到"""

class HdcCommandError(HarmonyError):
    """HDC 命令执行失败"""
    def __init__(self, cmd: str, output: str, exit_code: int):
        self.cmd = cmd
        self.output = output  
        self.exit_code = exit_code
```

### 处理策略

| 场景 | 处理 |
|------|------|
| HDC 工具不存在 | 平台 `is_available()` 返回 False |
| 设备离线 | `ensure_device_service` 返回 `("faulty", message)` |
| 截图失败 | 自动尝试备用方法 |
| HDC 命令超时 | subprocess timeout + 重试 |

## 文件清单

### 新增文件

| 文件 | 说明 |
|------|------|
| `worker/platforms/harmony.py` | HarmonyPlatformManager 实现 |
| `worker/platforms/harmony_hdc.py` | HarmonyHdcWrapper 命令封装 |
| `worker/discovery/harmony.py` | HarmonyDiscoverer 设备发现 |
| `tools/hdc/hdc.exe` | HDC 工具（从 SDK 复制） |
| `tools/hdc/README.md` | 工具说明文档 |

### 修改文件

| 文件 | 修改内容 |
|------|---------|
| `worker/config.py` | 增加 `discover_harmony_devices` |
| `worker/worker.py` | 注册 HarmonyPlatformManager |
| `worker/device_monitor.py` | 鸿蒙设备监控逻辑 |
| `worker/settings_window.py` | Harmony checkbox UI |
| `worker/actions/unlock.py` | Harmony unlock_screen 支持 |
| `config/worker.yaml` | 鸿蒙配置项 |

## 实现优先级

### Phase 1: 核心框架
1. 复制 HDC 工具到 tools/hdc/
2. 实现 HarmonyHdcWrapper 基础命令封装
3. 实现 HarmonyPlatformManager 基类方法
4. 配置和 Worker 注册

### Phase 2: 设备管理
1. 实现 HarmonyDiscoverer
2. DeviceMonitor 集成
3. 设置窗口 UI

### Phase 3: 动作实现
1. start_app / stop_app
2. unlock_screen
3. 按键映射
4. OCR/Image 动作测试验证

## 测试计划

### 单元测试
- HarmonyHdcWrapper 命令执行测试
- 按键映射正确性测试
- 截图方法测试

### 集成测试
- 设备发现流程测试
- OCR 点击流程测试
- start_app/unlock_screen 测试

### 设备需求
- 需要一台鸿蒙物理设备（如 HUAWEI Mate 60 Pro）
- 或鸿蒙模拟器进行测试验证