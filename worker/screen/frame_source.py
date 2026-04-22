"""FrameSource 帧获取抽象层。"""

import io
import logging
import time
from abc import ABC, abstractmethod
from typing import Optional, TYPE_CHECKING

import numpy
from PIL import Image

if TYPE_CHECKING:
    from worker.platforms.minicap.minicap import Minicap

logger = logging.getLogger(__name__)


class FrameSource(ABC):
    """帧获取抽象基类。"""

    MAX_RECONNECT_ATTEMPTS = 3
    RECONNECT_INTERVAL = 1  # 秒

    @abstractmethod
    def get_frame(self) -> bytes:
        """获取单帧（JPEG 格式）。"""
        pass

    @abstractmethod
    def get_screen_size(self) -> tuple[int, int]:
        """获取屏幕尺寸 (width, height)。"""
        pass

    @abstractmethod
    def start(self) -> None:
        """启动帧源（如建立 socket 连接）。"""
        pass

    @abstractmethod
    def stop(self) -> None:
        """停止帧源。"""
        pass

    @abstractmethod
    def get_blank_frame(self) -> bytes:
        """获取空白帧（连接失败时返回）。"""
        pass

    def get_frame_with_reconnect(self) -> bytes:
        """获取帧（带自动重连）。"""
        for attempt in range(self.MAX_RECONNECT_ATTEMPTS + 1):
            try:
                return self.get_frame()
            except ConnectionError:
                if attempt < self.MAX_RECONNECT_ATTEMPTS:
                    logger.warning(f"Frame source disconnected, reconnecting (attempt {attempt + 1})")
                    self.stop()
                    time.sleep(self.RECONNECT_INTERVAL)
                    self.start()
                else:
                    logger.error("Frame source reconnect failed, returning blank frame")
                    return self.get_blank_frame()

    def _img_to_jpeg(self, img_array: numpy.ndarray, quality: int = 80) -> bytes:
        """将 numpy 数组转换为 JPEG。"""
        img = Image.fromarray(img_array)
        buffer = io.BytesIO()
        img.save(buffer, format="JPEG", quality=quality)
        return buffer.getvalue()


class MinicapFrameSource(FrameSource):
    """Android: minicap socket 流。"""

    def __init__(self, device_id: str, minicap_instance: "Minicap"):
        self.device_id = device_id
        self.minicap = minicap_instance
        self._screen_size: Optional[tuple[int, int]] = None

    def get_frame(self) -> bytes:
        """从 minicap 流获取帧（JPEG 格式）。"""
        return self.minicap.get_frame()

    def get_screen_size(self) -> tuple[int, int]:
        """获取屏幕尺寸。"""
        if self._screen_size:
            return self._screen_size
        display_info = self.minicap.get_display_info()
        self._screen_size = (display_info["width"], display_info["height"])
        return self._screen_size

    def start(self) -> None:
        """启动 minicap 流。"""
        pass  # minicap.get_frame() 内部会自动启动流

    def stop(self) -> None:
        """停止 minicap 流。"""
        self.minicap.stop_stream()

    def get_blank_frame(self) -> bytes:
        """返回黑屏 JPEG 帧。"""
        width, height = self.get_screen_size()
        img = numpy.zeros((height, width, 3), dtype=numpy.uint8)
        return self._img_to_jpeg(img)


class MJPEGFrameSource(FrameSource):
    """iOS: WDA 9100 MJPEG 流。"""

    def __init__(self, device_id: str, wda_client):
        self.device_id = device_id
        self.wda_client = wda_client
        self._screen_size: Optional[tuple[int, int]] = None

    def get_frame(self) -> bytes:
        """从 WDA MJPEG 流获取帧。"""
        # WDA 客户端提供 screenshot 方法
        # 使用 WDA 的 MJPEG 流：http://<device_ip>:9100
        import requests
        mjpeg_url = f"http://{self.wda_client.url.split('/')[2]}:9100"
        response = requests.get(mjpeg_url, timeout=5)
        return response.content

    def get_screen_size(self) -> tuple[int, int]:
        """获取屏幕尺寸。"""
        if self._screen_size:
            return self._screen_size
        # 从 WDA 获取窗口尺寸
        try:
            window_size = self.wda_client.window_size()
            self._screen_size = (window_size.width, window_size.height)
        except Exception:
            self._screen_size = (375, 667)  # iPhone 8 默认逻辑分辨率
        return self._screen_size

    def start(self) -> None:
        """启动 MJPEG 流。"""
        pass  # MJPEG 流无需预先启动

    def stop(self) -> None:
        """停止 MJPEG 流。"""
        pass

    def get_blank_frame(self) -> bytes:
        """返回黑屏 JPEG 帧。"""
        width, height = self.get_screen_size()
        img = numpy.zeros((height, width, 3), dtype=numpy.uint8)
        return self._img_to_jpeg(img)


class WindowsFrameSource(FrameSource):
    """Windows: mss 截屏。"""

    def __init__(self, fps: int = 10, monitor: int = 1):
        self.fps = fps
        self.monitor = monitor
        self._screen_size: Optional[tuple[int, int]] = None

    def get_frame(self) -> bytes:
        """使用 mss 截屏，转为 JPEG。"""
        import mss

        with mss.mss() as sct:
            monitor_config = sct.monitors[self.monitor]
            screenshot = sct.grab(monitor_config)
            img = Image.frombytes("RGB", screenshot.size, screenshot.rgb)
            buffer = io.BytesIO()
            img.save(buffer, format="JPEG", quality=80)
            return buffer.getvalue()

    def get_screen_size(self) -> tuple[int, int]:
        """获取显示器尺寸。"""
        import mss

        with mss.mss() as sct:
            monitor_config = sct.monitors[self.monitor]
            return (monitor_config["width"], monitor_config["height"])

    def start(self) -> None:
        """mss 不需要启动。"""
        pass

    def stop(self) -> None:
        """mss 不需要停止。"""
        pass

    def get_blank_frame(self) -> bytes:
        """返回黑屏 JPEG 帧。"""
        width, height = self.get_screen_size()
        img = numpy.zeros((height, width, 3), dtype=numpy.uint8)
        return self._img_to_jpeg(img)


class WebFrameSource(FrameSource):
    """Web: Playwright screenshot（仅用于推流，不支持录屏）。"""

    def __init__(self, page):
        self.page = page
        self._screen_size: Optional[tuple[int, int]] = None

    def get_frame(self) -> bytes:
        """Playwright page screenshot。"""
        return self.page.screenshot(type="jpeg", quality=80)

    def get_screen_size(self) -> tuple[int, int]:
        """获取页面尺寸。"""
        if self._screen_size:
            return self._screen_size
        viewport = self.page.viewport_size
        if viewport:
            self._screen_size = (viewport["width"], viewport["height"])
        else:
            self._screen_size = (1280, 720)  # 默认值
        return self._screen_size

    def start(self) -> None:
        """Playwright 不需要启动。"""
        pass

    def stop(self) -> None:
        """Playwright 不需要停止。"""
        pass

    def get_blank_frame(self) -> bytes:
        """返回黑屏 JPEG 帧（固定尺寸 1280x720）。"""
        img = numpy.zeros((720, 1280, 3), dtype=numpy.uint8)
        return self._img_to_jpeg(img)