"""
平台执行引擎基类。

定义所有平台需要实现的接口，基于 OCR/图像识别定位。
"""

import base64
import logging
import os
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional

from worker.task import Action, ActionResult, ActionStatus
from worker.config import PlatformConfig

logger = logging.getLogger(__name__)


@dataclass
class Session:
    """
    会话数据结构。

    表示一个平台会话，可以复用以执行多个任务。
    """

    session_id: str
    platform: str
    device_id: Optional[str] = None
    created_at: datetime = field(default_factory=datetime.now)
    last_active: datetime = field(default_factory=datetime.now)
    context: Any = None  # Playwright BrowserContext / Appium Driver / etc.
    metadata: Dict[str, Any] = field(default_factory=dict)


class PlatformManager(ABC):
    """
    平台管理器抽象基类。

    所有平台执行引擎都需要继承此类并实现抽象方法。
    基于 OCR/图像识别定位，不依赖传统元素选择器。
    """

    def __init__(self, config: PlatformConfig, ocr_client=None):
        """
        初始化平台管理器。

        Args:
            config: 平台配置
            ocr_client: OCR 客户端
        """
        self.config = config
        self.ocr_client = ocr_client
        self.sessions: Dict[str, Session] = {}
        self._started = False

    @property
    @abstractmethod
    def platform(self) -> str:
        """平台名称。"""
        pass

    @abstractmethod
    def start(self) -> None:
        """
        启动平台资源。

        例如：启动浏览器、连接 Appium、初始化桌面自动化等。
        """
        pass

    @abstractmethod
    def stop(self) -> None:
        """
        停止平台资源。

        释放所有资源，关闭连接等。
        """
        pass

    @abstractmethod
    def is_available(self) -> bool:
        """
        检查平台是否可用。

        Returns:
            bool: 平台是否可用
        """
        pass

    @abstractmethod
    def create_session(self, device_id: Optional[str] = None, options: Optional[Dict] = None) -> Session:
        """
        创建会话。

        Args:
            device_id: 设备 ID（移动端需要）
            options: 其他选项

        Returns:
            Session: 会话对象
        """
        pass

    @abstractmethod
    def close_session(self, session_id: str) -> bool:
        """
        关闭会话。

        Args:
            session_id: 会话 ID

        Returns:
            bool: 是否成功关闭
        """
        pass

    @abstractmethod
    def execute_action(self, session: Session, action: Action) -> ActionResult:
        """
        执行动作。

        Args:
            session: 会话对象
            action: 动作对象

        Returns:
            ActionResult: 动作执行结果
        """
        pass

    @abstractmethod
    def get_screenshot(self, session: Session) -> bytes:
        """
        获取当前屏幕截图。

        Args:
            session: 会话对象

        Returns:
            bytes: 截图数据
        """
        pass

    def get_session(self, session_id: str) -> Optional[Session]:
        """
        获取会话。

        Args:
            session_id: 会话 ID

        Returns:
            Session | None: 会话对象
        """
        return self.sessions.get(session_id)

    def get_active_sessions(self) -> List[Session]:
        """
        获取所有活跃会话。

        Returns:
            List[Session]: 会话列表
        """
        return list(self.sessions.values())

    def cleanup_expired_sessions(self, timeout: int = 300) -> List[str]:
        """
        清理超时会话。

        Args:
            timeout: 超时时间（秒）

        Returns:
            List[str]: 被清理的会话 ID 列表
        """
        now = datetime.now()
        expired = []

        for session_id, session in list(self.sessions.items()):
            elapsed = (now - session.last_active).total_seconds()
            if elapsed > timeout:
                try:
                    self.close_session(session_id)
                    expired.append(session_id)
                    logger.info(f"Session {session_id} expired and closed")
                except Exception as e:
                    logger.error(f"Failed to close expired session {session_id}: {e}")

        return expired

    def _update_session_activity(self, session_id: str) -> None:
        """更新会话活动时间。"""
        if session_id in self.sessions:
            self.sessions[session_id].last_active = datetime.now()

    # ========== OCR/图像识别辅助方法 ==========

    def _find_text_position(self, image_bytes: bytes, text: str, match_mode: str = "exact") -> Optional[tuple[int, int]]:
        """
        在图像中查找文字位置。

        Args:
            image_bytes: 图像数据
            text: 目标文字
            match_mode: 匹配模式

        Returns:
            tuple[int, int] | None: 文字中心坐标 (x, y)
        """
        if not self.ocr_client:
            logger.error("OCR client not available")
            return None

        text_block = self.ocr_client.find_text(image_bytes, text, match_mode=match_mode)
        if text_block:
            return text_block.center
        return None

    def _find_image_position(self, source_bytes: bytes, template_path: str, threshold: float = 0.8) -> Optional[tuple[int, int]]:
        """
        在源图像中查找模板图像位置。

        Args:
            source_bytes: 源图像数据
            template_path: 模板图像路径
            threshold: 匹配阈值

        Returns:
            tuple[int, int] | None: 匹配中心坐标 (x, y)
        """
        if not self.ocr_client:
            logger.error("OCR client not available")
            return None

        if not os.path.exists(template_path):
            logger.error(f"Template image not found: {template_path}")
            return None

        with open(template_path, "rb") as f:
            template_bytes = f.read()

        match = self.ocr_client.find_image(source_bytes, template_bytes, threshold=threshold)
        if match:
            return match.center
        return None

    def _apply_offset(self, x: int, y: int, offset: Optional[Dict[str, int]]) -> tuple[int, int]:
        """
        应用偏移量。

        Args:
            x: 原始 X 坐标
            y: 原始 Y 坐标
            offset: 偏移量 {"x": 10, "y": 5}

        Returns:
            tuple[int, int]: 偏移后的坐标
        """
        if offset:
            x += offset.get("x", 0)
            y += offset.get("y", 0)
        return (x, y)

    def _save_screenshot(self, image_bytes: bytes, name: str, screenshot_dir: str) -> str:
        """
        保存截图到文件。

        Args:
            image_bytes: 图像数据
            name: 截图名称
            screenshot_dir: 截图目录

        Returns:
            str: 文件路径
        """
        os.makedirs(screenshot_dir, exist_ok=True)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"{name}_{timestamp}.png"
        filepath = os.path.join(screenshot_dir, filename)

        with open(filepath, "wb") as f:
            f.write(image_bytes)

        return filepath

    def _bytes_to_base64(self, data: bytes) -> str:
        """将字节数据转换为 base64 字符串。"""
        return base64.b64encode(data).decode("utf-8")

    def _base64_to_bytes(self, data: str) -> bytes:
        """将 base64 字符串转换为字节数据。"""
        return base64.b64decode(data)

    def _wait(self, ms: int) -> None:
        """等待指定毫秒。"""
        time.sleep(ms / 1000.0)