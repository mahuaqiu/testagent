"""H.264 流式编码器。"""

import logging
from typing import Callable, Optional

logger = logging.getLogger(__name__)


class H264Streamer:
    """H.264 流式编码器（使用 windows-screen-sidecar H264Encoder）。"""

    def __init__(self, frame_source, fps: int = 10, bitrate: int = 2000000):
        self.frame_source = frame_source
        self.fps = fps
        self.bitrate = bitrate
        self._encoder = None
        self._sps_pps_sent = False
        self._consecutive_failures: int = 0
        self._max_failures: int = 30  # 连续 30 次失败（约3秒）触发降级
        self._fallback_triggered: bool = False
        self._on_fallback: Optional[Callable[[], None]] = None

    def set_fallback_callback(self, callback: Callable[[], None]) -> None:
        """设置降级回调。"""
        self._on_fallback = callback

    def start(self) -> dict:
        """启动 H.264 编码器。"""
        try:
            import win_recorder
            # 使用 H264Encoder 替代 StreamingEncoder
            self._encoder = win_recorder.H264Encoder(
                fps=self.fps,
                bitrate=self.bitrate,
                monitor=self.frame_source.monitor
            )
            info = self._encoder.start()
            logger.info(f"H264 encoder started: {info}")
            self._sps_pps_sent = False
            return info
        except ImportError:
            # windows-screen-sidecar 未安装
            logger.warning("windows-screen-sidecar not available, H.264 streaming not available")
            raise
        except Exception as e:
            logger.error(f"Failed to start H264 encoder: {e}")
            raise

    def get_frame(self) -> Optional[bytes]:
        """获取编码帧（同步版本）。

        Returns:
            bytes: 格式 [1字节帧类型][N字节数据]
                   0x01=SPS/PPS, 0x02=IDR, 0x03=P帧
            None: 编码器未就绪
        """
        return self._encode_frame()

    async def get_frame_async(self) -> Optional[bytes]:
        """获取编码帧（异步版本，供 server.py 调用）。

        Returns:
            bytes: 格式 [1字节帧类型][N字节数据]
                   0x01=SPS/PPS, 0x02=IDR, 0x03=P帧
            None: 编码器未就绪
        """
        return self._encode_frame()

    def _encode_frame(self) -> Optional[bytes]:
        """内部编码逻辑，由 get_frame 和 get_frame_async 共用。"""
        if not self._encoder:
            return None

        # 获取 BGRA 帧
        try:
            bgra_frame = self.frame_source.get_frame_bgra()
            if not bgra_frame:
                self._consecutive_failures += 1
                self._check_fallback()
                return None
        except Exception as e:
            logger.warning(f"Failed to get BGRA frame: {e}")
            self._consecutive_failures += 1
            self._check_fallback()
            return None

        # 编码
        try:
            encoded = self._encoder.encode_frame(bytes(bgra_frame))
            if not encoded:
                self._consecutive_failures += 1
                self._check_fallback()
                return None

            # 编码成功，重置失败计数
            self._consecutive_failures = 0

            # 返回编码数据（已包含帧类型前缀）
            return encoded

        except Exception as e:
            logger.error(f"Failed to encode frame: {e}")
            self._consecutive_failures += 1
            self._check_fallback()
            return None

    def _check_fallback(self) -> None:
        """检查是否需要触发降级。"""
        if self._fallback_triggered:
            return
        if self._consecutive_failures >= self._max_failures:
            self._fallback_triggered = True
            logger.warning(f"H264 encoding failed {self._consecutive_failures} times, triggering fallback")
            if self._on_fallback:
                self._on_fallback()

    def stop(self):
        """停止编码器。"""
        if self._encoder:
            try:
                self._encoder.stop()
            except Exception as e:
                logger.warning(f"Error stopping H264 encoder: {e}")
            self._encoder = None
        self._consecutive_failures = 0
        self._fallback_triggered = False
        logger.info("H264 encoder stopped")