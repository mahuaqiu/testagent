# 机器配置更新功能实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 实现 POST /worker/config 接口，支持远程下发配置并自动重启 Worker。

**Architecture:** 版本存储在单独文件 `.config_version`，配置合并时保留本地 IP，通过 PyQt 信号触发 GUI 重启，通过子进程触发 CLI 重启，注册接口新增 config_version 上报字段。

**Tech Stack:** Python 3.11, FastAPI, Pydantic, YAML, PyQt5, threading

---

## 文件清单

| 文件 | 操作 | 说明 |
|------|------|------|
| `worker/config.py` | 修改 | 新增版本存储、配置合并、CLI 重启函数 |
| `worker/server.py` | 修改 | 新增配置更新接口、并发锁、GUIApp 引用 |
| `worker/gui_main.py` | 修改 | 新增 show_config_restart 信号连接 |
| `worker/reporter/client.py` | 修改 | register_env 新增 config_version 参数 |
| `worker/worker.py` | 修改 | _report_devices 上报 config_version |
| `tests/test_config_update.py` | 创建 | 单元测试 |

---

### Task 1: 版本存储函数（worker/config.py）

**Files:**
- Modify: `worker/config.py` (新增函数)

- [ ] **Step 1: 添加版本存储相关函数**

在 `worker/config.py` 文件末尾添加以下函数：

```python
def get_config_version_path() -> str:
    """获取配置版本文件路径。"""
    return os.path.join(_get_base_dir(), "config", ".config_version")


def load_config_version() -> Optional[str]:
    """从单独文件读取配置版本号。"""
    version_path = get_config_version_path()
    if os.path.exists(version_path):
        with open(version_path, encoding="utf-8") as f:
            return f.read().strip()
    return None


def save_config_version(version: str) -> None:
    """保存配置版本号到单独文件。"""
    version_path = get_config_version_path()
    # 确保 config 目录存在
    os.makedirs(os.path.dirname(version_path), exist_ok=True)
    with open(version_path, "w", encoding="utf-8") as f:
        f.write(version)
```

- [ ] **Step 2: 添加配置合并函数**

继续在 `worker/config.py` 添加：

```python
def merge_config_with_ip_protection(
    new_config_yaml: str,
    existing_config_path: str = get_user_config_path()
) -> dict:
    """
    合并配置：保留本地 IP 地址。

    Args:
        new_config_yaml: 新配置的 YAML 字符串
        existing_config_path: 现有配置文件路径

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

- [ ] **Step 3: 添加安全保存函数（带备份回滚）**

继续添加：

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

    # 确保 config 目录存在
    os.makedirs(os.path.dirname(config_path), exist_ok=True)
    os.makedirs(os.path.dirname(version_path), exist_ok=True)

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

- [ ] **Step 4: 添加 CLI 重启函数**

继续添加：

```python
import subprocess

def cli_restart():
    """CLI 模式重启：启动新进程并退出当前进程。"""
    executable = sys.executable
    args = sys.argv

    logger.info(f"CLI mode: restarting with args={args}")

    # 启动新进程（分离运行）
    subprocess.Popen([executable] + args)

    # 退出当前进程
    sys.exit(0)
```

- [ ] **Step 5: 修改 load_config() 加载版本**

修改 `load_config()` 函数，在返回前加载版本号：

找到 `load_config()` 函数的返回语句，修改为：

```python
def load_config() -> WorkerConfig:
    """加载 Worker 配置（含版本号）。"""
    user_config_path = get_user_config_path()
    default_template_path = get_default_template_path()

    # 优先读取用户配置
    if os.path.exists(user_config_path):
        logger.info(f"Loading user config: {user_config_path}")
        config = WorkerConfig.from_yaml(user_config_path)
    elif os.path.exists(default_template_path):
        logger.info(f"User config not found, copying default template to: {user_config_path}")
        _copy_default_to_user_config(default_template_path, user_config_path)
        config = WorkerConfig.from_yaml(user_config_path)
    else:
        logger.warning("No config file found, using default WorkerConfig")
        config = WorkerConfig()

    # 从单独文件读取版本号
    config.config_version = load_config_version()

    return config
```

- [ ] **Step 6: 修改 WorkerConfig 新增 config_version 属性**

在 `WorkerConfig` dataclass 中添加新属性：

```python
@dataclass
class WorkerConfig:
    # ... 现有字段 ...
    
    # 配置版本号
    config_version: Optional[str] = None    # 新增：配置版本号
```

- [ ] **Step 7: Commit**

```bash
git add worker/config.py
git commit -m "feat(config): 新增版本存储、配置合并、CLI重启函数"
```

---

### Task 2: 配置更新接口（worker/server.py）

**Files:**
- Modify: `worker/server.py`

- [ ] **Step 1: 添加并发锁和导入**

在 `worker/server.py` 文件顶部导入区域添加：

```python
import re
import threading
import yaml

from worker.config import load_config_version, merge_config_with_ip_protection, save_config_with_version
```

在导入后添加全局变量：

```python
# 配置更新并发锁
_config_update_lock = threading.Lock()

# GUIApp 引用（用于触发重启）
gui_app: Any | None = None


def set_gui_app(app: Any) -> None:
    """设置 GUIApp 实例。"""
    global gui_app
    gui_app = app
```

- [ ] **Step 2: 添加请求模型**

在 Pydantic 模型定义区域添加：

```python
class ConfigUpdateRequest(BaseModel):
    """配置更新请求。"""
    config_content: str = Field(..., description="完整的 YAML 配置文件内容")
    config_version: str = Field(..., description="配置版本号，格式：YYYYMMDD-HHMMSS")
```

- [ ] **Step 3: 添加配置更新接口**

在现有接口后添加：

```python
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
        _trigger_restart_after_response()

        return response

    finally:
        _config_update_lock.release()


def _trigger_restart_after_response():
    """在响应返回后触发重启。"""
    import time

    def _do_restart_async():
        # 等待一小段时间确保响应已返回
        time.sleep(0.5)

        if gui_app and hasattr(gui_app, 'ui_signals') and gui_app.ui_signals:
            # GUI 模式：通过信号触发重启
            gui_app.ui_signals.show_config_restart.emit()
        else:
            # CLI 模式：通过子进程重启
            from worker.config import cli_restart
            cli_restart()

    # 启动后台线程执行重启
    threading.Thread(target=_do_restart_async, daemon=True).start()
```

需要在顶部添加 `import yaml` 如果未导入。

- [ ] **Step 4: Commit**

```bash
git add worker/server.py
git commit -m "feat(server): 新增 POST /worker/config 配置更新接口"
```

---

### Task 3: GUI 信号连接（worker/gui_main.py）

**Files:**
- Modify: `worker/gui_main.py`

- [ ] **Step 1: 修改 UISignals 类**

找到 `UISignals` 类定义，添加新信号：

```python
class UISignals(QObject):
    """UI 信号管理器，用于跨线程通信。"""

    show_settings = pyqtSignal()
    show_restart_confirm = pyqtSignal()
    show_config_restart = pyqtSignal()    # 新增：配置更新后的重启信号
    show_upgrade = pyqtSignal()
    show_exit_confirm = pyqtSignal()
```

- [ ] **Step 2: 连接新信号**

在 `GUIApp.__init__` 中找到信号连接部分，添加：

```python
self.ui_signals.show_config_restart.connect(self._do_restart)
```

- [ ] **Step 3: 调用 set_gui_app**

在 `GUIApp` 启动 Worker 后（`_start_worker` 方法或 `run` 方法中），添加：

```python
from worker.server import set_gui_app
set_gui_app(self)
```

可以在 `_start_worker` 方法的 `set_worker(self.worker)` 之后添加：

```python
set_worker(self.worker)
set_gui_app(self)  # 新增
```

- [ ] **Step 4: Commit**

```bash
git add worker/gui_main.py
git commit -m "feat(gui): 新增 show_config_restart 信号连接"
```

---

### Task 4: 注册接口上报（worker/reporter/client.py）

**Files:**
- Modify: `worker/reporter/client.py`

- [ ] **Step 1: 修改 register_env 方法签名**

找到 `register_env` 方法，添加 `config_version` 参数（带类型注解）：

```python
def register_env(
    self,
    ip: str,
    port: int,
    devices: Dict[str, List[str]],
    version: Optional[str] = None,
    config_version: Optional[str] = None,   # 新增参数（带类型注解）
) -> bool:
```

- [ ] **Step 2: 修改 payload**

在 payload 构建中添加 config_version：

```python
payload = {
    "ip": ip,
    "port": str(port),
    "namespace": self.namespace,
    "version": version,
    "devices": devices,
    "config_version": config_version,    # 新增字段
}
```

- [ ] **Step 3: Commit**

```bash
git add worker/reporter/client.py
git commit -m "feat(reporter): register_env 新增 config_version 参数"
```

---

### Task 5: Worker 上报修改（worker/worker.py）

**Files:**
- Modify: `worker/worker.py`

- [ ] **Step 1: 修改 _report_devices 方法**

找到 `_report_devices` 方法中的 `register_env` 调用，添加 `config_version`：

```python
self.reporter.register_env(
    ip=devices_data["ip"],
    port=devices_data["port"],
    devices=devices_data["devices"],
    version=self._get_version(),
    config_version=self.config.config_version,   # 新增（config_version 属性）
)
```

- [ ] **Step 2: Commit**

```bash
git add worker/worker.py
git commit -m "feat(worker): _report_devices 上报 config_version"
```

---

### Task 6: 单元测试

**Files:**
- Create: `tests/test_config_update.py`

- [ ] **Step 1: 创建测试文件**

```python
"""
配置更新功能测试。
"""

import os
import tempfile
import shutil
import yaml
from unittest.mock import patch, MagicMock

import pytest


class TestConfigVersion:
    """版本存储测试。"""

    def test_load_config_version_not_exists(self):
        """测试版本文件不存在时返回 None。"""
        with patch('worker.config.get_config_version_path', return_value='/nonexistent/path/.config_version'):
            from worker.config import load_config_version
            result = load_config_version()
            assert result is None

    def test_load_config_version_exists(self):
        """测试版本文件存在时正确读取。"""
        with tempfile.TemporaryDirectory() as tmpdir:
            version_path = os.path.join(tmpdir, ".config_version")
            version = "20260415-143000"

            # 创建版本文件
            os.makedirs(os.path.dirname(version_path), exist_ok=True)
            with open(version_path, "w") as f:
                f.write(version)

            with patch('worker.config.get_config_version_path', return_value=version_path):
                from worker.config import load_config_version
                result = load_config_version()
                assert result == version

    def test_save_config_version(self):
        """测试版本保存。"""
        with tempfile.TemporaryDirectory() as tmpdir:
            version_path = os.path.join(tmpdir, ".config_version")
            version = "20260415-150000"

            with patch('worker.config.get_config_version_path', return_value=version_path):
                from worker.config import save_config_version
                save_config_version(version)

                # 验证文件内容
                with open(version_path) as f:
                    result = f.read().strip()
                assert result == version


class TestConfigMerge:
    """配置合并测试。"""

    def test_merge_config_preserves_local_ip(self):
        """测试合并保留本地 IP。"""
        existing_yaml = """
worker:
  ip: "192.168.1.100"
  port: 8088
"""

        new_yaml = """
worker:
  ip: null
  port: 8090
  namespace: new_ns
"""

        with tempfile.TemporaryDirectory() as tmpdir:
            existing_path = os.path.join(tmpdir, "worker.yaml")
            with open(existing_path, "w") as f:
                f.write(existing_yaml)

            with patch('worker.config.get_user_config_path', return_value=existing_path):
                from worker.config import merge_config_with_ip_protection
                result = merge_config_with_ip_protection(new_yaml, existing_path)

                # 验证 IP 保留
                assert result["worker"]["ip"] == "192.168.1.100"
                assert result["worker"]["port"] == 8090
                assert result["worker"]["namespace"] == "new_ns"

    def test_merge_config_no_existing_ip(self):
        """测试现有配置无 IP 时不影响新配置。"""
        existing_yaml = """
worker:
  port: 8088
"""

        new_yaml = """
worker:
  ip: "192.168.2.1"
  port: 8090
"""

        with tempfile.TemporaryDirectory() as tmpdir:
            existing_path = os.path.join(tmpdir, "worker.yaml")
            with open(existing_path, "w") as f:
                f.write(existing_yaml)

            from worker.config import merge_config_with_ip_protection
            result = merge_config_with_ip_protection(new_yaml, existing_path)

            # 无本地 IP，使用新配置的 IP
            assert result["worker"]["ip"] == "192.168.2.1"


class TestConfigSaveWithVersion:
    """配置保存测试（带事务保护）。"""

    def test_save_config_success(self):
        """测试配置成功保存。"""
        config_data = {"worker": {"port": 8090}}
        version = "20260415-143000"

        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = os.path.join(tmpdir, "worker.yaml")
            version_path = os.path.join(tmpdir, ".config_version")

            with patch('worker.config.get_user_config_path', return_value=config_path):
                with patch('worker.config.get_config_version_path', return_value=version_path):
                    from worker.config import save_config_with_version
                    save_config_with_version(config_data, version)

                    # 验证配置文件
                    with open(config_path) as f:
                        saved_config = yaml.safe_load(f)
                    assert saved_config["worker"]["port"] == 8090

                    # 验证版本文件
                    with open(version_path) as f:
                        saved_version = f.read().strip()
                    assert saved_version == version

    def test_save_config_rollback_on_failure(self):
        """测试配置保存失败时回滚。"""
        initial_config = {"worker": {"port": 8088}}
        new_config = {"worker": {"port": 8090}}
        version = "20260415-143000"

        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = os.path.join(tmpdir, "worker.yaml")
            version_path = os.path.join(tmpdir, ".config_version")

            # 创建初始配置
            with open(config_path, "w") as f:
                yaml.dump(initial_config, f)

            with patch('worker.config.get_user_config_path', return_value=config_path):
                with patch('worker.config.get_config_version_path', return_value=version_path):
                    # 模拟写入版本文件时失败
                    original_open = open
                    def mock_open(path, *args, **kwargs):
                        if path == version_path and args[0] == "w":
                            raise OSError("Mock write failure")
                        return original_open(path, *args, **kwargs)

                    with patch('builtins.open', side_effect=mock_open):
                        from worker.config import save_config_with_version
                        with pytest.raises(OSError):
                            save_config_with_version(new_config, version)

                    # 验证配置已回滚
                    with open(config_path) as f:
                        rolled_back_config = yaml.safe_load(f)
                    assert rolled_back_config["worker"]["port"] == 8088
```

- [ ] **Step 2: 运行测试**

```bash
pytest tests/test_config_update.py -v
```

- [ ] **Step 3: Commit**

```bash
git add tests/test_config_update.py
git commit -m "test: 新增配置更新功能单元测试"
```

---

### Task 7: 集成测试与最终提交

- [ ] **Step 1: 启动 Worker 测试接口**

启动 Worker 后使用 curl 测试：

```bash
# 测试版本相同
curl -X POST http://localhost:8088/worker/config \
  -H "Content-Type: application/json" \
  -d '{"config_content": "# test", "config_version": "20260415-143000"}'

# 测试版本不同
curl -X POST http://localhost:8088/worker/config \
  -H "Content-Type: application/json" \
  -d '{"config_content": "# Worker Configuration\nworker:\n  port: 8090\n", "config_version": "20260415-150000"}'
```

- [ ] **Step 2: 检查版本文件**

```bash
cat config/.config_version
```

- [ ] **Step 3: 最终提交**

```bash
git add -A
git commit -m "feat: 完成机器配置更新功能实现"
```

---

## 执行顺序

1. Task 1 (config.py) → 基础函数
2. Task 2 (server.py) → 接口实现
3. Task 3 (gui_main.py) → GUI 信号
4. Task 4 (reporter/client.py) → 上报参数
5. Task 5 (worker.py) → Worker 上报
6. Task 6 (测试) → 单元测试
7. Task 7 → 集成测试

依赖关系：Task 1 是基础，Task 2-5 依赖 Task 1，Task 6-7 依赖所有实现任务。