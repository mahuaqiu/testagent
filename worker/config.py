"""
Worker 配置管理模块。
"""

import os
import uuid
from dataclasses import dataclass, field
from typing import Optional, List, Dict, Any

import yaml


@dataclass
class WorkerConfig:
    """Worker 配置。"""

    # Worker 基础配置
    id: str = field(default_factory=lambda: str(uuid.uuid4())[:8])
    port: int = 8080
    device_check_interval: int = 60

    # 外部服务地址（必须从配置文件读取）
    platform_api: str = ""
    ocr_service: str = ""

    # 平台配置
    platforms: Dict[str, Dict[str, Any]] = field(default_factory=dict)

    # 日志配置
    log_level: str = "INFO"
    log_file: str = "logs/worker.log"

    @classmethod
    def from_yaml(cls, path: str) -> "WorkerConfig":
        """从 YAML 文件加载配置。"""
        with open(path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}

        worker_data = data.get("worker", {})
        external = data.get("external_services", {})
        platforms = data.get("platforms", {})
        logging_cfg = data.get("logging", {})

        return cls(
            id=worker_data.get("id") or str(uuid.uuid4())[:8],
            port=worker_data.get("port", 8080),
            device_check_interval=worker_data.get("device_check_interval", 60),
            platform_api=external.get("platform_api", ""),
            ocr_service=external.get("ocr_service", ""),
            platforms=platforms,
            log_level=logging_cfg.get("level", "INFO"),
            log_file=logging_cfg.get("file", "logs/worker.log"),
        )

    def get_platform_config(self, platform: str) -> Dict[str, Any]:
        """获取指定平台的配置。"""
        return self.platforms.get(platform, {})


@dataclass
class PlatformConfig:
    """平台通用配置。"""

    enabled: Optional[bool] = None
    session_timeout: int = 300
    screenshot_dir: str = "data/screenshots"

    # Web 专用
    headless: bool = True
    browser_type: str = "chromium"
    timeout: int = 30000

    # 移动端专用
    appium_server: str = ""

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "PlatformConfig":
        """从字典创建配置。"""
        return cls(
            enabled=data.get("enabled"),
            session_timeout=data.get("session_timeout", 300),
            screenshot_dir=data.get("screenshot_dir", "data/screenshots"),
            headless=data.get("headless", True),
            browser_type=data.get("browser_type", "chromium"),
            timeout=data.get("timeout", 30000),
            appium_server=data.get("appium_server", ""),
        )


def get_default_config_path() -> str:
    """获取默认配置文件路径。"""
    return os.path.join(
        os.path.dirname(os.path.dirname(__file__)),
        "config",
        "worker.yaml"
    )


def load_config(config_path: Optional[str] = None) -> WorkerConfig:
    """
    加载 Worker 配置。

    优先级：命令行参数 > 环境变量 > 配置文件 > 默认值

    Args:
        config_path: 配置文件路径，默认使用 config/worker.yaml

    Returns:
        WorkerConfig: 配置对象
    """
    if config_path is None:
        config_path = get_default_config_path()

    if os.path.exists(config_path):
        config = WorkerConfig.from_yaml(config_path)
    else:
        config = WorkerConfig()

    # 环境变量覆盖
    if os.environ.get("WORKER_ID"):
        config.id = os.environ["WORKER_ID"]
    if os.environ.get("WORKER_PORT"):
        config.port = int(os.environ["WORKER_PORT"])
    if os.environ.get("PLATFORM_API"):
        config.platform_api = os.environ["PLATFORM_API"]
    if os.environ.get("OCR_SERVICE"):
        config.ocr_service = os.environ["OCR_SERVICE"]

    return config