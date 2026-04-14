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
    OcrMoveAction,
    OcrDoubleClickAction,
    OcrExistAction,
    OcrClickSameRowTextAction,
    OcrCheckSameRowTextAction,
)
from worker.actions.image import (
    ImageClickAction,
    ImageWaitAction,
    ImageAssertAction,
    ImageClickNearTextAction,
    ImageMoveAction,
    ImageDoubleClickAction,
    ImageExistAction,
    OcrClickSameRowImageAction,
    OcrCheckSameRowImageAction,
)
from worker.actions.coordinate import (
    ClickAction,
    DoubleClickAction,
    MoveAction,
    InputAction,
    SwipeAction,
    DragAction,
    PressAction,
    ScreenshotAction,
    WaitAction,
)
from worker.actions.cmd_exec import CmdExecAction
from worker.actions.web_token import GetTokenAction
from worker.actions.position import OcrGetPositionExecutor, ImageGetPositionExecutor

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
    ActionRegistry.register(OcrMoveAction())
    ActionRegistry.register(OcrDoubleClickAction())
    ActionRegistry.register(OcrExistAction())
    ActionRegistry.register(OcrClickSameRowTextAction())
    ActionRegistry.register(OcrCheckSameRowTextAction())

    # Image Actions
    ActionRegistry.register(ImageClickAction())
    ActionRegistry.register(ImageWaitAction())
    ActionRegistry.register(ImageAssertAction())
    ActionRegistry.register(ImageClickNearTextAction())
    ActionRegistry.register(ImageMoveAction())
    ActionRegistry.register(ImageDoubleClickAction())
    ActionRegistry.register(ImageExistAction())
    ActionRegistry.register(OcrClickSameRowImageAction())
    ActionRegistry.register(OcrCheckSameRowImageAction())

    # Coordinate Actions
    ActionRegistry.register(ClickAction())
    ActionRegistry.register(DoubleClickAction())
    ActionRegistry.register(MoveAction())
    ActionRegistry.register(InputAction())
    ActionRegistry.register(SwipeAction())
    ActionRegistry.register(DragAction())
    ActionRegistry.register(PressAction())
    ActionRegistry.register(ScreenshotAction())
    ActionRegistry.register(WaitAction())

    # Cmd Exec Action
    ActionRegistry.register(CmdExecAction())

    # Web Token Action
    ActionRegistry.register(GetTokenAction())

    # Position Actions
    ActionRegistry.register(OcrGetPositionExecutor())
    ActionRegistry.register(ImageGetPositionExecutor())


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
    "OcrMoveAction",
    "OcrDoubleClickAction",
    "OcrExistAction",
    "OcrClickSameRowTextAction",
    "OcrCheckSameRowTextAction",
    # Image Actions
    "ImageClickAction",
    "ImageWaitAction",
    "ImageAssertAction",
    "ImageClickNearTextAction",
    "ImageMoveAction",
    "ImageDoubleClickAction",
    "ImageExistAction",
    "OcrClickSameRowImageAction",
    "OcrCheckSameRowImageAction",
    # Coordinate Actions
    "ClickAction",
    "DoubleClickAction",
    "MoveAction",
    "InputAction",
    "SwipeAction",
    "DragAction",
    "PressAction",
    "ScreenshotAction",
    "WaitAction",
    # Cmd Exec Action
    "CmdExecAction",
    # Web Token Action
    "GetTokenAction",
    # Position Actions
    "OcrGetPositionExecutor",
    "ImageGetPositionExecutor",
]