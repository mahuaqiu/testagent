"""Windows 窗口查找工具。"""

import logging

import win32gui

logger = logging.getLogger(__name__)


def find_window_handle(
    title: str | None = None,
    class_name: str | None = None
) -> int | None:
    """
    查找窗口句柄。

    Args:
        title: 窗口标题（包含匹配），可选
        class_name: 窗口类名（精确匹配），可选

    Returns:
        int: 窗口句柄，找不到返回 None

    匹配逻辑：
        - 只传 title: 遍历窗口，标题包含匹配
        - 只传 class: FindWindow 精确匹配
        - 都传: 先按 class 查找，再验证 title 包含匹配
        - 都不传: 返回 None（全屏模式）
    """
    if not title and not class_name:
        return None

    # 同时传 title + class: 先按 class 查找，再验证 title
    if class_name and title:
        hwnd = win32gui.FindWindow(class_name, None)
        if hwnd and win32gui.IsWindowVisible(hwnd):
            window_title = win32gui.GetWindowText(hwnd)
            if title in window_title:
                logger.debug(f"Window found by class+title: hwnd={hwnd}, title='{window_title}'")
                return hwnd
        logger.warning(f"Window not found: class='{class_name}', title='{title}'")
        return None

    # 只传 class: 精确匹配
    if class_name:
        hwnd = win32gui.FindWindow(class_name, None)
        if hwnd and win32gui.IsWindowVisible(hwnd):
            logger.debug(f"Window found by class: hwnd={hwnd}, class='{class_name}'")
            return hwnd
        logger.warning(f"Window not found by class: '{class_name}'")
        return None

    # 只传 title: 包含匹配
    if title:
        result: list[int | None] = [None]  # 使用列表作为可变容器
        def enum_callback(hwnd, _):
            if win32gui.IsWindowVisible(hwnd):
                window_title = win32gui.GetWindowText(hwnd)
                if title in window_title:
                    result[0] = hwnd
            return True
        win32gui.EnumWindows(enum_callback, None)
        found_hwnd = result[0]
        if found_hwnd:
            logger.debug(f"Window found by title: hwnd={found_hwnd}, title contains '{title}'")
        else:
            logger.warning(f"Window not found by title: '{title}'")
        return found_hwnd

    return None


def get_window_rect(hwnd: int) -> tuple[int, int, int, int]:
    """
    获取窗口矩形区域。

    Args:
        hwnd: 窗口句柄

    Returns:
        Tuple[int, int, int, int]: (left, top, right, bottom)
    """
    return win32gui.GetWindowRect(hwnd)


def get_window_title(hwnd: int) -> str:
    """
    获取窗口标题。

    Args:
        hwnd: 窗口句柄

    Returns:
        str: 窗口标题
    """
    return win32gui.GetWindowText(hwnd)


def get_window_class(hwnd: int) -> str:
    """
    获取窗口类名。

    Args:
        hwnd: 窗口句柄

    Returns:
        str: 窗口类名
    """
    return win32gui.GetClassName(hwnd)
