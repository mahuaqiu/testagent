"""H.264 流式编码器。"""

import logging
from typing import Optional

logger = logging.getLogger(__name__)


class H264Streamer:
    """H.264 流式编码器（使用 win-recorder 流式编码）。"""

    def __init__(self, frame_source, fps: int = 10, bitrate: int = 2000000):
        self.frame_source = frame_source
        self.fps = fps
        self.bitrate = bitrate
        self._encoder = None
        self._sps_pps_sent = False

    def start(self) -> dict:
        """启动 H.264 编码器。"""
        try:
            import win_recorder
            self._encoder = win_recorder.StreamingEncoder(
                fps=self.fps,
                bitrate=self.bitrate,
                monitor=self.frame_source.monitor
            )
            info = self._encoder.start()
            logger.info(f"H264 encoder started: {info}")
            self._sps_pps_sent = False
            return info
        except ImportError:
            # win-recorder 未安装
            logger.error("win-recorder not installed, H.264 streaming not available")
            raise
        except Exception as e:
            logger.error(f"Failed to start H264 encoder: {e}")
            raise

    def get_frame(self) -> Optional[bytes]:
        """获取编码帧。

        Returns:
            bytes: 格式 [1字节帧类型][N字节数据]
                   0x01=SPS/PPS, 0x02=IDR, 0x03=P帧
            None: 编码器未就绪
        """
        if not self._encoder:
            return None

        # 获取 BGRA 帧
        try:
            bgra_frame = self.frame_source.get_frame_bgra()
            if not bgra_frame:
                return None
        except Exception as e:
            logger.warning(f"Failed to get BGRA frame: {e}")
            return None

        # 编码
        try:
            encoded = self._encoder.encode_frame(bytes(bgra_frame))
            if not encoded:
                return None

            # 返回编码数据（已包含帧类型前缀）
            return encoded

        except Exception as e:
            logger.error(f"Failed to encode frame: {e}")
            return None

    def stop(self):
        """停止编码器。"""
        if self._encoder:
            try:
                self._encoder.stop()
            except Exception as e:
                logger.warning(f"Error stopping H264 encoder: {e}")
            self._encoder = None
            logger.info("H264 encoder stopped")