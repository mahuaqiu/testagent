"""
Worker 配置管理模块。
"""

import os
from dataclasses import dataclass, field
from typing import Dict, Any, Optional, List

import yaml

from worker.discovery.host import HostDiscoverer


def _generate_worker_id() -> str:
    """
    生成 Worker ID。

    使用本机 MAC 地址作为唯一标识，去掉冒号分隔符。

    Returns:
        str: Worker ID，格式为 MAC 地址去掉冒号（如 AABBCCDDEEFF）
    """
    mac = HostDiscoverer.get_mac_address()
    if mac:
        # 去掉冒号分隔符，得到纯 MAC 地址字符串
        return mac.replace(":", "").replace("-", "")
    else:
        # 无法获取 MAC 时，使用 hostname 的哈希作为后备
        import socket
        hostname = socket.gethostname()
        return hostname[:8].lower()


@dataclass
class WorkerConfig:
    """Worker 配置。"""

    # Worker 基础配置
    id: str = field(default_factory=_generate_worker_id)
    ip: Optional[str] = None  # 指定 IP 地址，None 表示自动获取
    port: int = 8080
    namespace: str = "public"               # 命名空间，用于分类 Worker
    device_check_interval: int = 300        # 设备检测间隔(秒)，改为5分钟
    service_retry_count: int = 3            # 服务启动重试次数
    service_retry_interval: int = 10        # 重试间隔(秒)
    action_step_delay: float = 0.5  # 动作间隔延迟(秒)

    # 外部服务地址
    platform_api: str = "http://192.168.0.102:8000"
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
            id=worker_data.get("id") or _generate_worker_id(),
            ip=worker_data.get("ip"),
            port=worker_data.get("port", 8080),
            namespace=worker_data.get("namespace", "public"),
            device_check_interval=worker_data.get("device_check_interval", 300),
            service_retry_count=worker_data.get("service_retry_count", 3),
            service_retry_interval=worker_data.get("service_retry_interval", 10),
            action_step_delay=worker_data.get("action_step_delay", 0.5),
            platform_api=external.get("platform_api", "http://192.168.0.102:8000"),
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
    user_data_dir: str = "data/chrome_profile"  # 浏览器用户数据目录

    # 启动前清理 Default 目录数据（保留 Cache 目录），避免账号缓存
    clear_profile_on_start: bool = True

    # 请求黑名单：拦截特定请求（如某些 JS 文件加载超时）
    # 格式：[{"pattern": "uba.js", "action": "abort"}, {"pattern": "tinyReporter.min.js", "action": "abort"}]
    # action 可选：abort（中止）、404（返回404）、empty（返回空响应）
    request_blacklist: List[Dict[str, str]] = field(default_factory=list)

    # Web 专用 - Token 捕获
    token_headers: List[str] = field(default_factory=list)  # 要监听的 token header 名称列表

    # iOS 专用
    wda_base_port: int = 8100
    wda_ipa_path: str = "wda/WebDriverAgent.ipa"

    # Android 专用
    u2_port: int = 7912

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
            user_data_dir=data.get("user_data_dir", "data/chrome_profile"),
            clear_profile_on_start=data.get("clear_profile_on_start", True),
            request_blacklist=data.get("request_blacklist", []),
            token_headers=data.get("token_headers", []),
            wda_base_port=data.get("wda_base_port", 8100),
            wda_ipa_path=data.get("wda_ipa_path", "wda/WebDriverAgent.ipa"),
            u2_port=data.get("u2_port", 7912),
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