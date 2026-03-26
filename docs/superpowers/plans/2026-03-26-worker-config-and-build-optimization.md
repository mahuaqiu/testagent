# Worker 配置与打包优化实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 为 Worker 添加 IP 配置字段和版本号字段，并将打包模式从单文件改为多文件目录，同时打包 Playwright 浏览器。

**Architecture:** 配置层新增 IP 字段和版本号生成逻辑；打包脚本改为目录模式并复制 Playwright chromium；启动时设置环境变量。

**Tech Stack:** Python, PyInstaller, PowerShell, Playwright

---

## 文件结构

| 文件 | 操作 | 说明 |
|------|------|------|
| `config/worker.yaml` | 修改 | 新增 `worker.ip` 字段 |
| `worker/config.py` | 修改 | 新增 `ip` 字段 |
| `worker/discovery/host.py` | 修改 | 新增 `get_preferred_ip()` 方法 |
| `worker/worker.py` | 修改 | IP 获取逻辑、版本号获取 |
| `worker/main.py` | 修改 | EXE 时设置 Playwright 路径 |
| `scripts/pyinstaller.spec` | 修改 | 改为目录模式 |
| `scripts/build_windows.ps1` | 修改 | 生成版本号、复制 playwright |
| `tests/test_host_discovery.py` | 创建 | 测试 IP 获取逻辑 |

---

## Task 1: 配置新增 IP 字段

**Files:**
- Modify: `config/worker.yaml:5-12`
- Modify: `worker/config.py:14-68`

### Step 1: 修改配置文件示例

- [ ] **在 `config/worker.yaml` 中添加 `ip` 字段**

```yaml
# Worker 基础配置
worker:
  id: null                          # 自动生成，也可指定
  ip: null                          # 指定 IP 地址，null 表示自动获取
  port: 8088                        # HTTP 服务端口
```

### Step 2: 修改配置类

- [ ] **在 `worker/config.py` 的 `WorkerConfig` 类中添加 `ip` 字段**

在 `WorkerConfig` dataclass 中添加字段：

```python
@dataclass
class WorkerConfig:
    """Worker 配置。"""

    # Worker 基础配置
    id: str = field(default_factory=lambda: str(uuid.uuid4())[:8])
    ip: Optional[str] = None  # 新增：指定 IP 地址
    port: int = 8080
```

### Step 3: 修改配置加载方法

- [ ] **在 `from_yaml` 方法中读取 `ip` 字段**

```python
return cls(
    id=worker_data.get("id") or str(uuid.uuid4())[:8],
    ip=worker_data.get("ip"),  # 新增
    port=worker_data.get("port", 8080),
    # ... 其他字段保持不变
)
```

### Step 4: 验证配置加载

- [ ] **测试配置加载是否正确**

```bash
python -c "from worker.config import load_config; c = load_config(); print(f'ip={c.ip}')"
```

Expected: `ip=None`

### Step 5: 提交

```bash
git add config/worker.yaml worker/config.py
git commit -m "feat(config): 新增 worker.ip 配置字段"
```

---

## Task 2: IP 获取逻辑

**Files:**
- Modify: `worker/discovery/host.py:97-125`
- Create: `tests/test_host_discovery.py`

### Step 1: 编写测试

- [ ] **创建 `tests/test_host_discovery.py`**

```python
"""测试 HostDiscovery 模块。"""

import pytest
from worker.discovery.host import HostDiscoverer


class TestGetPreferredIp:
    """测试 get_preferred_ip 方法。"""

    def test_no_config_returns_auto_ip(self):
        """未配置 IP 时，返回自动获取的 IP。"""
        result = HostDiscoverer.get_preferred_ip(configured_ip=None)
        assert result is not None
        assert result != ""

    def test_valid_config_ip_returns_config(self):
        """配置的 IP 在本机存在时，返回配置的 IP。"""
        # 先获取本机所有 IP
        all_ips = HostDiscoverer.get_ip_addresses()
        if not all_ips or all_ips[0] == "127.0.0.1":
            pytest.skip("No non-loopback IP available")

        valid_ip = all_ips[0]
        result = HostDiscoverer.get_preferred_ip(configured_ip=valid_ip)
        assert result == valid_ip

    def test_invalid_config_ip_falls_back(self):
        """配置的 IP 不在本机时，回退到自动获取。"""
        invalid_ip = "999.999.999.999"
        result = HostDiscoverer.get_preferred_ip(configured_ip=invalid_ip)
        # 应该返回本机的某个 IP，而不是无效 IP
        assert result != invalid_ip
        assert result is not None
```

### Step 2: 运行测试确认失败

```bash
pytest tests/test_host_discovery.py -v
```

Expected: FAIL - `AttributeError: type object 'HostDiscoverer' has no attribute 'get_preferred_ip'`

### Step 3: 实现 `get_preferred_ip` 方法

- [ ] **在 `worker/discovery/host.py` 的 `HostDiscoverer` 类中添加方法**

```python
import logging

logger = logging.getLogger(__name__)


class HostDiscoverer:
    # ... 现有方法保持不变 ...

    @staticmethod
    def get_preferred_ip(configured_ip: Optional[str] = None) -> str:
        """
        获取优先使用的 IP 地址。

        Args:
            configured_ip: 配置的 IP 地址

        Returns:
            str: IP 地址
        """
        all_ips = HostDiscoverer.get_ip_addresses()

        if configured_ip:
            if configured_ip in all_ips:
                return configured_ip
            else:
                logger.warning(
                    f"Configured IP '{configured_ip}' not found in local interfaces. "
                    f"Available IPs: {all_ips}. Falling back to auto-detection."
                )

        # 未配置或配置无效，返回第一个非回环 IP
        return all_ips[0] if all_ips else "127.0.0.1"
```

### Step 4: 运行测试确认通过

```bash
pytest tests/test_host_discovery.py -v
```

Expected: PASS

### Step 5: 提交

```bash
git add worker/discovery/host.py tests/test_host_discovery.py
git commit -m "feat(discovery): 新增 get_preferred_ip 方法支持配置 IP"
```

---

## Task 3: Worker 使用配置 IP

**Files:**
- Modify: `worker/worker.py:450-471`

### Step 1: 修改 `get_worker_devices` 方法

- [ ] **修改 `worker/worker.py` 中的 `get_worker_devices` 方法**

找到 `get_worker_devices` 方法，修改 IP 获取逻辑：

```python
from worker.discovery.host import HostDiscoverer

def get_worker_devices(self) -> Dict[str, Any]:
    """获取 Worker 状态和设备信息。"""
    devices = self.device_monitor.get_all_devices() if self.device_monitor else {}

    # 使用配置的 IP 或自动获取
    ip = HostDiscoverer.get_preferred_ip(self.config.ip)

    return {
        "status": self._status,
        "started_at": self._started_at,
        "supported_platforms": self.supported_platforms,
        "ip": ip,
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

### Step 2: 验证功能

```bash
python -c "
from worker.config import load_config
from worker.worker import Worker
c = load_config()
print(f'Config IP: {c.ip}')
"
```

Expected: `Config IP: None`

### Step 3: 提交

```bash
git add worker/worker.py
git commit -m "feat(worker): get_worker_devices 使用配置的 IP"
```

---

## Task 4: 版本号功能

**Files:**
- Modify: `worker/worker.py:450-471`

### Step 1: 添加版本号获取方法

- [ ] **在 `worker/worker.py` 的 `Worker` 类中添加 `_get_version` 方法**

```python
def _get_version(self) -> Optional[str]:
    """
    获取版本号。

    Returns:
        str | None: 版本号，非 EXE 运行时返回 None
    """
    try:
        from worker._version import VERSION
        return VERSION
    except ImportError:
        return None
```

### Step 2: 修改 `get_worker_devices` 返回版本号

- [ ] **在 `get_worker_devices` 返回值中添加 `version` 字段**

```python
def get_worker_devices(self) -> Dict[str, Any]:
    """获取 Worker 状态和设备信息。"""
    devices = self.device_monitor.get_all_devices() if self.device_monitor else {}
    ip = HostDiscoverer.get_preferred_ip(self.config.ip)

    return {
        "status": self._status,
        "started_at": self._started_at,
        "supported_platforms": self.supported_platforms,
        "ip": ip,
        "port": self.port,
        "version": self._get_version(),  # 新增
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

### Step 3: 测试版本号获取

```bash
python -c "
from worker.worker import Worker
from worker.config import WorkerConfig
w = Worker(WorkerConfig())
print(f'Version: {w._get_version()}')
"
```

Expected: `Version: None` (开发模式)

### Step 4: 提交

```bash
git add worker/worker.py
git commit -m "feat(worker): 新增 version 字段，打包时生成时间戳版本"
```

---

## Task 5: main.py 设置 Playwright 路径

**Files:**
- Modify: `worker/main.py:22-71`

### Step 1: 修改 main 函数开头

- [ ] **在 `worker/main.py` 的 `main()` 函数开头添加 Playwright 路径设置**

```python
def main():
    """主函数。"""
    # EXE 运行时设置 Playwright 浏览器路径
    if getattr(sys, 'frozen', False):
        app_dir = os.path.dirname(sys.executable)
        playwright_path = os.path.join(app_dir, 'playwright')
        os.environ['PLAYWRIGHT_BROWSERS_PATH'] = playwright_path

    # 加载配置
    config = load_config()
    # ... 后续代码不变
```

### Step 2: 验证语法正确

```bash
python -c "import worker.main; print('OK')"
```

Expected: `OK`

### Step 3: 提交

```bash
git add worker/main.py
git commit -m "feat(main): EXE 运行时设置 PLAYWRIGHT_BROWSERS_PATH"
```

---

## Task 6: PyInstaller 改为目录模式

**Files:**
- Modify: `scripts/pyinstaller.spec:91-113`

### Step 1: 修改 spec 文件

- [ ] **修改 `scripts/pyinstaller.spec`，从单文件模式改为目录模式**

将文件末尾的 `EXE` 和后续代码替换为：

```python
# -*- mode: python ; coding: utf-8 -*-
"""
PyInstaller 打包配置文件。

使用目录模式打包，生成 test-worker.exe 和 _internal 目录。
"""

import os
import sys

block_cipher = None

# 项目根目录
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(SPEC)))

# 收集数据文件
datas = [
    (os.path.join(PROJECT_ROOT, 'config'), 'config'),
]

# 收集隐藏导入
hiddenimports = [
    # FastAPI / Uvicorn
    'uvicorn',
    'uvicorn.logging',
    'uvicorn.loops',
    'uvicorn.loops.auto',
    'uvicorn.protocols',
    'uvicorn.protocols.http',
    'uvicorn.protocols.http.auto',
    'uvicorn.protocols.websockets',
    'uvicorn.protocols.websockets.auto',
    'uvicorn.lifespan',
    'uvicorn.lifespan.on',
    'starlette',
    'starlette.responses',
    'starlette.routing',
    'starlette.middleware',
    'starlette.exceptions',
    # HTTP 客户端
    'httpx',
    'h11',
    'h2',
    'hpack',
    # Playwright
    'playwright',
    'playwright.sync_api',
    'playwright._impl',
    # Appium
    'appium',
    'appium.webdriver',
    'appium.options',
    'appium.options.android',
    'appium.options.ios',
    'selenium',
    'selenium.webdriver',
    # 桌面自动化
    'pyautogui',
    'pyscreeze',
    'pygetwindow',
    'mouseinfo',
    'pyrect',
    # 图像处理
    'PIL',
    'PIL.Image',
    'cv2',
    # 工具
    'yaml',
    'dotenv',
    'psutil',
]

a = Analysis(
    [os.path.join(PROJECT_ROOT, 'worker', 'main.py')],
    pathex=[PROJECT_ROOT],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        'pytest',
        'allure',
        'faker',
    ],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

# 目录模式：EXE 不包含依赖，由 COLLECT 收集
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,  # 不包含二进制文件
    name='test-worker',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

# 收集所有依赖到 dist/test-worker 目录
coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='test-worker',
)
```

### Step 2: 验证 spec 文件语法

```bash
python -c "exec(open('scripts/pyinstaller.spec').read())"
```

Expected: 无错误输出

### Step 3: 提交

```bash
git add scripts/pyinstaller.spec
git commit -m "refactor(build): PyInstaller 改为目录模式打包"
```

---

## Task 7: 打包脚本修改

**Files:**
- Modify: `scripts/build_windows.ps1`

### Step 1: 重写打包脚本

- [ ] **修改 `scripts/build_windows.ps1`，添加版本号生成和 Playwright 打包**

完整重写文件：

```powershell
# Windows Build Script (PowerShell)

param(
    [string]$Version = "2.0.0",
    [string]$OutputDir = "dist\windows",
    [switch]$Clean  # Use -Clean to force rebuild venv
)

Write-Host "=========================================="
Write-Host "Building Test Worker for Windows"
Write-Host "Version: $Version"
Write-Host "Output: $OutputDir"
Write-Host "=========================================="

# Check Python
if (-not (Get-Command python -ErrorAction SilentlyContinue)) {
    Write-Error "Python not found!"
    exit 1
}

# Virtual environment path
$VenvPath = "build_env"

# Check if we need to recreate virtual environment
if ($Clean -or -not (Test-Path $VenvPath)) {
    if (Test-Path $VenvPath) {
        Write-Host "[1/7] Removing old virtual environment..."
        Remove-Item -Recurse -Force $VenvPath
    }
    Write-Host "[1/7] Creating virtual environment..."
    python -m venv $VenvPath
} else {
    Write-Host "[1/7] Using existing virtual environment..."
}

# Activate virtual environment
& ".\$VenvPath\Scripts\Activate.ps1"

# Check if pyinstaller exists in venv
$PyinstallerExists = Test-Path ".\$VenvPath\Scripts\pyinstaller.exe"

if (-not $PyinstallerExists) {
    Write-Host "[2/7] Installing dependencies (pyinstaller not found in venv)..."
    pip install --upgrade pip
    pip install -e ".[all]"
    pip install pyinstaller
} else {
    Write-Host "[2/7] Dependencies already installed, skipping..."
}

# Check if Playwright chromium is already installed
$ChromiumPath = "$env:LOCALAPPDATA\ms-playwright\chromium-*"
$ChromiumInstalled = Test-Path $ChromiumPath

if ($ChromiumInstalled) {
    Write-Host "[3/7] Playwright chromium already installed, skipping..."
} else {
    Write-Host "[3/7] Installing Playwright browsers..."
    playwright install chromium
    if ($LASTEXITCODE -ne 0) {
        Write-Warning "Playwright browser installation may have issues"
    }
}

# Generate version file
Write-Host "[4/7] Generating version file..."
$BuildVersion = Get-Date -Format "yyyyMMddHHmm"
$VersionFile = "worker\_version.py"
$VersionContent = "VERSION = `"$BuildVersion`""
Set-Content -Path $VersionFile -Value $VersionContent -Encoding UTF8
Write-Host "Build version: $BuildVersion"

# Build
Write-Host "[5/7] Building executable..."
pyinstaller scripts/pyinstaller.spec --clean --noconfirm
if ($LASTEXITCODE -ne 0) {
    Write-Error "PyInstaller build failed!"
    Remove-Item $VersionFile -ErrorAction SilentlyContinue
    deactivate
    exit 1
}

# Clean up version file
Remove-Item $VersionFile -ErrorAction SilentlyContinue

# Check generated directory
$BuildDir = "dist\test-worker"
if (-not (Test-Path $BuildDir)) {
    Write-Error "Build directory not found: $BuildDir"
    deactivate
    exit 1
}

# Create release package
Write-Host "[6/7] Creating release package..."
$PackageDir = "$OutputDir\test-worker-$Version"

# Clean old release directory
if (Test-Path $PackageDir) {
    Remove-Item -Recurse -Force $PackageDir
}
New-Item -ItemType Directory -Force -Path $PackageDir | Out-Null

# Move build directory to package
Move-Item "$BuildDir\*" $PackageDir

# Copy Playwright chromium to package
Write-Host "Copying Playwright chromium..."
$SourcePlaywright = "$env:LOCALAPPDATA\ms-playwright"
$DestPlaywright = "$PackageDir\playwright"

$ChromiumDir = Get-ChildItem -Path $SourcePlaywright -Filter "chromium-*" -Directory | Select-Object -First 1
if ($ChromiumDir) {
    Copy-Item -Path $ChromiumDir.FullName -Destination "$DestPlaywright\$($ChromiumDir.Name)" -Recurse
    Write-Host "Copied chromium: $($ChromiumDir.Name)"
} else {
    Write-Warning "Playwright chromium not found at $SourcePlaywright"
}

# Create start script
@"
@echo off
chcp 65001 >nul 2>&1
cd /d "%~dp0"
test-worker.exe
pause
"@ | Out-File "$PackageDir\start.bat" -Encoding ASCII

# Create README
@"
Test Worker v$Version - Windows

Usage:
  1. Edit config\worker.yaml to configure settings
  2. Double-click start.bat to start the worker
  3. Or run from command line: test-worker.exe

Configuration:
  All settings are read from config\worker.yaml, including:
  - Server port (default: 8088)
  - IP address (optional, auto-detected if not specified)
  - OCR service URL
  - Platform API URL
  - Platform-specific options

Requirements:
  - For Android/iOS: ADB and libimobiledevice must be installed
  - For OCR: OCR service must be running

Build Version: $BuildVersion
"@ | Out-File "$PackageDir\README.txt" -Encoding UTF8

# Deactivate virtual environment
deactivate

Write-Host "[7/7] Build complete!"
Write-Host "=========================================="
Write-Host "Build successful!"
Write-Host "Package: $PackageDir"
Write-Host "Build Version: $BuildVersion"
Write-Host ""
Write-Host "Note: Virtual environment preserved at: $VenvPath"
Write-Host "Use -Clean flag to rebuild from scratch: .\build_windows.ps1 -Clean"
Write-Host "=========================================="
```

### Step 2: 提交

```bash
git add scripts/build_windows.ps1
git commit -m "feat(build): 打包脚本支持版本号生成和 Playwright 打包"
```

---

## Task 8: 验证和测试

### Step 1: 运行单元测试

```bash
pytest tests/test_host_discovery.py -v
```

Expected: All tests PASS

### Step 2: 验证配置加载

```bash
python -c "
from worker.config import load_config
from worker.discovery.host import HostDiscoverer
from worker.worker import Worker

config = load_config()
print(f'Config IP: {config.ip}')

ip = HostDiscoverer.get_preferred_ip(config.ip)
print(f'Preferred IP: {ip}')

worker = Worker(config)
version = worker._get_version()
print(f'Version: {version}')
"
```

Expected:
- `Config IP: None`
- `Preferred IP: <本机IP>`
- `Version: None`

### Step 3: 提交最终修改

```bash
git add -A
git commit -m "chore: 完成配置与打包优化"
```

---

## 打包测试（手动）

完成上述任务后，运行打包命令验证：

```powershell
powershell scripts/build_windows.ps1
```

验证点：
1. `dist/windows/test-worker-2.0.0/` 目录存在
2. `test-worker.exe` 在目录中
3. `_internal/` 目录存在
4. `playwright/chromium-*/` 目录存在
5. `config/worker.yaml` 文件存在
6. 启动后 `/worker_devices` 返回 `version` 字段

---

## 注意事项

1. **版本文件不提交**: `worker/_version.py` 在打包时生成，构建后删除，不提交到 git
2. **Playwright 路径**: EXE 运行时会自动设置 `PLAYWRIGHT_BROWSERS_PATH`
3. **目录分发**: 打包后需要分发整个目录，不能只分发 EXE