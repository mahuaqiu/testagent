# Worker 配置与打包优化设计

## 概述

本设计针对 Worker 的四个改进需求：
1. 配置新增 IP 字段，支持多网卡环境下指定 IP
2. 新增版本号字段，打包时生成时间戳版本
3. 打包模式从单文件改为多文件目录
4. Playwright 浏览器打包到程序目录，支持离线运行

## 需求详情

### 1. IP 配置字段

**问题**: 多网卡环境下，自动获取的 IP 可能不是期望的 IP。

**解决方案**:
- `config/worker.yaml` 新增 `worker.ip` 字段
- 配置优先策略：配置了 IP 且在本机网卡中存在 → 使用配置值；未配置 → 自动获取

### 2. 版本号字段

**问题**: 无法识别 Worker 的打包版本。

**解决方案**:
- 打包时生成 `worker/_version.py`，内容为 `VERSION = "YYYYMMDDHHMM"` 格式时间戳
- `/worker_devices` 接口返回 `version` 字段
- 非 EXE 运行时，`version` 为 `null`

### 3. 多文件打包模式

**问题**: 单 EXE 文件过大，启动慢。

**解决方案**:
- PyInstaller 从 onefile 模式改为目录模式
- 生成 `test-worker.exe` + 依赖文件目录
- 启动更快，便于单独更新配置文件

### 4. Playwright 浏览器打包

**问题**: EXE 在其他电脑运行时缺少 Playwright 浏览器。

**解决方案**:
- 打包时复制 chromium 浏览器到程序目录的 `playwright/` 子目录
- 程序启动时设置 `PLAYWRIGHT_BROWSERS_PATH` 环境变量指向该目录
- 仅打包 chromium，与现有行为一致

## 详细设计

### 1. 配置修改

**文件**: `config/worker.yaml`

```yaml
worker:
  id: null
  ip: null              # 新增：指定 IP，null 表示自动获取
  port: 8088
  # ... 其他配置不变
```

**文件**: `worker/config.py`

```python
@dataclass
class WorkerConfig:
    # 新增字段
    ip: Optional[str] = None

    @classmethod
    def from_yaml(cls, path: str) -> "WorkerConfig":
        # 新增读取 ip 字段
        ip=worker_data.get("ip"),
```

### 2. IP 获取逻辑修改

**文件**: `worker/discovery/host.py`

```python
@staticmethod
def get_ip_addresses() -> List[str]:
    # 保持现有逻辑不变

@staticmethod
def get_preferred_ip(configured_ip: Optional[str] = None) -> str:
    """
    获取优先使用的 IP 地址。

    Args:
        configured_ip: 配置的 IP 地址

    Returns:
        str: IP 地址
    """
    if configured_ip:
        # 验证配置的 IP 是否在本机网卡中
        all_ips = cls.get_ip_addresses()
        if configured_ip in all_ips:
            return configured_ip
        # 配置的 IP 不存在，记录警告后回退

    # 未配置或配置无效，返回第一个非回环 IP
    ips = cls.get_ip_addresses()
    return ips[0] if ips else "127.0.0.1"
```

**文件**: `worker/worker.py`

```python
def get_worker_devices(self) -> Dict[str, Any]:
    # 使用配置的 IP 或自动获取
    ip = HostDiscoverer.get_preferred_ip(self.config.ip)

    return {
        # ...
        "ip": ip,
        "version": self._get_version(),
        # ...
    }
```

### 3. 版本号实现

**文件**: `worker/_version.py` (打包时生成)

```python
VERSION = "202603261512"
```

**文件**: `worker/worker.py`

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

**文件**: `scripts/build_windows.ps1`

```powershell
# 生成版本号
$Version = Get-Date -Format "yyyyMMddHHmm"
$VersionContent = "VERSION = `"$Version`""
$VersionFile = "worker\_version.py"
Set-Content -Path $VersionFile -Value $VersionContent -Encoding UTF8

# 构建后删除版本文件（避免提交到 git）
Remove-Item $VersionFile -ErrorAction SilentlyContinue
```

### 4. PyInstaller Spec 修改

**文件**: `scripts/pyinstaller.spec`

```python
# 从 onefile 改为目录模式
exe = EXE(
    pyz,
    a.scripts,
    [],  # 不再包含 binaries 和 datas
    exclude_binaries=True,  # 新增
    name='test-worker',
    # ... 其他参数
)

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

### 5. Playwright 浏览器打包

**文件**: `scripts/build_windows.ps1`

```powershell
# 复制 Playwright chromium 到程序目录
$SourcePlaywright = "$env:LOCALAPPDATA\ms-playwright"
$DestPlaywright = "$PackageDir\playwright"

# 只复制 chromium
$ChromiumDir = Get-ChildItem -Path $SourcePlaywright -Filter "chromium-*" -Directory | Select-Object -First 1
if ($ChromiumDir) {
    Copy-Item -Path $ChromiumDir.FullName -Destination "$DestPlaywright\$($ChromiumDir.Name)" -Recurse
}
```

**文件**: `worker/main.py`

```python
def main():
    # EXE 运行时设置 Playwright 浏览器路径
    if getattr(sys, 'frozen', False):
        app_dir = os.path.dirname(sys.executable)
        playwright_path = os.path.join(app_dir, 'playwright')
        os.environ['PLAYWRIGHT_BROWSERS_PATH'] = playwright_path

    # ... 原有逻辑
```

### 6. 打包脚本整体修改

**文件**: `scripts/build_windows.ps1`

主要变更：
1. 构建前生成 `_version.py`
2. PyInstaller 使用目录模式
3. 复制 playwright chromium 到输出目录
4. 更新 README 说明

## 文件修改清单

| 文件 | 修改类型 | 说明 |
|------|----------|------|
| `config/worker.yaml` | 修改 | 新增 `worker.ip` 字段 |
| `worker/config.py` | 修改 | 新增 `ip` 字段和读取逻辑 |
| `worker/discovery/host.py` | 修改 | 新增 `get_preferred_ip()` 方法 |
| `worker/worker.py` | 修改 | IP 获取逻辑、版本号获取方法 |
| `worker/main.py` | 修改 | EXE 运行时设置 Playwright 路径 |
| `scripts/pyinstaller.spec` | 修改 | 改为目录模式打包 |
| `scripts/build_windows.ps1` | 修改 | 生成版本号、复制 playwright |

## 目录结构

打包后的目录结构：

```
dist/test-worker-2.0.0/
├── test-worker.exe
├── config/
│   └── worker.yaml
├── playwright/
│   └── chromium-1234/
│       └── chrome-xxx/
├── _internal/          # PyInstaller 依赖目录
│   └── ... (DLLs, Python 库等)
├── start.bat
└── README.txt
```

## 兼容性

- 配置文件：新增 `ip` 字段，默认 `null`，不影响现有配置
- API 接口：`/worker_devices` 新增 `version` 字段，向后兼容
- 部署：需要将整个目录打包分发，而非单个 EXE

## 测试要点

1. IP 配置：
   - 未配置 IP 时，自动获取正确
   - 配置有效 IP 时，使用配置值
   - 配置无效 IP 时，回退到自动获取

2. 版本号：
   - 开发模式运行，`version` 为 `null`
   - EXE 运行，`version` 为时间戳格式

3. Playwright：
   - EXE 在无 playwright 的机器上能正常运行 Web 平台任务