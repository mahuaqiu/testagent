# Windows 系统托盘 GUI 实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 为 Test Worker 添加 Windows 系统托盘功能，实现管理员权限启动、无 CMD 窗口、托盘菜单、自动升级、设置界面。

**Architecture:** 纯 Python 实现，使用 pystray 实现系统托盘，PyQt5 实现设置界面和下载进度窗口。Worker 作为后台线程运行，托盘主线程管理生命周期。

**Tech Stack:** pystray>=1.9.0, PyQt5>=5.15.0, threading, httpx

---

## 文件结构

### 新增文件

```
worker/
├── gui_main.py          # GUI 入口，启动托盘和 Worker
├── tray_manager.py      # 托盘管理器，菜单和状态管理
├── settings_window.py   # PyQt5 设置窗口
├── upgrade_manager.py   # 升级管理器，检查和下载
├── download_dialog.py   # PyQt5 下载进度窗口
└── single_instance.py   # Windows 单实例锁
```

### 修改文件

```
scripts/pyinstaller.spec  # 添加 icon、uac_admin、console=False
installer/installer.iss   # 静默安装自动启动
config/worker.yaml        # 添加 upgrade 配置项
pyproject.toml            # 添加 pystray、PyQt5 依赖
worker/config.py          # 添加 upgrade 配置字段
```

---

## Task 1: 添加依赖

**Files:**
- Modify: `pyproject.toml`（dependencies 列表）

- [ ] **Step 1: 添加 pystray 和 PyQt5 依赖**

在 `pyproject.toml` 的 `dependencies` 列表末尾（`"pyperclip>=1.8.0",` 之后）添加：

```python
    # GUI 组件
    "pystray>=1.9.0",
    "PyQt5>=5.15.0",
```

- [ ] **Step 2: 安装依赖**

Run: `pip install -e ".[all]"`
Expected: 依赖安装成功

- [ ] **Step 3: Commit**

```bash
git add pyproject.toml
git commit -m "feat: 添加 pystray 和 PyQt5 依赖"
```

---

## Task 2: 创建单实例锁模块

**Files:**
- Create: `worker/single_instance.py`
- Test: `tests/test_single_instance.py`

- [ ] **Step 1: Write the failing test**

```python
import pytest
from unittest.mock import patch, MagicMock
from worker.single_instance import check_single_instance, release_instance_lock


def test_check_single_instance():
    """测试单实例检查。"""
    # 模拟首次启动，GetLastError 返回 0（无错误）
    mock_kernel32 = MagicMock()
    mock_kernel32.CreateMutexW.return_value = 12345  # 返回句柄
    mock_kernel32.GetLastError.return_value = 0

    with patch('ctypes.windll.kernel32', mock_kernel32):
        result = check_single_instance()
        assert result is True


def test_check_single_instance_already_running():
    """测试已有实例运行时的检查。"""
    # 模拟已有实例，GetLastError 返回 183（ERROR_ALREADY_EXISTS）
    mock_kernel32 = MagicMock()
    mock_kernel32.CreateMutexW.return_value = None
    mock_kernel32.GetLastError.return_value = 183

    with patch('ctypes.windll.kernel32', mock_kernel32):
        result = check_single_instance()
        assert result is False


def test_release_instance_lock():
    """测试释放实例锁。"""
    mock_kernel32 = MagicMock()
    mock_kernel32.CreateMutexW.return_value = 12345
    mock_kernel32.GetLastError.return_value = 0
    mock_kernel32.CloseHandle.return_value = True

    with patch('ctypes.windll.kernel32', mock_kernel32):
        check_single_instance()
        release_instance_lock()
        mock_kernel32.CloseHandle.assert_called_once()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_single_instance.py -v`
Expected: FAIL with "module not found"

- [ ] **Step 3: Write minimal implementation**

```python
"""
Windows 单实例锁模块。

使用 Windows Mutex 确保只有一个实例运行。
"""

import ctypes
import logging

logger = logging.getLogger(__name__)

# 全局 Mutex 句柄
_mutex_handle = None
MUTEX_NAME = "Global\\TestWorkerSingleInstance"


def check_single_instance() -> bool:
    """
    检查是否已有实例运行。

    Returns:
        bool: True 表示可以启动（无其他实例），False 表示已有实例运行
    """
    global _mutex_handle

    # 创建 Mutex
    _mutex_handle = ctypes.windll.kernel32.CreateMutexW(
        None, False, MUTEX_NAME
    )

    # 检查是否已存在
    last_error = ctypes.windll.kernel32.GetLastError()
    if last_error == 183:  # ERROR_ALREADY_EXISTS
        logger.warning("Another instance is already running")
        return False

    logger.debug("Single instance lock acquired")
    return True


def release_instance_lock() -> None:
    """释放单实例锁。"""
    global _mutex_handle

    if _mutex_handle:
        ctypes.windll.kernel32.CloseHandle(_mutex_handle)
        _mutex_handle = None
        logger.debug("Single instance lock released")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_single_instance.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add worker/single_instance.py tests/test_single_instance.py
git commit -m "feat: 添加 Windows 单实例锁模块"
```

---

## Task 3: 创建升级管理器

**Files:**
- Create: `worker/upgrade_manager.py`
- Test: `tests/test_upgrade_manager.py`

- [ ] **Step 1: Write the failing test**

```python
import pytest
from worker.upgrade_manager import UpgradeManager, UpgradeInfo


def test_upgrade_info_from_response():
    """测试从响应创建 UpgradeInfo。"""
    response = {
        "version": "202604101500",
        "download_url": "http://example.com/download.exe"
    }
    info = UpgradeInfo.from_response(response)
    assert info.version == "202604101500"
    assert info.download_url == "http://example.com/download.exe"


def test_is_newer_version():
    """测试版本比较。"""
    # 当前版本
    current = "202604101400"

    # 新版本
    newer = "202604101500"
    assert UpgradeManager.is_newer_version(current, newer) is True

    # 相同版本
    same = "202604101400"
    assert UpgradeManager.is_newer_version(current, same) is False

    # 旧版本
    older = "202604101300"
    assert UpgradeManager.is_newer_version(current, older) is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_upgrade_manager.py -v`
Expected: FAIL with "module not found"

- [ ] **Step 3: Write minimal implementation**

```python
"""
升级管理器模块。

负责检查更新、下载安装包、执行静默安装。
"""

import httpx
import os
import subprocess
import tempfile
from dataclasses import dataclass
from typing import Optional, Callable

import logging

logger = logging.getLogger(__name__)


@dataclass
class UpgradeInfo:
    """升级信息。"""

    version: str
    download_url: str

    @classmethod
    def from_response(cls, data: dict) -> "UpgradeInfo":
        """从 API 响应创建。"""
        return cls(
            version=data.get("version", ""),
            download_url=data.get("download_url", ""),
        )


class UpgradeManager:
    """升级管理器。"""

    def __init__(
        self,
        check_url: str,
        current_version: str,
        check_timeout: int = 30,
        download_timeout: int = 300,
    ):
        """
        初始化升级管理器。

        Args:
            check_url: 升级检查接口地址
            current_version: 当前版本号
            check_timeout: 检查超时（秒）
            download_timeout: 下载超时（秒）
        """
        self.check_url = check_url
        self.current_version = current_version
        self.check_timeout = check_timeout
        self.download_timeout = download_timeout

    def check_upgrade(self) -> Optional[UpgradeInfo]:
        """
        检查是否有新版本。

        Returns:
            UpgradeInfo | None: 有新版本返回信息，无新版本返回 None
        """
        if not self.check_url:
            logger.warning("Upgrade check URL not configured")
            return None

        try:
            response = httpx.get(self.check_url, timeout=self.check_timeout)
            response.raise_for_status()

            data = response.json()
            info = UpgradeInfo.from_response(data)

            # 检查是否是新版本
            if self.is_newer_version(self.current_version, info.version):
                logger.info(f"New version available: {info.version}")
                return info
            else:
                logger.info("No new version available")
                return None

        except Exception as e:
            logger.error(f"Upgrade check failed: {e}")
            raise

    @staticmethod
    def is_newer_version(current: str, target: str) -> bool:
        """
        检查目标版本是否比当前版本新。

        Args:
            current: 当前版本号（格式：yyyyMMddHHmm）
            target: 目标版本号

        Returns:
            bool: True 表示目标版本更新
        """
        try:
            return int(target) > int(current)
        except ValueError:
            # 版本格式错误，字符串比较
            return target > current

    def download_installer(
        self,
        download_url: str,
        progress_callback: Optional[Callable[[int, int], None]] = None,
        cancel_event: Optional[object] = None,
    ) -> str:
        """
        下载安装包。

        Args:
            download_url: 下载地址
            progress_callback: 进度回调函数 (已下载字节, 总字节)
            cancel_event: 取消信号（threading.Event）

        Returns:
            str: 下载文件路径
        """
        # 临时文件路径
        temp_dir = tempfile.gettempdir()
        temp_file = os.path.join(temp_dir, "test-worker-update.exe")

        try:
            with httpx.stream("GET", download_url, timeout=self.download_timeout) as response:
                response.raise_for_status()

                # 获取文件总大小
                total_size = int(response.headers.get("Content-Length", 0))
                downloaded = 0

                with open(temp_file, "wb") as f:
                    for chunk in response.iter_bytes(chunk_size=1024 * 1024):  # 1MB chunks
                        # 检查取消信号
                        if cancel_event and cancel_event.is_set():
                            logger.info("Download cancelled by user")
                            f.close()
                            os.remove(temp_file)
                            raise Exception("Download cancelled")

                        f.write(chunk)
                        downloaded += len(chunk)

                        # 更新进度
                        if progress_callback:
                            progress_callback(downloaded, total_size)

            logger.info(f"Download completed: {temp_file}")
            return temp_file

        except Exception as e:
            # 清理临时文件
            if os.path.exists(temp_file):
                os.remove(temp_file)
            logger.error(f"Download failed: {e}")
            raise

    def run_silent_install(self, installer_path: str, install_dir: Optional[str] = None) -> None:
        """
        执行静默安装。

        Args:
            installer_path: 安装包路径
            install_dir: 安装目录（可选）
        """
        # 构建静默安装参数
        args = [
            installer_path,
            "/VERYSILENT",
            "/SUPPRESSMSGBOXES",
            "/NORESTART",
            "/SP-",
        ]

        if install_dir:
            args.append(f"/DIR={install_dir}")

        logger.info(f"Running silent install: {args}")

        # 启动安装程序
        subprocess.Popen(args)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_upgrade_manager.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add worker/upgrade_manager.py tests/test_upgrade_manager.py
git commit -m "feat: 添加升级管理器模块"
```

---

## Task 4: 创建下载进度窗口

**Files:**
- Create: `worker/download_dialog.py`

- [ ] **Step 1: Write the implementation**

```python
"""
下载进度窗口模块。

PyQt5 实现的下载进度对话框。
"""

import os
import httpx
import tempfile
from PyQt5.QtWidgets import (
    QDialog,
    QVBoxLayout,
    QLabel,
    QProgressBar,
    QPushButton,
)
from PyQt5.QtCore import Qt, QThread, pyqtSignal


class DownloadThread(QThread):
    """下载线程。"""

    progress_signal = pyqtSignal(int, int)  # (已下载字节, 总字节)
    finished_signal = pyqtSignal(str)  # 文件路径
    error_signal = pyqtSignal(str)  # 错误信息
    cancelled_signal = pyqtSignal()

    def __init__(
        self,
        download_url: str,
        download_timeout: int = 300,
    ):
        super().__init__()
        self.download_url = download_url
        self.download_timeout = download_timeout
        self._cancel_event = False

    def run(self):
        """执行下载。"""
        temp_file = os.path.join(tempfile.gettempdir(), "test-worker-update.exe")

        try:
            with httpx.stream("GET", self.download_url, timeout=self.download_timeout) as response:
                response.raise_for_status()

                total_size = int(response.headers.get("Content-Length", 0))
                downloaded = 0

                with open(temp_file, "wb") as f:
                    for chunk in response.iter_bytes(chunk_size=1024 * 1024):
                        if self._cancel_event:
                            f.close()
                            os.remove(temp_file)
                            self.cancelled_signal.emit()
                            return

                        f.write(chunk)
                        downloaded += len(chunk)
                        self.progress_signal.emit(downloaded, total_size)

                self.finished_signal.emit(temp_file)

        except Exception as e:
            if os.path.exists(temp_file):
                os.remove(temp_file)
            self.error_signal.emit(str(e))

    def cancel(self):
        """取消下载。"""
        self._cancel_event = True


class DownloadDialog(QDialog):
    """下载进度对话框。"""

    def __init__(
        self,
        version: str,
        download_url: str,
        download_timeout: int = 300,
        parent=None,
    ):
        super().__init__(parent)
        self.version = version
        self.download_url = download_url
        self.download_timeout = download_timeout

        self._downloaded_file = None
        self._cancelled = False
        self._error = None

        self._setup_ui()
        self._start_download()

    def _setup_ui(self):
        """设置界面。"""
        self.setWindowTitle("正在下载更新")
        self.setFixedSize(400, 150)
        self.setModal(True)

        layout = QVBoxLayout(self)

        # 版本标签
        version_label = QLabel(f"版本: v{self.version}")
        layout.addWidget(version_label)

        # 进度条
        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        layout.addWidget(self.progress_bar)

        # 进度文本
        self.progress_text = QLabel("已下载: 0 MB / 0 MB")
        layout.addWidget(self.progress_text)

        # 取消按钮
        self.cancel_button = QPushButton("取消下载")
        self.cancel_button.clicked.connect(self._on_cancel)
        layout.addWidget(self.cancel_button, alignment=Qt.AlignCenter)

    def _start_download(self):
        """启动下载线程。"""
        self.download_thread = DownloadThread(
            self.download_url,
            self.download_timeout,
        )
        self.download_thread.progress_signal.connect(self._on_progress)
        self.download_thread.finished_signal.connect(self._on_finished)
        self.download_thread.error_signal.connect(self._on_error)
        self.download_thread.cancelled_signal.connect(self._on_cancelled)
        self.download_thread.start()

    def _on_progress(self, downloaded: int, total: int):
        """更新进度。"""
        if total > 0:
            percent = int(downloaded * 100 / total)
            self.progress_bar.setValue(percent)

        downloaded_mb = downloaded / (1024 * 1024)
        total_mb = total / (1024 * 1024)
        self.progress_text.setText(f"已下载: {downloaded_mb:.1f} MB / {total_mb:.1f} MB")

    def _on_finished(self, file_path: str):
        """下载完成。"""
        self._downloaded_file = file_path
        self.accept()

    def _on_error(self, error: str):
        """下载失败。"""
        self._error = error
        self.reject()

    def _on_cancel(self):
        """取消按钮点击。"""
        self.download_thread.cancel()
        self.cancel_button.setEnabled(False)
        self.cancel_button.setText("正在取消...")

    def _on_cancelled(self):
        """下载已取消。"""
        self._cancelled = True
        self.reject()

    def get_downloaded_file(self) -> str:
        """获取下载的文件路径。"""
        return self._downloaded_file

    def was_cancelled(self) -> bool:
        """是否被取消。"""
        return self._cancelled

    def get_error(self) -> str:
        """获取错误信息。"""
        return self._error

    def closeEvent(self, event):
        """关闭事件。"""
        if self.download_thread.isRunning():
            self.download_thread.cancel()
            self.download_thread.wait()
        event.accept()
```

- [ ] **Step 2: Commit**

```bash
git add worker/download_dialog.py
git commit -m "feat: 添加下载进度窗口模块"
```

---

## Task 5: 创建设置窗口

**Files:**
- Create: `worker/settings_window.py`

- [ ] **Step 1: Write the implementation**

```python
"""
设置窗口模块。

PyQt5 实现的配置设置对话框。
"""

import os
import re
import yaml
from PyQt5.QtWidgets import (
    QDialog,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QComboBox,
    QPushButton,
    QMessageBox,
)
from PyQt5.QtCore import Qt

import logging

logger = logging.getLogger(__name__)


class SettingsWindow(QDialog):
    """设置窗口。"""

    def __init__(self, config_path: str, parent=None):
        super().__init__(parent)
        self.config_path = config_path
        self._config = self._load_config()

        self._setup_ui()
        self._load_values()

    def _load_config(self) -> dict:
        """加载配置文件。"""
        if os.path.exists(self.config_path):
            with open(self.config_path, "r", encoding="utf-8") as f:
                return yaml.safe_load(f) or {}
        return {}

    def _setup_ui(self):
        """设置界面。"""
        self.setWindowTitle("Test Worker 设置")
        self.setFixedSize(450, 300)
        self.setModal(True)

        layout = QVBoxLayout(self)

        # Worker IP
        ip_layout = QHBoxLayout()
        ip_label = QLabel("Worker IP 地址:")
        ip_label.setFixedWidth(120)
        self.ip_input = QLineEdit()
        ip_layout.addWidget(ip_label)
        ip_layout.addWidget(self.ip_input)
        layout.addLayout(ip_layout)

        # Worker 端口
        port_layout = QHBoxLayout()
        port_label = QLabel("Worker 端口:")
        port_label.setFixedWidth(120)
        self.port_input = QLineEdit()
        self.port_input.setPlaceholderText("8088")
        port_layout.addWidget(port_label)
        port_layout.addWidget(self.port_input)
        layout.addLayout(port_layout)

        # 命名空间
        namespace_layout = QHBoxLayout()
        namespace_label = QLabel("命名空间:")
        namespace_label.setFixedWidth(120)
        self.namespace_input = QLineEdit()
        self.namespace_input.setPlaceholderText("meeting_public")
        namespace_layout.addWidget(namespace_label)
        namespace_layout.addWidget(self.namespace_input)
        layout.addLayout(namespace_layout)

        # 平台 API 地址
        platform_api_layout = QHBoxLayout()
        platform_api_label = QLabel("平台 API 地址:")
        platform_api_label.setFixedWidth(120)
        self.platform_api_input = QLineEdit()
        platform_api_layout.addWidget(platform_api_label)
        platform_api_layout.addWidget(self.platform_api_input)
        layout.addLayout(platform_api_layout)

        # OCR 服务地址
        ocr_service_layout = QHBoxLayout()
        ocr_service_label = QLabel("OCR 服务地址:")
        ocr_service_label.setFixedWidth(120)
        self.ocr_service_input = QLineEdit()
        ocr_service_layout.addWidget(ocr_service_label)
        ocr_service_layout.addWidget(self.ocr_service_input)
        layout.addLayout(ocr_service_layout)

        # 日志级别
        log_level_layout = QHBoxLayout()
        log_level_label = QLabel("日志级别:")
        log_level_label.setFixedWidth(120)
        self.log_level_combo = QComboBox()
        self.log_level_combo.addItems(["DEBUG", "INFO", "WARNING", "ERROR"])
        log_level_layout.addWidget(log_level_label)
        log_level_layout.addWidget(self.log_level_combo)
        layout.addLayout(log_level_layout)

        # 添加弹性空间
        layout.addStretch()

        # 按钮
        button_layout = QHBoxLayout()
        button_layout.addStretch()

        save_button = QPushButton("保存并重启")
        save_button.clicked.connect(self._on_save)
        button_layout.addWidget(save_button)

        cancel_button = QPushButton("取消")
        cancel_button.clicked.connect(self.reject)
        button_layout.addWidget(cancel_button)

        layout.addLayout(button_layout)

    def _load_values(self):
        """从配置加载值。"""
        worker = self._config.get("worker", {})
        external = self._config.get("external_services", {})
        logging_cfg = self._config.get("logging", {})

        # Worker IP
        ip = worker.get("ip")
        if ip:
            self.ip_input.setText(ip)

        # Worker 端口
        port = worker.get("port", 8088)
        self.port_input.setText(str(port))

        # 命名空间
        namespace = worker.get("namespace", "meeting_public")
        self.namespace_input.setText(namespace)

        # 平台 API 地址
        platform_api = external.get("platform_api", "")
        self.platform_api_input.setText(platform_api)

        # OCR 服务地址
        ocr_service = external.get("ocr_service", "")
        self.ocr_service_input.setText(ocr_service)

        # 日志级别
        log_level = logging_cfg.get("level", "INFO")
        index = self.log_level_combo.findText(log_level)
        if index >= 0:
            self.log_level_combo.setCurrentIndex(index)

    def _validate(self) -> bool:
        """验证输入。"""
        # 端口验证
        port_text = self.port_input.text().strip()
        if not port_text:
            QMessageBox.warning(self, "验证失败", "Worker 端口不能为空")
            return False
        try:
            port = int(port_text)
            if port < 1 or port > 65535:
                QMessageBox.warning(self, "验证失败", "Worker 端口范围应为 1-65535")
                return False
        except ValueError:
            QMessageBox.warning(self, "验证失败", "Worker 端口应为数字")
            return False

        # IP 验证（可选）
        ip_text = self.ip_input.text().strip()
        if ip_text:
            ipv4_pattern = r"^(\d{1,3}\.){3}\d{1,3}$"
            if not re.match(ipv4_pattern, ip_text):
                QMessageBox.warning(self, "验证失败", "Worker IP 格式错误")
                return False

        # 命名空间验证
        namespace_text = self.namespace_input.text().strip()
        if not namespace_text:
            QMessageBox.warning(self, "验证失败", "命名空间不能为空")
            return False

        # URL 验证
        url_pattern = r"^https?://.+"

        platform_api_text = self.platform_api_input.text().strip()
        if not platform_api_text:
            QMessageBox.warning(self, "验证失败", "平台 API 地址不能为空")
            return False
        if not re.match(url_pattern, platform_api_text):
            QMessageBox.warning(self, "验证失败", "平台 API 地址格式错误（应为 http:// 或 https://）")
            return False

        ocr_service_text = self.ocr_service_input.text().strip()
        if not ocr_service_text:
            QMessageBox.warning(self, "验证失败", "OCR 服务地址不能为空")
            return False
        if not re.match(url_pattern, ocr_service_text):
            QMessageBox.warning(self, "验证失败", "OCR 服务地址格式错误（应为 http:// 或 https://）")
            return False

        return True

    def _on_save(self):
        """保存按钮点击。"""
        if not self._validate():
            return

        # 更新配置
        self._config.setdefault("worker", {})
        self._config["worker"]["ip"] = self.ip_input.text().strip() or None
        self._config["worker"]["port"] = int(self.port_input.text().strip())
        self._config["worker"]["namespace"] = self.namespace_input.text().strip()

        self._config.setdefault("external_services", {})
        self._config["external_services"]["platform_api"] = self.platform_api_input.text().strip()
        self._config["external_services"]["ocr_service"] = self.ocr_service_input.text().strip()

        self._config.setdefault("logging", {})
        self._config["logging"]["level"] = self.log_level_combo.currentText()

        # 写入配置文件
        try:
            with open(self.config_path, "w", encoding="utf-8") as f:
                yaml.dump(self._config, f, allow_unicode=True, default_flow_style=False)
            logger.info(f"Configuration saved: {self.config_path}")
            self.accept()
        except Exception as e:
            QMessageBox.warning(self, "保存失败", f"无法保存配置文件: {e}")
            logger.error(f"Failed to save config: {e}")

    def get_config(self) -> dict:
        """获取更新后的配置。"""
        return self._config
```

- [ ] **Step 2: Commit**

```bash
git add worker/settings_window.py
git commit -m "feat: 添加设置窗口模块"
```

---

## Task 6: 创建托盘管理器

**Files:**
- Create: `worker/tray_manager.py`

- [ ] **Step 1: Write the implementation**

```python
"""
托盘管理器模块。

使用 pystray 实现系统托盘，管理托盘菜单和 Worker 生命周期。
"""

import logging
import os
import sys
import threading
from typing import Callable, Optional

import pystray
from PIL import Image

logger = logging.getLogger(__name__)


class TrayManager:
    """托盘管理器。"""

    def __init__(
        self,
        icon_path: str,
        status_callback: Optional[Callable[[], str]] = None,
        on_upgrade: Optional[Callable[[], None]] = None,
        on_restart: Optional[Callable[[], None]] = None,
        on_settings: Optional[Callable[[], None]] = None,
        on_exit: Optional[Callable[[], None]] = None,
    ):
        """
        初始化托盘管理器。

        Args:
            icon_path: 图标文件路径
            status_callback: 获取状态的回调函数
            on_upgrade: 升级回调
            on_restart: 重启回调
            on_settings: 设置回调
            on_exit: 退出回调
        """
        self.icon_path = icon_path
        self.status_callback = status_callback
        self.on_upgrade = on_upgrade
        self.on_restart = on_restart
        self.on_settings = on_settings
        self.on_exit = on_exit

        self._icon: Optional[pystray.Icon] = None
        self._running = False
        self._stop_event = threading.Event()

    def _load_icon(self) -> Image.Image:
        """加载图标。"""
        if os.path.exists(self.icon_path):
            return Image.open(self.icon_path)
        else:
            # 创建默认图标（红色方块）
            image = Image.new("RGB", (64, 64), color="red")
            return image

    def _get_tooltip(self) -> str:
        """获取托盘提示文本。"""
        if self.status_callback:
            status = self.status_callback()
            return f"Test Worker - {status}"
        return "Test Worker"

    def _create_menu(self) -> pystray.Menu:
        """创建托盘菜单。"""
        return pystray.Menu(
            pystray.MenuItem("升级", self._on_upgrade_click),
            pystray.MenuItem("重启", self._on_restart_click),
            pystray.MenuItem("日志", self._on_log_click),
            pystray.MenuItem("设置", self._on_settings_click),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("退出", self._on_exit_click),
        )

    def _on_upgrade_click(self):
        """升级菜单点击。"""
        if self.on_upgrade:
            # 在后台线程执行，避免阻塞托盘
            threading.Thread(target=self.on_upgrade, daemon=True).start()

    def _on_restart_click(self):
        """重启菜单点击。"""
        if self.on_restart:
            self.on_restart()

    def _on_log_click(self):
        """日志菜单点击。"""
        # 获取日志目录
        if getattr(sys, "frozen", False):
            # EXE 运行
            app_dir = os.path.dirname(sys.executable)
        else:
            # 源码运行
            app_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

        log_dir = os.path.join(app_dir, "logs")

        # 如果目录不存在，创建它
        if not os.path.exists(log_dir):
            os.makedirs(log_dir)

        # 打开目录
        if sys.platform == "win32":
            os.startfile(log_dir)

    def _on_settings_click(self):
        """设置菜单点击。"""
        if self.on_settings:
            self.on_settings()

    def _on_exit_click(self):
        """退出菜单点击。"""
        if self.on_exit:
            self.on_exit()
        # 停止托盘
        self.stop()

    def start(self):
        """启动托盘。"""
        if self._running:
            return

        # 创建图标
        image = self._load_icon()
        menu = self._create_menu()

        self._icon = pystray.Icon(
            "test_worker",
            image,
            self._get_tooltip(),
            menu,
        )

        self._running = True

        # 运行托盘（阻塞）
        logger.info("Tray icon started")
        self._icon.run()

    def stop(self):
        """停止托盘。"""
        self._stop_event.set()
        if self._icon:
            self._icon.stop()
            self._running = False
            logger.info("Tray icon stopped")

    def update_tooltip(self):
        """更新托盘提示文本。"""
        if self._icon:
            self._icon.title = self._get_tooltip()

    def is_running(self) -> bool:
        """检查托盘是否运行。"""
        return self._running
```

- [ ] **Step 2: Commit**

```bash
git add worker/tray_manager.py
git commit -m "feat: 添加托盘管理器模块"
```

---

## Task 7: 修改配置类添加升级配置字段

**Files:**
- Modify: `worker/config.py`
- Test: `tests/test_config.py`

- [ ] **Step 1: Write the failing test**

创建 `tests/test_config.py` 文件（如已存在则追加），添加以下测试：

```python
def test_worker_config_upgrade_fields():
    """测试升级配置字段。"""
    from worker.config import WorkerConfig

    config = WorkerConfig()
    assert hasattr(config, 'upgrade_check_url')
    assert config.upgrade_check_url == ""
    assert config.upgrade_check_timeout == 30
    assert config.upgrade_download_timeout == 300


def test_worker_config_upgrade_from_yaml(tmp_path):
    """测试从 YAML 加载升级配置。"""
    import yaml
    from worker.config import WorkerConfig

    config_file = tmp_path / "worker.yaml"
    config_data = {
        "upgrade": {
            "check_url": "http://example.com/upgrade",
            "check_timeout": 60,
            "download_timeout": 600,
        }
    }
    with open(config_file, "w") as f:
        yaml.dump(config_data, f)

    config = WorkerConfig.from_yaml(str(config_file))
    assert config.upgrade_check_url == "http://example.com/upgrade"
    assert config.upgrade_check_timeout == 60
    assert config.upgrade_download_timeout == 600
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_config.py::test_worker_config_upgrade_fields -v`
Expected: FAIL with "has no attribute 'upgrade_check_url'"

- [ ] **Step 3: Write minimal implementation**

在 `worker/config.py` 的 `WorkerConfig` 类中添加字段：

```python
@dataclass
class WorkerConfig:
    """Worker 配置。"""

    # ... 现有字段 ...

    # 升级配置
    upgrade_check_url: str = ""       # 升级检查 URL（对应 YAML 的 upgrade.check_url）
    upgrade_check_timeout: int = 30
    upgrade_download_timeout: int = 300
```

在 `from_yaml` 方法中添加：

```python
@classmethod
def from_yaml(cls, path: str) -> "WorkerConfig":
    """从 YAML 文件加载配置。"""
    # ... 现有代码 ...

    upgrade_cfg = data.get("upgrade", {})

    return cls(
        # ... 现有字段 ...
        upgrade_check_url=upgrade_cfg.get("check_url", ""),
        upgrade_check_timeout=upgrade_cfg.get("check_timeout", 30),
        upgrade_download_timeout=upgrade_cfg.get("download_timeout", 300),
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_config.py::test_worker_config_upgrade_fields -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add worker/config.py tests/test_config.py
git commit -m "feat: 配置类添加升级配置字段"
```

---

## Task 8: 创建 GUI 入口

**Files:**
- Create: `worker/gui_main.py`

- [ ] **Step 1: Write the implementation**

```python
"""
GUI 入口文件。

启动系统托盘和 Worker 服务。
"""

import logging
import os
import sys
import threading

from PyQt5.QtWidgets import QApplication, QMessageBox, QDialog

from worker.config import load_config, get_default_config_path
from worker.logger import setup_logging
from worker.worker import Worker
from worker.tray_manager import TrayManager
from worker.settings_window import SettingsWindow
from worker.upgrade_manager import UpgradeManager, UpgradeInfo
from worker.download_dialog import DownloadDialog
from worker.single_instance import check_single_instance, release_instance_lock

logger = logging.getLogger(__name__)


class GUIApp:
    """GUI 应用。"""

    def __init__(self):
        """初始化应用。"""
        # EXE 运行时设置 Playwright 浏览器路径
        if getattr(sys, "frozen", False):
            app_dir = os.path.dirname(sys.executable)
            playwright_path = os.path.join(app_dir, "playwright")
            os.environ["PLAYWRIGHT_BROWSERS_PATH"] = playwright_path

        # 加载配置
        self.config = load_config()
        self.config_path = get_default_config_path()

        # 初始化日志
        log_path = setup_logging(
            level=self.config.log_level,
            log_file=self.config.log_file,
            max_bytes=self.config.log_max_size,
            backup_count=self.config.log_backup_count,
        )
        self.log_dir = os.path.dirname(log_path) if log_path else os.path.join(
            os.path.dirname(sys.executable) if getattr(sys, "frozen", False) else os.getcwd(), "logs"
        )

        # Worker
        self.worker: Worker = None
        self.worker_thread: threading.Thread = None

        # PyQt 应用
        self.qt_app = QApplication(sys.argv)

        # 托盘管理器
        icon_path = self._get_icon_path()
        self.tray_manager = TrayManager(
            icon_path=icon_path,
            status_callback=self._get_worker_status,
            on_upgrade=self._on_upgrade,
            on_restart=self._on_restart,
            on_settings=self._on_settings,
            on_exit=self._on_exit,
        )

        # 升级管理器
        self.upgrade_manager = UpgradeManager(
            check_url=self.config.upgrade_check_url,
            current_version=self._get_current_version(),
            check_timeout=self.config.upgrade_check_timeout,
            download_timeout=self.config.upgrade_download_timeout,
        )

    def _get_icon_path(self) -> str:
        """获取图标路径。"""
        if getattr(sys, "frozen", False):
            # EXE 运行
            app_dir = os.path.dirname(sys.executable)
            return os.path.join(app_dir, "assets", "icon.png")
        else:
            # 源码运行
            return os.path.join(os.path.dirname(os.path.dirname(__file__)), "assets", "icon.png")

    def _get_current_version(self) -> str:
        """获取当前版本。"""
        try:
            from worker._version import VERSION
            return VERSION
        except ImportError:
            return "0"

    def _get_worker_status(self) -> str:
        """获取 Worker 状态。"""
        if self.worker and self.worker.status == "online":
            return "运行中"
        return "已停止"

    def _start_worker(self):
        """启动 Worker。"""
        if self.worker and self.worker.status == "online":
            logger.warning("Worker already running")
            return

        # 创建 Worker
        self.worker = Worker(self.config)

        # 启动 Worker（在后台线程）
        def run_worker():
            try:
                self.worker.start()

                # 导入并设置 server
                from worker.server import app, set_worker
                set_worker(self.worker)

                # 启动 HTTP Server
                import uvicorn
                uvicorn.run(
                    app,
                    host="0.0.0.0",
                    port=self.config.port,
                    log_level=self.config.log_level.lower(),
                )
            except Exception as e:
                logger.error(f"Failed to start worker: {e}")
                self._show_error(f"Worker 启动失败: {e}")

        self.worker_thread = threading.Thread(target=run_worker, daemon=True)
        self.worker_thread.start()

        # 等待 Worker 启动
        import time
        for _ in range(10):
            if self.worker and self.worker.status == "online":
                break
            time.sleep(0.5)

        self.tray_manager.update_tooltip()

    def _stop_worker(self, timeout: int = 10):
        """停止 Worker。"""
        if not self.worker:
            return

        if self.worker:
            try:
                self.worker.stop()
            except Exception as e:
                logger.error(f"Failed to stop worker: {e}")

        self.worker = None
        self.tray_manager.update_tooltip()

    def _show_message(self, title: str, message: str):
        """显示消息对话框。"""
        QMessageBox.information(None, title, message)

    def _show_error(self, message: str):
        """显示错误对话框。"""
        QMessageBox.warning(None, "错误", message)

    def _show_question(self, title: str, message: str) -> bool:
        """显示确认对话框。"""
        reply = QMessageBox.question(
            None, title, message,
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No
        )
        return reply == QMessageBox.Yes

    def _on_upgrade(self):
        """升级菜单点击。"""
        try:
            info = self.upgrade_manager.check_upgrade()

            if info:
                # 有新版本
                reply = self._show_question(
                    "升级",
                    f"发现新版本 v{info.version}\n是否升级？"
                )

                if reply:
                    self._do_upgrade(info)
            else:
                self._show_message("升级", "已是最新版本")

        except Exception as e:
            self._show_error(f"升级检查失败: {e}")

    def _do_upgrade(self, info: UpgradeInfo):
        """执行升级。"""
        # 显示下载对话框
        dialog = DownloadDialog(
            version=info.version,
            download_url=info.download_url,
        )

        result = dialog.exec_()

        if result == QDialog.Accepted:
            # 下载完成
            installer_path = dialog.get_downloaded_file()

            # 停止 Worker 和托盘
            self._stop_worker()
            self.tray_manager.stop()

            # 执行静默安装
            install_dir = os.path.dirname(sys.executable) if getattr(sys, "frozen", False) else None
            self.upgrade_manager.run_silent_install(installer_path, install_dir)

            # 退出程序
            sys.exit(0)

        elif dialog.was_cancelled():
            self._show_message("升级", "下载已取消")

        else:
            self._show_error(f"下载失败: {dialog.get_error()}")

    def _on_restart(self):
        """重启菜单点击。"""
        reply = self._show_question("重启", "是否重启 Worker 服务？")

        if reply:
            self._stop_worker()

            # 重新加载配置
            self.config = load_config()

            self._start_worker()
            self._show_message("重启", "Worker 服务已重启")

    def _on_settings(self):
        """设置菜单点击。"""
        dialog = SettingsWindow(self.config_path)

        if dialog.exec_() == QDialog.Accepted:
            # 配置已保存，重启 Worker
            self.config = load_config()
            self._stop_worker()
            self._start_worker()

    def _on_exit(self):
        """退出菜单点击。"""
        reply = self._show_question("退出", "是否退出 Test Worker？")

        if reply:
            self._stop_worker()
            self.tray_manager.stop()
            release_instance_lock()
            self.qt_app.quit()

    def run(self):
        """运行应用。"""
        # 检查单实例
        if not check_single_instance():
            QMessageBox.warning(None, "警告", "Test Worker 已在运行")
            sys.exit(1)

        # 启动 Worker
        self._start_worker()

        # 启动托盘（在后台线程）
        tray_thread = threading.Thread(target=self.tray_manager.start, daemon=False)
        tray_thread.start()

        # 运行 Qt 应用（主线程）
        self.qt_app.exec_()

        # 清理
        self._stop_worker()
        release_instance_lock()


def main():
    """主函数。"""
    app = GUIApp()
    app.run()


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Commit**

```bash
git add worker/gui_main.py
git commit -m "feat: 添加 GUI 入口模块"
```

---

## Task 9: 修改 PyInstaller 配置

**Files:**
- Modify: `scripts/pyinstaller.spec`

- [ ] **Step 1: 添加图标数据文件**

在 `datas` 列表中添加：

```python
datas = [
    (os.path.join(PROJECT_ROOT, 'config'), 'config'),
    (os.path.join(PROJECT_ROOT, 'assets'), 'assets'),  # 新增：图标文件
]
```

- [ ] **Step 2: 添加 GUI 组件隐藏导入**

在 `hiddenimports` 列表中添加：

```python
hiddenimports = [
    # ... 现有导入 ...
    # GUI 组件（新增）
    'pystray',
    'PIL',
    'PIL.Image',
    'PyQt5',
    'PyQt5.QtCore',
    'PyQt5.QtGui',
    'PyQt5.QtWidgets',
    'PyQt5.sip',
]
```

- [ ] **Step 3: 修改 EXE 配置**

修改 `scripts/pyinstaller.spec` 中的 EXE 定义：

```python
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='test-worker',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,        # 新增：关闭 CMD 窗口
    uac_admin=True,       # 新增：管理员权限
    icon=os.path.join(PROJECT_ROOT, 'assets', 'icon.ico'),  # 新增：EXE 图标（使用绝对路径）
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
```

- [ ] **Step 4: Commit**

```bash
git add scripts/pyinstaller.spec
git commit -m "feat: PyInstaller 配置添加管理员权限、图标和 GUI 隐藏导入"
```

---

## Task 10: 修改 installer.iss

**Files:**
- Modify: `installer/installer.iss`

- [ ] **Step 1: 添加注册表配置**

在现有 `[Registry]` 部分后添加（如果没有则新建）：

```iss
[Registry]
Root: HKLM; Subkey: "Software\Test Worker"; ValueType: string; ValueName: "InstallPath"; ValueData: "{app}"
```

- [ ] **Step 2: 修改 CurStepChanged 函数**

找到现有的 `CurStepChanged` 函数，在 `if CurStep = ssPostInstall then` 块的开头添加静默安装启动逻辑：

```pascal
procedure CurStepChanged(CurStep: TSetupStep);
var
  ConfigFile: String;
  ConfigContent: String;
begin
  if CurStep = ssPostInstall then
  begin
    // 静默安装模式下自动启动（添加在开头）
    if WizardSilent then
      ShellExec('', ExpandConstant('{app}\test-worker.exe'), '', '', SW_HIDE, ewNoWait, 0);

    // 原有配置写入逻辑保持不变
    if not IsUpgradeInstall() then
    begin
      // ... 现有代码 ...
    end;
  end;
end;
```

- [ ] **Step 3: Commit**

```bash
git add installer/installer.iss
git commit -m "feat: 安装脚本添加静默安装自动启动和注册表配置"
```

---

## Task 11: 修改配置文件模板

**Files:**
- Modify: `config/worker.yaml`（文件末尾）

- [ ] **Step 1: 添加 upgrade 配置项**

在 `config/worker.yaml` 文件末尾添加：

```yaml
# 升级配置
upgrade:
  check_url: ""           # 升级检查接口地址，如 "http://192.168.0.102:8000/get_worker_upgrade"
  check_timeout: 30       # 升级检查超时（秒）
  download_timeout: 300   # 下载超时（秒）
```

- [ ] **Step 2: Commit**

```bash
git add config/worker.yaml
git commit -m "feat: 配置文件添加升级配置项"
```

---

## Task 12: 测试打包

**Files:**
- None

- [ ] **Step 1: 运行打包脚本**

Run: `powershell scripts/build_windows.ps1`
Expected: 打包成功，生成 `dist/windows/test-worker/` 目录

- [ ] **Step 2: 检查打包输出**

Run: `ls dist/windows/test-worker/`
Expected: 目录包含 `test-worker.exe`、`assets/icon.png`、`assets/icon.ico`

- [ ] **Step 3: 测试运行**

手动测试：
1. 双击 `test-worker.exe`，检查是否弹出 UAC 提示
2. 确认无 CMD 窗口显示
3. 检查系统托盘是否显示图标
4. 测试托盘菜单功能（升级、重启、日志、设置、退出）

- [ ] **Step 4: Final Commit**

```bash
git add -A
git commit -m "feat: Windows 系统托盘 GUI 完成"
```

---

## 注意事项

1. **GUI 测试说明**：PyQt5 GUI 组件（Task 4、5、6、8）使用手动测试而非单元测试，原因：
   - GUI 测试需要显示环境，CI 环境不支持
   - PyQt5 测试框架复杂，维护成本高
   - 关键业务逻辑已通过核心模块测试覆盖
   - 手动测试清单见 Task 12

2. **图标格式说明**：
   - 托盘图标使用 `icon.png`（pystray 支持 PNG 格式）
   - EXE 图标使用 `icon.ico`（Windows 要求 ICO 格式）
   - 两个文件都需放在 `assets/` 目录
   - **准备 PNG 图标**：可将 `icon.ico` 转换为 `icon.png`：
     ```python
     from PIL import Image
     img = Image.open('assets/icon.ico')
     img.save('assets/icon.png')
     ```

3. **版本号文件**：`worker/_version.py` 在打包时由 `build_windows.ps1` 动态生成，无需手动创建

4. **线程安全**：Worker 和托盘在不同线程，注意状态同步

5. **异常处理**：所有回调函数需要捕获异常，避免程序崩溃

6. **PyQt5 依赖**：打包时需要确保 PyQt5 正确包含