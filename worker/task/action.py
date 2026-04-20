"""
动作类型定义。

所有平台统一使用 OCR/图像识别定位，不依赖传统元素选择器。
"""

from dataclasses import dataclass
from enum import Enum
from typing import Any


class ActionType(Enum):
    """动作类型枚举。"""

    # OCR 文字操作
    OCR_CLICK = "ocr_click"           # 点击识别到的文字
    OCR_ASSERT = "ocr_assert"         # 断言文字存在
    OCR_WAIT = "ocr_wait"             # 等待文字出现
    OCR_INPUT = "ocr_input"           # 在文字附近输入
    OCR_GET_TEXT = "ocr_get_text"     # 获取文字区域内容
    OCR_PASTE = "ocr_paste"           # OCR定位后粘贴
    OCR_EXIST = "ocr_exist"           # 检查文字是否存在

    # 图像匹配操作
    IMAGE_CLICK = "image_click"       # 点击匹配的图像
    IMAGE_ASSERT = "image_assert"     # 断言图像存在
    IMAGE_WAIT = "image_wait"         # 等待图像出现
    IMAGE_CLICK_NEAR_TEXT = "image_click_near_text"  # 点击文本附近最近的图像
    IMAGE_EXIST = "image_exist"       # 检查图像是否存在

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
    START_APP = "start_app"          # 启动应用
    STOP_APP = "stop_app"            # 关闭应用


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
    value: str | None = None              # 文字/URL/按键值
    image_base64: str | None = None       # 图像模板 base64 编码
    offset: dict[str, int] | None = None  # 点击偏移 {"x": 10, "y": 5}
    threshold: float = 0.8                   # 图像匹配阈值
    timeout: int = 30000                     # 超时时间(ms)
    match_mode: str = "exact"                # OCR 匹配模式
    screenshot: bool = False                 # 是否截图
    wait: int | None = None               # 等待时间(ms)

    # 执行层级（Web 平台专用）
    level: str = "browser"                   # "browser" 使用 Playwright，"system" 使用系统级操作（pyautogui）

    # 系统级截图参数（Web 平台专用，配合 level: system 使用）
    monitor: int = 1                         # 截取哪个显示器：1=第一个显示器，2=第二个显示器，默认 1

    # 扩展参数
    index: int | None = None              # 选择第几个匹配结果（默认0）
    time: int | None = None               # 等待时间（秒），用于 ocr_wait/wait
    text: str | None = None               # 粘贴内容，用于 ocr_paste

    # 坐标操作
    x: int | None = None                  # X 坐标
    y: int | None = None                  # Y 坐标
    end_x: int | None = None              # 终点 X 坐标（滑动）
    end_y: int | None = None              # 终点 Y 坐标（滑动）
    direction: str | None = None          # 滑动方向
    duration: int | None = None           # 滑动持续时间（毫秒）
    click_duration: int | None = None     # 点击持续时间（毫秒），用于长按
    steps: int | None = None              # 滑动步数，控制轨迹平滑度

    # 应用操作
    app_path: str | None = None           # 应用路径
    restart: bool = False                 # 是否强制重启（Windows 平台专用）
    bundle_id: str | None = None          # iOS Bundle ID
    package_name: str | None = None       # Android 包名
    permissions: Any | None = None         # Web 权限配置（如 ["camera","microphone"] 或 "false"）

    # 同行定位参数
    anchor_text: str | None = None        # 锚点文本（用于同行定位）
    anchor_index: int | None = None       # 锚点索引（第几个匹配）
    row_tolerance: int | None = None      # 水平带范围（像素，默认20）
    target_index: int | None = None       # 目标索引（同行第几个匹配）
    region: list[int] | None = None       # 操作区域 [x1, y1, x2, y2]

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Action":
        """从字典创建动作。"""
        # 兼容处理：key 字段映射到 value（用于 press 动作）
        value = data.get("value")
        if value is None and data.get("key"):
            value = data.get("key")

        # 处理 swipe/drag 的 from/to 格式
        x = data.get("x")
        y = data.get("y")
        end_x = data.get("end_x")
        end_y = data.get("end_y")

        # 兼容 from: {x, y} / to: {x, y} 格式
        from_coord = data.get("from")
        to_coord = data.get("to")
        if from_coord:
            x = from_coord.get("x", x)
            y = from_coord.get("y", y)
        if to_coord:
            end_x = to_coord.get("x", end_x)
            end_y = to_coord.get("y", end_y)

        return cls(
            action_type=data.get("action_type", ""),
            value=value,
            image_base64=data.get("image_base64"),
            offset=data.get("offset"),
            threshold=data.get("threshold", 0.8),
            timeout=data.get("timeout", 30000),
            match_mode=data.get("match_mode", "exact"),
            screenshot=data.get("screenshot", False),
            wait=data.get("wait"),
            level=data.get("level", "browser"),
            monitor=data.get("monitor", 1),
            x=x,
            y=y,
            end_x=end_x,
            end_y=end_y,
            direction=data.get("direction"),
            duration=data.get("duration"),
            click_duration=data.get("click_duration"),
            steps=data.get("steps"),
            app_path=data.get("app_path"),
            restart=data.get("restart", False),
            bundle_id=data.get("bundle_id"),
            package_name=data.get("package_name"),
            permissions=data.get("permissions"),
            index=data.get("index"),
            time=data.get("time"),
            text=data.get("text"),
            anchor_text=data.get("anchor_text"),
            anchor_index=data.get("anchor_index"),
            row_tolerance=data.get("row_tolerance"),
            target_index=data.get("target_index"),
            region=data.get("region"),
        )

    def to_dict(self) -> dict[str, Any]:
        """转换为字典。"""
        result = {"action_type": self.action_type}

        if self.value is not None:
            result["value"] = self.value
        if self.image_base64 is not None:
            result["image_base64"] = self.image_base64
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
        if self.level != "browser":
            result["level"] = self.level
        if self.monitor != 1:
            result["monitor"] = self.monitor
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
        if self.duration is not None:
            result["duration"] = self.duration
        if self.click_duration is not None:
            result["click_duration"] = self.click_duration
        if self.steps is not None:
            result["steps"] = self.steps
        if self.app_path is not None:
            result["app_path"] = self.app_path
        if self.restart:
            result["restart"] = self.restart
        if self.bundle_id is not None:
            result["bundle_id"] = self.bundle_id
        if self.package_name is not None:
            result["package_name"] = self.package_name
        if self.index is not None:
            result["index"] = self.index
        if self.time is not None:
            result["time"] = self.time
        if self.text is not None:
            result["text"] = self.text
        if self.anchor_text is not None:
            result["anchor_text"] = self.anchor_text
        if self.anchor_index is not None:
            result["anchor_index"] = self.anchor_index
        if self.row_tolerance is not None:
            result["row_tolerance"] = self.row_tolerance
        if self.target_index is not None:
            result["target_index"] = self.target_index
        if self.region is not None:
            result["region"] = self.region

        return result
