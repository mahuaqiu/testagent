"""
日志配置模块。

提供日志持久化和轮转功能。
"""

import logging
import os
import sys
from logging.handlers import RotatingFileHandler
from typing import Optional

from common.request_context import get_request_id


class RequestIdFormatter(logging.Formatter):
    """自定义 Formatter，自动注入 request-id。"""

    def format(self, record):
        # 在格式化前设置 request_id
        record.request_id = get_request_id() or '-'
        return super().format(record)


def get_default_log_path() -> str:
    """
    获取默认日志文件路径。

    - 打包环境 (PyInstaller)：exe 所在目录下的 worker.log
    - 普通环境：当前工作目录下的 worker.log

    Returns:
        str: 默认日志文件路径
    """
    if getattr(sys, 'frozen', False):
        # PyInstaller 打包环境
        base_dir = os.path.dirname(sys.executable)
    else:
        # 普通环境
        base_dir = os.getcwd()

    return os.path.join(base_dir, "worker.log")


def setup_logging(
    level: str = "INFO",
    log_file: Optional[str] = None,
    max_bytes: int = 52428800,  # 50MB
    backup_count: int = 5,
) -> str:
    """
    初始化日志配置。

    Args:
        level: 日志级别 (DEBUG, INFO, WARNING, ERROR)
        log_file: 日志文件路径，为 None 时使用默认路径
        max_bytes: 单个日志文件最大字节数
        backup_count: 保留的备份文件数量

    Returns:
        str: 实际使用的日志文件路径
    """
    # 确定日志文件路径
    if log_file:
        # 使用配置的路径
        log_path = log_file
    else:
        # 使用默认路径
        log_path = get_default_log_path()

    # 确保日志目录存在
    log_dir = os.path.dirname(log_path)
    if log_dir and not os.path.exists(log_dir):
        os.makedirs(log_dir, exist_ok=True)

    # 获取根日志器
    root_logger = logging.getLogger()

    # 清除现有处理器（避免重复添加）
    root_logger.handlers.clear()

    # 设置日志级别
    log_level = getattr(logging, level.upper(), logging.INFO)
    root_logger.setLevel(log_level)

    # 日志格式（包含 request_id）
    log_format = "%(asctime)s [%(request_id)s] %(levelname)s %(name)s: %(message)s"
    formatter = RequestIdFormatter(log_format)

    # 添加文件处理器（轮转）
    file_handler = RotatingFileHandler(
        log_path,
        maxBytes=max_bytes,
        backupCount=backup_count,
        encoding="utf-8",
    )
    file_handler.setLevel(log_level)
    file_handler.setFormatter(formatter)
    root_logger.addHandler(file_handler)

    # 添加控制台处理器
    console_handler = logging.StreamHandler()
    console_handler.setLevel(log_level)
    console_handler.setFormatter(formatter)
    root_logger.addHandler(console_handler)

    return log_path