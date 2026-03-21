"""
WDA (WebDriverAgent) HTTP 客户端。

通过 HTTP 调用 WDA 服务控制 iOS 设备。
"""

import base64
import logging
import time
from typing import Optional

import httpx

logger = logging.getLogger(__name__)


class WDAClient:
    """WDA HTTP 客户端。"""

    def __init__(self, base_url: str, timeout: int = 30):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.session = httpx.Client(timeout=timeout)
        self._session_id: Optional[str] = None

    def health_check(self) -> bool:
        """检查服务状态。"""
        try:
            response = self.session.get(f"{self.base_url}/status")
            return response.status_code == 200
        except Exception:
            return False

    def wait_ready(self, timeout: int = 30) -> bool:
        """等待服务就绪。"""
        start = time.time()
        while time.time() - start < timeout:
            if self.health_check():
                return True
            time.sleep(1)
        return False

    def _get_session(self) -> str:
        """获取或创建 WebDriver 会话。"""
        if self._session_id:
            return self._session_id

        response = self.session.post(
            f"{self.base_url}/session",
            json={"capabilities": {}}
        )
        if response.status_code == 200:
            data = response.json()
            self._session_id = data.get("sessionId") or data.get("value", {}).get("sessionId")
            return self._session_id
        raise RuntimeError(f"Failed to create session: {response.text}")

    def tap(self, x: int, y: int) -> bool:
        """点击坐标。"""
        try:
            session_id = self._get_session()
            response = self.session.post(
                f"{self.base_url}/session/{session_id}/wda/tap/{x}/{y}"
            )
            return response.status_code == 200
        except Exception as e:
            logger.error(f"Tap failed: {e}")
            return False

    def swipe(self, sx: int, sy: int, ex: int, ey: int, duration: float = 0.5) -> bool:
        """滑动。"""
        try:
            session_id = self._get_session()
            response = self.session.post(
                f"{self.base_url}/session/{session_id}/wda/dragfromtoforduration",
                json={
                    "fromX": sx,
                    "fromY": sy,
                    "toX": ex,
                    "toY": ey,
                    "duration": duration
                }
            )
            return response.status_code == 200
        except Exception as e:
            logger.error(f"Swipe failed: {e}")
            return False

    def screenshot(self) -> bytes:
        """截图。"""
        try:
            response = self.session.get(f"{self.base_url}/screenshot")
            if response.status_code == 200:
                data = response.json()
                value = data.get("value", data)
                if isinstance(value, str):
                    return base64.b64decode(value)
                return value
        except Exception as e:
            logger.error(f"Screenshot failed: {e}")
        return b""

    def send_keys(self, text: str) -> bool:
        """输入文本。"""
        try:
            session_id = self._get_session()
            response = self.session.post(
                f"{self.base_url}/session/{session_id}/wda/keys",
                json={"value": list(text)}
            )
            return response.status_code == 200
        except Exception as e:
            logger.error(f"Send keys failed: {e}")
            return False

    def press_button(self, name: str) -> bool:
        """按键（HOME, VOLUME_UP 等）。"""
        try:
            response = self.session.post(
                f"{self.base_url}/wda/pressButton",
                json={"name": name}
            )
            return response.status_code == 200
        except Exception as e:
            logger.error(f"Press button failed: {e}")
            return False

    def close(self) -> None:
        """关闭客户端。"""
        if self._session_id:
            try:
                self.session.delete(f"{self.base_url}/session/{self._session_id}")
            except Exception:
                pass
        self.session.close()