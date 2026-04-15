# 机器配置更新功能设计

## 概述

实现 Worker 机器配置远程更新功能，支持平台通过 HTTP API 下发配置，Worker 接收后自动更新并重启。

## 功能需求

1. 接收配置内容，保存到本地配置文件（worker.yaml）
2. IP 地址不更新，下发了也不更新（保留本地指定）
3. 记录 config_version 到单独文件（用于后续注册时上报）
4. 对比现有配置版本：
   - 版本相同 → 返回成功响应（updated=false），无需更新和重启
   - 版本不同 → 执行配置更新、保存版本后返回响应，并触发重启
5. 重启 Worker 服务（无需确认，自动重启）
6. 重启后注册时，上报 config_version 字段

## 接口设计

### 请求接口

```
POST /worker/config
```

### 请求参数

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| config_content | string | 是 | 完整的 YAML 配置文件内容 |
| config_version | string | 是 | 配置版本号，格式：YYYYMMDD-HHMMSS |

**请求示例**：

```json
{
  "config_content": "# Worker Configuration File\nworker:\n  id: null\n  ip: null\n  port: 8088\n  namespace: meeting_public\n  ...",
  "config_version": "20260415-143000"
}
```

### 响应格式

**成功响应**：

```json
{
  "status": "success",
  "message": "配置更新成功",
  "updated": true,
  "config_version": "20260415-143000",
  "restart_triggered": true
}
```

| 字段 | 说明 |
|------|------|
| status | 状态：success 或 error |
| message | 结果描述 |
| updated | 是否有更新（版本相同时为 false） |
| config_version | 当前版本号 |
| restart_triggered | 是否触发了重启 |

**版本相同（无更新）响应**：

```json
{
  "status": "success",
  "message": "配置版本相同，无需更新",
  "updated": false,
  "config_version": "20260415-143000",
  "restart_triggered": false
}
```

**错误响应**：

```json
{
  "status": "error",
  "message": "配置内容无效: YAML 解析失败"
}
```

**并发冲突响应**：

```json
{
  "status": "error",
  "message": "配置更新正在进行中，请稍后重试"
}
```

## 版本存储设计

### 存储位置

- 文件路径：`config/.config_version`（与 worker.yaml 同级目录）
- 文件内容：纯文本版本号（如 `20260415-143000`）

**Why**：下发的 worker.yaml 中不包含 config_version 字段，版本号通过接口参数传递，需要单独存储以便重启后读取。

**How to apply**：在配置更新时写入版本文件，在 Worker 启动时读取版本并上报。

### 版本文件路径函数

```python
def get_config_version_path() -> str:
    """获取配置版本文件路径。"""
    return os.path.join(_get_base_dir(), "config", ".config_version")
```

### 版本读取函数

```python
def load_config_version() -> Optional[str]:
    """从单独文件读取配置版本号。"""
    version_path = get_config_version_path()
    if os.path.exists(version_path):
        with open(version_path, encoding="utf-8") as f:
            return f.read().strip()
    return None
```

### 版本格式校验

版本号格式为 `YYYYMMDD-HHMMSS`，接口接收时进行校验：

```python
import re

def validate_config_version(version: str) -> bool:
    """校验版本号格式 YYYYMMDD-HHMMSS。"""
    pattern = r"^\d{8}-\d{6}$"
    return bool(re.match(pattern, version))
```

### 版本比较流程

```
1. 校验版本号格式（不符合返回 error）
2. 读取本地 config/.config_version（不存在则为空）
3. 比较 request.config_version 与本地版本
   - 相同 → 返回 {"updated": false, "restart_triggered": false}
   - 不同 → 继续更新流程
4. 更新成功后写入新版本到 .config_version
```

## 配置合并设计

### 合并策略

只保留本地 IP 地址，其他所有字段从下发配置更新。

**Why**：IP 地址可能是用户手动指定的特殊值（如内网特定 IP），不应该被远程配置覆盖。

**How to apply**：解析下发配置 YAML 后，读取本地配置中的 IP 字段值，替换到新配置中。

### 实现方案

```python
def merge_config_with_ip_protection(
    new_config_yaml: str,
    existing_config_path: str = get_user_config_path()
) -> dict:
    """
    合并配置：保留本地 IP 地址。

    Args:
        new_config_yaml: 新配置的 YAML 字符串
        existing_config_path: 现有配置文件路径，默认使用 get_user_config_path()

    Returns:
        dict: 合并后的配置数据
    """
    # 解析新配置
    new_data = yaml.safe_load(new_config_yaml) or {}

    # 读取现有配置的 IP
    if os.path.exists(existing_config_path):
        with open(existing_config_path, encoding="utf-8") as f:
            existing_data = yaml.safe_load(f) or {}
        existing_ip = existing_data.get("worker", {}).get("ip")
    else:
        existing_ip = None

    # 合并：保留本地 IP
    if existing_ip is not None and "worker" in new_data:
        new_data["worker"]["ip"] = existing_ip

    return new_data
```

### 合并示例

**现有配置 worker.yaml**：
```yaml
worker:
  ip: "192.168.1.100"  # 本地指定的 IP
  port: 8088
```

**下发配置 config_content**：
```yaml
worker:
  ip: null             # 下发的 IP（将被忽略）
  port: 8090           # 新端口（将更新）
  namespace: new_ns    # 新命名空间（将更新）
```

**合并结果**：
```yaml
worker:
  ip: "192.168.1.100"  # 保留本地 IP
  port: 8090           # 更新
  namespace: new_ns    # 更新
```

## 配置保存设计（事务保护）

### 问题场景

配置文件和版本文件分开写入可能导致状态不一致：
- 配置文件写入成功，版本文件写入失败 → 配置已更新但版本号未记录，重启后上报错误版本
- 两个请求同时写入 → 配置文件内容混乱

### 解决方案：备份回滚机制

```python
import shutil

def save_config_with_version(
    config_data: dict,
    version: str,
    config_path: str = get_user_config_path(),
    version_path: str = get_config_version_path()
) -> None:
    """
    安全保存配置和版本（带备份和回滚）。

    Args:
        config_data: 合并后的配置数据
        version: 新版本号
        config_path: 配置文件路径
        version_path: 版本文件路径

    Raises:
        OSError: 文件写入失败时抛出，自动回滚
    """
    config_yaml = yaml.dump(config_data, default_flow_style=False, allow_unicode=True)

    # 1. 备份现有配置
    backup_path = config_path + ".bak"
    if os.path.exists(config_path):
        shutil.copy2(config_path, backup_path)

    # 2. 写入新配置
    try:
        with open(config_path, "w", encoding="utf-8") as f:
            f.write(config_yaml)

        # 3. 写入版本文件
        with open(version_path, "w", encoding="utf-8") as f:
            f.write(version)

        # 4. 删除备份（成功后清理）
        if os.path.exists(backup_path):
            os.remove(backup_path)

        logger.info(f"Config saved: version={version}")

    except Exception as e:
        # 5. 回滚：恢复备份
        if os.path.exists(backup_path):
            shutil.copy2(backup_path, config_path)
            os.remove(backup_path)
        logger.error(f"Config save failed, rolled back: {e}")
        raise
```

## 重启触发设计

### 重启方式

复用现有托盘菜单重启机制（`_do_restart`），但无需用户确认。

**Why**：托盘重启机制已经成熟稳定，包含停止 Worker、重新加载配置、重新初始化日志、启动 Worker 的完整流程。

**How to apply**：通过 PyQt 信号机制触发重启，不显示确认对话框。

### 实现方案

在 `GUIApp` 类中新增信号和回调：

```python
# gui_main.py 中 UISignals 类新增
class UISignals(QObject):
    show_settings = pyqtSignal()
    show_restart_confirm = pyqtSignal()
    show_config_restart = pyqtSignal()    # 新增：配置更新后的重启信号
    show_upgrade = pyqtSignal()
    show_exit_confirm = pyqtSignal()

# 连接信号（直接调用 _do_restart，无需确认）
self.ui_signals.show_config_restart.connect(self._do_restart)
```

在 `server.py` 中新增 GUIApp 引用：

```python
# server.py
gui_app: GUIApp | None = None

def set_gui_app(app: GUIApp) -> None:
    global gui_app
    gui_app = app
```

### CLI 模式兼容

CLI 模式下（无 gui_app），通过子进程重启。

新增于 `worker/config.py`：

```python
import subprocess
import sys

def cli_restart():
    """CLI 模式重启：启动新进程并退出当前进程。"""
    # 获取当前启动命令
    executable = sys.executable
    args = sys.argv

    logger.info(f"CLI mode: restarting with args={args}")

    # 启动新进程（分离运行）
    subprocess.Popen([executable] + args)

    # 退出当前进程
    sys.exit(0)
```

### 重启提示说明

配置更新触发的重启复用 `_do_restart` 方法，重启完成后会显示成功提示对话框（仅提示，无需用户确认操作）。这是现有行为，符合用户体验预期。

### 重启触发时机

**关键点**：HTTP 响应必须在配置保存完成后返回，重启在响应返回后异步触发。

```python
@app.post("/worker/config")
async def update_worker_config(request: ConfigUpdateRequest):
    # 1. 版本校验
    # 2. 版本比较
    # 3. 配置合并
    # 4. 保存配置（含版本文件）← 必须在响应前完成
    # 5. 返回响应 ← 响应返回
    # 6. 触发重启 ← 响应返回后异步执行
```

### 重启失败处理

重启在响应返回后异步执行。如果重启失败：

- **GUI 模式**：`_do_restart` 会显示错误对话框，用户可以手动处理
- **CLI 模式**：进程可能终止，错误记录在日志中

调用方应通过后续的 `/worker_devices` 接口确认 Worker 状态和新的 `config_version`。

## 并发保护设计

### 问题场景

两个配置更新请求同时到达可能导致：
- 配置写入竞态（文件内容混乱）
- 多次重启触发

### 解决方案：互斥锁

使用 `threading.Lock` 配合 `blocking=False` 实现非阻塞检查（不阻塞事件循环）：

```python
import threading

# server.py 中添加
_config_update_lock = threading.Lock()
```

## 完整接口实现示例

以下是将并发保护、版本校验、配置保存、重启触发整合在一起的完整实现：

```python
# server.py 中新增

import re
import threading
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

# 并发锁
_config_update_lock = threading.Lock()


class ConfigUpdateRequest(BaseModel):
    """配置更新请求。"""
    config_content: str = Field(..., description="完整的 YAML 配置文件内容")
    config_version: str = Field(..., description="配置版本号，格式：YYYYMMDD-HHMMSS")


@app.post("/worker/config")
async def update_worker_config(request: ConfigUpdateRequest):
    """
    更新 Worker 配置。

    流程：
    1. 版本格式校验
    2. 并发保护（获取锁）
    3. 版本比较（相同则跳过）
    4. 配置合并（保留本地 IP）
    5. 保存配置（含版本文件）
    6. 返回响应
    7. 触发重启（异步）
    """
    if not worker:
        raise HTTPException(status_code=503, detail="Worker not initialized")

    # 1. 版本格式校验
    if not re.match(r"^\d{8}-\d{6}$", request.config_version):
        return JSONResponse(
            status_code=400,
            content={"status": "error", "message": "版本号格式无效，应为 YYYYMMDD-HHMMSS"}
        )

    # 2. 并发保护（非阻塞）
    if not _config_update_lock.acquire(blocking=False):
        return JSONResponse(
            status_code=409,
            content={"status": "error", "message": "配置更新正在进行中，请稍后重试"}
        )

    try:
        # 3. 版本比较
        from worker.config import load_config_version, merge_config_with_ip_protection, save_config_with_version

        local_version = load_config_version()
        if local_version == request.config_version:
            return {
                "status": "success",
                "message": "配置版本相同，无需更新",
                "updated": False,
                "config_version": request.config_version,
                "restart_triggered": False
            }

        # 4. 配置合并（保留本地 IP）
        try:
            merged_config = merge_config_with_ip_protection(request.config_content)
        except yaml.YAMLError as e:
            return JSONResponse(
                status_code=400,
                content={"status": "error", "message": f"配置内容无效: YAML 解析失败 - {e}"}
            )

        # 5. 保存配置（含版本文件）
        try:
            save_config_with_version(merged_config, request.config_version)
        except Exception as e:
            return JSONResponse(
                status_code=500,
                content={"status": "error", "message": f"配置保存失败: {e}"}
            )

        # 6. 返回响应
        response = {
            "status": "success",
            "message": "配置更新成功",
            "updated": True,
            "config_version": request.config_version,
            "restart_triggered": True
        }

        # 7. 触发重启（响应返回后执行）
        # 时序：启动后台线程 → return response（响应返回）→ finally 释放锁
        # 安全性：配置已在锁内完全保存，锁释放不影响数据一致性
        _trigger_restart_after_response()

        return response

    finally:
        _config_update_lock.release()


def _trigger_restart_after_response():
    """在响应返回后触发重启。"""
    import threading

    def _do_restart_async():
        import time
        # 等待一小段时间确保响应已返回
        time.sleep(0.5)

        if gui_app and gui_app.ui_signals:
            # GUI 模式：通过信号触发重启
            gui_app.ui_signals.show_config_restart.emit()
        else:
            # CLI 模式：通过子进程重启
            from worker.config import cli_restart
            cli_restart()

    # 启动后台线程执行重启
    threading.Thread(target=_do_restart_async, daemon=True).start()
```

## 注册接口上报设计

### Reporter.register_env() 修改

新增 `config_version` 参数：

```python
def register_env(
    self,
    ip: str,
    port: int,
    devices: Dict[str, List[str]],
    version: Optional[str] = None,
    config_version: Optional[str] = None,   # 新增参数
) -> bool:
    payload = {
        "ip": ip,
        "port": str(port),
        "namespace": self.namespace,
        "version": version,
        "devices": devices,
        "config_version": config_version,    # 新增字段
    }
```

### WorkerConfig 修改

新增 `config_version` 属性：

```python
@dataclass
class WorkerConfig:
    # ... 现有字段 ...
    config_version: Optional[str] = None    # 新增：配置版本号
```

**职责边界说明**：
- `from_yaml()` 方法：只读取 YAML 文件内容，不读取版本文件
- `load_config()` 方法：调用 `from_yaml()` 加载配置后，再调用 `load_config_version()` 加载版本号

### load_config() 修改

配置加载时从单独文件读取版本：

```python
def load_config() -> WorkerConfig:
    """加载 Worker 配置（含版本号）。"""
    user_config_path = get_user_config_path()
    default_template_path = get_default_template_path()

    # 优先读取用户配置
    if os.path.exists(user_config_path):
        config = WorkerConfig.from_yaml(user_config_path)
    elif os.path.exists(default_template_path):
        _copy_default_to_user_config(default_template_path, user_config_path)
        config = WorkerConfig.from_yaml(user_config_path)
    else:
        config = WorkerConfig()

    # 从单独文件读取版本号（不从 YAML 中读取）
    config.config_version = load_config_version()

    return config
```

### Worker._report_devices() 修改

上报时携带 config_version：

```python
def _report_devices(self) -> None:
    self.reporter.register_env(
        ip=devices_data["ip"],
        port=devices_data["port"],
        devices=devices_data["devices"],
        version=self._get_version(),
        config_version=self.config.config_version,   # 新增
    )
```

## 错误处理

| 场景 | HTTP 状态码 | 处理方式 |
|------|-------------|----------|
| 版本格式无效 | 400 | 返回 error，提示格式要求 |
| YAML 解析失败 | 400 | 返回 error，包含解析错误详情 |
| 配置文件写入失败 | 500 | 返回 error，自动回滚备份 |
| 版本文件写入失败 | 500 | 视为整体失败，触发回滚 |
| Worker 未初始化 | 503 | 与现有接口一致 |
| 并发请求冲突 | 409 | 返回 error，提示稍后重试 |

## 测试要点

1. 版本相同时不更新、不重启
2. 版本不同时更新配置、保存版本、触发重启
3. IP 地址保留本地值
4. 重启后 config_version 正确上报
5. YAML 解析失败时返回 400 错误
6. Worker 未初始化时返回 503
7. 并发请求返回 409
8. 配置写入失败时自动回滚
9. 版本格式无效时返回 400