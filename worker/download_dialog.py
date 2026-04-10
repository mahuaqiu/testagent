"""
下载进度对话框模块。

提供 PyQt5 下载进度对话框，支持取消下载。
"""

import os
import logging
import httpx
from typing import Optional

from PyQt5.QtWidgets import (
    QDialog,
    QVBoxLayout,
    QLabel,
    QProgressBar,
    QPushButton,
    QHBoxLayout,
)
from PyQt5.QtCore import (
    QThread,
    pyqtSignal,
    Qt,
)

logger = logging.getLogger(__name__)


class DownloadThread(QThread):
    """
    后台下载线程。

    使用 httpx.stream 进行流式下载，支持进度更新和取消。
    """

    # 信号定义
    progress_signal = pyqtSignal(int, int)  # (已下载字节, 总字节)
    finished_signal = pyqtSignal(str)  # 文件路径
    error_signal = pyqtSignal(str)  # 错误信息
    cancelled_signal = pyqtSignal()  # 取消信号

    def __init__(
        self,
        download_url: str,
        save_path: str,
        timeout: float = 300.0,
        parent=None
    ):
        """
        初始化下载线程。

        Args:
            download_url: 下载地址
            save_path: 保存路径
            timeout: 超时时间（秒）
            parent: 父对象
        """
        super().__init__(parent)
        self.download_url = download_url
        self.save_path = save_path
        self.timeout = timeout
        self._is_cancelled = False

    def run(self) -> None:
        """执行下载。"""
        logger.info(f"开始下载: {self.download_url}")
        logger.info(f"保存路径: {self.save_path}")

        try:
            with httpx.Client(
                timeout=self.timeout,
                trust_env=False,
                follow_redirects=True
            ) as client:
                # 使用流式下载获取总大小
                with client.stream("GET", self.download_url) as response:
                    response.raise_for_status()

                    # 获取文件总大小
                    total_size = int(response.headers.get("content-length", 0))
                    downloaded = 0

                    # 确保目录存在
                    os.makedirs(os.path.dirname(self.save_path), exist_ok=True)

                    # 写入文件
                    with open(self.save_path, "wb") as f:
                        for chunk in response.iter_bytes(chunk_size=8192):
                            # 检查是否取消
                            if self._is_cancelled:
                                logger.info("下载已取消")
                                self.cancelled_signal.emit()
                                # 删除未完成的文件
                                if os.path.exists(self.save_path):
                                    os.remove(self.save_path)
                                return

                            f.write(chunk)
                            downloaded += len(chunk)

                            # 发送进度
                            self.progress_signal.emit(downloaded, total_size)

                    # 下载完成
                    logger.info(f"下载完成: {downloaded} bytes")
                    self.finished_signal.emit(self.save_path)

        except httpx.HTTPStatusError as e:
            error_msg = f"下载失败 (HTTP {e.response.status_code}): {e}"
            logger.error(error_msg)
            self.error_signal.emit(error_msg)
        except httpx.RequestError as e:
            error_msg = f"下载请求失败: {e}"
            logger.error(error_msg)
            self.error_signal.emit(error_msg)
        except Exception as e:
            error_msg = f"下载失败: {e}"
            logger.error(error_msg)
            self.error_signal.emit(error_msg)

    def cancel(self) -> None:
        """取消下载。"""
        self._is_cancelled = True


class DownloadDialog(QDialog):
    """
    下载进度对话框。

    显示下载进度条、版本信息和取消按钮。
    """

    def __init__(
        self,
        download_url: str,
        save_path: str,
        version: str = "",
        timeout: float = 300.0,
        parent=None
    ):
        """
        初始化下载对话框。

        Args:
            download_url: 下载地址
            save_path: 保存路径
            version: 版本号（可选）
            timeout: 超时时间（秒）
            parent: 父窗口
        """
        super().__init__(parent)
        self.download_url = download_url
        self.save_path = save_path
        self.version = version
        self.timeout = timeout

        # 下载结果
        self._downloaded_file: Optional[str] = None
        self._was_cancelled = False
        self._error_message: Optional[str] = None

        # 下载线程
        self._download_thread: Optional[DownloadThread] = None

        self._setup_ui()
        self._start_download()

    def _setup_ui(self) -> None:
        """设置 UI。"""
        self.setWindowTitle("下载更新")
        self.setMinimumWidth(400)
        self.setWindowFlags(self.windowFlags() & ~Qt.WindowCloseButtonHint)

        layout = QVBoxLayout(self)
        layout.setSpacing(10)

        # 版本标签
        if self.version:
            version_label = QLabel(f"正在下载版本 {self.version}")
            version_label.setStyleSheet("font-weight: bold;")
            layout.addWidget(version_label)

        # 进度条
        self.progress_bar = QProgressBar()
        self.progress_bar.setMinimum(0)
        self.progress_bar.setMaximum(0)  # 初始为不确定进度
        layout.addWidget(self.progress_bar)

        # 进度文本
        self.progress_label = QLabel("正在连接...")
        self.progress_label.setAlignment(Qt.AlignCenter)
        layout.addWidget(self.progress_label)

        # 取消按钮
        button_layout = QHBoxLayout()
        button_layout.addStretch()
        self.cancel_button = QPushButton("取消")
        self.cancel_button.clicked.connect(self._on_cancel)
        button_layout.addWidget(self.cancel_button)
        button_layout.addStretch()
        layout.addLayout(button_layout)

    def _start_download(self) -> None:
        """启动下载线程。"""
        self._download_thread = DownloadThread(
            self.download_url,
            self.save_path,
            self.timeout,
            parent=self
        )

        # 连接信号
        self._download_thread.progress_signal.connect(self._on_progress)
        self._download_thread.finished_signal.connect(self._on_finished)
        self._download_thread.error_signal.connect(self._on_error)
        self._download_thread.cancelled_signal.connect(self._on_cancelled)

        # 启动线程
        self._download_thread.start()

    def _on_progress(self, downloaded: int, total: int) -> None:
        """
        更新进度。

        Args:
            downloaded: 已下载字节数
            total: 总字节数
        """
        if total > 0:
            self.progress_bar.setMaximum(total)
            self.progress_bar.setValue(downloaded)

            # 格式化显示
            downloaded_mb = downloaded / (1024 * 1024)
            total_mb = total / (1024 * 1024)
            percent = int(downloaded / total * 100)
            self.progress_label.setText(
                f"{downloaded_mb:.1f} MB / {total_mb:.1f} MB ({percent}%)"
            )
        else:
            # 无法获取总大小时
            downloaded_mb = downloaded / (1024 * 1024)
            self.progress_label.setText(f"已下载 {downloaded_mb:.1f} MB")

    def _on_finished(self, file_path: str) -> None:
        """
        下载完成处理。

        Args:
            file_path: 下载文件路径
        """
        self._downloaded_file = file_path
        self.progress_label.setText("下载完成")
        self.cancel_button.setText("关闭")
        self.accept()

    def _on_error(self, error_message: str) -> None:
        """
        下载错误处理。

        Args:
            error_message: 错误信息
        """
        self._error_message = error_message
        self.progress_label.setText(f"下载失败: {error_message}")
        self.progress_bar.setStyleSheet("QProgressBar::chunk { background-color: red; }")
        self.cancel_button.setText("关闭")

    def _on_cancel(self) -> None:
        """取消按钮点击处理。"""
        if self._download_thread and self._download_thread.isRunning():
            # 正在下载，取消下载
            self._was_cancelled = True
            self.cancel_button.setEnabled(False)
            self.progress_label.setText("正在取消...")
            self._download_thread.cancel()
        else:
            # 下载已完成或失败，关闭对话框
            self.reject()

    def _on_cancelled(self) -> None:
        """下载已取消处理。"""
        self.progress_label.setText("下载已取消")
        self.reject()

    def get_downloaded_file(self) -> Optional[str]:
        """
        获取下载文件路径。

        Returns:
            str | None: 下载文件路径，未完成或取消时返回 None
        """
        return self._downloaded_file

    def was_cancelled(self) -> bool:
        """
        检查是否被取消。

        Returns:
            bool: 是否被取消
        """
        return self._was_cancelled

    def get_error(self) -> Optional[str]:
        """
        获取错误信息。

        Returns:
            str | None: 错误信息，无错误时返回 None
        """
        return self._error_message

    def closeEvent(self, event) -> None:
        """
        关闭事件处理。

        Args:
            event: 关闭事件
        """
        if self._download_thread and self._download_thread.isRunning():
            # 正在下载时禁止关闭
            event.ignore()
        else:
            event.accept()