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

from common.packaging import is_packaged, get_base_dir

logger = logging.getLogger(__name__)


def _get_version() -> str:
    """获取当前版本号。"""
    try:
        from worker._version import VERSION
        return VERSION
    except ImportError:
        return "未知"


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
        logger.info(f"Loading icon from: {self.icon_path}")
        if os.path.exists(self.icon_path):
            try:
                image = Image.open(self.icon_path)
                logger.info(f"Icon loaded successfully: {image.size}")
                return image
            except Exception as e:
                logger.error(f"Failed to load icon: {e}")
                return Image.new("RGB", (64, 64), color="red")
        else:
            logger.warning(f"Icon file not found: {self.icon_path}")
            # 创建默认图标（红色方块）
            return Image.new("RGB", (64, 64), color="red")

    def _get_tooltip(self) -> str:
        """获取托盘提示文本。"""
        if self.status_callback:
            status = self.status_callback()
            return f"Test Worker - {status}"
        return "Test Worker"

    def _create_menu(self) -> pystray.Menu:
        """创建托盘菜单。"""
        return pystray.Menu(
            pystray.MenuItem(f"版本: {_get_version()}", None, default=False),
            pystray.MenuItem(
                "工具",
                pystray.Menu(
                    pystray.MenuItem("class-finder", self._on_tools_class_finder_click),
                ),
            ),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("升级", self._on_upgrade_click),
            pystray.MenuItem("重启", self._on_restart_click),
            pystray.MenuItem("日志", self._on_log_click),
            pystray.MenuItem("设置", self._on_settings_click),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("退出", self._on_exit_click),
        )

    def _safe_callback(self, name: str, callback: Optional[Callable]) -> None:
        """安全执行回调，确保快速返回并捕获异常。

        使用 threading.Thread 执行回调，避免阻塞 pystray 线程。
        QTimer.singleShot 在非 Qt 主线程中无法工作，因此改用 threading。
        """
        logger.info(f"Menu clicked: {name}")
        try:
            if callback:
                # 在后台线程执行回调，确保不阻塞 pystray 线程
                threading.Thread(target=callback, daemon=True).start()
                logger.debug(f"Callback started in thread: {name}")
        except Exception as e:
            logger.error(f"Menu callback error ({name}): {e}")

    def _on_tools_class_finder_click(self):
        """工具 - class-finder 菜单点击。"""
        import subprocess

        logger.info("Menu clicked: tools/class-finder")
        base_dir = get_base_dir()
        exe_path = os.path.join(base_dir, "tools", "window-class-finder.exe")

        if os.path.exists(exe_path):
            try:
                subprocess.Popen([exe_path], shell=True)
                logger.info(f"Launched window-class-finder: {exe_path}")
            except Exception as e:
                logger.error(f"Failed to launch window-class-finder: {e}")
        else:
            logger.warning(f"window-class-finder.exe not found: {exe_path}")

    def _on_upgrade_click(self):
        """升级菜单点击。"""
        self._safe_callback("upgrade", self.on_upgrade)

    def _on_restart_click(self):
        """重启菜单点击。"""
        self._safe_callback("restart", self.on_restart)

    def _on_log_click(self):
        """日志菜单点击。"""
        logger.info("Menu clicked: log")
        # 获取日志文件所在目录（与日志文件同级）
        app_dir = get_base_dir()

        # 日志文件在根目录（worker.log），直接打开根目录
        if sys.platform == "win32":
            os.startfile(app_dir)

    def _on_settings_click(self):
        """设置菜单点击。"""
        self._safe_callback("settings", self.on_settings)

    def _on_exit_click(self):
        """退出菜单点击。"""
        self._safe_callback("exit", self.on_exit)

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