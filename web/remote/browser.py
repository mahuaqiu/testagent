"""
RemoteBrowser —— 浏览器生命周期管理，CDP 连接。

支持两种模式:
1. 本地启动: 启动本地浏览器并暴露 CDP 端口
2. 远程连接: 连接到已有浏览器的 CDP 端点

Usage:
    # 本地模式
    browser = RemoteBrowser(cdp_port=9222, headless=True)
    browser.start()
    print(browser.get_ws_endpoint())  # ws://localhost:9222

    # 远程连接模式
    browser = RemoteBrowser()
    browser.connect("ws://remote-host:9222")
"""

from dataclasses import dataclass
from typing import Optional
import time

from playwright.sync_api import sync_playwright, Playwright, Browser, BrowserContext


@dataclass
class BrowserConfig:
    """浏览器配置。"""

    cdp_port: int = 9222
    headless: bool = True
    browser_type: str = "chromium"  # chromium, firefox, webkit
    timeout: int = 30000  # 默认超时（毫秒）
    slow_motion: int = 0  # 慢动作延迟（毫秒）
    args: list[str] = None  # 额外的浏览器启动参数

    def __post_init__(self):
        if self.args is None:
            self.args = []


class RemoteBrowser:
    """
    浏览器生命周期管理，CDP 连接。

    提供本地启动和远程连接两种模式。

    Attributes:
        config: 浏览器配置
        _playwright: Playwright 实例
        _browser: 浏览器实例
        _ws_endpoint: WebSocket 端点
        _mode: 运行模式 (local/connect)
    """

    def __init__(
        self,
        cdp_port: int = 9222,
        headless: bool = True,
        browser_type: str = "chromium",
        timeout: int = 30000,
        slow_motion: int = 0,
        args: Optional[list[str]] = None,
    ):
        """
        初始化 RemoteBrowser。

        Args:
            cdp_port: CDP 端口
            headless: 是否无头模式
            browser_type: 浏览器类型 (chromium/firefox/webkit)
            timeout: 默认超时（毫秒）
            slow_motion: 慢动作延迟（毫秒）
            args: 额外的浏览器启动参数
        """
        self.config = BrowserConfig(
            cdp_port=cdp_port,
            headless=headless,
            browser_type=browser_type,
            timeout=timeout,
            slow_motion=slow_motion,
            args=args or [],
        )
        self._playwright: Optional[Playwright] = None
        self._browser: Optional[Browser] = None
        self._ws_endpoint: Optional[str] = None
        self._mode: str = "local"  # local 或 connect

    @property
    def browser(self) -> Optional[Browser]:
        """获取浏览器实例。"""
        return self._browser

    @property
    def is_connected(self) -> bool:
        """是否已连接。"""
        return self._browser is not None and self._browser.is_connected()

    def start(self) -> str:
        """
        启动本地浏览器并暴露 CDP 端口。

        Returns:
            str: WebSocket 端点 URL

        Raises:
            RuntimeError: 浏览器已启动
        """
        if self._browser is not None:
            raise RuntimeError("Browser already started. Call close() first.")

        self._playwright = sync_playwright().start()

        # 构建启动参数
        launch_args = [
            f"--remote-debugging-port={self.config.cdp_port}",
        ]
        if self.config.args:
            launch_args.extend(self.config.args)

        # 选择浏览器类型
        browser_launcher = getattr(self._playwright, self.config.browser_type)

        # 启动浏览器
        self._browser = browser_launcher.launch(
            headless=self.config.headless,
            args=launch_args,
            slow_mo=self.config.slow_motion,
        )

        # 设置默认超时
        self._browser.set_default_timeout(self.config.timeout)

        # 获取 WebSocket 端点
        self._ws_endpoint = f"ws://localhost:{self.config.cdp_port}"
        self._mode = "local"

        return self._ws_endpoint

    def connect(self, endpoint: str) -> Browser:
        """
        连接到远程浏览器的 CDP 端点。

        Args:
            endpoint: CDP WebSocket 端点 URL (如 ws://host:9222)

        Returns:
            Browser: 连接的浏览器实例

        Raises:
            RuntimeError: 已有浏览器实例
        """
        if self._browser is not None:
            raise RuntimeError("Browser already connected. Call close() first.")

        self._playwright = sync_playwright().start()

        # 通过 CDP 连接
        self._browser = self._playwright.chromium.connect_over_cdp(endpoint)
        self._ws_endpoint = endpoint
        self._mode = "connect"

        # 设置默认超时
        self._browser.set_default_timeout(self.config.timeout)

        return self._browser

    def new_context(self, **kwargs) -> BrowserContext:
        """
        创建新的浏览器上下文。

        每个上下文是独立的会话，不共享 cookies、localStorage 等。

        Args:
            **kwargs: 传递给 browser.new_context() 的参数
                - viewport: 视口大小 {"width": 1280, "height": 720}
                - user_agent: 用户代理
                - locale: 语言环境
                - base_url: 基础 URL

        Returns:
            BrowserContext: 浏览器上下文

        Raises:
            RuntimeError: 浏览器未启动
        """
        if self._browser is None:
            raise RuntimeError("Browser not started. Call start() or connect() first.")

        return self._browser.new_context(**kwargs)

    def close(self) -> None:
        """
        关闭浏览器。

        在 local 模式下会关闭浏览器实例。
        在 connect 模式下仅断开连接。
        """
        if self._browser is not None:
            self._browser.close()
            self._browser = None

        if self._playwright is not None:
            self._playwright.stop()
            self._playwright = None

        self._ws_endpoint = None

    def get_ws_endpoint(self) -> Optional[str]:
        """
        获取 WebSocket 端点。

        Returns:
            str 或 None: WebSocket 端点 URL
        """
        return self._ws_endpoint

    def get_cdp_url(self) -> Optional[str]:
        """
        获取 CDP HTTP URL（用于获取 devtools 页面）。

        Returns:
            str 或 None: CDP HTTP URL
        """
        if self._ws_endpoint:
            # ws://localhost:9222 -> http://localhost:9222
            return self._ws_endpoint.replace("ws://", "http://")
        return None

    def contexts(self) -> list[BrowserContext]:
        """
        获取所有浏览器上下文。

        Returns:
            list[BrowserContext]: 上下文列表
        """
        if self._browser is None:
            return []
        return self._browser.contexts

    def version(self) -> str:
        """
        获取浏览器版本。

        Returns:
            str: 浏览器版本
        """
        if self._browser is None:
            return "not connected"
        return self._browser.version()

    def __enter__(self) -> "RemoteBrowser":
        """上下文管理器入口。"""
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        """上下文管理器退出。"""
        self.close()

    def __repr__(self) -> str:
        return (
            f"RemoteBrowser(mode={self._mode}, cdp_port={self.config.cdp_port}, "
            f"connected={self.is_connected})"
        )