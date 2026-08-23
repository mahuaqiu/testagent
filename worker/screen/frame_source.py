"""FrameSource 帧获取抽象层。"""

import io
import logging
import os
import tempfile
import time
import uuid
from abc import ABC, abstractmethod
from typing import Optional, TYPE_CHECKING

import numpy
from PIL import Image

if TYPE_CHECKING:
    from worker.platforms.minicap.minicap import Minicap
    from worker.platforms.harmony_hdc import HarmonyHdcWrapper
    from worker.platforms.harmony import HarmonyPlatformManager

logger = logging.getLogger(__name__)


class FrameSource(ABC):
    """帧获取抽象基类。"""

    @property
    def prefers_latest_frame(self) -> bool:
        """是否要求消费者跳过 ScreenManager 中积压的旧帧。"""
        return False

    @abstractmethod
    def get_frame(self) -> bytes:
        """获取单帧（JPEG 格式）。"""
        pass

    def get_frame_bgra(self) -> bytearray:
        """获取 BGRA 原始帧（用于 windows-screen-sidecar 硬件编码）。

        Returns:
            bytearray: BGRA 格式的原始像素数据

        Note:
            默认实现抛出 NotImplementedError，子类根据能力选择实现。
            不使用 @abstractmethod，因为并非所有子类都支持 BGRA 输出
            （如 MJPEGFrameSource、MinicapFrameSource），
            调用方通过 try/except NotImplementedError 处理不支持的情况。
        """
        raise NotImplementedError(f"{self.__class__.__name__} does not support BGRA frame")

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
        logger.info("MinicapFrameSource stopping")
        self.minicap.stop_stream()
        logger.info("MinicapFrameSource stopped")

    def get_blank_frame(self) -> bytes:
        """返回黑屏 JPEG 帧。"""
        width, height = self.get_screen_size()
        img = numpy.zeros((height, width, 3), dtype=numpy.uint8)
        return self._img_to_jpeg(img)


class MJPEGFrameSource(FrameSource):
    """iOS: WDA MJPEG 流（固定端口 9100）。"""

    def __init__(self, device_id: str, wda_client):
        self.device_id = device_id
        self.wda_client = wda_client
        self._screen_size: Optional[tuple[int, int]] = None
        self._stream_response = None
        self._stream_iterator = None
        self._stream_buffer = b""

    def get_frame(self) -> bytes:
        """从 WDA MJPEG 流获取帧（流式读取 multipart 格式）。"""
        import re
        import requests

        host_with_port = self.wda_client.base_url.split('/')[2]
        host = host_with_port.split(':')[0]
        mjpeg_url = f"http://{host}:9100"

        # 打开持续流（stream=True）
        if self._stream_response is None:
            self._stream_response = requests.get(mjpeg_url, stream=True, timeout=30)
            self._stream_iterator = self._stream_response.iter_content(chunk_size=8192)

        # 解析 multipart 格式：--BoundaryString + Content-Length + JPEG 数据
        boundary = b"--BoundaryString"
        content_length_pattern = re.compile(rb"Content-Length: (\d+)")

        # 读取数据直到找到完整帧
        while True:
            # 查找 boundary
            boundary_pos = self._stream_buffer.find(boundary)
            if boundary_pos != -1:
                # 查找 Content-Length
                header_start = boundary_pos + len(boundary)
                header_end = self._stream_buffer.find(b"\r\n\r\n", header_start)
                if header_end != -1:
                    header = self._stream_buffer[header_start:header_end]
                    match = content_length_pattern.search(header)
                    if match:
                        content_length = int(match.group(1))
                        data_start = header_end + 4  # \r\n\r\n

                        # 检查是否有完整帧数据
                        if len(self._stream_buffer) >= data_start + content_length:
                            # 提取 JPEG 数据
                            frame_data = self._stream_buffer[data_start:data_start + content_length]
                            # 清理已处理的数据
                            self._stream_buffer = self._stream_buffer[data_start + content_length:]
                            return frame_data

            # 从流读取更多数据
            try:
                chunk = next(self._stream_iterator)
                self._stream_buffer += chunk
            except StopIteration:
                # 流结束，重新连接
                self._stream_response.close()
                self._stream_response = None
                self._stream_iterator = None
                self._stream_buffer = b""
                raise ConnectionError("MJPEG stream ended")

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
        pass  # 流在 get_frame 时自动打开

    def stop(self) -> None:
        """停止 MJPEG 流。"""
        if self._stream_response:
            self._stream_response.close()
            self._stream_response = None
        self._stream_iterator = None
        self._stream_buffer = b""

    def get_blank_frame(self) -> bytes:
        """返回黑屏 JPEG 帧。"""
        width, height = self.get_screen_size()
        img = numpy.zeros((height, width, 3), dtype=numpy.uint8)
        return self._img_to_jpeg(img)

    def start_mjpeg_proxy(self):
        """启动 MJPEG 透传（HTTP→WS 代理）。"""
        from worker.screen.mjpeg_proxy import MJPEGProxy

        # 从 wda_client 获取主机地址
        host_with_port = self.wda_client.base_url.split('/')[2]
        host = host_with_port.split(':')[0]

        proxy = MJPEGProxy(host=host, port=9100)
        proxy.start()
        return proxy


class MacFrameSource(FrameSource):
    """Mac: 使用 pyautogui 截屏。

    注意：后续实现 sidecar 后，这里应该降级到 sidecar。
    """

    def __init__(self, fps: int = 10, monitor: int = 1):
        import pyautogui

        self.fps = fps
        self.monitor = monitor
        self._pyautogui = pyautogui
        self._screen_size: Optional[tuple[int, int]] = None
        self._stopped = False

    def get_frame(self) -> bytes:
        """使用 pyautogui 截屏，转为 JPEG。"""
        if self._stopped:
            return self.get_blank_frame()

        screenshot = self._pyautogui.screenshot()
        buffer = io.BytesIO()
        screenshot.save(buffer, format="JPEG", quality=80)
        return buffer.getvalue()

    def get_frame_bgra(self) -> bytearray:
        """获取 BGRA 原始帧。

        pyautogui 返回 RGB，需要转换为 BGRA。
        """
        if self._stopped:
            return bytearray()

        screenshot = self._pyautogui.screenshot()
        # 转换为 numpy 数组
        img_array = numpy.array(screenshot)
        # RGB to BGRA: 交换 R 和 B 通道
        if len(img_array.shape) == 3 and img_array.shape[2] == 3:
            # 添加 Alpha 通道
            rgba = numpy.dstack([img_array, numpy.full(img_array.shape[:2], 255, dtype=numpy.uint8)])
            # RGB to BGR
            bgra = rgba.copy()
            bgra[:, :, 0] = rgba[:, :, 2]  # B = R
            bgra[:, :, 2] = rgba[:, :, 0]  # R = B
            return bytearray(bgra.tobytes())

        return bytearray()

    def get_screen_size(self) -> tuple[int, int]:
        """获取显示器尺寸。"""
        if self._screen_size:
            return self._screen_size
        self._screen_size = self._pyautogui.size()
        return self._screen_size

    def start(self) -> None:
        """启动帧源（预留接口）。"""
        pass

    def stop(self) -> None:
        """停止帧源，设置停止标志。"""
        logger.info("MacFrameSource stopping")
        self._stopped = True
        logger.info("MacFrameSource stopped")

    def get_blank_frame(self) -> bytes:
        """返回黑屏 JPEG 帧。"""
        width, height = self.get_screen_size()
        img = numpy.zeros((height, width, 3), dtype=numpy.uint8)
        return self._img_to_jpeg(img)


class HarmonyFrameSource(FrameSource):
    """鸿蒙: uitest daemon 帧流优先，snapshot_display 轮询兜底。

    帧流模式约 10fps（agent.so + fport 8012 + startCaptureScreen）；
    启动失败（PC 不支持 daemon、agent.so 不兼容等）时自动降级为
    snapshot_display + file recv 轮询（约 1-2fps）。
    """

    # 轮询模式最低截图间隔（秒），避免高频 hdc 命令拖垮设备
    POLL_MIN_INTERVAL = 0.5

    def __init__(self, device_id: str, hdc_wrapper: "HarmonyHdcWrapper"):
        self.device_id = device_id
        self.hdc = hdc_wrapper
        self._capture = None  # HarmonyScreenCapture 实例（帧流模式）
        self._polling = False
        self._screen_size: Optional[tuple[int, int]] = None
        self._last_poll_ts = 0.0
        self._last_poll_frame: Optional[bytes] = None

    def start(self) -> None:
        """尝试建立 uitest 帧流，失败则降级为轮询模式。"""
        from worker.platforms.harmony_capture import (
            HarmonyCaptureError,
            HarmonyScreenCapture,
        )

        self._polling = False
        try:
            self._capture = HarmonyScreenCapture(self.hdc)
            self._capture.start()
            logger.info(f"HarmonyFrameSource 帧流模式已启动: {self.device_id}")
        except HarmonyCaptureError as e:
            logger.warning(
                f"uitest 帧流启动失败，降级为 snapshot_display 轮询: {e}"
            )
            self._capture = None
            self._polling = True

    def get_frame(self) -> bytes:
        """帧流模式取最新 JPEG；轮询模式即时截一张。"""
        if self._capture is not None:
            if not self._capture.is_running:
                # 帧流中途断开抛 ConnectionError，由 ScreenManager 捕获循环
                # 按连续错误计数处理
                raise ConnectionError("Harmony uitest frame stream disconnected")
            frame = self._capture.get_frame(timeout=2.0)
            if frame is None:
                raise ConnectionError("Harmony uitest frame stream timeout")
            return frame
        return self._poll_frame()

    def _poll_frame(self) -> bytes:
        """snapshot_display 轮询截图（控制最低间隔，命中缓存直接复用）。"""
        now = time.monotonic()
        if (
            self._last_poll_frame is not None
            and now - self._last_poll_ts < self.POLL_MIN_INTERVAL
        ):
            return self._last_poll_frame

        local_path = os.path.join(
            tempfile.gettempdir(), f"harmony_frame_{uuid.uuid4().hex}.jpeg"
        )
        try:
            if not self.hdc.screenshot(local_path):
                raise ConnectionError("Harmony snapshot_display screenshot failed")
            with open(local_path, "rb") as f:
                frame = f.read()
        finally:
            try:
                if os.path.isfile(local_path):
                    os.remove(local_path)
            except OSError:
                pass

        if not frame:
            raise ConnectionError("Harmony screenshot file is empty")
        self._last_poll_frame = frame
        self._last_poll_ts = time.monotonic()
        return frame

    def get_screen_size(self) -> tuple[int, int]:
        """获取屏幕尺寸（display_size 优先，首帧 JPEG 解析兜底）。"""
        if self._screen_size:
            return self._screen_size

        size = self.hdc.display_size()
        if size != (0, 0):
            self._screen_size = size
            return self._screen_size

        # 兜底：从首帧 JPEG 解析尺寸
        try:
            frame = self.get_frame()
            with Image.open(io.BytesIO(frame)) as img:
                self._screen_size = img.size
            return self._screen_size
        except Exception as e:
            logger.warning(f"从首帧解析屏幕尺寸失败: {e}")
            return (0, 0)

    def stop(self) -> None:
        """停止帧流并清理资源。"""
        logger.info("HarmonyFrameSource stopping")
        if self._capture is not None:
            try:
                self._capture.stop()
            except Exception as e:
                logger.warning(f"停止鸿蒙帧流失败: {e}")
            self._capture = None
        self._polling = False
        self._last_poll_frame = None
        logger.info("HarmonyFrameSource stopped")

    def get_blank_frame(self) -> bytes:
        """返回黑屏 JPEG 帧。"""
        width, height = self.get_screen_size()
        if width <= 0 or height <= 0:
            width, height = 1280, 720
        img = numpy.zeros((height, width, 3), dtype=numpy.uint8)
        return self._img_to_jpeg(img)


class HarmonyOfficialFrameSource(FrameSource):
    """鸿蒙官方 HOScrcpy H.264 会话帧源。

    官方会话由 ``HarmonyPlatformManager`` 按设备复用。帧源持有一份推流租约；
    推流停止且没有其他消费者时，立即释放并终止 Java 会话。
    官方链路不可用时，在 ``auto`` 模式回退到既有 HarmonyFrameSource。
    """

    def __init__(self, device_id: str, manager: "HarmonyPlatformManager"):
        self.device_id = device_id
        self.manager = manager
        self._session = None
        self._fallback: HarmonyFrameSource | None = None
        self._screen_size: Optional[tuple[int, int]] = None
        self._owner = f"stream:{id(self)}"

    @property
    def prefers_latest_frame(self) -> bool:
        """官方源只保留最新解码帧，避免通用 FIFO 再次放大延迟。"""
        return True

    def start(self) -> None:
        """启动或获取官方会话；无法启动时在 auto 模式创建旧帧源。"""
        if self._session is not None or self._fallback is not None:
            return
        self._session = self.manager.acquire_official_session(self.device_id, self._owner)
        if self._session is not None:
            logger.info("HarmonyOfficialFrameSource 官方推流已启动: %s", self.device_id)
            return

        client = self.manager._device_clients.get(self.device_id)
        if client is None:
            raise ConnectionError(f"Harmony device client 不存在: {self.device_id}")
        self._fallback = HarmonyFrameSource(self.device_id, client)
        self._fallback.start()
        logger.warning("HarmonyOfficialFrameSource 已回退旧帧源: %s", self.device_id)

    def get_frame(self) -> bytes:
        if self._session is None and self._fallback is None:
            self.start()
        if self._session is not None:
            try:
                # SDK READY 到首个可解码帧之间可能还有一小段缓冲时间，
                # 不要把正常的首帧延迟误判为官方会话故障。
                return self._session.get_latest_jpeg(timeout=5.0)
            except Exception as exc:
                self.manager._handle_official_failure(self.device_id, "推流取帧", exc)
                self._session = None
                self._start_fallback_after_failure()
        if self._fallback is None:
            raise ConnectionError("鸿蒙推流帧源不可用")
        return self._fallback.get_frame()

    def get_screen_size(self) -> tuple[int, int]:
        if self._screen_size:
            return self._screen_size
        if self._session is not None:
            size = self._session.screen_size
            if size != (0, 0):
                self._screen_size = size
                return size
        if self._fallback is not None:
            self._screen_size = self._fallback.get_screen_size()
            return self._screen_size

        client = self.manager._device_clients.get(self.device_id)
        if client:
            self._screen_size = client.display_size()
            return self._screen_size
        return (0, 0)

    def stop(self) -> None:
        """停止帧源并释放官方会话租约。"""
        if self._fallback is not None:
            self._fallback.stop()
            self._fallback = None
        if self._session is not None:
            self.manager.release_official_session(self.device_id, self._owner)
        self._session = None

    def get_blank_frame(self) -> bytes:
        width, height = self.get_screen_size()
        if width <= 0 or height <= 0:
            width, height = 1280, 720
        img = numpy.zeros((height, width, 3), dtype=numpy.uint8)
        return self._img_to_jpeg(img)

    def _start_fallback_after_failure(self) -> None:
        if self._fallback is not None:
            return
        client = self.manager._device_clients.get(self.device_id)
        if client is None:
            raise ConnectionError(f"Harmony device client 不存在: {self.device_id}")
        self._fallback = HarmonyFrameSource(self.device_id, client)
        self._fallback.start()
