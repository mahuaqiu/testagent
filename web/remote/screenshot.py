"""
截图管理器。

管理截图的生成、存储和编码。
"""

import os
import base64
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional
from pathlib import Path


@dataclass
class ScreenshotData:
    """
    截图数据。

    Attributes:
        name: 截图名称
        data: 图片二进制数据
        timestamp: 截图时间
        action_index: 对应的动作索引
        format: 图片格式
    """

    name: str
    data: bytes
    timestamp: datetime = field(default_factory=datetime.now)
    action_index: Optional[int] = None
    format: str = "png"

    def to_base64(self) -> str:
        """转换为 Base64 字符串。"""
        return base64.b64encode(self.data).decode("utf-8")

    def to_dict(self, include_data: bool = True) -> dict:
        """转换为字典。"""
        result = {
            "name": self.name,
            "timestamp": self.timestamp.isoformat(),
            "action_index": self.action_index,
            "format": self.format,
        }
        if include_data:
            result["data"] = self.to_base64()
        return result


class ScreenshotManager:
    """
    截图管理器。

    管理截图的生成、存储和编码。

    Usage:
        manager = ScreenshotManager(output_dir="data/screenshots")
        screenshot = manager.capture(page, name="login_page")
        manager.save(screenshot)
    """

    def __init__(
        self,
        output_dir: str = "data/screenshots",
        auto_save: bool = True,
        format: str = "png",
    ):
        """
        初始化截图管理器。

        Args:
            output_dir: 截图输出目录
            auto_save: 是否自动保存到文件
            format: 图片格式 (png/jpeg)
        """
        self._output_dir = Path(output_dir)
        self._auto_save = auto_save
        self._format = format
        self._screenshots: list[ScreenshotData] = []

        # 确保输出目录存在
        if self._auto_save:
            self._output_dir.mkdir(parents=True, exist_ok=True)

    @property
    def output_dir(self) -> Path:
        return self._output_dir

    @output_dir.setter
    def output_dir(self, value: str) -> None:
        self._output_dir = Path(value)
        if self._auto_save:
            self._output_dir.mkdir(parents=True, exist_ok=True)

    def capture(
        self,
        page,
        name: Optional[str] = None,
        full_page: bool = False,
        action_index: Optional[int] = None,
    ) -> ScreenshotData:
        """
        从 Playwright Page 捕获截图。

        Args:
            page: Playwright Page 对象
            name: 截图名称（不含扩展名）
            full_page: 是否截取整个页面
            action_index: 对应的动作索引

        Returns:
            ScreenshotData: 截图数据
        """
        if name is None:
            name = f"screenshot_{self._timestamp()}"

        # 使用 Playwright 的截图方法
        screenshot_bytes = page.screenshot(
            type=self._format,
            full_page=full_page,
        )

        screenshot = ScreenshotData(
            name=name,
            data=screenshot_bytes,
            timestamp=datetime.now(),
            action_index=action_index,
            format=self._format,
        )

        self._screenshots.append(screenshot)

        # 自动保存
        if self._auto_save:
            self.save(screenshot)

        return screenshot

    def capture_element(
        self,
        page,
        selector: str,
        name: Optional[str] = None,
        action_index: Optional[int] = None,
    ) -> Optional[ScreenshotData]:
        """
        截取指定元素的截图。

        Args:
            page: Playwright Page 对象
            selector: 元素选择器
            name: 截图名称
            action_index: 对应的动作索引

        Returns:
            ScreenshotData 或 None（元素不存在时）
        """
        if name is None:
            name = f"element_{self._timestamp()}"

        try:
            element = page.locator(selector)
            if element.count() == 0:
                return None

            screenshot_bytes = element.screenshot(type=self._format)

            screenshot = ScreenshotData(
                name=name,
                data=screenshot_bytes,
                timestamp=datetime.now(),
                action_index=action_index,
                format=self._format,
            )

            self._screenshots.append(screenshot)

            if self._auto_save:
                self.save(screenshot)

            return screenshot

        except Exception:
            return None

    def save(self, screenshot: ScreenshotData) -> Path:
        """
        保存截图到文件。

        Args:
            screenshot: 截图数据

        Returns:
            Path: 保存的文件路径
        """
        filename = f"{screenshot.name}.{screenshot.format}"
        filepath = self._output_dir / filename

        # 避免文件名冲突
        counter = 1
        while filepath.exists():
            filename = f"{screenshot.name}_{counter}.{screenshot.format}"
            filepath = self._output_dir / filename
            counter += 1

        filepath.write_bytes(screenshot.data)
        return filepath

    def get_screenshots(self) -> list[ScreenshotData]:
        """获取所有截图。"""
        return self._screenshots.copy()

    def get_screenshots_as_dicts(self, include_data: bool = True) -> list[dict]:
        """获取所有截图（字典格式）。"""
        return [s.to_dict(include_data=include_data) for s in self._screenshots]

    def get_by_action_index(self, action_index: int) -> list[ScreenshotData]:
        """根据动作索引获取截图。"""
        return [s for s in self._screenshots if s.action_index == action_index]

    def clear(self) -> None:
        """清空截图缓存。"""
        self._screenshots.clear()

    def _timestamp(self) -> str:
        """生成时间戳字符串。"""
        return time.strftime("%Y%m%d_%H%M%S")

    def __len__(self) -> int:
        return len(self._screenshots)

    def __repr__(self) -> str:
        return f"ScreenshotManager(output_dir={str(self._output_dir)!r}, screenshots_count={len(self._screenshots)})"