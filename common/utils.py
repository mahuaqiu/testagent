"""
通用工具函数模块。

Usage:
    from common.utils import retry, timestamp, wait_until, run_cmd, popen_cmd
"""

import platform
import subprocess
import time
import functools
import io
import locale
import logging
from typing import Callable, Optional, Union, List, Any

from PIL import Image

logger = logging.getLogger(__name__)


# Windows 上隐藏子进程窗口的标志
if platform.system().lower() == "windows":
    SUBPROCESS_HIDE_WINDOW = subprocess.CREATE_NO_WINDOW
else:
    SUBPROCESS_HIDE_WINDOW = 0


def _decode_output(raw: bytes) -> str:
    """自动检测编码解码子进程输出。

    先尝试 UTF-8，失败后使用系统默认编码（Windows 上通常是 GBK）。
    如果都失败，使用 replace 模式避免乱码导致异常。

    Args:
        raw: 子进程原始字节输出

    Returns:
        str: 解码后的字符串
    """
    if not raw:
        return ""
    # 先尝试 UTF-8
    try:
        return raw.decode("utf-8")
    except (UnicodeDecodeError, ValueError):
        pass
    # 回退到系统默认编码（Windows 上是 GBK/cp936）
    try:
        default_encoding = locale.getpreferredencoding(False)
        return raw.decode(default_encoding)
    except (UnicodeDecodeError, ValueError):
        pass
    # 最终兜底：用 UTF-8 + replace 模式
    return raw.decode("utf-8", errors="replace")


def run_cmd(
    cmd: Union[str, List[str]],
    shell: bool = False,
    capture_output: bool = True,
    text: bool = True,
    timeout: Optional[float] = None,
    check: bool = False,
    **kwargs: Any,
) -> subprocess.CompletedProcess:
    """
    执行命令并等待完成（封装 subprocess.run）。

    在 Windows 上自动添加 CREATE_NO_WINDOW 标志，隐藏子进程的控制台窗口，
    避免打包后的 GUI 程序出现黑色 CMD 弹窗。

    自动检测输出编码：先尝试 UTF-8，失败后使用系统默认编码（如 GBK），
    解决 Windows 命令（如 taskkill）输出中文乱码的问题。

    Args:
        cmd: 命令（字符串或列表）
        shell: 是否使用 shell 执行
        capture_output: 是否捕获输出
        text: 是否以文本模式返回输出（已由本函数自动处理编码，保留参数兼容）
        timeout: 超时时间（秒）
        check: 是否检查返回码（非零时抛异常）
        **kwargs: 其他 subprocess.run 参数

    Returns:
        subprocess.CompletedProcess: 执行结果（stdout/stderr 为解码后的字符串）

    Usage:
        result = run_cmd(["adb", "devices"])
        result = run_cmd("echo hello", shell=True)
    """
    # 在 Windows 上添加隐藏窗口标志
    if platform.system().lower() == "windows":
        kwargs.setdefault("creationflags", SUBPROCESS_HIDE_WINDOW)

    # 以字节模式捕获输出，自行解码以支持自动编码检测
    result = subprocess.run(
        cmd,
        shell=shell,
        stdout=subprocess.PIPE if capture_output else None,
        stderr=subprocess.PIPE if capture_output else None,
        timeout=timeout,
        check=check,
        **kwargs,
    )

    # 自动检测编码解码输出
    if capture_output:
        result.stdout = _decode_output(result.stdout) if result.stdout else ""
        result.stderr = _decode_output(result.stderr) if result.stderr else ""

    return result


def popen_cmd(
    cmd: Union[str, List[str]],
    shell: bool = False,
    stdout: Optional[Any] = None,
    stderr: Optional[Any] = None,
    stdin: Optional[Any] = None,
    **kwargs: Any,
) -> subprocess.Popen:
    """
    启动子进程（封装 subprocess.Popen）。

    在 Windows 上自动添加 CREATE_NO_WINDOW 标志，隐藏子进程的控制台窗口，
    避免打包后的 GUI 程序出现黑色 CMD 弹窗。

    Args:
        cmd: 命令（字符串或列表）
        shell: 是否使用 shell 执行
        stdout: 标准输出处理
        stderr: 标准错误处理
        stdin: 标准输入处理
        **kwargs: 其他 subprocess.Popen 参数（creationflags 会自动合并隐藏窗口标志）

    Returns:
        subprocess.Popen: 进程对象

    Usage:
        process = popen_cmd(["adb", "logcat"], stdout=subprocess.PIPE)
        process = popen_cmd("some_app.exe")  # 启动 GUI 应用
    """
    # 在 Windows 上合并隐藏窗口标志（即使已有其他 creationflags）
    if platform.system().lower() == "windows":
        existing_flags = kwargs.get("creationflags", 0)
        kwargs["creationflags"] = existing_flags | SUBPROCESS_HIDE_WINDOW

    return subprocess.Popen(
        cmd,
        shell=shell,
        stdout=stdout,
        stderr=stderr,
        stdin=stdin,
        **kwargs,
    )


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


def compress_image_to_jpeg(image_bytes: bytes, quality: int = 80) -> bytes:
    """
    将图片压缩为 JPEG 格式。

    用于返回给调用方的截图压缩，减少传输体积。
    OCR/图像匹配场景应使用原始 PNG 数据。

    Args:
        image_bytes: 原始图片字节数据（PNG/JPEG等）
        quality: JPEG 压缩质量（1-100），默认 80
                 - 80: 压缩率高，肉眼几乎无损，适合查看
                 - 90: 高质量，细节更清晰

    Returns:
        bytes: JPEG 格式的压缩图片数据

    Usage:
        compressed = compress_image_to_jpeg(screenshot_bytes, quality=80)
    """
    try:
        img = Image.open(io.BytesIO(image_bytes))
        # 如果是 RGBA 模式，转换为 RGB（JPEG不支持透明通道）
        if img.mode == "RGBA":
            img = img.convert("RGB")
        buffer = io.BytesIO()
        img.save(buffer, format="JPEG", quality=quality)
        compressed_bytes = buffer.getvalue()
        # 记录压缩效果
        original_size = len(image_bytes)
        compressed_size = len(compressed_bytes)
        ratio = (1 - compressed_size / original_size) * 100 if original_size > 0 else 0
        logger.debug(
            f"图片压缩: {original_size} -> {compressed_size} bytes, 减少 {ratio:.1f}%"
        )
        return compressed_bytes
    except Exception as e:
        logger.warning(f"图片压缩失败，返回原始数据: {e}")
        return image_bytes
