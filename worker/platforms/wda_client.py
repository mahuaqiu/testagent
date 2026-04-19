"""
WDA (WebDriverAgent) HTTP 客户端。

通过 HTTP 调用 WDA 服务控制 iOS 设备。
"""

import base64
import logging
import time

import httpx

logger = logging.getLogger(__name__)


class WDAClient:
    """WDA HTTP 客户端。"""

    def __init__(self, base_url: str, timeout: int = 30):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.session = httpx.Client(timeout=timeout)
        self._session_id: str | None = None

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
            try:
                response = self.session.get(f"{self.base_url}/status", timeout=5)
                if response.status_code == 200:
                    return True
            except Exception:
                pass
            time.sleep(1)  # 间隔 1 秒，更快检测
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
                f"{self.base_url}/session/{session_id}/wda/tap",
                json={"x": x, "y": y}
            )
            if response.status_code != 200:
                logger.warning(f"Tap failed: status={response.status_code}, body={response.text}")
            return response.status_code == 200
        except Exception as e:
            logger.error(f"Tap failed: {e}")
            return False

    def swipe(self, sx: int, sy: int, ex: int, ey: int, duration: float = 0.5) -> bool:
        """滑动（快速滑动，避免长按效果）。"""
        try:
            session_id = self._get_session()
            # WDA 的 dragfromtoforduration duration 是"按住时间"
            # duration >= 0.5s 会造成明显的长按效果
            # 强制使用短 duration（0.1-0.2s）实现快速滑动
            actual_duration = 0.2 if duration >= 0.5 else max(duration, 0.1)
            response = self.session.post(
                f"{self.base_url}/session/{session_id}/wda/dragfromtoforduration",
                json={
                    "fromX": sx,
                    "fromY": sy,
                    "toX": ex,
                    "toY": ey,
                    "duration": actual_duration
                }
            )
            if response.status_code != 200:
                logger.warning(f"Swipe failed: status={response.status_code}, body={response.text}")
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
            session_id = self._get_session()
            response = self.session.post(
                f"{self.base_url}/session/{session_id}/wda/pressButton",
                json={"name": name}
            )
            return response.status_code == 200
        except Exception as e:
            logger.error(f"Press button failed: {e}")
            return False

    def is_locked(self) -> bool:
        """检测屏幕是否锁定（GET /wda/locked）。"""
        try:
            session_id = self._get_session()
            response = self.session.get(
                f"{self.base_url}/session/{session_id}/wda/locked"
            )
            if response.status_code == 200:
                data = response.json()
                # WDA 返回 {"value": true/false}
                return data.get("value", False)
            return False
        except Exception as e:
            logger.error(f"Is locked check failed: {e}")
            # 检测失败时假设已锁定，安全起见
            return True

    def wake_screen(self) -> bool:
        """唤醒屏幕（按 HOME 键）。"""
        return self.press_button("home")

    def swipe_up_for_unlock(self) -> bool:
        """从底部向上滑动解锁（iOS 锁屏界面）。"""
        try:
            session_id = self._get_session()
            # 获取屏幕尺寸
            response = self.session.get(f"{self.base_url}/session/{session_id}/window/size")
            if response.status_code == 200:
                data = response.json()
                size = data.get("value", {})
                width = size.get("width", 375)
                height = size.get("height", 667)
            else:
                # 默认尺寸（iPhone 8）
                width, height = 187, 333  # WDA 逻辑坐标

            # 从底部中间向上滑动（避开底部边缘）
            center_x = width // 2
            start_y = int(height * 0.9)
            end_y = int(height * 0.3)
            return self.swipe(center_x, start_y, center_x, end_y, duration=0.3)
        except Exception as e:
            logger.error(f"Swipe up for unlock failed: {e}")
            return False

    def close(self) -> None:
        """关闭客户端。"""
        if self._session_id:
            try:
                self.session.delete(f"{self.base_url}/session/{self._session_id}")
            except Exception:
                pass
        self.session.close()
