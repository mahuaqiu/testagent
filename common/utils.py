"""
通用工具函数模块。

Usage:
    from common.utils import retry, timestamp, wait_until
"""

import time
import functools
from typing import Callable


def retry(max_attempts: int = 3, delay: float = 1.0):
    """重试装饰器，用于不稳定操作（如网络请求、元素定位）。

    Args:
        max_attempts: 最大重试次数（含首次执行）。
        delay: 每次重试间隔秒数。

    Usage:
        @retry(max_attempts=3, delay=2)
        def flaky_operation():
            ...
    """
    def decorator(func):
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            last_exc = None
            for attempt in range(1, max_attempts + 1):
                try:
                    return func(*args, **kwargs)
                except Exception as e:
                    last_exc = e
                    print(f"  [retry] {func.__name__} 第 {attempt}/{max_attempts} 次失败: {e}")
                    if attempt < max_attempts:
                        time.sleep(delay)
            raise last_exc
        return wrapper
    return decorator


def timestamp() -> str:
    """返回当前时间戳字符串，格式: 20240101_120000。

    常用于生成唯一文件名或数据标识。

    Returns:
        str: 时间戳字符串。
    """
    return time.strftime("%Y%m%d_%H%M%S")


def wait_until(condition: Callable[[], bool], timeout: float = 10, interval: float = 0.5) -> bool:
    """轮询等待条件满足。

    Args:
        condition: 返回 bool 的可调用对象。
        timeout: 超时秒数。
        interval: 轮询间隔秒数。

    Returns:
        bool: 条件在超时内是否满足。

    Usage:
        wait_until(lambda: element.is_visible(), timeout=5)
    """
    end_time = time.time() + timeout
    while time.time() < end_time:
        if condition():
            return True
        time.sleep(interval)
    return False
