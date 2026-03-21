"""
Action 执行器模块。

将动作执行逻辑从平台类中抽离，通过策略模式实现平台无关的动作执行。
"""

from worker.actions.base import ActionExecutor, BaseActionExecutor
from worker.actions.registry import ActionRegistry
from worker.task import ActionResult, ActionStatus

# 导入所有 Action 执行器
from worker.actions.ocr import (
    OcrClickAction,
    OcrInputAction,
    OcrWaitAction,
    OcrAssertAction,
    OcrGetTextAction,
    OcrPasteAction,
)
from worker.actions.image import (
    ImageClickAction,
    ImageWaitAction,
    ImageAssertAction,
)
from worker.actions.coordinate import (
    ClickAction,
    InputAction,
    SwipeAction,
    PressAction,
    ScreenshotAction,
    WaitAction,
)

# 注册所有 Actions
def _register_all_actions():
    """注册所有 Action 执行器。"""
    # OCR Actions
    ActionRegistry.register(OcrClickAction())
    ActionRegistry.register(OcrInputAction())
    ActionRegistry.register(OcrWaitAction())
    ActionRegistry.register(OcrAssertAction())
    ActionRegistry.register(OcrGetTextAction())
    ActionRegistry.register(OcrPasteAction())

    # Image Actions
    ActionRegistry.register(ImageClickAction())
    ActionRegistry.register(ImageWaitAction())
    ActionRegistry.register(ImageAssertAction())

    # Coordinate Actions
    ActionRegistry.register(ClickAction())
    ActionRegistry.register(InputAction())
    ActionRegistry.register(SwipeAction())
    ActionRegistry.register(PressAction())
    ActionRegistry.register(ScreenshotAction())
    ActionRegistry.register(WaitAction())


# 模块加载时自动注册
_register_all_actions()

__all__ = [
    "ActionExecutor",
    "BaseActionExecutor",
    "ActionRegistry",
    "ActionResult",
    "ActionStatus",
    # OCR Actions
    "OcrClickAction",
    "OcrInputAction",
    "OcrWaitAction",
    "OcrAssertAction",
    "OcrGetTextAction",
    "OcrPasteAction",
    # Image Actions
    "ImageClickAction",
    "ImageWaitAction",
    "ImageAssertAction",
    # Coordinate Actions
    "ClickAction",
    "InputAction",
    "SwipeAction",
    "PressAction",
    "ScreenshotAction",
    "WaitAction",
]