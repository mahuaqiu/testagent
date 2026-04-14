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
from typing import Any, Dict, List, Optional, Set

from worker.task import Action, ActionResult, ActionStatus
from worker.config import PlatformConfig

logger = logging.getLogger(__name__)


class PlatformManager(ABC):
    """
    平台管理器抽象基类。

    所有平台执行引擎都需要继承此类并实现抽象方法。
    基于 OCR/图像识别定位，不依赖传统元素选择器。
    """

    # 通用动作列表（所有平台支持）
    BASE_SUPPORTED_ACTIONS: Set[str] = {
        "ocr_click", "ocr_input", "ocr_wait", "ocr_assert", "ocr_get_text", "ocr_paste",
        "ocr_move", "ocr_double_click", "ocr_exist",
        "ocr_click_same_row_text", "ocr_click_same_row_image",
        "ocr_check_same_row_text", "ocr_check_same_row_image",
        "ocr_get_position",
        "image_click", "image_wait", "image_assert", "image_click_near_text",
        "image_move", "image_double_click", "image_exist",
        "image_get_position",
        "click", "double_click", "swipe", "drag", "input", "press", "screenshot", "wait",
        "move",
        "cmd_exec",  # 宿主机命令执行
    }

    # 子类可覆盖，定义平台特有动作
    SUPPORTED_ACTIONS: Set[str] = set()

    def __init__(self, config: PlatformConfig, ocr_client=None):
        """
        初始化平台管理器。

        Args:
            config: 平台配置
            ocr_client: OCR 客户端
        """
        self.config = config
        self.ocr_client = ocr_client
        self._started = False
        # 存储当前活跃的执行上下文（driver/context）
        self._contexts: Dict[str, Any] = {}

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
    def create_context(self, device_id: Optional[str] = None, options: Optional[Dict] = None) -> Any:
        """
        创建执行上下文。

        Args:
            device_id: 设备 ID（移动端需要）
            options: 其他选项

        Returns:
            Any: 执行上下文（如 Page、Driver 等）
        """
        pass

    @abstractmethod
    def close_context(self, context: Any, close_session: bool = False) -> None:
        """
        关闭执行上下文。

        Args:
            context: 执行上下文
            close_session: 是否关闭整个会话（True=关闭 browser/driver，False=只关闭 page/context）
        """
        pass

    @abstractmethod
    def execute_action(self, context: Any, action: Action) -> ActionResult:
        """
        执行动作。

        Args:
            context: 执行上下文
            action: 动作对象

        Returns:
            ActionResult: 动作执行结果
        """
        pass

    @abstractmethod
    def get_screenshot(self, context: Any) -> bytes:
        """
        获取当前屏幕截图。

        Args:
            context: 执行上下文

        Returns:
            bytes: 截图数据
        """
        pass

    # ========== 平台基础能力接口 ==========

    @abstractmethod
    def click(self, x: int, y: int, context: Any = None) -> None:
        """
        点击指定坐标。

        Args:
            x: X 坐标
            y: Y 坐标
            context: 执行上下文（可选）
        """
        pass

    @abstractmethod
    def double_click(self, x: int, y: int, context: Any = None) -> None:
        """
        双击指定坐标。

        Args:
            x: X 坐标
            y: Y 坐标
            context: 执行上下文（可选）
        """
        pass

    @abstractmethod
    def move(self, x: int, y: int, context: Any = None) -> None:
        """
        移动鼠标到指定坐标（不点击）。

        Args:
            x: X 坐标
            y: Y 坐标
            context: 执行上下文（可选）
        """
        pass

    @abstractmethod
    def input_text(self, text: str, context: Any = None) -> None:
        """
        输入文本。

        Args:
            text: 要输入的文本
            context: 执行上下文（可选）
        """
        pass

    @abstractmethod
    def swipe(self, start_x: int, start_y: int, end_x: int, end_y: int, context: Any = None) -> None:
        """
        滑动/拖拽。

        Args:
            start_x: 起始 X 坐标
            start_y: 起始 Y 坐标
            end_x: 结束 X 坐标
            end_y: 结束 Y 坐标
            context: 执行上下文（可选）
        """
        pass

    @abstractmethod
    def press(self, key: str, context: Any = None) -> None:
        """
        按键。

        Args:
            key: 按键名称
            context: 执行上下文（可选）
        """
        pass

    @abstractmethod
    def take_screenshot(self, context: Any = None) -> bytes:
        """
        获取截图（基础能力接口）。

        Args:
            context: 执行上下文（可选）

        Returns:
            bytes: 截图数据
        """
        pass

    # ========== 设备服务管理（移动端平台实现） ==========

    def ensure_device_service(self, udid: str) -> tuple[str, str]:
        """
        确保设备服务可用。

        Args:
            udid: 设备 UDID

        Returns:
            tuple[str, str]: (status, message) - status 为 "online" 或 "faulty"
        """
        # 默认实现：非移动端平台始终返回 online
        return ("online", "OK")

    def mark_device_faulty(self, udid: str) -> None:
        """
        标记设备为异常。

        Args:
            udid: 设备 UDID
        """
        pass

    def get_online_devices(self) -> list[str]:
        """
        获取在线设备 UDID 列表。

        Returns:
            list[str]: 在线设备 UDID 列表
        """
        return []

    def get_supported_actions(self) -> Set[str]:
        """
        获取支持的动作列表。

        Returns:
            Set[str]: 支持的动作类型集合
        """
        return self.BASE_SUPPORTED_ACTIONS | self.SUPPORTED_ACTIONS

    def is_action_supported(self, action_type: str) -> bool:
        """
        检查动作是否支持。

        Args:
            action_type: 动作类型

        Returns:
            bool: 是否支持
        """
        return action_type in self.get_supported_actions()

    # ========== OCR/图像识别辅助方法 ==========

    def _find_text_position(self, image_bytes: bytes, text: str, match_mode: str = "exact", index: int = 0) -> Optional[tuple[int, int]]:
        """
        在图像中查找文字位置，支持 index 参数选择第几个匹配结果。

        OCR 服务端自动处理匹配策略：精确匹配 → 模糊匹配。
        正则匹配通过 "reg_" 前缀标识。

        Args:
            image_bytes: 图像数据
            text: 目标文字
            match_mode: 匹配模式（regex 需添加 reg_ 前缀，其他直接透传）
            index: 选择第几个匹配结果（0=第一个，1=第二个，以此类推）

        Returns:
            tuple[int, int] | None: 文字中心坐标 (x, y)
        """
        if not self.ocr_client:
            logger.error("OCR client not available")
            return None

        # 处理正则模式：添加 reg_ 前缀
        actual_text = text
        if match_mode == "regex" and not text.startswith("reg_"):
            actual_text = f"reg_{text}"

        if index == 0:
            # 使用原有的 find_text，只返回第一个结果
            text_block = self.ocr_client.find_text(image_bytes, actual_text)
            if text_block:
                return text_block.center
        else:
            # index > 0 时，获取所有匹配结果
            all_texts = self.ocr_client.find_all_texts(image_bytes, actual_text)
            if all_texts and len(all_texts) > index:
                return all_texts[index].center
        return None

    def _find_image_position(self, source_bytes: bytes, template_base64: str, threshold: float = 0.8, index: int = 0) -> Optional[tuple[int, int]]:
        """
        在源图像中查找模板图像位置，支持 index 参数选择第几个匹配结果。

        Args:
            source_bytes: 源图像数据
            template_base64: 模板图像 base64 编码
            threshold: 匹配阈值
            index: 选择第几个匹配结果（0=第一个，1=第二个，以此类推）

        Returns:
            tuple[int, int] | None: 匹配中心坐标 (x, y)
        """
        if not self.ocr_client:
            logger.error("OCR client not available")
            return None

        if not template_base64:
            logger.error("Template image base64 is empty")
            return None

        # 解码 base64
        template_bytes = self._base64_to_bytes(template_base64)

        if index == 0:
            # 使用原有的 find_image，只返回第一个结果
            match = self.ocr_client.find_image(source_bytes, template_bytes, threshold=threshold)
            if match:
                return match.center
        else:
            # index > 0 时，获取所有匹配结果
            matches = self.ocr_client.match_image(source_bytes, template_bytes, threshold=threshold, multi_target=True)
            if matches and len(matches) > index:
                return matches[index].center
        return None

    def _find_all_text_positions(self, image_bytes: bytes, text: str) -> List[tuple[int, int]]:
        """
        获取所有匹配文字的坐标列表。

        OCR 服务端自动处理匹配策略：精确匹配 → 模糊匹配。
        正则匹配通过 "reg_" 前缀标识。

        Args:
            image_bytes: 截图数据
            text: 目标文字（以 reg_ 开头表示正则表达式）

        Returns:
            坐标列表 [(x1, y1), (x2, y2), ...]
        """
        if not self.ocr_client:
            logger.error("OCR client not available")
            return []

        # 直接透传给 OCR 服务端，服务端自动处理精确→模糊降级匹配
        all_texts = self.ocr_client.find_all_texts(image_bytes, text)
        positions = [t.center for t in all_texts]
        logger.debug(f"Found {len(positions)} positions for text: \"{text}\"")
        return positions

    def _find_all_image_positions(
        self,
        source_bytes: bytes,
        template_base64: str,
        threshold: float = 0.8
    ) -> List[tuple[int, int]]:
        """
        获取所有匹配图片的坐标列表。

        Args:
            source_bytes: 源图像数据
            template_base64: 模板图像 base64
            threshold: 匹配阈值

        Returns:
            坐标列表 [(x1, y1), (x2, y2), ...]
        """
        if not self.ocr_client:
            logger.error("OCR client not available")
            return []

        if not template_base64:
            logger.error("Template image base64 is empty")
            return []

        # 解码 base64
        template_bytes = self._base64_to_bytes(template_base64)

        # 获取所有匹配结果
        matches = self.ocr_client.match_image(source_bytes, template_bytes, threshold=threshold, multi_target=True)
        positions = [m.center for m in matches]
        logger.debug(f"Found {len(positions)} image positions with threshold={threshold}")

        return positions

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

    # ========== 会话管理方法（可由子类覆盖） ==========

    def has_active_session(self, device_id: Optional[str] = None) -> bool:
        """
        检查是否有活跃的会话。

        Args:
            device_id: 设备 ID（可选）

        Returns:
            bool: 是否有活跃会话
        """
        return False

    def get_session_context(self, device_id: Optional[str] = None) -> Any:
        """
        获取当前会话的上下文。

        Args:
            device_id: 设备 ID（可选）

        Returns:
            Any: 会话上下文（如 Page、Driver），如果没有则返回 None
        """
        return None

    def close_session(self, device_id: Optional[str] = None) -> None:
        """
        关闭会话（由 stop_app 调用）。

        Args:
            device_id: 设备 ID（可选）
        """
        pass