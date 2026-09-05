"""显示器信息工具模块。

提供多显示器场景下的配置获取和坐标转换功能。

显示器编号规则（与用户直觉一致）：
- monitor=1: 主屏幕（left=0 的显示器）
- monitor=2: 副屏幕（另一个显示器）

使用 Rust sidecar 获取显示器配置。
"""

import ctypes
import logging
import sys
import threading
from typing import Dict, List, Tuple

logger = logging.getLogger(__name__)

# 缓存显示器信息
_monitors_cache: List[Dict] | None = None
_monitors_cache_lock = threading.Lock()

# 进程 DPI 感知设置状态
_dpi_awareness_done = False
_dpi_awareness_lock = threading.Lock()


def invalidate_monitors_cache() -> None:
    """清除显示器几何缓存。

    set_resolution 等操作会改变显示器布局，之后必须刷新缓存，
    否则坐标换算继续使用旧几何导致点击错位。
    """
    global _monitors_cache
    with _monitors_cache_lock:
        _monitors_cache = None
    logger.info("Monitors cache invalidated")


def _ensure_dpi_awareness() -> None:
    """让进程感知 DPI 缩放（仅需设置一次）。

    非 100% 缩放下，未声明 DPI 感知的进程拿到的窗口矩形和截图像素
    都是系统虚拟化后的逻辑值，与 sidecar 的物理像素不一致。当前测试机
    都是 100% 缩放，此调用不改变行为；一旦出现非 100% 缩放也能保证
    全链路使用同一物理像素基准。
    """
    global _dpi_awareness_done
    if _dpi_awareness_done or not sys.platform.startswith("win"):
        return
    with _dpi_awareness_lock:
        if _dpi_awareness_done:
            return
        try:
            try:
                # Per-Monitor v2（Win10 1703+）
                ctypes.windll.shcore.SetProcessDpiAwareness(2)
            except (AttributeError, OSError):
                # 旧系统回退到系统级 DPI 感知
                ctypes.windll.user32.SetProcessDPIAware()
            logger.debug("Process DPI awareness enabled")
        except Exception as e:
            logger.debug(f"SetProcessDpiAwareness failed: {e}")
        finally:
            _dpi_awareness_done = True


def get_monitors() -> List[Dict]:
    """获取所有显示器配置列表。

    使用 Rust sidecar 获取显示器配置。

    Returns:
        list: 显示器配置列表
    """
    global _monitors_cache
    _ensure_dpi_awareness()
    with _monitors_cache_lock:
        cached = _monitors_cache
    if cached is not None:
        return cached

    try:
        from worker.screen.windows_sidecar import get_shared_windows_sidecar_client

        client = get_shared_windows_sidecar_client()
        client.acquire()
        try:
            monitors = client.get_monitors()
            result = []
            for m in monitors:
                result.append({
                    "left": m["left"],
                    "top": m["top"],
                    "width": m["width"],
                    "height": m["height"],
                })
            with _monitors_cache_lock:
                _monitors_cache = result
            logger.info(f"Got {len(result)} monitors from sidecar")
            return result
        finally:
            client.release()
    except Exception as e:
        logger.warning(f"Failed to get monitors from sidecar: {e}")
        # 返回默认显示器配置
        default_monitors = [{"left": 0, "top": 0, "width": 1920, "height": 1080}]
        with _monitors_cache_lock:
            _monitors_cache = default_monitors
        return default_monitors


def get_mapped_monitor_index(monitor: int) -> Tuple[int, Dict]:
    """将用户显示器编号映射到实际显示器索引。

    显示器编号规则（与用户直觉一致）：
    - monitor=1: 主屏幕（left=0 的显示器）
    - monitor=2: 副屏幕（另一个显示器）
    """
    monitors = get_monitors()

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