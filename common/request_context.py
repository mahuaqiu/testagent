"""
Request-ID 线程局部存储模块。

用于在多线程环境下传递 request-id，实现日志追踪。
"""

import threading
import uuid

_request_context = threading.local()


def generate_request_id() -> str:
    """生成 request-id（UUID 格式）。"""
    return str(uuid.uuid4())


def set_request_id(request_id: str) -> None:
    """设置当前线程的 request-id。"""
    _request_context.request_id = request_id


def get_request_id() -> str | None:
    """获取当前线程的 request-id。"""
    return getattr(_request_context, 'request_id', None)


def clear_request_id() -> None:
    """清除当前线程的 request-id。"""
    if hasattr(_request_context, 'request_id'):
        del _request_context.request_id