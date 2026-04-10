"""
GUI 入口模块。

整合托盘、Worker、升级管理器、设置窗口，提供完整的应用生命周期管理。
"""

import logging
import os
import sys
import threading
import tempfile

import uvicorn
from PyQt5.QtWidgets import QApplication, QMessageBox
from PyQt5.QtCore import Qt

from worker.config import load_config, get_default_config_path, WorkerConfig
from worker.logger import setup_logging
from worker.worker import Worker
from worker.server import app, set_worker
from worker.single_instance import check_single_instance, release_instance_lock
from worker.tray_manager import TrayManager
from worker.upgrade_manager import UpgradeManager, UpgradeInfo, DownloadError, InstallError
from worker.download_dialog import DownloadDialog
from worker.settings_window import SettingsWindow

logger = logging.getLogger(__name__)


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
        # EXE 运行时设置 Playwright 浏览器路径
        if getattr(sys, 'frozen', False):
            app_dir = os.path.dirname(sys.executable)
            playwright_path = os.path.join(app_dir, 'playwright')
            os.environ['PLAYWRIGHT_BROWSERS_PATH'] = playwright_path
            logger.info(f"Playwright browsers path: {playwright_path}")

        # 加载配置
        self.config: WorkerConfig = load_config()
        logger.info(f"Config loaded: port={self.config.port}")

        # 初始化日志
        log_path = setup_logging(
            level=self.config.log_level,
            log_file=self.config.log_file,
            max_bytes=self.config.log_max_size,
            backup_count=self.config.log_backup_count,
        )
        logger.info(f"Log file: {log_path}")

        # 创建 Qt 应用
        self.app = QApplication(sys.argv)
        self.app.setQuitOnLastWindowClosed(False)  # 关闭窗口不退出应用

        # 获取图标路径
        icon_path = self._get_icon_path()
        logger.info(f"Icon path: {icon_path}")

        # 创建托盘管理器
        self.tray_manager = TrayManager(
            icon_path=icon_path,
            status_callback=self._get_worker_status,
            on_upgrade=self._on_upgrade,
            on_restart=self._on_restart,
            on_settings=self._on_settings,
            on_exit=self._on_exit,
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
        """
        获取图标路径。

        EXE 运行时使用 exe 所在目录的 assets/icon.ico，
        源码运行时使用项目根目录的 assets/icon.ico。

        Returns:
            str: 图标文件路径
        """
        if getattr(sys, 'frozen', False):
            # EXE 运行
            base_dir = os.path.dirname(sys.executable)
        else:
            # 源码运行
            base_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

        return os.path.join(base_dir, "assets", "icon.ico")

    def _get_current_version(self) -> str:
        """
        获取当前版本号。

        从 worker._version.py 导入 VERSION，不存在时返回 "0"。

        Returns:
            str: 版本号
        """
        try:
            from worker._version import VERSION
            return VERSION
        except ImportError:
            return "0"

    def _get_worker_status(self) -> str:
        """
        获取 Worker 状态。

        Returns:
            str: 状态文本（"运行中" 或 "已停止"）
        """
        if self.worker and self._server_running:
            return "运行中"
        return "已停止"

    def _start_worker(self) -> None:
        """
        启动 Worker。

        在后台线程启动 HTTP Server。
        """
        if self._server_running:
            logger.warning("Worker already running")
            return

        # 创建 Worker
        self.worker = Worker(self.config)

        # 启动 Worker（初始化平台管理器等）
        try:
            self.worker.start()
        except Exception as e:
            logger.error(f"Failed to start worker: {e}")
            self._show_error(f"启动 Worker 失败: {e}")
            return

        # 设置 Worker 实例到 Server
        set_worker(self.worker)

        # 启动 HTTP Server（后台线程）
        self._server_thread = threading.Thread(
            target=self._run_server,
            daemon=True,
        )
        self._server_thread.start()
        self._server_running = True

        logger.info("Worker started")
        self.tray_manager.update_tooltip()

    def _run_server(self) -> None:
        """运行 HTTP Server（后台线程）。"""
        try:
            uvicorn.run(
                app,
                host="0.0.0.0",
                port=self.config.port,
                log_level=self.config.log_level.lower(),
            )
        except Exception as e:
            logger.error(f"HTTP Server error: {e}")
            self._server_running = False
            self.tray_manager.update_tooltip()

    def _stop_worker(self) -> None:
        """停止 Worker。"""
        if not self._server_running:
            logger.warning("Worker not running")
            return

        # 停止 Worker
        if self.worker:
            try:
                self.worker.stop()
            except Exception as e:
                logger.error(f"Failed to stop worker: {e}")

        self.worker = None
        self._server_running = False

        logger.info("Worker stopped")
        self.tray_manager.update_tooltip()

    def _show_message(self, title: str, message: str) -> None:
        """
        显示消息对话框。

        Args:
            title: 标题
            message: 消息内容
        """
        QMessageBox.information(None, title, message)

    def _show_error(self, message: str) -> None:
        """
        显示错误对话框。

        Args:
            message: 错误信息
        """
        QMessageBox.critical(None, "错误", message)

    def _show_question(self, title: str, message: str) -> bool:
        """
        显示确认对话框。

        Args:
            title: 标题
            message: 消息内容

        Returns:
            bool: 用户是否确认
        """
        reply = QMessageBox.question(
            None,
            title,
            message,
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        return reply == QMessageBox.Yes

    def _on_upgrade(self) -> None:
        """
        升级菜单点击回调。

        检查更新，有新版本则调用 _do_upgrade。
        """
        # 检查升级 URL 是否配置
        if not self.config.upgrade_check_url:
            self._show_message("升级", "未配置升级检查 URL，请在设置中配置")
            return

        # 检查更新
        try:
            upgrade_info = self.upgrade_manager.check_upgrade()

            if upgrade_info:
                # 有新版本
                current_version = self._get_current_version()
                reply = self._show_question(
                    "升级",
                    f"发现新版本 {upgrade_info.version}\n"
                    f"当前版本: {current_version}\n"
                    f"是否立即升级？"
                )

                if reply:
                    self._do_upgrade(upgrade_info)
            else:
                # 无新版本
                self._show_message("升级", f"当前版本已是最新 ({self._get_current_version()})")

        except Exception as e:
            logger.error(f"检查更新失败: {e}")
            self._show_error(f"检查更新失败: {e}")

    def _do_upgrade(self, upgrade_info: UpgradeInfo) -> None:
        """
        执行升级。

        显示下载对话框，下载完成后执行静默安装。

        Args:
            upgrade_info: 升级信息
        """
        # 获取临时保存路径
        temp_dir = tempfile.gettempdir()
        installer_path = os.path.join(temp_dir, "test-worker-installer.exe")

        # 显示下载对话框
        dialog = DownloadDialog(
            download_url=upgrade_info.download_url,
            save_path=installer_path,
            version=upgrade_info.version,
            timeout=self.config.upgrade_download_timeout,
        )
        dialog.exec_()

        # 检查下载结果
        downloaded_file = dialog.get_downloaded_file()

        if not downloaded_file:
            if dialog.was_cancelled():
                self._show_message("升级", "下载已取消")
            else:
                error = dialog.get_error() or "未知错误"
                self._show_error(f"下载失败: {error}")
            return

        # 执行静默安装
        try:
            self.upgrade_manager.run_silent_install(downloaded_file)
            self._show_message("升级", "安装程序已启动，Worker 将退出以完成安装")
            # 退出应用
            self._on_exit()
        except InstallError as e:
            logger.error(f"安装失败: {e}")
            self._show_error(f"安装失败: {e}")
        except Exception as e:
            logger.error(f"安装失败: {e}")
            self._show_error(f"安装失败: {e}")

    def _on_restart(self) -> None:
        """
        重启菜单点击回调。

        停止 Worker，重新加载配置，启动 Worker。
        """
        logger.info("Restarting Worker...")

        # 停止 Worker
        self._stop_worker()

        # 重新加载配置
        try:
            self.config = load_config()
            logger.info(f"Config reloaded: port={self.config.port}")
        except Exception as e:
            logger.error(f"重新加载配置失败: {e}")
            self._show_error(f"重新加载配置失败: {e}")
            return

        # 重新初始化日志
        setup_logging(
            level=self.config.log_level,
            log_file=self.config.log_file,
            max_bytes=self.config.log_max_size,
            backup_count=self.config.log_backup_count,
        )

        # 启动 Worker
        self._start_worker()

        self._show_message("重启", "Worker 已重启")

    def _on_settings(self) -> None:
        """
        设置菜单点击回调。

        打开设置窗口，保存后重启 Worker。
        """
        config_path = get_default_config_path()

        # 创建设置窗口
        dialog = SettingsWindow(config_path)
        result = dialog.exec_()

        # 如果保存成功，重启 Worker
        if result == dialog.Accepted:
            logger.info("Settings saved, restarting Worker...")
            self._on_restart()

    def _on_exit(self) -> None:
        """
        退出菜单点击回调。

        停止 Worker，释放单实例锁，退出应用。
        """
        logger.info("Exiting application...")

        # 停止 Worker
        self._stop_worker()

        # 停止托盘
        self.tray_manager.stop()

        # 释放单实例锁
        release_instance_lock()

        # 退出 Qt 应用
        self.app.quit()

        logger.info("Application exited")

    def run(self) -> int:
        """
        运行 GUI 应用。

        检查单实例 -> 启动 Worker -> 启动托盘 -> 运行 Qt 应用 -> 清理

        Returns:
            int: 退出码
        """
        # 检查单实例
        if not check_single_instance():
            logger.warning("Another instance is already running")
            self._show_error("已有一个实例运行")
            return 1

        # 启动 Worker
        self._start_worker()

        # 启动托盘（阻塞）
        logger.info("Starting tray icon...")
        tray_thread = threading.Thread(target=self.tray_manager.start, daemon=True)
        tray_thread.start()

        # 运行 Qt 应用（阻塞）
        exit_code = self.app.exec_()

        # 清理（托盘已通过 _on_exit 停止）
        logger.info(f"Application exiting with code {exit_code}")

        return exit_code


def main():
    """GUI 主函数。"""
    gui_app = GUIApp()
    exit_code = gui_app.run()
    sys.exit(exit_code)


if __name__ == "__main__":
    main()