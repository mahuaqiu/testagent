"""Windows 窗口查找工具。"""

import logging

import pywintypes
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

    匹配逻辑（统一使用 EnumWindows 遍历，确保可见性）：
        - 只传 title: 遍历窗口，第一个可见且标题包含匹配的
        - 只传 class: 遍历窗口，第一个可见且类名精确匹配的
        - 都传: 遍历窗口，第一个可见且类名精确匹配 + 标题包含匹配的
        - 都不传: 返回 None（全屏模式）
    """
    if not title and not class_name:
        return None

    # 统一使用 EnumWindows 遍历查找，避免 FindWindow 只返回一个窗口的问题
    result: list[int | None] = [None]

    def enum_callback(hwnd, _):
        try:
            # 只查找可见窗口
            if not win32gui.IsWindowVisible(hwnd):
                return True

            # class 精确匹配（如果指定了 class_name）
            if class_name:
                cls = win32gui.GetClassName(hwnd)
                if cls != class_name:
                    return True  # 类名不匹配，继续枚举

            # title 包含匹配（如果指定了 title）
            if title:
                window_title = win32gui.GetWindowText(hwnd)
                if title not in window_title:
                    return True  # 标题不匹配，继续枚举

            # 所有条件都匹配，记录结果并停止枚举
            result[0] = hwnd
            return False
        except pywintypes.error:
            # 某些系统窗口访问属性会抛异常，跳过即可
            return True
        except Exception:
            # 回调函数中任何异常都不应中断枚举
            return True

    try:
        win32gui.EnumWindows(enum_callback, None)
    except Exception as e:
        logger.error(f"EnumWindows failed: {e}")
        return None

    found_hwnd = result[0]
    if found_hwnd:
        try:
            window_title = win32gui.GetWindowText(found_hwnd)
            window_class = win32gui.GetClassName(found_hwnd)
            logger.debug(
                f"Window found: hwnd={found_hwnd}, "
                f"class='{window_class}', title='{window_title}'"
            )
        except Exception:
            logger.debug(f"Window found: hwnd={found_hwnd}")
    else:
        logger.warning(f"Window not found: class='{class_name}', title='{title}'")

    return found_hwnd


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
    try:
        return win32gui.GetWindowText(hwnd) or ""
    except Exception:
        return ""


def get_window_class(hwnd: int) -> str:
    """
    获取窗口类名。

    Args:
        hwnd: 窗口句柄

    Returns:
        str: 窗口类名
    """
    try:
        return win32gui.GetClassName(hwnd) or ""
    except Exception:
        return ""
