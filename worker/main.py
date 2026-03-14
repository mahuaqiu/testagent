"""
Worker 入口文件。

启动 Worker 服务，包括 HTTP Server 和后台任务。
"""

import argparse
import logging
import os
import sys

import uvicorn

# 添加项目根目录到 Python 路径
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from worker.config import load_config, WorkerConfig
from worker.worker import Worker
from worker.server import app, set_worker

# 配置日志
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)


def parse_args():
    """解析命令行参数。"""
    parser = argparse.ArgumentParser(description="Test Worker - 多端自动化测试执行基建")

    # Worker 基础配置
    parser.add_argument("--port", type=int, default=None, help="HTTP 服务端口")
    parser.add_argument("--config", type=str, default=None, help="配置文件路径")
    parser.add_argument("--worker-id", type=str, default=None, help="Worker ID")

    # 外部服务
    parser.add_argument("--platform-api", type=str, default=None, help="配置平台 API 地址")
    parser.add_argument("--ocr-service", type=str, default=None, help="OCR 服务地址")

    # Web 平台
    parser.add_argument("--web", action="store_true", help="启用 Web 平台")
    parser.add_argument("--web-headless", action="store_true", default=None, help="Web 无头模式")
    parser.add_argument("--web-browser", type=str, default=None, help="浏览器类型")

    # Android 平台
    parser.add_argument("--android", action="store_true", help="启用 Android 平台")
    parser.add_argument("--android-server", type=str, default=None, help="Appium Server")

    # iOS 平台
    parser.add_argument("--ios", action="store_true", help="启用 iOS 平台")
    parser.add_argument("--ios-server", type=str, default=None, help="Appium Server")

    # 桌面平台
    parser.add_argument("--windows", action="store_true", help="启用 Windows 平台")
    parser.add_argument("--mac", action="store_true", help="启用 Mac 平台")

    # 其他
    parser.add_argument("--log-level", type=str, default=None, help="日志级别")
    parser.add_argument("--device-check-interval", type=int, default=None, help="设备检测间隔(秒)")
    parser.add_argument("--version", action="store_true", help="显示版本")

    return parser.parse_args()


def main():
    """主函数。"""
    args = parse_args()

    # 显示版本
    if args.version:
        from worker import __version__
        print(f"Test Worker v{__version__}")
        return

    # 加载配置
    config = load_config(args.config)

    # 设置日志级别（配置文件或命令行）
    log_level = args.log_level or config.log_level
    logging.getLogger().setLevel(getattr(logging, log_level.upper()))

    # 命令行参数覆盖配置
    if args.port:
        config.port = args.port
    if args.worker_id:
        config.id = args.worker_id
    if args.platform_api:
        config.platform_api = args.platform_api
    if args.ocr_service:
        config.ocr_service = args.ocr_service
    if args.device_check_interval:
        config.device_check_interval = args.device_check_interval

    # 打印启动信息
    logger.info("=" * 50)
    logger.info("Test Worker Starting...")
    logger.info(f"Worker ID: {config.id}")
    logger.info(f"Port: {config.port}")
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
            log_level=log_level.lower(),
        )
    except KeyboardInterrupt:
        logger.info("Shutting down...")
    finally:
        worker.stop()


if __name__ == "__main__":
    main()