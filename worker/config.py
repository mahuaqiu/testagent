"""
Worker 配置管理模块。
"""

import logging
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from typing import Dict, Any, Optional, List

import yaml

from worker.discovery.host import HostDiscoverer

logger = logging.getLogger(__name__)


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

    # 升级配置
    upgrade_check_url: str = ""       # 升级检查 URL（对应 YAML 的 upgrade.check_url）
    upgrade_check_timeout: int = 30   # 升级检查超时（秒）
    upgrade_download_timeout: int = 300  # 升级下载超时（秒）

    # 配置版本号
    config_version: Optional[str] = None

    @classmethod
    def from_yaml(cls, path: str) -> "WorkerConfig":
        """从 YAML 文件加载配置。

        尝试多种编码读取，兼容不同来源的配置文件：
        - UTF-8：标准编码
        - GBK/GB18030：Windows 中文系统默认编码（如 Inno Setup 生成的配置）
        """
        # 尝试多种编码
        encodings = ["utf-8", "gbk", "gb18030"]
        data = None

        for encoding in encodings:
            try:
                with open(path, "r", encoding=encoding) as f:
                    data = yaml.safe_load(f) or {}
                break
            except UnicodeDecodeError:
                continue

        # 所有编码都失败时使用默认配置
        if data is None:
            data = {}

        worker_data = data.get("worker", {})
        external = data.get("external_services", {})
        platforms = data.get("platforms", {})
        logging_cfg = data.get("logging", {})
        image_matching = data.get("image_matching", {})
        upgrade_cfg = data.get("upgrade", {})

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
            upgrade_check_url=upgrade_cfg.get("check_url", ""),
            upgrade_check_timeout=upgrade_cfg.get("check_timeout", 30),
            upgrade_download_timeout=upgrade_cfg.get("download_timeout", 300),
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


def _get_base_dir() -> str:
    """获取基础目录路径。

    返回打包环境下的 exe 所在目录，或开发环境下的项目根目录。

    Returns:
        str: 基础目录路径
    """
    if getattr(sys, 'frozen', False):
        # PyInstaller 打包模式
        return os.path.dirname(sys.executable)
    else:
        # 开发模式
        return os.path.dirname(os.path.dirname(__file__))


def get_user_config_path() -> str:
    """获取用户配置文件路径（安装目录根目录的 config/worker.yaml）。

    此路径用于：
    - 安装时写入用户配置
    - 设置界面保存配置
    - Worker 启动时读取配置
    """
    return os.path.join(_get_base_dir(), "config", "worker.yaml")


def get_default_template_path() -> str:
    """获取默认配置模板路径（_internal/config/worker.yaml）。

    此路径用于：
    - 作为用户配置的备份来源
    - 用户配置不存在时自动复制
    """
    if getattr(sys, 'frozen', False):
        # 打包模式：模板在 _internal 目录下
        return os.path.join(_get_base_dir(), "_internal", "config", "worker.yaml")
    else:
        # 开发模式：用户配置和模板路径相同
        return os.path.join(_get_base_dir(), "config", "worker.yaml")


def _copy_default_to_user_config(src: str, dst: str) -> None:
    """复制默认配置模板到用户配置路径。

    Args:
        src: 默认配置模板路径
        dst: 用户配置文件路径

    Raises:
        OSError: 文件复制失败时抛出
    """
    try:
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        shutil.copy2(src, dst)
        logger.info(f"Default config copied to user config: {dst}")
    except OSError as e:
        logger.error(f"Failed to copy default config from {src} to {dst}: {e}")
        raise


def get_default_config_path() -> str:
    """获取配置文件路径（向后兼容别名）。

    注意：此函数现在返回用户配置路径，而非默认模板路径。
    若需要获取默认模板路径，请使用 get_default_template_path()。
    """
    return get_user_config_path()


def load_config() -> WorkerConfig:
    """加载 Worker 配置（含版本号）。"""
    user_config_path = get_user_config_path()
    default_template_path = get_default_template_path()

    # 优先读取用户配置
    if os.path.exists(user_config_path):
        logger.info(f"Loading user config: {user_config_path}")
        config = WorkerConfig.from_yaml(user_config_path)
    elif os.path.exists(default_template_path):
        logger.info(f"User config not found, copying default template to: {user_config_path}")
        _copy_default_to_user_config(default_template_path, user_config_path)
        config = WorkerConfig.from_yaml(user_config_path)
    else:
        logger.warning("No config file found, using default WorkerConfig")
        config = WorkerConfig()

    # 从单独文件读取版本号
    config.config_version = load_config_version()

    return config


def get_config_version_path() -> str:
    """获取配置版本文件路径。"""
    return os.path.join(_get_base_dir(), "config", ".config_version")


def load_config_version() -> Optional[str]:
    """
    从单独文件读取配置版本号。

    Returns:
        Optional[str]: 版本号字符串，文件不存在时返回 None
    """
    version_path = get_config_version_path()
    if os.path.exists(version_path):
        with open(version_path, encoding="utf-8") as f:
            return f.read().strip()
    return None


def save_config_version(version: str) -> None:
    """
    保存配置版本号到单独文件（原子写入）。

    Args:
        version: 版本号字符串，格式 YYYYMMDD-HHMMSS
    """
    version_path = get_config_version_path()
    os.makedirs(os.path.dirname(version_path), exist_ok=True)

    # 原子写入：先写临时文件，再重命名
    temp_path = version_path + ".tmp"
    with open(temp_path, "w", encoding="utf-8") as f:
        f.write(version)

    # 重命名（原子操作）
    os.replace(temp_path, version_path)


def merge_config_with_ip_protection(
    new_config_yaml: str,
    existing_config_path: str = get_user_config_path()
) -> dict:
    """
    合并配置：保留本地 IP 地址。

    Args:
        new_config_yaml: 新配置的 YAML 字符串
        existing_config_path: 现有配置文件路径

    Returns:
        dict: 合并后的配置数据
    """
    # 解析新配置
    new_data = yaml.safe_load(new_config_yaml) or {}

    # 读取现有配置的 IP
    if os.path.exists(existing_config_path):
        with open(existing_config_path, encoding="utf-8") as f:
            existing_data = yaml.safe_load(f) or {}
        existing_ip = existing_data.get("worker", {}).get("ip")
    else:
        existing_ip = None

    # 合并：保留本地 IP
    if existing_ip is not None and "worker" in new_data:
        new_data["worker"]["ip"] = existing_ip

    return new_data


def save_config_with_version(
    config_data: dict,
    version: str,
    config_path: str = get_user_config_path(),
    version_path: str = get_config_version_path()
) -> None:
    """
    安全保存配置和版本（带备份和回滚）。

    Args:
        config_data: 合并后的配置数据
        version: 新版本号
        config_path: 配置文件路径
        version_path: 版本文件路径

    Raises:
        OSError: 文件写入失败时抛出，自动回滚
    """
    config_yaml = yaml.dump(config_data, default_flow_style=False, allow_unicode=True)

    # 确保 config 目录存在
    os.makedirs(os.path.dirname(config_path), exist_ok=True)
    os.makedirs(os.path.dirname(version_path), exist_ok=True)

    # 1. 备份现有配置
    backup_path = config_path + ".bak"
    if os.path.exists(config_path):
        shutil.copy2(config_path, backup_path)

    # 2. 写入新配置
    try:
        with open(config_path, "w", encoding="utf-8") as f:
            f.write(config_yaml)

        # 3. 写入版本文件
        with open(version_path, "w", encoding="utf-8") as f:
            f.write(version)

        # 4. 删除备份（成功后清理）
        if os.path.exists(backup_path):
            os.remove(backup_path)

        logger.info(f"Config saved: version={version}")

    except Exception as e:
        # 5. 回滚：恢复备份
        if os.path.exists(backup_path):
            shutil.copy2(backup_path, config_path)
            os.remove(backup_path)
        logger.error(f"Config save failed, rolled back: {e}")
        raise


def cli_restart():
    """CLI 模式重启：启动新进程并退出当前进程。"""
    executable = sys.executable
    args = sys.argv

    logger.info(f"CLI mode: restarting with args={args}")

    try:
        # 启动新进程（分离运行）
        subprocess.Popen([executable] + args)
        # 退出当前进程
        sys.exit(0)
    except Exception as e:
        logger.error(f"CLI restart failed: {e}")
        sys.exit(1)
