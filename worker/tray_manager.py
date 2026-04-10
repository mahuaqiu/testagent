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