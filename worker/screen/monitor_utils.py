"""显示器信息工具模块。

提供多显示器场景下的配置获取和坐标转换功能。

显示器编号规则（与用户直觉一致）：
- monitor=1: 主屏幕（left=0 的显示器）
- monitor=2: 副屏幕（另一个显示器）

优先使用 Rust sidecar 获取显示器配置，fallback 到 mss。
"""

import logging
from typing import Dict, List, Tuple

logger = logging.getLogger(__name__)

# 缓存显示器信息
_monitors_cache: List[Dict] | None = None


def _get_monitors_from_sidecar() -> List[Dict]:
    """从 sidecar 获取显示器配置"""
    global _monitors_cache
    if _monitors_cache is not None:
        return _monitors_cache

    try:
        from worker.screen.windows_sidecar import get_shared_windows_sidecar_client
        client = get_shared_windows_sidecar_client()
        client.acquire()
        try:
            monitors = client.get_monitors()
            # 转换为与 mss 相同���格式
            result = []
            for m in monitors:
                result.append({
                    "left": m["left"],
                    "top": m["top"],
                    "width": m["width"],
                    "height": m["height"],
                })
            _monitors_cache = result
            return result
        finally:
            client.release()
    except Exception as e:
        logger.warning(f"Failed to get monitors from sidecar: {e}")
        return _get_monitors_from_mss()


def _get_monitors_from_mss() -> List[Dict]:
    """从 mss 获取显示器配置（fallback）"""
    import mss
    with mss.mss() as sct:
        result = []
        for i in range(1, len(sct.monitors)):
            m = sct.monitors[i]
            result.append({
                "left": m.left,
                "top": m.top,
                "width": m.width,
                "height": m.height,
            })
        return result


def get_mss_monitors() -> list:
    """获取所有显示器配置列表。

    优先使用 sidecar，fallback 到 mss。

    Returns:
        list: 显示器配置列表
    """
    global _monitors_cache
    _monitors_cache = None  # 清除缓存强制刷新

    try:
        return _get_monitors_from_sidecar()
    except Exception as e:
        logger.warning(f"Sidecar monitors failed: {e}, fallback to mss")
        return _get_monitors_from_mss()


def get_mapped_monitor_index(monitor: int) -> Tuple[int, Dict]:
    """将用户显示器编号映射到实际显示器索引。

    显示器编号规则（与用户直觉一致）：
    - monitor=1: 主屏幕（left=0 的显示器）
    - monitor=2: 副屏幕（另一个显示器）
    """
    monitors = get_mss_monitors()

    if len(monitors) <= 1:
        if monitors:
            return (1, monitors[0])
        else:
            return (0, {"left": 0, "top": 0, "width": 1920, "height": 1080})

    # 多显示器：找 left=0 的作为主屏幕
    primary_index = None
    secondary_index = None
    for i in range(len(monitors)):
        if monitors[i]['left'] == 0:
            primary_index = i
        else:
            secondary_index = i

    if primary_index is None:
        logger.warning("Could not find primary monitor (left=0), using default order")
        target_index = monitor - 1
    else:
        if monitor == 1:
            target_index = primary_index
        elif monitor == 2:
            target_index = secondary_index if secondary_index is not None else primary_index
        else:
            target_index = min(monitor - 1, len(monitors) - 1)

    logger.debug(f"Monitor mapping: user requested {monitor} -> index {target_index + 1}")
    return target_index + 1, monitors[target_index]


def get_monitor_offset(monitor: int) -> Tuple[int, int]:
    """获取指定显示器相对于虚拟屏幕的偏移量。"""
    _, monitor_config = get_mapped_monitor_index(monitor)
    return monitor_config['left'], monitor_config['top']


def convert_to_global_coords(x: int, y: int, monitor: int) -> Tuple[int, int]:
    """将截图相对坐标转换为 pyautogui 全局坐标。"""
    offset_x, offset_y = get_monitor_offset(monitor)
    global_x = x + offset_x
    global_y = y + offset_y
    logger.debug(f"Coordinate conversion: ({x}, {y}) + offset ({offset_x}, {offset_y}) = ({global_x}, {global_y})")
    return global_x, global_y