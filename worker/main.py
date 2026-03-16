"""
Worker 入口文件。

启动 Worker 服务，包括 HTTP Server 和后台任务。
"""

import logging
import os
import sys

import uvicorn

# 添加项目根目录到 Python 路径
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from worker.config import load_config
from worker.logger import setup_logging
from worker.worker import Worker
from worker.server import app, set_worker


def main():
    """主函数。"""
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

    # 打印启动信息
    logger.info("=" * 50)
    logger.info("Test Worker Starting...")
    logger.info(f"Worker ID: {config.id}")
    logger.info(f"Port: {config.port}")
    logger.info(f"Log file: {log_path}")
    logger.info(f"Platform API: {config.platform_api or 'Not configured'}")
    logger.info(f"OCR Service: {config.ocr_service or 'Not configured'}")
    logger.info("=" * 50)

    # 创建 Worker
    worker = Worker(config)

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