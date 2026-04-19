"""
Worker 入口文件。

启动 Worker 服务，包括 HTTP Server 和后台任务。
"""

import logging
import os
import sys
import warnings

import uvicorn

# 添加项目根目录到 Python 路径
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# 过滤 pymobiledevice3/tidevice3 的垃圾日志（在导入前设置）
os.environ["PYMOBILEDEVICE3_LOG_LEVEL"] = "ERROR"

# 过滤 websockets 弃用警告
warnings.filterwarnings("ignore", category=DeprecationWarning, module="websockets")

from worker.config import load_config
from worker.logger import setup_logging
from worker.server import app, set_worker
from worker.worker import Worker


class SocketErrorFilter(logging.Filter):
    """过滤 socket 相关的垃圾日志。"""
    def filter(self, record):
        msg = record.getMessage()
        # 过滤 pymobiledevice3 socket 错误
        if "Error reading from socket" in msg:
            return False
        if "Connection closed by the peer" in msg:
            return False
        if "Connection closed by the peer" in str(record):
            return False
        return True


class StderrFilter:
    """stderr 过滤器，抑制 pymobiledevice3 的噪音。"""
    def __init__(self, original_stderr):
        self.original = original_stderr
        self.suppress_patterns = [
            "Error reading from socket",
            "Connection closed by the peer",
            "failed to connect to port",
        ]

    def write(self, text):
        # 检查是否需要抑制
        for pattern in self.suppress_patterns:
            if pattern in text:
                return len(text)  # 假装写入了，实际丢弃
        self.original.write(text)

    def flush(self):
        self.original.flush()

    def __getattr__(self, name):
        return getattr(self.original, name)


def suppress_pymobiledevice3_logs():
    """抑制 pymobiledevice3 相关的垃圾日志。"""
    # 设置 pymobiledevice3 相关 logger 为 ERROR 级别
    loggers_to_suppress = [
        "pymobiledevice3",
        "pymobiledevice3.cli",
        "pymobiledevice3.services",
        "pymobiledevice3.services.remote_server",
        "tidevice3",
    ]
    for name in loggers_to_suppress:
        logger = logging.getLogger(name)
        logger.setLevel(logging.ERROR)
        logger.addFilter(SocketErrorFilter())

    # 重定向 stderr 过滤直接 print 的噪音
    sys.stderr = StderrFilter(sys.stderr)


def main():
    """主函数。"""
    # EXE 运行时设置 Playwright 浏览器路径
    if getattr(sys, 'frozen', False):
        app_dir = os.path.dirname(sys.executable)
        playwright_path = os.path.join(app_dir, 'playwright')
        os.environ['PLAYWRIGHT_BROWSERS_PATH'] = playwright_path

    # 加载配置
    config = load_config()

    # 初始化日志
    log_path = setup_logging(
        level=config.log_level,
        log_file=config.log_file,
        max_bytes=config.log_max_size,
        backup_count=config.log_backup_count,
    )
    logger = logging.getLogger(__name__)

    # 抑制 pymobiledevice3 噪音日志
    suppress_pymobiledevice3_logs()

    # 添加 socket 错误过滤器到根 logger
    root_logger = logging.getLogger()
    root_logger.addFilter(SocketErrorFilter())

    # 打印启动信息
    logger.info("=" * 50)
    logger.info("Test Worker Starting...")
    logger.info(f"Worker ID: {config.id}")
    logger.info(f"Port: {config.port}")
    logger.info(f"Log file: {log_path}")
    logger.info(f"Platform API: {config.platform_api or 'Not configured'}")
    logger.info(f"OCR Service: {config.ocr_service or 'Not configured'}")
    logger.info("=" * 50)

    # 创建 Worker（传入日志路径）
    worker = Worker(config, log_path=log_path)

    # 启动 Worker
    try:
        worker.start()
    except Exception as e:
        logger.error(f"Failed to start worker: {e}")
        sys.exit(1)

    # 设置 Worker 实例到 Server
    set_worker(worker)

    # 启动 HTTP Server
    try:
        uvicorn.run(
            app,
            host="0.0.0.0",
            port=config.port,
            log_level=config.log_level.lower(),
        )
    except KeyboardInterrupt:
        logger.info("Shutting down...")
    finally:
        worker.stop()


if __name__ == "__main__":
    main()
