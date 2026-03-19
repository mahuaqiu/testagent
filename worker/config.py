"""
Worker 配置管理模块。
"""

import os
import uuid
from dataclasses import dataclass, field
from typing import Dict, Any, Optional, List

import yaml


@dataclass
class WorkerConfig:
    """Worker 配置。"""

    # Worker 基础配置
    id: str = field(default_factory=lambda: str(uuid.uuid4())[:8])
    port: int = 8080
    device_check_interval: int = 60

    # 外部服务地址
    platform_api: str = ""
    ocr_service: str = ""

    # 平台配置
    platforms: Dict[str, Dict[str, Any]] = field(default_factory=dict)

    # 日志配置
    log_level: str = "INFO"
    log_file: Optional[str] = None  # None 表示使用默认路径
    log_max_size: int = 52428800  # 50MB
    log_backup_count: int = 5

    # 图像匹配配置
    image_matching: Dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_yaml(cls, path: str) -> "WorkerConfig":
        """从 YAML 文件加载配置。"""
        with open(path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}

        worker_data = data.get("worker", {})
        external = data.get("external_services", {})
        platforms = data.get("platforms", {})
        logging_cfg = data.get("logging", {})
        image_matching = data.get("image_matching", {})

        return cls(
            id=worker_data.get("id") or str(uuid.uuid4())[:8],
            port=worker_data.get("port", 8080),
            device_check_interval=worker_data.get("device_check_interval", 60),
            platform_api=external.get("platform_api", ""),
            ocr_service=external.get("ocr_service", ""),
            platforms=platforms,
            log_level=logging_cfg.get("level", "INFO"),
            log_file=logging_cfg.get("file"),
            log_max_size=logging_cfg.get("max_size", 52428800),
            log_backup_count=logging_cfg.get("backup_count", 5),
            image_matching=image_matching,
        )

    def get_platform_config(self, platform: str) -> Dict[str, Any]:
        """获取指定平台的配置。"""
        return self.platforms.get(platform, {})


@dataclass
class PlatformConfig:
    """平台通用配置。"""

    enabled: bool = True
    session_timeout: int = 300
    screenshot_dir: str = "data/screenshots"

    # Web 专用
    headless: bool = True
    browser_type: str = "chromium"
    timeout: int = 30000
    ignore_https_errors: bool = True
    permissions: List[str] = field(default_factory=lambda: ["camera", "microphone"])

    # 移动端专用
    appium_server: str = ""

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "PlatformConfig":
        """从字典创建配置。"""
        return cls(
            enabled=data.get("enabled", True),
            session_timeout=data.get("session_timeout", 300),
            screenshot_dir=data.get("screenshot_dir", "data/screenshots"),
            headless=data.get("headless", True),
            browser_type=data.get("browser_type", "chromium"),
            timeout=data.get("timeout", 30000),
            ignore_https_errors=data.get("ignore_https_errors", True),
            permissions=data.get("permissions", ["camera", "microphone"]),
            appium_server=data.get("appium_server", ""),
        )


def get_default_config_path() -> str:
    """获取默认配置文件路径。"""
    return os.path.join(
        os.path.dirname(os.path.dirname(__file__)),
        "config",
        "worker.yaml"
    )


def load_config() -> WorkerConfig:
    """
    加载 Worker 配置。

    从 config/worker.yaml 加载配置，若文件不存在则使用默认配置。

    Returns:
        WorkerConfig: 配置对象
    """
    config_path = get_default_config_path()

    if os.path.exists(config_path):
        return WorkerConfig.from_yaml(config_path)
    else:
        return WorkerConfig()