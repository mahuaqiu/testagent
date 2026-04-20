---
name: Android Minicap 截图方案设计
description: 集成 minicap 截图能力，绑过 FLAG_SECURE 防截屏限制
type: project
---

# Android Minicap 截图方案设计

## 背景

当前项目使用 `uiautomator2.screenshot()` 进行截图，底层依赖 `adb shell screencap` 命令。当 APP 设置 `FLAG_SECURE` 安全标志（银行APP、企业APP、会议软件等）时，截图会被限制，返回黑屏或失败。

**minicap** 是 openstf 开发的截图工具，直接读取 framebuffer，绑过应用层的截图限制，成功率更高。

## 目标

- 集成 minicap 截图能力，解决 FLAG_SECURE 防截屏问题
- 保持现有架构不变，仅替换截图实现
- 自动适配不同 Android 版本和 CPU 架构

## 设计方案

### 1. 资源文件结构

```
worker/
├── platforms/
│   ├── android.py              # 修改：集成 minicap 截图
│   └── minicap/                # 新增：minicap 模块
│       ├── __init__.py
│       ├── minicap.py          # minicap 核心实现（从 airtest 适配）
│       └── static/
│           └── stf_libs/       # minicap 二进制资源（从 airtest 复制）
│               ├── arm64-v8a/
│               │   ├── minicap
│               │   ├── minicap-nopie
│               │   └── minicap.so
│               ├── armeabi-v7a/
│               ├── x86/
│               ├── x86_64/
│               └── minicap-shared/
│                   └── aosp/libs/
│                       └── android-{sdk}/{abi}/minicap.so
```

**资源来源**：从 `D:\code\Airtest-master\airtest\core\android\static\stf_libs` 复制完整目录。

### 2. 文件改动清单

| 文件 | 操作 | 说明 |
|------|------|------|
| `worker/platforms/minicap/__init__.py` | 新增 | 模块导出 |
| `worker/platforms/minicap/minicap.py` | 新增 | minicap 核心类，从 airtest 适配 |
| `worker/platforms/minicap/static/stf_libs/` | 新增 | 复制二进制资源 |
| `worker/platforms/android.py` | 修改 | 集成 minicap 截图逻辑 |

### 3. Minicap 类设计

```python
class Minicap:
    """Android minicap 截图工具"""
    
    VERSION = 5
    DEVICE_DIR = "/data/local/tmp"
    
    def __init__(self, udid: str):
        self.udid = udid
        self._installed = False
    
    def install(self) -> None:
        """安装 minicap 到设备"""
        # 1. 获取设备信息：abi, sdk_version
        # 2. 推送 minicap 二进制文件
        # 3. 推送对应版本的 minicap.so
        # 4. 设置执行权限
    
    def get_frame(self) -> bytes:
        """获取单帧截图（JPG格式）"""
        # 执行 minicap -s 命令，返回 JPG 数据
    
    def get_display_info(self) -> dict:
        """获取屏幕显示信息"""
        # width, height, rotation
```

**关键适配点**：
- 使用纯 ADB 命令（`adb -s {udid} shell/push`）而非 airtest 的 ADB 类
- 简化实现：仅保留 `install()` 和 `get_frame()` 方法，无需流式传输

### 4. Android 平台改动

```python
class AndroidPlatformManager(PlatformManager):
    
    def __init__(self, ...):
        ...
        self._minicap_instances: dict[str, Minicap] = {}
    
    def ensure_device_service(self, udid: str) -> tuple[str, str]:
        """确保设备服务可用"""
        # 原有逻辑：连接 uiautomator2
        
        # 新增：安装并初始化 minicap
        minicap = Minicap(udid)
        minicap.install()
        self._minicap_instances[udid] = minicap
    
    def take_screenshot(self, context: Any = None) -> bytes:
        """获取截图"""
        device_id = self._current_device
        minicap = self._minicap_instances.get(device_id)
        
        if minicap:
            jpg_data = minicap.get_frame()
            # 转换 JPG → PNG（保持接口一致）
            from PIL import Image
            img = Image.open(BytesIO(jpg_data))
            buffer = BytesIO()
            img.save(buffer, format="PNG")
            return buffer.getvalue()
        
        return b""
```

### 5. 安装流程

```
ensure_device_service(udid)
  → 连接 uiautomator2（原有逻辑）
  → 创建 Minicap(udid)
  → minicap.install():
      1. adb shell getprop ro.product.cpu.abi → 获取 CPU 架构
      2. adb shell getprop ro.build.version.sdk → 获取 SDK 版本
      3. adb push {stf_libs}/{abi}/minicap → /data/local/tmp/minicap
      4. adb push {stf_libs}/android-{sdk}/{abi}/minicap.so → /data/local/tmp/
      5. adb shell chmod 755 /data/local/tmp/minicap*
  → 存储到 _minicap_instances[udid]
```

### 6. 截图流程

```
take_screenshot(context)
  → 获取 current_device
  → minicap = _minicap_instances[device_id]
  → jpg_data = minicap.get_frame():
      adb shell LD_LIBRARY_PATH=/data/local/tmp /data/local/tmp/minicap -P ... -s
  → JPG → PNG 转换
  → 返回 PNG bytes
```

### 7. 错误处理

| 场景 | 处理方式 |
|------|----------|
| minicap 安装失败 | 记录日志，截图时抛出异常 |
| minicap 截图失败 | 抛出异常，Task 执行失败并返回错误信息 |
| 设备断开重连 | 在 `ensure_device_service` 中检测并重新安装 |

### 8. 配置项（可选扩展）

可在 `config/worker.yaml` 中添加配置：

```yaml
android:
  screenshot_method: minicap  # 可选: minicap, uiautomator2
```

**当前阶段**：直接使用 minicap，不添加配置项，保持简单。

## 实现步骤

1. 创建 `worker/platforms/minicap/` 目录结构
2. 从 airtest 复制 `stf_libs` 资源目录
3. 实现 `minicap.py` 核心类
4. 修改 `android.py` 集成 minicap
5. 测试验证

## Why

**Why: FLAG_SECURE 防截屏限制导致现有 uiautomator2 截图在部分 APP 场景失效**

## How to apply

集成 minicap 截图方案后，Android 平台截图将绑过 FLAG_SECURE 限制，支持银行APP、企业APP、会议软件等防截屏场景。