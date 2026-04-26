"""显示器信息工具模块。

提供多显示器场景下的配置获取和坐标转换功能。

显示器编号规则（与用户直觉一致）：
- monitor=1: 主屏幕（left=0 的显示器）
- monitor=2: 副屏幕（另一个显示器）

注意：mss 库的原始编号顺序与 Windows 主屏幕设置无关，
这里做了映射处理使其符合用户直觉。
"""

import logging
from typing import Dict, Tuple

logger = logging.getLogger(__name__)


def get_mss_monitors() -> list:
    """获取 mss 所有显示器配置列表。

    mss.monitors 结构：
    - monitors[0]: 所有显示器的合集（虚拟屏幕）
    - monitors[1]: 第一个物理显示器
    - monitors[2]: 第二个物理显示器（如有）

    Returns:
        list: mss 显示器配置列表
    """
    import mss
    with mss.mss() as sct:
        return sct.monitors


def get_mapped_monitor_index(monitor: int) -> Tuple[int, Dict]:
    """
    将用户显示器编号映射到 mss 的实际显示器索引。

    显示器编号规则（与用户直觉一致）：
    - monitor=1: 主屏幕（left=0 的显示器）
    - monitor=2: 副屏幕（另一个显示器）

    Args:
        monitor: 用户请求的显示器编号（1=主屏幕，2=副屏幕）

    Returns:
        Tuple[int, Dict]: (mss索引, 显示器配置字典)
    """
    monitors = get_mss_monitors()

    # 只有一个显示器的情况
    if len(monitors) <= 2:
        if len(monitors) > 1:
            return (1, monitors[1])
        else:
            return (0, monitors[0])

    # 多显示器：找 left=0 的作为主屏幕
    primary_index = None
    secondary_index = None
    for i in range(1, len(monitors)):
        if monitors[i]['left'] == 0:
            primary_index = i
        else:
            secondary_index = i

    if primary_index is None:
        # 没找到 left=0，使用默认顺序
        logger.warning("Could not find primary monitor (left=0), using mss default order")
        target_index = monitor
    else:
        # 映射：monitor=1 -> 主屏幕，monitor=2 -> 副屏幕
        if monitor == 1:
            target_index = primary_index
        elif monitor == 2:
            target_index = secondary_index if secondary_index else primary_index
        else:
            target_index = min(monitor, len(monitors) - 1)

    logger.debug(f"Monitor mapping: user requested {monitor} -> mss index {target_index}")
    return target_index, monitors[target_index]


def get_monitor_offset(monitor: int) -> Tuple[int, int]:
    """
    获取指定显示器相对于虚拟屏幕的偏移量。

    截图坐标是相对坐标（从显示器左上角 0,0 开始），
    pyautogui 需要全局坐标（虚拟屏幕坐标系）。

    Args:
        monitor: 用户请求的显示器编号（1=主屏幕，2=副屏幕）

    Returns:
        Tuple[int, int]: 显示器偏移量 (left, top)，用于坐标转换
    """
    _, monitor_config = get_mapped_monitor_index(monitor)
    return monitor_config['left'], monitor_config['top']


def convert_to_global_coords(x: int, y: int, monitor: int) -> Tuple[int, int]:
    """
    将截图相对坐标转换为 pyautogui 全局坐标。

    Args:
        x: 截图中的 X 坐标（相对坐标）
        y: 截图中的 Y 坐标（相对坐标）
        monitor: 用户请求的显示器编号

    Returns:
        Tuple[int, int]: 全局坐标（可直接传给 pyautogui）
    """
    offset_x, offset_y = get_monitor_offset(monitor)
    global_x = x + offset_x
    global_y = y + offset_y
    logger.debug(f"Coordinate conversion: ({x}, {y}) + offset ({offset_x}, {offset_y}) = ({global_x}, {global_y})")
    return global_x, global_y