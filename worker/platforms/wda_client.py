"""
WDA (WebDriverAgent) 客户端。

使用 python-wda 库封装，提供更可靠的 iOS 设备控制。
"""

import base64
import logging
import time
from typing import Optional

import wda

logger = logging.getLogger(__name__)


class WDAClient:
    """WDA 客户端，基于 python-wda 库。"""

    def __init__(self, base_url: str, timeout: int = 30):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self._client: Optional[wda.Client] = None
        self._session: Optional[wda.Session] = None

    def _get_client(self) -> wda.Client:
        """获取 wda.Client 实例。"""
        if self._client is None:
            self._client = wda.Client(self.base_url)
        return self._client

    def health_check(self) -> bool:
        """检查服务状态。"""
        try:
            client = self._get_client()
            return client.status() is not None
        except Exception:
            return False

    def wait_ready(self, timeout: int = 30) -> bool:
        """等待服务就绪。"""
        start = time.time()
        while time.time() - start < timeout:
            if self.health_check():
                return True
            time.sleep(3)
        return False

    def _get_session(self) -> wda.Session:
        """获取或创建会话。"""
        if self._session is None or not self._session:
            client = self._get_client()
            self._session = client.session()
        return self._session

    def tap(self, x: int, y: int) -> bool:
        """点击坐标。"""
        try:
            session = self._get_session()
            session.tap(x, y)
            return True
        except Exception as e:
            logger.error(f"Tap failed: {e}")
            return False

    def swipe(self, sx: int, sy: int, ex: int, ey: int, duration: float = 0.5) -> bool:
        """滑动（流畅滑动，不按住）。"""
        try:
            session = self._get_session()
            # python-wda 的 swipe 方法实现真正的滑动
            session.swipe(sx, sy, ex, ey, duration)
            return True
        except Exception as e:
            logger.error(f"Swipe failed: {e}")
            return False

    def drag(self, sx: int, sy: int, ex: int, ey: int, duration: float = 0.5) -> bool:
        """拖拽（按住后拖动）。"""
        try:
            session = self._get_session()
            # 使用 swipe 方法的 0.5s 压住效果
            session.swipe(sx, sy, ex, ey, duration)
            return True
        except Exception as e:
            logger.error(f"Drag failed: {e}")
            return False

    def screenshot(self) -> bytes:
        """截图。"""
        try:
            client = self._get_client()
            # python-wda 返回 PIL Image，需要转为 bytes
            img = client.screenshot()
            if img:
                from io import BytesIO
                buffer = BytesIO()
                img.save(buffer, format="PNG")
                return buffer.getvalue()
        except Exception as e:
            logger.error(f"Screenshot failed: {e}")
        return b""

    def send_keys(self, text: str) -> bool:
        """输入文本。"""
        try:
            session = self._get_session()
            session.send_keys(text)
            return True
        except Exception as e:
            logger.error(f"Send keys failed: {e}")
            return False

    def press_button(self, name: str) -> bool:
        """按键（HOME, VOLUME_UP 等）。"""
        try:
            session = self._get_session()
            # python-wda 使用 home() 方法或 press_button
            if name.upper() == "HOME":
                session.home()
            else:
                session.press_button(name.lower())
            return True
        except Exception as e:
            logger.error(f"Press button failed: {e}")
            return False

    def close(self) -> None:
        """关闭客户端。"""
        if self._session:
            try:
                self._session.close()
            except Exception:
                pass
        self._session = None
        self._client = None