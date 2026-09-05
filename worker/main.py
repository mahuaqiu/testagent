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

from common.packaging import is_packaged, get_app_dir

# 过滤 websockets 弃用警告
warnings.filterwarnings("ignore", category=DeprecationWarning, module="websockets")

from worker.config import load_config
from worker.logger import setup_logging
from worker.server import app, set_uvicorn_server, set_worker
from worker.worker import Worker


class SocketErrorFilter(logging.Filter):
    """过滤 socket 相关的垃圾日志。"""
    def filter(self, record):
        msg = record.getMessage()
        if "Error reading from socket" in msg:
            return False
        if "Connection closed by the peer" in msg:
            return False
        if "Connection closed by the peer" in str(record):
            return False
        return True


def _wait_port_released(port: int, timeout: float = 60.0) -> None:
    """重启场景下等待旧进程释放端口后再绑定。

    配置更新触发的重启会先拉起新进程再优雅退出旧进程；新进程必须
    等旧进程释放端口，否则绑定失败直接退出，重启永远不会生效。
    """
    import socket
    import time

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            probe.bind(("0.0.0.0", port))
            logger.info(f"Port {port} released, starting server")
            return
        except OSError:
            time.sleep(0.5)
        finally:
            try:
                probe.close()
            except OSError:
                pass
    logger.warning(f"Timed out waiting for port {port} to be released, trying to bind anyway")


def main():
    """主函数。"""
    # EXE 运行时设置 Playwright 浏览器路径
    if is_packaged():
        app_dir = get_app_dir()
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

    # 重启场景：等待旧进程释放端口
    if os.environ.get("WORKER_RESTARTED") == "1":
        logger.info("Restarted by config update, waiting for old process to release port")
        _wait_port_released(config.port)

    # 启动 HTTP Server（持有 Server 实例，供配置重启触发优雅停机）
    try:
        server = uvicorn.Server(
            uvicorn.Config(
                app,
                host="0.0.0.0",
                port=config.port,
                log_level=config.log_level.lower(),
            )
        )
        set_uvicorn_server(server)
        server.run()
    except KeyboardInterrupt:
        logger.info("Shutting down...")
    finally:
        worker.stop()


if __name__ == "__main__":
    main()
