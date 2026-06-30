"""
Request-ID 上下文变量模块。

用于在 asyncio 任务和 asyncio.to_thread 创建的新线程之间传递 request-id，
实现日志追踪。使用 ContextVar 而非 threading.local()，因为 ContextVar
能在 asyncio.to_thread 创建的新线程中自动复制当前上下文。
"""

import contextvars
import uuid
import threading

# 使用 ContextVar：在 asyncio.to_thread 新线程中会自动复制当前值
_request_id_var: contextvars.ContextVar[str | None] = contextvars.ContextVar("request_id", default=None)

# 请求级别的 Token 存储（线程安全）
# Key: 线程 ID, Value: 该线程最近一次 set_request_id 的 Token
_request_id_tokens: dict[int, contextvars.Token[str | None]] = {}
_tokens_lock = threading.Lock()


def generate_request_id() -> str:
    """生成 request-id（UUID 格式）。"""
    return str(uuid.uuid4())


def set_request_id(request_id: str) -> contextvars.Token[str | None]:
    """设置当前上下文的 request-id（自动传播到 asyncio.to_thread 新线程）。

    Returns:
        contextvars.Token: 用于 reset 恢复上下文的 Token
    """
    thread_id = threading.get_ident()
    token = _request_id_var.set(request_id)

    # 线程安全地存储 Token
    with _tokens_lock:
        _request_id_tokens[thread_id] = token

    return token


def get_request_id() -> str | None:
    """获取当前上下文的 request-id。"""
    return _request_id_var.get()


def clear_request_id() -> None:
    """清除当前上下文的 request-id，使用线程对应的 Token 正确恢复之前的状态。"""
    thread_id = threading.get_ident()

    with _tokens_lock:
        token = _request_id_tokens.pop(thread_id, None)

    if token is not None:
        _request_id_var.reset(token)
    else:
        # 如果没有 Token（说明从未设置过或已清除），直接设为 None
        _request_id_var.set(None)