"""
动作类型定义。

所有平台统一使用 OCR/图像识别定位，不依赖传统元素选择器。
"""

from enum import Enum
from dataclasses import dataclass, field
from typing import Optional, Dict, Any


class ActionType(Enum):
    """动作类型枚举。"""

    # OCR 文字操作
    OCR_CLICK = "ocr_click"           # 点击识别到的文字
    OCR_ASSERT = "ocr_assert"         # 断言文字存在
    OCR_WAIT = "ocr_wait"             # 等待文字出现
    OCR_INPUT = "ocr_input"           # 在文字附近输入
    OCR_GET_TEXT = "ocr_get_text"     # 获取文字区域内容

    # 图像匹配操作
    IMAGE_CLICK = "image_click"       # 点击匹配的图像
    IMAGE_ASSERT = "image_assert"     # 断言图像存在
    IMAGE_WAIT = "image_wait"         # 等待图像出现

    # 基础操作（坐标/按键）
    CLICK = "click"                   # 坐标点击 (x, y)
    SWIPE = "swipe"                   # 滑动 (方向/坐标)
    INPUT = "input"                   # 输入文本
    PRESS = "press"                   # 按键
    SCREENSHOT = "screenshot"         # 截图
    WAIT = "wait"                     # 固定等待

    # Web 专用
    NAVIGATE = "navigate"             # 跳转 URL

    # 应用操作
    LAUNCH_APP = "launch_app"         # 启动应用
    CLOSE_APP = "close_app"           # 关闭应用


class MatchMode(Enum):
    """OCR 匹配模式。"""
    EXACT = "exact"           # 精确匹配
    FUZZY = "fuzzy"           # 模糊匹配
    CONTAINS = "contains"     # 包含匹配
    REGEX = "regex"           # 正则匹配


class SwipeDirection(Enum):
    """滑动方向。"""
    UP = "up"
    DOWN = "down"
    LEFT = "left"
    RIGHT = "right"


@dataclass
class Action:
    """
    动作数据结构。

    所有操作基于 OCR/图像识别或坐标定位。
    """

    action_type: str                         # 动作类型
    value: Optional[str] = None              # 文字/URL/按键值
    image_path: Optional[str] = None         # 图像模板路径
    offset: Optional[Dict[str, int]] = None  # 点击偏移 {"x": 10, "y": 5}
    threshold: float = 0.8                   # 图像匹配阈值
    timeout: int = 30000                     # 超时时间(ms)
    match_mode: str = "exact"                # OCR 匹配模式
    screenshot: bool = False                 # 是否截图
    wait: Optional[int] = None               # 等待时间(ms)

    # 坐标操作
    x: Optional[int] = None                  # X 坐标
    y: Optional[int] = None                  # Y 坐标
    end_x: Optional[int] = None              # 终点 X 坐标（滑动）
    end_y: Optional[int] = None              # 终点 Y 坐标（滑动）
    direction: Optional[str] = None          # 滑动方向

    # 应用操作
    app_path: Optional[str] = None           # 应用路径
    bundle_id: Optional[str] = None          # iOS Bundle ID
    package_name: Optional[str] = None       # Android 包名

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Action":
        """从字典创建动作。"""
        return cls(
            action_type=data.get("action_type", ""),
            value=data.get("value"),
            image_path=data.get("image_path"),
            offset=data.get("offset"),
            threshold=data.get("threshold", 0.8),
            timeout=data.get("timeout", 30000),
            match_mode=data.get("match_mode", "exact"),
            screenshot=data.get("screenshot", False),
            wait=data.get("wait"),
            x=data.get("x"),
            y=data.get("y"),
            end_x=data.get("end_x"),
            end_y=data.get("end_y"),
            direction=data.get("direction"),
            app_path=data.get("app_path"),
            bundle_id=data.get("bundle_id"),
            package_name=data.get("package_name"),
        )

    def to_dict(self) -> Dict[str, Any]:
        """转换为字典。"""
        result = {"action_type": self.action_type}

        if self.value is not None:
            result["value"] = self.value
        if self.image_path is not None:
            result["image_path"] = self.image_path
        if self.offset is not None:
            result["offset"] = self.offset
        if self.threshold != 0.8:
            result["threshold"] = self.threshold
        if self.timeout != 30000:
            result["timeout"] = self.timeout
        if self.match_mode != "exact":
            result["match_mode"] = self.match_mode
        if self.screenshot:
            result["screenshot"] = self.screenshot
        if self.wait is not None:
            result["wait"] = self.wait
        if self.x is not None:
            result["x"] = self.x
        if self.y is not None:
            result["y"] = self.y
        if self.end_x is not None:
            result["end_x"] = self.end_x
        if self.end_y is not None:
            result["end_y"] = self.end_y
        if self.direction is not None:
            result["direction"] = self.direction
        if self.app_path is not None:
            result["app_path"] = self.app_path
        if self.bundle_id is not None:
            result["bundle_id"] = self.bundle_id
        if self.package_name is not None:
            result["package_name"] = self.package_name

        return result