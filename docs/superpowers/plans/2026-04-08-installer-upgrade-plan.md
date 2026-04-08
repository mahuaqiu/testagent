# Worker 安装包与远程升级系统实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 实现 Worker 安装包制作和远程升级功能，支持图形界面安装、命令行静默安装、以及平台下发升级指令自动更新。

**Architecture:** 升级模块独立于 Worker 核心逻辑，通过 HTTP 接口 `/worker/upgrade` 接收升级指令。安装包使用 Inno Setup 制作，支持配置参数传入和升级时配置保留。

**Tech Stack:** Python 3.x, FastAPI, Pydantic, httpx, Inno Setup 6

---

## 文件结构

```
新增文件：
worker/upgrade/
├── __init__.py          # 模块导出
├── models.py            # 升级请求/响应模型
├── state.py             # 升级状态管理
├── downloader.py        # 安装包下载
├── installer.py         # 静默安装执行
└── handler.py           # HTTP 接口处理

installer/
├── installer.iss        # Inno Setup 脚本
├── build_installer.ps1  # 构建脚本

tests/upgrade/
├── __init__.py
├── test_models.py       # 模型测试
├── test_state.py        # 状态管理测试
├── test_downloader.py   # 下载器测试
├── test_installer.py    # 安装器测试
├── test_handler.py      # 处理器测试

修改文件：
worker/server.py         # 添加 /worker/upgrade 路由
scripts/build_windows.ps1 # 集成安装包构建
```

---

## Task 1: 升级模块数据模型

**Files:**
- Create: `worker/upgrade/__init__.py`
- Create: `worker/upgrade/models.py`
- Test: `tests/upgrade/test_models.py`

- [ ] **Step 1: 创建模块目录和初始化文件**

```bash
mkdir -p worker/upgrade tests/upgrade
touch worker/upgrade/__init__.py tests/upgrade/__init__.py
```

- [ ] **Step 2: 编写 models.py**

```python
# worker/upgrade/models.py
"""
升级模块数据模型。
"""

from dataclasses import dataclass
from typing import Optional
from enum import Enum


class UpgradeStatus(Enum):
    """升级状态。"""
    SKIPPED = "skipped"           # 无需升级
    DOWNLOADING = "downloading"   # 正在下载
    INSTALLING = "installing"     # 正在安装
    COMPLETED = "completed"       # 升级完成
    FAILED = "failed"             # 升级失败


@dataclass
class UpgradeRequest:
    """升级请求。"""
    version: Optional[str] = None           # 目标版本号
    download_url: str                       # 安装包下载地址
    force: bool = True                      # 是否强制升级


@dataclass
class UpgradeResponse:
    """升级响应。"""
    status: str
    message: str
    current_version: Optional[str] = None
    target_version: Optional[str] = None

    def to_dict(self) -> dict:
        return {
            "status": self.status,
            "message": self.message,
            "current_version": self.current_version,
            "target_version": self.target_version,
        }


@dataclass
class UpgradeState:
    """升级状态（持久化）。"""
    status: str
    target_version: str
    current_version: str
    download_url: str
    started_at: str
    completed_at: Optional[str] = None
    error: Optional[str] = None

    def to_dict(self) -> dict:
        return {
            "status": self.status,
            "target_version": self.target_version,
            "current_version": self.current_version,
            "download_url": self.download_url,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
            "error": self.error,
        }
```

- [ ] **Step 3: 编写测试文件**

```python
# tests/upgrade/test_models.py
"""
升级模块数据模型测试。
"""

import pytest
from worker.upgrade.models import (
    UpgradeStatus,
    UpgradeRequest,
    UpgradeResponse,
    UpgradeState,
)


def test_upgrade_status_enum():
    """测试升级状态枚举值。"""
    assert UpgradeStatus.SKIPPED.value == "skipped"
    assert UpgradeStatus.DOWNLOADING.value == "downloading"
    assert UpgradeStatus.INSTALLING.value == "installing"
    assert UpgradeStatus.COMPLETED.value == "completed"
    assert UpgradeStatus.FAILED.value == "failed"


def test_upgrade_request_defaults():
    """测试升级请求默认值。"""
    request = UpgradeRequest(download_url="http://example.com/installer.exe")
    assert request.version is None
    assert request.download_url == "http://example.com/installer.exe"
    assert request.force is True


def test_upgrade_request_with_version():
    """测试升级请求带版本号。"""
    request = UpgradeRequest(
        version="20260408150000",
        download_url="http://example.com/installer.exe",
        force=False,
    )
    assert request.version == "20260408150000"
    assert request.force is False


def test_upgrade_response_to_dict():
    """测试升级响应序列化。"""
    response = UpgradeResponse(
        status="skipped",
        message="当前版本已是最新",
        current_version="20260408150000",
        target_version="20260408150000",
    )
    result = response.to_dict()
    assert result["status"] == "skipped"
    assert result["message"] == "当前版本已是最新"
    assert result["current_version"] == "20260408150000"
    assert result["target_version"] == "20260408150000"


def test_upgrade_state_to_dict():
    """测试升级状态序列化。"""
    state = UpgradeState(
        status="downloading",
        target_version="20260408150000",
        current_version="20260405120000",
        download_url="http://example.com/installer.exe",
        started_at="2026-04-08T15:00:00",
    )
    result = state.to_dict()
    assert result["status"] == "downloading"
    assert result["target_version"] == "20260408150000"
    assert result["current_version"] == "20260405120000"
    assert result["download_url"] == "http://example.com/installer.exe"
    assert result["started_at"] == "2026-04-08T15:00:00"
    assert result["completed_at"] is None
    assert result["error"] is None
```

- [ ] **Step 4: 运行测试**

```bash
pytest tests/upgrade/test_models.py -v
```

Expected: 所有测试通过

- [ ] **Step 5: 提交**

```bash
git add worker/upgrade/__init__.py worker/upgrade/models.py tests/upgrade/
git commit -m "feat(upgrade): 新增升级模块数据模型"
```

---

## Task 2: 升级状态管理

**Files:**
- Create: `worker/upgrade/state.py`
- Test: `tests/upgrade/test_state.py`

- [ ] **Step 1: 编写 state.py**

```python
# worker/upgrade/state.py
"""
升级状态管理。

负责升级状态的持久化和读取。
"""

import json
import os
import sys
import logging
from typing import Optional
from worker.upgrade.models import UpgradeState

logger = logging.getLogger(__name__)

# 状态文件名
STATE_FILE = "upgrade.json"


def get_state_file_path() -> str:
    """
    获取状态文件路径。

    状态文件存储在 Worker 安装目录。

    Returns:
        str: 状态文件完整路径
    """
    if getattr(sys, 'frozen', False):
        # PyInstaller 打包后，使用 exe 所在目录
        base_dir = os.path.dirname(sys.executable)
    else:
        # 开发模式，使用项目根目录
        base_dir = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
    return os.path.join(base_dir, STATE_FILE)


def save_state(state: UpgradeState) -> None:
    """
    保存升级状态到文件。

    Args:
        state: 升级状态对象
    """
    path = get_state_file_path()
    try:
        with open(path, 'w', encoding='utf-8') as f:
            json.dump(state.to_dict(), f, ensure_ascii=False, indent=2)
        logger.info(f"升级状态已保存: {path}")
    except Exception as e:
        logger.warning(f"保存升级状态失败: {e}")


def load_state() -> Optional[UpgradeState]:
    """
    从文件加载升级状态。

    Returns:
        UpgradeState | None: 升级状态对象，不存在或读取失败返回 None
    """
    path = get_state_file_path()
    try:
        if os.path.exists(path):
            with open(path, 'r', encoding='utf-8') as f:
                data = json.load(f)
            return UpgradeState(**data)
    except Exception as e:
        logger.warning(f"加载升级状态失败: {e}")
    return None


def clear_state() -> None:
    """
    清除升级状态文件。
    """
    path = get_state_file_path()
    try:
        if os.path.exists(path):
            os.remove(path)
            logger.info(f"升级状态文件已清除: {path}")
    except Exception as e:
        logger.warning(f"清除升级状态文件失败: {e}")
```

- [ ] **Step 2: 编写测试**

```python
# tests/upgrade/test_state.py
"""
升级状态管理测试。
"""

import os
import json
import tempfile
import pytest
from unittest.mock import patch, MagicMock

from worker.upgrade.state import (
    get_state_file_path,
    save_state,
    load_state,
    clear_state,
)
from worker.upgrade.models import UpgradeState


class TestStateFilePath:
    """测试状态文件路径获取。"""

    def test_get_state_file_path_frozen(self):
        """测试打包后路径获取。"""
        with patch('sys.frozen', True):
            with patch('sys.executable', '/path/to/test-worker.exe'):
                path = get_state_file_path()
                assert path == '/path/to/upgrade.json'

    def test_get_state_file_path_development(self):
        """测试开发模式路径获取。"""
        # sys.frozen 默认不存在
        path = get_state_file_path()
        assert path.endswith('upgrade.json')


class TestSaveLoadState:
    """测试状态保存和加载。"""

    def test_save_and_load_state(self, tmp_path):
        """测试状态保存和加载完整流程。"""
        # 使用临时目录
        state_file = tmp_path / "upgrade.json"

        with patch('worker.upgrade.state.get_state_file_path', return_value=str(state_file)):
            # 创建状态
            state = UpgradeState(
                status="downloading",
                target_version="20260408150000",
                current_version="20260405120000",
                download_url="http://example.com/installer.exe",
                started_at="2026-04-08T15:00:00",
            )

            # 保存
            save_state(state)

            # 验证文件存在
            assert state_file.exists()

            # 加载
            loaded = load_state()
            assert loaded is not None
            assert loaded.status == "downloading"
            assert loaded.target_version == "20260408150000"
            assert loaded.current_version == "20260405120000"

    def test_load_state_not_exists(self, tmp_path):
        """测试加载不存在的状态文件。"""
        state_file = tmp_path / "upgrade.json"

        with patch('worker.upgrade.state.get_state_file_path', return_value=str(state_file)):
            loaded = load_state()
            assert loaded is None

    def test_load_state_invalid_json(self, tmp_path):
        """测试加载无效 JSON 文件。"""
        state_file = tmp_path / "upgrade.json"
        state_file.write_text("invalid json content")

        with patch('worker.upgrade.state.get_state_file_path', return_value=str(state_file)):
            loaded = load_state()
            assert loaded is None


class TestClearState:
    """测试状态清除。"""

    def test_clear_state_existing(self, tmp_path):
        """测试清除存在的状态文件。"""
        state_file = tmp_path / "upgrade.json"
        state_file.write_text("{}")

        with patch('worker.upgrade.state.get_state_file_path', return_value=str(state_file)):
            clear_state()
            assert not state_file.exists()

    def test_clear_state_not_exists(self, tmp_path):
        """测试清除不存在的状态文件（无错误）。"""
        state_file = tmp_path / "upgrade.json"

        with patch('worker.upgrade.state.get_state_file_path', return_value=str(state_file)):
            clear_state()  # 应该不报错
            assert not state_file.exists()
```

- [ ] **Step 3: 运行测试**

```bash
pytest tests/upgrade/test_state.py -v
```

Expected: 所有测试通过

- [ ] **Step 4: 提交**

```bash
git add worker/upgrade/state.py tests/upgrade/test_state.py
git commit -m "feat(upgrade): 新增升级状态管理模块"
```

---

## Task 3: 安装包下载器

**Files:**
- Create: `worker/upgrade/downloader.py`
- Test: `tests/upgrade/test_downloader.py`

- [ ] **Step 1: 编写 downloader.py**

```python
# worker/upgrade/downloader.py
"""
安装包下载模块。

负责从远程下载升级安装包。
"""

import os
import sys
import logging
import httpx
from typing import Optional

logger = logging.getLogger(__name__)

# 临时目录名
TEMP_DIR = "temp"
INSTALLER_FILENAME = "installer.exe"


def get_temp_dir() -> str:
    """
    获取临时目录路径。

    Returns:
        str: 临时目录完整路径
    """
    if getattr(sys, 'frozen', False):
        base_dir = os.path.dirname(sys.executable)
    else:
        base_dir = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
    temp_dir = os.path.join(base_dir, TEMP_DIR)
    os.makedirs(temp_dir, exist_ok=True)
    return temp_dir


def download_installer(url: str, expected_size: Optional[int] = None) -> str:
    """
    下载安装包。

    Args:
        url: 安装包下载地址
        expected_size: 预期文件大小（字节），用于校验，可选

    Returns:
        str: 安装包本地路径

    Raises:
        DownloadError: 下载失败
    """
    temp_dir = get_temp_dir()
    installer_path = os.path.join(temp_dir, INSTALLER_FILENAME)

    logger.info(f"开始下载安装包: {url}")
    logger.info(f"目标路径: {installer_path}")

    try:
        with httpx.Client(timeout=300.0, trust_env=False, follow_redirects=True) as client:
            response = client.get(url)
            response.raise_for_status()

            # 写入文件
            with open(installer_path, 'wb') as f:
                f.write(response.content)

            # 校验文件大小
            actual_size = os.path.getsize(installer_path)
            logger.info(f"下载完成，文件大小: {actual_size} bytes")

            if expected_size and actual_size != expected_size:
                os.remove(installer_path)
                raise DownloadError(
                    f"文件大小不匹配: 预期 {expected_size}, 实际 {actual_size}"
                )

            return installer_path

    except httpx.HTTPStatusError as e:
        raise DownloadError(f"下载失败 (HTTP {e.response.status_code}): {e}")
    except httpx.RequestError as e:
        raise DownloadError(f"下载请求失败: {e}")
    except Exception as e:
        raise DownloadError(f"下载失败: {e}")


class DownloadError(Exception):
    """下载错误。"""
    pass
```

- [ ] **Step 2: 编写测试**

```python
# tests/upgrade/test_downloader.py
"""
安装包下载器测试。
"""

import os
import pytest
from unittest.mock import patch, MagicMock
import httpx

from worker.upgrade.downloader import (
    get_temp_dir,
    download_installer,
    DownloadError,
)


class TestGetTempDir:
    """测试临时目录获取。"""

    def test_get_temp_dir_frozen(self, tmp_path):
        """测试打包后临时目录。"""
        with patch('sys.frozen', True):
            with patch('sys.executable', str(tmp_path / "test-worker.exe")):
                temp_dir = get_temp_dir()
                assert temp_dir.endswith("temp")
                assert os.path.exists(temp_dir)

    def test_get_temp_dir_development(self):
        """测试开发模式临时目录。"""
        temp_dir = get_temp_dir()
        assert temp_dir.endswith("temp")
        assert os.path.exists(temp_dir)


class TestDownloadInstaller:
    """测试安装包下载。"""

    def test_download_success(self, tmp_path):
        """测试成功下载。"""
        # 模拟 httpx 客户端
        mock_response = MagicMock()
        mock_response.content = b"fake installer content"
        mock_response.raise_for_status = MagicMock()

        mock_client = MagicMock()
        mock_client.get.return_value = mock_response
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)

        temp_dir = tmp_path / "temp"
        temp_dir.mkdir()

        with patch('worker.upgrade.downloader.get_temp_dir', return_value=str(temp_dir)):
            with patch('httpx.Client', return_value=mock_client):
                result = download_installer("http://example.com/installer.exe")
                assert result == str(temp_dir / "installer.exe")
                assert os.path.exists(result)

    def test_download_with_size_validation(self, tmp_path):
        """测试带大小校验的下载。"""
        content = b"fake installer content"
        expected_size = len(content)

        mock_response = MagicMock()
        mock_response.content = content
        mock_response.raise_for_status = MagicMock()

        mock_client = MagicMock()
        mock_client.get.return_value = mock_response
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)

        temp_dir = tmp_path / "temp"
        temp_dir.mkdir()

        with patch('worker.upgrade.downloader.get_temp_dir', return_value=str(temp_dir)):
            with patch('httpx.Client', return_value=mock_client):
                result = download_installer(
                    "http://example.com/installer.exe",
                    expected_size=expected_size
                )
                assert os.path.exists(result)

    def test_download_size_mismatch(self, tmp_path):
        """测试文件大小不匹配。"""
        content = b"fake installer content"

        mock_response = MagicMock()
        mock_response.content = content
        mock_response.raise_for_status = MagicMock()

        mock_client = MagicMock()
        mock_client.get.return_value = mock_response
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)

        temp_dir = tmp_path / "temp"
        temp_dir.mkdir()

        with patch('worker.upgrade.downloader.get_temp_dir', return_value=str(temp_dir)):
            with patch('httpx.Client', return_value=mock_client):
                with pytest.raises(DownloadError, match="文件大小不匹配"):
                    download_installer(
                        "http://example.com/installer.exe",
                        expected_size=100  # 错误的预期大小
                    )

    def test_download_http_error(self, tmp_path):
        """测试 HTTP 错误。"""
        mock_response = MagicMock()
        mock_response.status_code = 404
        http_error = httpx.HTTPStatusError(
            "Not Found",
            request=MagicMock(),
            response=mock_response
        )
        mock_response.raise_for_status.side_effect = http_error

        mock_client = MagicMock()
        mock_client.get.return_value = mock_response
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)

        temp_dir = tmp_path / "temp"
        temp_dir.mkdir()

        with patch('worker.upgrade.downloader.get_temp_dir', return_value=str(temp_dir)):
            with patch('httpx.Client', return_value=mock_client):
                with pytest.raises(DownloadError, match="HTTP 404"):
                    download_installer("http://example.com/installer.exe")

    def test_download_request_error(self, tmp_path):
        """测试请求错误（网络问题）。"""
        mock_client = MagicMock()
        mock_client.get.side_effect = httpx.RequestError("Connection failed")
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)

        temp_dir = tmp_path / "temp"
        temp_dir.mkdir()

        with patch('worker.upgrade.downloader.get_temp_dir', return_value=str(temp_dir)):
            with patch('httpx.Client', return_value=mock_client):
                with pytest.raises(DownloadError, match="下载请求失败"):
                    download_installer("http://example.com/installer.exe")
```

- [ ] **Step 3: 运行测试**

```bash
pytest tests/upgrade/test_downloader.py -v
```

Expected: 所有测试通过

- [ ] **Step 4: 提交**

```bash
git add worker/upgrade/downloader.py tests/upgrade/test_downloader.py
git commit -m "feat(upgrade): 新增安装包下载模块"
```

---

## Task 4: 静默安装执行器

**Files:**
- Create: `worker/upgrade/installer.py`
- Test: `tests/upgrade/test_installer.py`

- [ ] **Step 1: 编写 installer.py**

```python
# worker/upgrade/installer.py
"""
静默安装执行模块。

负责启动 Inno Setup 静默安装进程。
"""

import os
import sys
import subprocess
import logging
from typing import Optional

logger = logging.getLogger(__name__)


def get_current_install_dir() -> str:
    """
    获取当前安装目录。

    Returns:
        str: 当前安装目录路径
    """
    if getattr(sys, 'frozen', False):
        return os.path.dirname(sys.executable)
    else:
        # 开发模式，返回模拟路径
        return os.path.dirname(os.path.dirname(os.path.dirname(__file__)))


def run_silent_install(installer_path: str, install_dir: Optional[str] = None) -> None:
    """
    执行静默安装。

    启动 Inno Setup 静默安装进程后立即返回，不等待安装完成。

    Args:
        installer_path: 安装包路径
        install_dir: 安装目录（可选，默认使用当前目录）

    Raises:
        InstallError: 安装包不存在或启动失败
    """
    if not os.path.exists(installer_path):
        raise InstallError(f"安装包不存在: {installer_path}")

    # 获取安装目录
    if install_dir is None:
        install_dir = get_current_install_dir()

    # 构建静默安装命令
    # /VERYSILENT - 完全静默，无任何界面
    # /SUPPRESSMSGBOXES - 抑制消息框
    # /NORESTART - 不自动重启系统
    cmd = [
        installer_path,
        "/VERYSILENT",
        "/SUPPRESSMSGBOXES",
        "/NORESTART",
        f'/DIR="{install_dir}"',
    ]

    logger.info(f"启动静默安装: {' '.join(cmd)}")

    try:
        # 启动安装进程（后台运行，不等待）
        subprocess.Popen(
            cmd,
            shell=True,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        logger.info("静默安装进程已启动")

    except Exception as e:
        raise InstallError(f"启动安装失败: {e}")


class InstallError(Exception):
    """安装错误。"""
    pass
```

- [ ] **Step 2: 编写测试**

```python
# tests/upgrade/test_installer.py
"""
静默安装执行器测试。
"""

import os
import pytest
from unittest.mock import patch, MagicMock

from worker.upgrade.installer import (
    get_current_install_dir,
    run_silent_install,
    InstallError,
)


class TestGetCurrentInstallDir:
    """测试获取当前安装目录。"""

    def test_get_install_dir_frozen(self, tmp_path):
        """测试打包后安装目录。"""
        exe_path = tmp_path / "test-worker.exe"
        exe_path.touch()

        with patch('sys.frozen', True):
            with patch('sys.executable', str(exe_path)):
                result = get_current_install_dir()
                assert result == str(tmp_path)

    def test_get_install_dir_development(self):
        """测试开发模式安装目录。"""
        result = get_current_install_dir()
        # 应返回项目根目录或其父目录
        assert os.path.exists(result)


class TestRunSilentInstall:
    """测试静默安装执行。"""

    def test_run_silent_install_success(self, tmp_path):
        """测试成功启动静默安装。"""
        installer_path = tmp_path / "installer.exe"
        installer_path.touch()

        mock_popen = MagicMock()

        with patch('worker.upgrade.installer.get_current_install_dir', return_value=str(tmp_path)):
            with patch('subprocess.Popen', mock_popen):
                run_silent_install(str(installer_path))
                # 验证 Popen 被调用
                mock_popen.assert_called_once()
                # 验证命令参数
                call_args = mock_popen.call_args[0][0]
                assert str(installer_path) in call_args
                assert "/VERYSILENT" in call_args
                assert "/SUPPRESSMSGBOXES" in call_args

    def test_run_silent_install_with_custom_dir(self, tmp_path):
        """测试指定安装目录的静默安装。"""
        installer_path = tmp_path / "installer.exe"
        installer_path.touch()
        custom_dir = tmp_path / "custom"

        mock_popen = MagicMock()

        with patch('subprocess.Popen', mock_popen):
            run_silent_install(str(installer_path), str(custom_dir))
            call_args = mock_popen.call_args[0][0]
            assert str(custom_dir) in str(call_args)

    def test_run_silent_install_installer_not_exists(self):
        """测试安装包不存在。"""
        with pytest.raises(InstallError, match="安装包不存在"):
            run_silent_install("/nonexistent/installer.exe")

    def test_run_silent_install_popen_error(self, tmp_path):
        """测试启动进程失败。"""
        installer_path = tmp_path / "installer.exe"
        installer_path.touch()

        with patch('subprocess.Popen', side_effect=OSError("Process failed")):
            with pytest.raises(InstallError, match="启动安装失败"):
                run_silent_install(str(installer_path))
```

- [ ] **Step 3: 运行测试**

```bash
pytest tests/upgrade/test_installer.py -v
```

Expected: 所有测试通过

- [ ] **Step 4: 提交**

```bash
git add worker/upgrade/installer.py tests/upgrade/test_installer.py
git commit -m "feat(upgrade): 新增静默安装执行模块"
```

---

## Task 5: 升级 HTTP 接口处理

**Files:**
- Create: `worker/upgrade/handler.py`
- Test: `tests/upgrade/test_handler.py`

- [ ] **Step 1: 编写 handler.py**

```python
# worker/upgrade/handler.py
"""
升级 HTTP 接口处理。

负责处理 /worker/upgrade 接口请求。
"""

import sys
import logging
from datetime import datetime
from typing import Optional

from worker.upgrade.models import (
    UpgradeRequest,
    UpgradeResponse,
    UpgradeState,
)
from worker.upgrade.downloader import download_installer, DownloadError
from worker.upgrade.installer import run_silent_install, InstallError
from worker.upgrade.state import save_state

logger = logging.getLogger(__name__)


def get_current_version() -> Optional[str]:
    """
    获取当前版本号。

    Returns:
        str | None: 版本号，非 EXE 运行时返回 None
    """
    try:
        from worker._version import VERSION
        return VERSION
    except ImportError:
        return None


async def handle_upgrade(request: UpgradeRequest) -> UpgradeResponse:
    """
    处理升级请求。

    Args:
        request: 升级请求对象

    Returns:
        UpgradeResponse: 升级响应

    Raises:
        UpgradeError: 升级过程中发生错误
    """
    current_version = get_current_version()
    target_version = request.version

    # 1. 版本校验：版本一致则无需升级
    if target_version and target_version == current_version:
        logger.info(f"版本一致，无需升级: {current_version}")
        return UpgradeResponse(
            status="skipped",
            message="当前版本已是最新，无需升级",
            current_version=current_version,
            target_version=target_version,
        )

    # 2. 记录升级状态
    state = UpgradeState(
        status="downloading",
        target_version=target_version or "unknown",
        current_version=current_version or "unknown",
        download_url=request.download_url,
        started_at=datetime.now().isoformat(),
    )
    save_state(state)

    logger.info(f"开始升级: {current_version} → {target_version}")

    try:
        # 3. 下载安装包
        state.status = "downloading"
        save_state(state)

        installer_path = download_installer(request.download_url)

        # 4. 启动静默安装
        state.status = "installing"
        save_state(state)

        run_silent_install(installer_path)

        # 5. 返回响应后 Worker 立即退出
        logger.info("升级安装已启动，Worker 即将退出")

        # 注意：实际退出逻辑在调用方处理（sys.exit(0)）
        return UpgradeResponse(
            status="upgrading",
            message="Worker 正在升级，预计 30 秒后恢复",
            current_version=current_version,
            target_version=target_version,
        )

    except DownloadError as e:
        state.status = "failed"
        state.error = str(e)
        save_state(state)
        raise UpgradeError(f"下载失败: {e}")

    except InstallError as e:
        state.status = "failed"
        state.error = str(e)
        save_state(state)
        raise UpgradeError(f"安装失败: {e}")

    except Exception as e:
        state.status = "failed"
        state.error = str(e)
        save_state(state)
        raise UpgradeError(f"升级失败: {e}")


class UpgradeError(Exception):
    """升级错误。"""
    pass
```

- [ ] **Step 2: 编写测试**

```python
# tests/upgrade/test_handler.py
"""
升级 HTTP 接口处理测试。
"""

import pytest
from unittest.mock import patch, MagicMock, AsyncMock
from datetime import datetime

from worker.upgrade.handler import (
    get_current_version,
    handle_upgrade,
    UpgradeError,
)
from worker.upgrade.models import UpgradeRequest, UpgradeResponse


class TestGetCurrentVersion:
    """测试获取当前版本。"""

    def test_get_version_exists(self):
        """测试版本模块存在。"""
        mock_version = MagicMock()
        mock_version.VERSION = "20260408150000"

        with patch.dict('sys.modules', {'worker._version': mock_version}):
            result = get_current_version()
            assert result == "20260408150000"

    def test_get_version_not_exists(self):
        """测试版本模块不存在。"""
        with patch.dict('sys.modules', {}, clear=True):
            # ImportError 会被捕获
            result = get_current_version()
            assert result is None


class TestHandleUpgrade:
    """测试升级请求处理。"""

    @pytest.mark.asyncio
    async def test_version_skipped(self):
        """测试版本一致，跳过升级。"""
        request = UpgradeRequest(
            version="20260408150000",
            download_url="http://example.com/installer.exe",
        )

        with patch('worker.upgrade.handler.get_current_version', return_value="20260408150000"):
            result = await handle_upgrade(request)
            assert result.status == "skipped"
            assert "无需升级" in result.message
            assert result.current_version == "20260408150000"
            assert result.target_version == "20260408150000"

    @pytest.mark.asyncio
    async def test_upgrade_success(self, tmp_path):
        """测试升级成功流程。"""
        request = UpgradeRequest(
            version="20260408150000",
            download_url="http://example.com/installer.exe",
        )

        state_file = tmp_path / "upgrade.json"

        with patch('worker.upgrade.handler.get_current_version', return_value="20260405120000"):
            with patch('worker.upgrade.state.get_state_file_path', return_value=str(state_file)):
                with patch('worker.upgrade.downloader.download_installer', return_value=str(tmp_path / "installer.exe")):
                    with patch('worker.upgrade.installer.run_silent_install'):
                        result = await handle_upgrade(request)
                        assert result.status == "upgrading"
                        assert "正在升级" in result.message

    @pytest.mark.asyncio
    async def test_upgrade_download_error(self, tmp_path):
        """测试下载失败。"""
        request = UpgradeRequest(
            version="20260408150000",
            download_url="http://example.com/installer.exe",
        )

        state_file = tmp_path / "upgrade.json"

        from worker.upgrade.downloader import DownloadError

        with patch('worker.upgrade.handler.get_current_version', return_value="20260405120000"):
            with patch('worker.upgrade.state.get_state_file_path', return_value=str(state_file)):
                with patch('worker.upgrade.downloader.download_installer', side_effect=DownloadError("网络错误")):
                    with pytest.raises(UpgradeError, match="下载失败"):
                        await handle_upgrade(request)

    @pytest.mark.asyncio
    async def test_upgrade_install_error(self, tmp_path):
        """测试安装失败。"""
        request = UpgradeRequest(
            version="20260408150000",
            download_url="http://example.com/installer.exe",
        )

        state_file = tmp_path / "upgrade.json"

        from worker.upgrade.installer import InstallError

        with patch('worker.upgrade.handler.get_current_version', return_value="20260405120000"):
            with patch('worker.upgrade.state.get_state_file_path', return_value=str(state_file)):
                with patch('worker.upgrade.downloader.download_installer', return_value=str(tmp_path / "installer.exe")):
                    with patch('worker.upgrade.installer.run_silent_install', side_effect=InstallError("启动失败")):
                        with pytest.raises(UpgradeError, match="安装失败"):
                            await handle_upgrade(request)
```

- [ ] **Step 3: 运行测试**

```bash
pytest tests/upgrade/test_handler.py -v
```

Expected: 所有测试通过

- [ ] **Step 4: 提交**

```bash
git add worker/upgrade/handler.py tests/upgrade/test_handler.py
git commit -m "feat(upgrade): 新增升级 HTTP 接口处理模块"
```

---

## Task 6: Server 路由集成

**Files:**
- Modify: `worker/server.py`
- Create: `worker/upgrade/__init__.py` (更新导出)

- [ ] **Step 1: 更新 worker/upgrade/__init__.py**

```python
# worker/upgrade/__init__.py
"""
升级模块。

提供 Worker 远程升级功能。
"""

from worker.upgrade.models import (
    UpgradeStatus,
    UpgradeRequest,
    UpgradeResponse,
    UpgradeState,
)
from worker.upgrade.handler import handle_upgrade, UpgradeError
from worker.upgrade.state import save_state, load_state, clear_state
from worker.upgrade.downloader import download_installer, DownloadError
from worker.upgrade.installer import run_silent_install, InstallError

__all__ = [
    "UpgradeStatus",
    "UpgradeRequest",
    "UpgradeResponse",
    "UpgradeState",
    "handle_upgrade",
    "UpgradeError",
    "save_state",
    "load_state",
    "clear_state",
    "download_installer",
    "DownloadError",
    "run_silent_install",
    "InstallError",
]
```

- [ ] **Step 2: 在 worker/server.py 添加升级路由**

在现有路由后添加：

```python
# worker/server.py 新增内容

# 导入升级模块
from worker.upgrade import handle_upgrade, UpgradeError, UpgradeRequest
import sys

# 在 /devices/refresh 路由后添加

@app.post("/worker/upgrade")
async def upgrade_worker(request: UpgradeRequest):
    """
    Worker 升级接口。

    接收平台下发的升级指令，下载安装包并执行静默安装。

    Args:
        request: 升级请求
            - version: 目标版本号（可选）
            - download_url: 安装包下载地址
            - force: 是否强制升级（可选，默认 true）

    Returns:
        Dict: 升级响应
            - status: skipped/upgrading/failed
            - message: 状态描述
            - current_version: 当前版本
            - target_version: 目标版本

    注意：升级成功后 Worker 会立即退出，由安装程序重启。
    """
    if not worker:
        raise HTTPException(status_code=503, detail="Worker not initialized")

    logger.info(
        f"Upgrade request: version={request.version}, "
        f"download_url={request.download_url}, force={request.force}"
    )

    try:
        result = await handle_upgrade(request)

        # 如果状态是 upgrading，Worker 立即退出
        if result.status == "upgrading":
            logger.info("Worker 即将退出以完成升级...")
            # 返回响应后退出
            import threading
            # 延迟退出，确保响应已发送
            def delayed_exit():
                import time
                time.sleep(0.5)
                sys.exit(0)
            threading.Thread(target=delayed_exit, daemon=True).start()

        return result.to_dict()

    except UpgradeError as e:
        logger.error(f"Upgrade failed: {e}")
        return {
            "status": "failed",
            "message": str(e),
        }
```

- [ ] **Step 3: 运行完整测试**

```bash
pytest tests/upgrade/ -v
```

Expected: 所有测试通过

- [ ] **Step 4: 提交**

```bash
git add worker/upgrade/__init__.py worker/server.py
git commit -m "feat(upgrade): 集成升级接口到 HTTP Server"
```

---

## Task 7: Inno Setup 安装脚本

**Files:**
- Create: `installer/installer.iss`
- Create: `installer/assets/` (目录)

- [ ] **Step 1: 创建安装程序目录**

```bash
mkdir -p installer/assets
```

- [ ] **Step 2: 编写 installer.iss**

```pascal
; installer/installer.iss
; Test Worker 安装脚本
; Inno Setup 6.x

#define Version "2.0.0"

[Setup]
AppId={{A1B2C3D4-E5F6-7890-ABCD-EF1234567890}}
AppName=Test Worker
AppVersion={#Version}
AppPublisher=Test Worker Team
DefaultDirName=C:\Program Files\Test Worker
DefaultGroupName=Test Worker
OutputDir=dist
OutputBaseFilename=test-worker-installer
Compression=lzma2/max
SolidCompression=yes
PrivilegesRequired=admin
PrivilegesRequiredOverridesAllowed=dialog

; 界面设置
WizardStyle=modern
WizardSizePercent=100

; 静默安装支持
Uninstallable=yes
CreateUninstallRegKey=yes


[Files]
; Worker 主程序和依赖
Source: "dist\test-worker\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs

; 配置目录 - 升级时不覆盖（保留用户配置）
Source: "dist\test-worker\config\*"; DestDir: "{app}\config"; Flags: onlyifdoesntexist recursesubdirs


[Dirs]
Name: "{app}\config"; Permissions: users-modify
Name: "{app}\temp"; Permissions: users-modify
Name: "{app}\data"; Permissions: users-modify


[Icons]
Name: "{group}\Test Worker"; Filename: "{app}\test-worker.exe"
Name: "{group}\卸载 Test Worker"; Filename: "{app}\unins000.exe"
Name: "{autodesktop}\Test Worker"; Filename: "{app}\test-worker.exe"; Tasks: desktopicon


[Tasks]
Name: "desktopicon"; Description: "创建桌面快捷方式"; GroupDescription: "附加选项:"


[Run]
Filename: "{app}\test-worker.exe"; Description: "启动 Test Worker"; Flags: nowait postinstall skipifsilent


[UninstallRun]
Filename: "taskkill"; Parameters: "/f /im test-worker.exe"; Flags: runhidden


[UninstallDelete]
Type: filesandordirs; Name: "{app}\logs"
Type: filesandordirs; Name: "{app}\data"
Type: filesandordirs; Name: "{app}\temp"


[Code]
var
  ConfigPage: TInputQueryWizardPage;
  IpEdit, PortEdit, NamespaceEdit, PlatformApiEdit, OcrServiceEdit: TNewEdit;
  CmdIp, CmdPort, CmdNamespace, CmdPlatformApi, CmdOcrService: String;

function GetCmdParam(Name: String): String;
var
  I: Integer;
begin
  Result := '';
  for I := 1 to ParamCount do
  begin
    if Pos('/' + Name + '=', ParamStr(I)) = 1 then
    begin
      Result := Copy(ParamStr(I), Length('/' + Name + '=') + 1, MaxInt);
      Break;
    end;
  end;
end;

function GetLocalIP: String;
var
  WSAData: TWSAData;
  HostName: String;
  HostEnt: PHostEnt;
  IPAddr: PInAddr;
begin
  Result := '127.0.0.1';
  try
    WSAStartup(MakeWord(1, 1), WSAData);
    SetLength(HostName, 255);
    GetHostName(PChar(HostName), 255);
    HostEnt := GetHostByName(PChar(HostName));
    if HostEnt <> nil then
    begin
      IPAddr := PInAddr(HostEnt^.h_addr_list^[0]);
      Result := inet_ntoa(IPAddr^);
    end;
    WSACleanup;
  except
  end;
end;

function IsUpgradeInstall: Boolean;
begin
  Result := RegValueExists(HKEY_LOCAL_MACHINE,
    'Software\Microsoft\Windows\CurrentVersion\Uninstall\{#SetupSetting("AppId")}_is1',
    'UninstallString');
end;

function ShouldSkipPage(PageID: Integer): Boolean;
begin
  Result := False;
  if PageID = ConfigPage.ID then
    Result := IsUpgradeInstall();
end;

procedure InitializeWizard;
begin
  CmdIp := GetCmdParam('IP');
  CmdPort := GetCmdParam('PORT');
  CmdNamespace := GetCmdParam('NAMESPACE');
  CmdPlatformApi := GetCmdParam('PLATFORM_API');
  CmdOcrService := GetCmdParam('OCR_SERVICE');

  ConfigPage := CreateInputQueryPage(wpSelectDir,
    '配置 Worker 参数', '请填写以下配置信息',
    '这些配置将写入 config/worker.yaml 文件');

  IpEdit := TNewEdit.Create(ConfigPage);
  IpEdit.Parent := ConfigPage.Surface;
  IpEdit.Left := ScaleX(0);
  IpEdit.Top := ScaleY(10);
  IpEdit.Width := ScaleX(300);
  if CmdIp <> '' then
    IpEdit.Text := CmdIp
  else
    IpEdit.Text := GetLocalIP();

  PortEdit := TNewEdit.Create(ConfigPage);
  PortEdit.Parent := ConfigPage.Surface;
  PortEdit.Left := ScaleX(0);
  PortEdit.Top := ScaleY(40);
  PortEdit.Width := ScaleX(100);
  if CmdPort <> '' then
    PortEdit.Text := CmdPort
  else
    PortEdit.Text := '8088';

  NamespaceEdit := TNewEdit.Create(ConfigPage);
  NamespaceEdit.Parent := ConfigPage.Surface;
  NamespaceEdit.Left := ScaleX(0);
  NamespaceEdit.Top := ScaleY(70);
  NamespaceEdit.Width := ScaleX(200);
  if CmdNamespace <> '' then
    NamespaceEdit.Text := CmdNamespace
  else
    NamespaceEdit.Text := 'meeting_public';

  PlatformApiEdit := TNewEdit.Create(ConfigPage);
  PlatformApiEdit.Parent := ConfigPage.Surface;
  PlatformApiEdit.Left := ScaleX(0);
  PlatformApiEdit.Top := ScaleY(100);
  PlatformApiEdit.Width := ScaleX(350);
  if CmdPlatformApi <> '' then
    PlatformApiEdit.Text := CmdPlatformApi
  else
    PlatformApiEdit.Text := 'http://192.168.0.102:8000';

  OcrServiceEdit := TNewEdit.Create(ConfigPage);
  OcrServiceEdit.Parent := ConfigPage.Surface;
  OcrServiceEdit.Left := ScaleX(0);
  OcrServiceEdit.Top := ScaleY(130);
  OcrServiceEdit.Width := ScaleX(350);
  if CmdOcrService <> '' then
    OcrServiceEdit.Text := CmdOcrService
  else
    OcrServiceEdit.Text := 'http://192.168.0.102:9021';
end;

procedure CurStepChanged(CurStep: TSetupStep);
var
  ConfigFile: String;
  ConfigContent: String;
begin
  if CurStep = ssPostInstall then
  begin
    if not IsUpgradeInstall() then
    begin
      ConfigFile := ExpandConstant('{app}\config\worker.yaml');
      ConfigContent :=
        '# Worker 配置文件（安装时生成）' + #13#10 +
        '' + #13#10 +
        'worker:' + #13#10 +
        '  id: null' + #13#10 +
        '  ip: "' + IpEdit.Text + '"' + #13#10 +
        '  port: ' + PortEdit.Text + #13#10 +
        '  namespace: "' + NamespaceEdit.Text + '"' + #13#10 +
        '  device_check_interval: 300' + #13#10 +
        '' + #13#10 +
        'external_services:' + #13#10 +
        '  platform_api: "' + PlatformApiEdit.Text + '"' + #13#10 +
        '  ocr_service: "' + OcrServiceEdit.Text + '"' + #13#10 +
        '' + #13#10 +
        '# 其他配置请参考完整配置文件模板' + #13#10;
      SaveStringToFile(ConfigFile, ConfigContent, False);
    end;
  end;
end;
```

- [ ] **Step 3: 提交**

```bash
git add installer/
git commit -m "feat(installer): 新增 Inno Setup 安装脚本"
```

---

## Task 8: 安装包构建脚本

**Files:**
- Create: `installer/build_installer.ps1`

- [ ] **Step 1: 编写 build_installer.ps1**

```powershell
# installer/build_installer.ps1
# Windows 安装包构建脚本

param(
    [string]$Version = "2.0.0",
    [string]$PyInstallerOutput = "..\dist\test-worker"
)

Write-Host "=========================================="
Write-Host "Building Test Worker Installer"
Write-Host "Version: $Version"
Write-Host "=========================================="

# 检查 Inno Setup
$InnoPath = "C:\Program Files (x86)\Inno Setup 6\ISCC.exe"
if (-not (Test-Path $InnoPath)) {
    $InnoPath = "C:\Program Files\Inno Setup 6\ISCC.exe"
}
if (-not (Test-Path $InnoPath)) {
    Write-Error "Inno Setup 6 not found!"
    Write-Host "Please download from: https://jrsoftware.org/isdl.php"
    exit 1
}

# 检查 PyInstaller 输出
$OutputDir = Join-Path $PSScriptRoot $PyInstallerOutput
if (-not (Test-Path $OutputDir)) {
    Write-Error "PyInstaller output not found: $OutputDir"
    Write-Host "Please run scripts/build_windows.ps1 first"
    exit 1
}

# 检查 dist 目录
$DistDir = Join-Path $PSScriptRoot "..\dist"
if (-not (Test-Path $DistDir)) {
    New-Item -ItemType Directory -Force -Path $DistDir | Out-Null
}

# 编译安装脚本
Write-Host "Compiling installer script..."
$ScriptPath = Join-Path $PSScriptRoot "installer.iss"

& $InnoPath "/DVersion=$Version" $ScriptPath

if ($LASTEXITCODE -ne 0) {
    Write-Error "Inno Setup compilation failed!"
    exit 1
}

$InstallerPath = Join-Path $DistDir "test-worker-installer.exe"
if (-not (Test-Path $InstallerPath)) {
    Write-Error "Installer not generated: $InstallerPath"
    exit 1
}

Write-Host "=========================================="
Write-Host "Installer build complete!"
Write-Host "Output: $InstallerPath"
Write-Host "Size: $([math]::Round((Get-Item $InstallerPath).Length / 1MB, 2)) MB"
Write-Host "=========================================="
```

- [ ] **Step 2: 提交**

```bash
git add installer/build_installer.ps1
git commit -m "feat(installer): 新增安装包构建脚本"
```

---

## Task 9: 集成构建流程

**Files:**
- Modify: `scripts/build_windows.ps1`

- [ ] **Step 1: 修改 scripts/build_windows.ps1**

在脚本末尾添加安装包构建调用：

```powershell
# 在现有 build_windows.ps1 的末尾添加

# 构建安装包（可选）
Write-Host ""
Write-Host "是否构建安装包? (用于分发部署)"
$BuildInstaller = Read-Host "输入 'y' 构建，其他键跳过"

if ($BuildInstaller -eq 'y') {
    Write-Host "Building installer..."
    & ".\installer\build_installer.ps1" -Version $Version
    if ($LASTEXITCODE -ne 0) {
        Write-Warning "Installer build failed, but EXE package is available"
    }
}

Write-Host "=========================================="
Write-Host "All builds complete!"
Write-Host "EXE package: $PackageDir"
Write-Host "Installer: $OutputDir\test-worker-installer.exe (if built)"
Write-Host "=========================================="
```

- [ ] **Step 2: 提交**

```bash
git add scripts/build_windows.ps1
git commit -m "feat(build): 集成安装包构建到主构建流程"
```

---

## Task 10: 运行完整测试并验证

- [ ] **Step 1: 运行所有升级模块测试**

```bash
pytest tests/upgrade/ -v
```

Expected: 所有测试通过

- [ ] **Step 2: 运行完整项目测试**

```bash
pytest -v
```

Expected: 所有测试通过

- [ ] **Step 3: 手动验证升级接口**

启动 Worker 后，使用 curl 测试升级接口：

```bash
# 测试版本一致（跳过升级）
curl -X POST http://localhost:8088/worker/upgrade \
  -H "Content-Type: application/json" \
  -d '{"version": "当前版本", "download_url": "http://example.com/installer.exe"}'

# 测试版本不一致（触发升级，注意会退出进程）
curl -X POST http://localhost:8088/worker/upgrade \
  -H "Content-Type: application/json" \
  -d '{"version": "新版本", "download_url": "http://example.com/installer.exe"}'
```

- [ ] **Step 4: 最终提交**

```bash
git add -A
git commit -m "feat: 完成安装包和远程升级系统实现

- 升级模块：models, state, downloader, installer, handler
- HTTP接口：POST /worker/upgrade
- 安装程序：Inno Setup 脚本，支持图形界面和静默安装
- 构建集成：scripts/build_windows.ps1 集成安装包构建
- 配置保留：升级时保留 config/worker.yaml
- 版本校验：相同版本跳过升级"
```

---

## 验收清单

- [ ] 升级模块所有单元测试通过
- [ ] `/worker/upgrade` 接口正常响应
- [ ] 版本一致时返回 `skipped`
- [ ] Inno Setup 脚本编译成功
- [ ] 安装包生成成功
- [ ] 静默安装参数 `/VERYSILENT` 正常工作
- [ ] 自定义配置参数 `/IP`, `/PORT` 等正常工作
- [ ] 升级安装时配置文件保留

---

## 风险提示

1. **Windows 环境要求**：Inno Setup 需要在 Windows 上安装
2. **httpx 依赖**：需要确保 httpx 已安装
3. **进程退出时机**：升级响应发送后延迟 0.5 秒退出，确保响应完整
4. **测试覆盖**：handler.py 的实际退出逻辑需要手动验证