"""
GUI 入口模块。

整合托盘、Worker、升级管理器、设置窗口，提供完整的应用生命周期管理。
"""

import sys

# 在导入其他模块之前先创建 QApplication（避免黑框闪烁）
from PyQt5.QtWidgets import QApplication
_app = QApplication(sys.argv)
_app.setQuitOnLastWindowClosed(False)

# 然后再导入其他模块
import logging
import os
import threading
import tempfile

import uvicorn
from PyQt5.QtWidgets import (
    QMessageBox,
    QDialog,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QWidget,
    QProgressBar,
)
from PyQt5.QtCore import Qt, QObject, pyqtSignal, QTimer
from PyQt5.QtGui import QIcon, QFont

from worker.config import load_config, WorkerConfig
from worker.logger import setup_logging
from worker.worker import Worker
from worker.server import app, set_worker, set_gui_app
from worker.single_instance import check_single_instance, release_instance_lock
from worker.tray_manager import TrayManager
from worker.upgrade_manager import UpgradeManager, UpgradeInfo, DownloadError, InstallError
from worker.download_dialog import DownloadDialog
from worker.settings_window import SettingsWindow

logger = logging.getLogger(__name__)


class SplashScreen(QWidget):
    """启动画面。"""

    def __init__(self, icon_path: str = None):
        super().__init__()
        self.setFixedSize(300, 120)
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint)
        self.setAttribute(Qt.WA_TranslucentBackground, False)

        # 设置窗口图标
        if icon_path:
            self.setWindowIcon(QIcon(icon_path))

        layout = QVBoxLayout(self)
        layout.setSpacing(15)
        layout.setContentsMargins(20, 20, 20, 20)

        # 标题
        title_label = QLabel("Test Worker")
        title_label.setAlignment(Qt.AlignCenter)
        title_label.setStyleSheet("font-size: 18px; font-weight: bold; color: #333333;")
        layout.addWidget(title_label)

        # 状态文本
        self.status_label = QLabel("启动中...")
        self.status_label.setAlignment(Qt.AlignCenter)
        self.status_label.setStyleSheet("font-size: 14px; color: #666666;")
        layout.addWidget(self.status_label)

        # 进度条（动画效果）
        self.progress = QProgressBar()
        self.progress.setRange(0, 0)  # 无限滚动模式
        self.progress.setFixedHeight(6)
        self.progress.setStyleSheet("""
            QProgressBar {
                border: none;
                background-color: #e0e0e0;
                border-radius: 3px;
            }
            QProgressBar::chunk {
                background-color: #1a73e8;
                border-radius: 3px;
            }
        """)
        layout.addWidget(self.progress)

        # 设置整体样式
        self.setStyleSheet("""
            QWidget {
                background-color: #ffffff;
                border-radius: 10px;
            }
        """)

    def update_status(self, text: str):
        """更新状态文本。"""
        self.status_label.setText(text)

    def close_splash(self):
        """关闭启动画面。"""
        self.progress.setRange(0, 100)
        self.progress.setValue(100)
        QTimer.singleShot(300, self.close)


class UISignals(QObject):
    """UI 信号管理器，用于跨线程通信。"""

    show_settings = pyqtSignal()
    show_restart_confirm = pyqtSignal()
    show_config_restart = pyqtSignal()    # 配置更新后的重启信号
    show_upgrade = pyqtSignal()
    show_exit_confirm = pyqtSignal()


class GUIApp:
    """
    GUI 应用管理器。

    负责：
    - 单实例检查
    - Worker 生命周期管理
    - 系统托盘管理
    - 升级管理
    - 设置窗口
    """

    def __init__(self):
        """初始化 GUI 应用。"""
        # EXE 运行时设置 Playwright 浏览器路径（必须在最开始）
        if getattr(sys, 'frozen', False):
            app_dir = os.path.dirname(sys.executable)
            playwright_path = os.path.join(app_dir, 'playwright')
            os.environ['PLAYWRIGHT_BROWSERS_PATH'] = playwright_path

        # 获取图标路径
        self._icon_path = self._get_icon_path()

        # 使用预先创建的 QApplication（避免黑框闪烁）
        self.app = _app

        # 立即显示启动画面
        self._splash = SplashScreen(self._icon_path)
        self._splash.show()
        self.app.processEvents()

        # 创建 UI 信号管理器
        self.ui_signals = UISignals()
        self.ui_signals.show_settings.connect(self._show_settings_dialog)
        self.ui_signals.show_restart_confirm.connect(self._show_restart_dialog)
        self.ui_signals.show_config_restart.connect(self._do_restart)
        self.ui_signals.show_upgrade.connect(self._on_upgrade_internal)
        self.ui_signals.show_exit_confirm.connect(self._show_exit_dialog)

        # 更新启动状态
        self._splash.update_status("加载配置...")
        self.app.processEvents()

        # 加载配置
        self.config: WorkerConfig = load_config()

        # 初始化日志
        self._splash.update_status("初始化日志...")
        self.app.processEvents()
        self._log_path = setup_logging(
            level=self.config.log_level,
            log_file=self.config.log_file,
            max_bytes=self.config.log_max_size,
            backup_count=self.config.log_backup_count,
        )

        # 创建托盘管理器（回调发送信号，不直接操作 UI）
        self._splash.update_status("初始化托盘...")
        self.app.processEvents()
        self.tray_manager = TrayManager(
            icon_path=self._icon_path,
            status_callback=self._get_worker_status,
            on_upgrade=lambda: self.ui_signals.show_upgrade.emit(),
            on_restart=lambda: self.ui_signals.show_restart_confirm.emit(),
            on_settings=lambda: self.ui_signals.show_settings.emit(),
            on_exit=lambda: self.ui_signals.show_exit_confirm.emit(),
        )

        # 创建升级管理器
        self.upgrade_manager = UpgradeManager(
            check_url=self.config.upgrade_check_url,
            current_version=self._get_current_version(),
            check_timeout=self.config.upgrade_check_timeout,
            download_timeout=self.config.upgrade_download_timeout,
        )

        # Worker 实例
        self.worker: Worker = None

        # HTTP Server 状态
        self._server_running = False
        self._server_thread: threading.Thread = None

        # 打印启动信息
        logger.info("=" * 50)
        logger.info("Test Worker GUI Starting...")
        logger.info(f"Worker ID: {self.config.id}")
        logger.info(f"Port: {self.config.port}")
        logger.info(f"Platform API: {self.config.platform_api or 'Not configured'}")
        logger.info(f"OCR Service: {self.config.ocr_service or 'Not configured'}")
        logger.info(f"Upgrade URL: {self.config.upgrade_check_url or 'Not configured'}")
        logger.info("=" * 50)

    def _get_icon_path(self) -> str:
        """获取图标路径（PNG 格式，pystray 需要）。"""
        if getattr(sys, 'frozen', False):
            base_dir = os.path.dirname(sys.executable)
            return os.path.join(base_dir, "_internal", "assets", "icon.png")
        else:
            base_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
            return os.path.join(base_dir, "assets", "icon.png")

    def _get_current_version(self) -> str:
        """获取当前版本号。"""
        try:
            from worker._version import VERSION
            return VERSION
        except ImportError:
            return "0"

    def _get_worker_status(self) -> str:
        """获取 Worker 状态。"""
        if self.worker and self._server_running:
            return "运行中"
        return "已停止"

    def _start_worker(self) -> None:
        """启动 Worker。"""
        if self._server_running:
            logger.warning("Worker already running")
            return

        try:
            self.worker = Worker(self.config, log_path=self._log_path)
            self.worker.start()
        except Exception as e:
            logger.error(f"Failed to start worker: {e}")
            self._show_error_dialog("启动 Worker 失败", str(e))
            return

        set_worker(self.worker)
        set_gui_app(self)

        self._server_thread = threading.Thread(target=self._run_server, daemon=True)
        self._server_thread.start()
        self._server_running = True

        logger.info("Worker started")
        self.tray_manager.update_tooltip()

    def _run_server(self) -> None:
        """运行 HTTP Server（后台线程）。"""
        try:
            log_config = {
                "version": 1,
                "disable_existing_loggers": False,
                "formatters": {
                    "default": {
                        "()": logging.Formatter,
                        "format": "%(asctime)s - %(name)s - %(levelname)s - %(message)s",
                    },
                },
                "handlers": {
                    "default": {
                        "class": logging.StreamHandler,
                        "formatter": "default",
                        "stream": "ext://sys.stderr",
                    },
                },
                "loggers": {
                    "uvicorn": {"handlers": ["default"], "level": "INFO", "propagate": False},
                    "uvicorn.error": {"handlers": ["default"], "level": "INFO", "propagate": False},
                    "uvicorn.access": {"handlers": ["default"], "level": "INFO", "propagate": False},
                },
            }

            config = uvicorn.Config(
                app=app,
                host="0.0.0.0",
                port=self.config.port,
                log_level=self.config.log_level.lower(),
                log_config=log_config,
                access_log=False,
            )
            server = uvicorn.Server(config)
            server.run()
        except Exception as e:
            logger.error(f"HTTP Server error: {e}")
            self._server_running = False
            self.tray_manager.update_tooltip()

    def _stop_worker(self) -> None:
        """停止 Worker。"""
        if not self._server_running:
            logger.warning("Worker not running")
            return

        if self.worker:
            try:
                self.worker.stop()
            except Exception as e:
                logger.error(f"Failed to stop worker: {e}")

        self.worker = None
        self._server_running = False

        logger.info("Worker stopped")
        self.tray_manager.update_tooltip()

    def _show_settings_dialog(self) -> None:
        """显示设置对话框（在 Qt 主线程中）。"""
        try:
            logger.info("Showing settings dialog")
            # 不再传入 config_path，让 SettingsWindow 内部获取
            dialog = SettingsWindow(icon_path=self._icon_path)
            result = dialog.exec_()

            if result == QDialog.Accepted:
                logger.info("Settings saved, restarting Worker...")
                self._do_restart()
        except Exception as e:
            logger.error(f"Settings dialog error: {e}")

    def _show_restart_dialog(self) -> None:
        """显示重启确认对话框（在 Qt 主线程中）。"""
        try:
            logger.info("Showing restart confirm dialog")
            dialog = ModernDialog("重启 Worker", "确定要重启 Worker 服务吗？", icon_path=self._icon_path)
            result = dialog.exec_()

            if result == QDialog.Accepted:
                logger.info("User confirmed restart")
                self._do_restart()
        except Exception as e:
            logger.error(f"Restart dialog error: {e}")

    def _do_restart(self) -> None:
        """执行重启操作。"""
        logger.info("Restarting Worker...")
        self._stop_worker()

        try:
            self.config = load_config()
            logger.info(f"Config reloaded: port={self.config.port}")
        except Exception as e:
            logger.error(f"重新加载配置失败: {e}")
            self._show_error_dialog("重新加载配置失败", str(e))
            return

        self._log_path = setup_logging(
            level=self.config.log_level,
            log_file=self.config.log_file,
            max_bytes=self.config.log_max_size,
            backup_count=self.config.log_backup_count,
        )

        self._start_worker()
        self._show_success_dialog("重启成功", "Worker 已重启")

    def _on_upgrade_internal(self) -> None:
        """升级操作（在 Qt 主线程中）。"""
        try:
            logger.info("Checking for upgrades")

            if not self.config.upgrade_check_url:
                self._show_info_dialog("升级", "未配置升级检查 URL")
                return

            upgrade_info = self.upgrade_manager.check_upgrade()

            if upgrade_info:
                current_version = self._get_current_version()
                dialog = ModernDialog(
                    "发现新版本",
                    f"发现新版本 {upgrade_info.version}\n"
                    f"当前版本: {current_version}\n\n"
                    f"是否立即升级？",
                    icon_path=self._icon_path,
                )
                result = dialog.exec_()

                if result == QDialog.Accepted:
                    self._do_upgrade(upgrade_info)
            else:
                self._show_info_dialog("升级", f"当前版本已是最新 ({self._get_current_version()})")

        except Exception as e:
            logger.error(f"检查更新失败: {e}")
            self._show_error_dialog("检查更新失败", str(e))

    def _do_upgrade(self, upgrade_info: UpgradeInfo) -> None:
        """执行升级。"""
        temp_dir = tempfile.gettempdir()
        installer_path = os.path.join(temp_dir, "test-worker-installer.exe")

        dialog = DownloadDialog(
            download_url=upgrade_info.download_url,
            save_path=installer_path,
            version=upgrade_info.version,
            timeout=self.config.upgrade_download_timeout,
        )
        dialog.exec_()

        downloaded_file = dialog.get_downloaded_file()

        if not downloaded_file:
            if dialog.was_cancelled():
                self._show_info_dialog("升级", "下载已取消")
            else:
                error = dialog.get_error() or "未知错误"
                self._show_error_dialog("下载失败", error)
            return

        try:
            self.upgrade_manager.run_silent_install(downloaded_file)
            self._show_info_dialog("升级", "安装程序已启动，Worker 将退出")
            self._do_exit()
        except Exception as e:
            logger.error(f"安装失败: {e}")
            self._show_error_dialog("安装失败", str(e))

    def _show_exit_dialog(self) -> None:
        """显示退出确认对话框（在 Qt 主线程中）。"""
        try:
            logger.info("Showing exit confirm dialog")
            dialog = ModernDialog("退出", "确定要退出 Test Worker 吗？", icon_path=self._icon_path)
            result = dialog.exec_()

            if result == QDialog.Accepted:
                logger.info("User confirmed exit")
                self._do_exit()
        except Exception as e:
            logger.error(f"Exit dialog error: {e}")

    def _do_exit(self) -> None:
        """执行退出操作。"""
        logger.info("Exiting application...")
        self._stop_worker()
        self.tray_manager.stop()
        release_instance_lock()
        self.app.quit()
        logger.info("Application exited")

    def _show_info_dialog(self, title: str, message: str) -> None:
        """显示信息对话框。"""
        dialog = ModernDialog(title, message, show_cancel=False, icon_path=self._icon_path)
        dialog.exec_()

    def _show_success_dialog(self, title: str, message: str) -> None:
        """显示成功对话框。"""
        dialog = ModernDialog(title, message, show_cancel=False, icon_path=self._icon_path)
        dialog.exec_()

    def _show_error_dialog(self, title: str, message: str) -> None:
        """显示错误对话框。"""
        dialog = ModernDialog(title, message, show_cancel=False, is_error=True, icon_path=self._icon_path)
        dialog.exec_()

    def run(self) -> int:
        """运行 GUI 应用。"""
        # 检查单实例（启动画面已在 __init__ 中显示）
        if not check_single_instance():
            logger.warning("Another instance is already running")
            self._splash.close()
            self._show_error_dialog("错误", "已有一个实例运行")
            return 1

        # 启动 Worker
        self._splash.update_status("启动 Worker 服务...")
        self.app.processEvents()
        self._start_worker()

        # 启动托盘
        self._splash.update_status("启动系统托盘...")
        self.app.processEvents()
        logger.info("Starting tray icon...")
        tray_thread = threading.Thread(target=self.tray_manager.start, daemon=True)
        tray_thread.start()

        # 等待一下让托盘启动
        import time
        time.sleep(0.5)

        # 关闭启动画面
        self._splash.update_status("启动完成")
        self.app.processEvents()
        self._splash.close_splash()

        exit_code = self.app.exec_()
        logger.info(f"Application exiting with code {exit_code}")
        return exit_code


class ModernDialog(QDialog):
    """现代化风格的对话框。"""

    def __init__(self, title: str, message: str, show_cancel: bool = True, is_error: bool = False, icon_path: str = None):
        super().__init__()
        self.setWindowTitle(title)
        self.setMinimumWidth(380)
        self.setMinimumHeight(140)
        self.setModal(True)

        # 移除右上角问号按钮，保留关闭按钮
        self.setWindowFlags(self.windowFlags() & ~Qt.WindowContextHelpButtonHint | Qt.CustomizeWindowHint | Qt.WindowCloseButtonHint | Qt.WindowTitleHint)

        # 设置窗口图标
        if icon_path:
            self.setWindowIcon(QIcon(icon_path))

        layout = QVBoxLayout(self)
        layout.setSpacing(20)
        layout.setContentsMargins(30, 30, 30, 25)

        # 消息内容（放大加粗，像启动弹窗一样）
        message_label = QLabel(message)
        message_label.setWordWrap(True)
        message_label.setAlignment(Qt.AlignCenter)
        message_label.setStyleSheet("font-size: 18px; font-weight: bold; line-height: 1.4;")
        layout.addWidget(message_label)

        layout.addStretch()

        # 按钮区域
        button_layout = QHBoxLayout()
        button_layout.setSpacing(15)

        if show_cancel:
            cancel_btn = QPushButton("取消")
            cancel_btn.setFixedSize(100, 36)
            cancel_btn.clicked.connect(self.reject)
            button_layout.addWidget(cancel_btn)
            button_layout.addStretch()

            ok_btn = QPushButton("确定")
            ok_btn.setFixedSize(100, 36)
            ok_btn.clicked.connect(self.accept)
            ok_btn.setDefault(True)
            button_layout.addWidget(ok_btn)
        else:
            button_layout.addStretch()
            ok_btn = QPushButton("确定")
            ok_btn.setFixedSize(100, 36)
            ok_btn.clicked.connect(self.accept)
            ok_btn.setDefault(True)
            button_layout.addWidget(ok_btn)

        layout.addLayout(button_layout)

        # 统一样式（不再区分错误和正常）
        base_style = """
            QDialog {
                background-color: #ffffff;
            }
            QLabel {
                color: #333333;
            }
            QPushButton {
                border: 1px solid #d0d0d0;
                border-radius: 6px;
                padding: 8px 20px;
                font-size: 14px;
                background-color: #f5f5f5;
                color: #333333;
            }
            QPushButton:hover {
                background-color: #e8e8e8;
                border: 1px solid #b0b0b0;
            }
            QPushButton:pressed {
                background-color: #d8d8d8;
            }
        """

        if is_error:
            # 错误对话框样式
            self.setStyleSheet(base_style + """
                QLabel {
                    color: #d32f2f;
                }
                QPushButton#ok {
                    background-color: #d32f2f;
                    color: #ffffff;
                    border: 1px solid #d32f2f;
                }
                QPushButton#ok:hover {
                    background-color: #b71c1c;
                    border: 1px solid #b71c1c;
                }
                QPushButton#ok:pressed {
                    background-color: #9a0007;
                }
            """)
        else:
            # 正常对话框样式
            self.setStyleSheet(base_style + """
                QPushButton#ok {
                    background-color: #1a73e8;
                    color: #ffffff;
                    border: 1px solid #1a73e8;
                }
                QPushButton#ok:hover {
                    background-color: #1557b0;
                    border: 1px solid #1557b0;
                }
                QPushButton#ok:pressed {
                    background-color: #0d47a1;
                }
            """)

        cancel_btn.setObjectName("cancel") if show_cancel else None
        ok_btn.setObjectName("ok")


def main():
    """GUI 主函数。"""
    gui_app = GUIApp()
    exit_code = gui_app.run()
    sys.exit(exit_code)


if __name__ == "__main__":
    main()