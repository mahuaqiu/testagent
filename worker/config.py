"""
Worker 配置管理模块。
"""

import logging
import os
import re
import shutil
import sys
from dataclasses import dataclass, field
from typing import Any

import yaml

from common.packaging import get_base_dir, is_packaged
from common.utils import popen_cmd
from worker.constants import (
    DEFAULT_ARTIFACT_RETENTION_DAYS,
    DEFAULT_CLEANUP_INTERVAL_HOURS,
    DEFAULT_FILES_DOWNLOAD_RATE_LIMIT_MB,
    DEFAULT_FILES_MAX_CONCURRENT_DOWNLOADS,
    DEFAULT_FILES_MAX_CONCURRENT_UPLOADS,
    DEFAULT_FILES_MAX_UPLOAD_SIZE_MB,
    DEFAULT_FILES_ROOT,
    DEFAULT_FILES_UPLOAD_RATE_LIMIT_MB,
    DEFAULT_HARMONY_STREAMING_FPS,
    DEFAULT_HARMONY_STREAMING_JPEG_QUALITY,
    DEFAULT_HARMONY_STREAMING_MAX_LONG_EDGE,
    DEFAULT_LOG_BACKUP_COUNT,
    DEFAULT_LOG_FILE,
    DEFAULT_LOG_LEVEL,
    DEFAULT_LOG_MAX_SIZE,
    DEFAULT_MAX_CONNECTIONS_PER_DEVICE,
    DEFAULT_REALTIME_INPUT_ENABLED,
    DEFAULT_RECORDING_FPS,
    DEFAULT_RECORDING_MAX_TIMEOUT_MS,
    DEFAULT_RECORDING_OUTPUT_DIR,
    DEFAULT_TASK_RETENTION_HOURS,
    DEFAULT_UNLOCK_PASSWORD,
    DEFAULT_UPGRADE_CHECK_TIMEOUT,
    DEFAULT_UPGRADE_DOWNLOAD_TIMEOUT,
    DEFAULT_WEBSOCKET_IDLE_TIMEOUT_SECONDS,
    DEFAULT_WEBSOCKET_SEND_TIMEOUT_SECONDS,
    DEFAULT_WEBSOCKET_STREAMING_BITRATE,
    DEFAULT_WEBSOCKET_STREAMING_CODEC,
    DEFAULT_WEBSOCKET_STREAMING_FPS,
    DEFAULT_WEBSOCKET_STREAMING_PROFILE,
    LOG_LEVEL_OPTIONS,
)

logger = logging.getLogger(__name__)


def _generate_worker_id() -> str:
    """
    生成 Worker ID。

    使用本机 MAC 地址作为唯一标识，去掉冒号分隔符。

    Returns:
        str: Worker ID，格式为 MAC 地址去掉冒号（如 AABBCCDDEEFF）
    """
    # 延迟导入避免循环依赖
    from worker.discovery.host import HostDiscoverer

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
    ip: str | None = None  # 指定 IP 地址，None 表示自动获取
    port: int = 8080
    namespace: str = "public"               # 命名空间，用于分类 Worker
    namespace_overrides: dict[str, str] = field(default_factory=dict)
    device_check_interval: int = 300
    service_retry_count: int = 3
    service_retry_interval: int = 10
    action_step_delay: float = 0.5
    discover_android_devices: bool = False  # 是否发现 Android 设备
    discover_ios_devices: bool = False      # 是否发现 iOS 设备
    discover_harmony_mobile_devices: bool = False  # 是否发现鸿蒙移动设备
    discover_harmony_pc_devices: bool = False  # 是否发现鸿蒙 PC 设备

    # 外部服务地址
    platform_api: str = "http://192.168.0.102:8000"
    ocr_service: str = ""

    # 平台配置
    platforms: dict[str, dict[str, Any]] = field(default_factory=dict)

    # 鸿蒙官方 HOScrcpy Java SDK 配置
    harmony_official: dict[str, Any] = field(default_factory=dict)

    # 日志配置
    log_level: str = DEFAULT_LOG_LEVEL
    log_file: str | None = DEFAULT_LOG_FILE
    log_max_size: int = DEFAULT_LOG_MAX_SIZE
    log_backup_count: int = DEFAULT_LOG_BACKUP_COUNT

    # 升级配置
    upgrade_check_url: str = ""       # 升级检查 URL（对应 YAML 的 upgrade.check_url）
    upgrade_check_timeout: int = DEFAULT_UPGRADE_CHECK_TIMEOUT
    upgrade_download_timeout: int = DEFAULT_UPGRADE_DOWNLOAD_TIMEOUT

    # 解锁屏幕配置
    unlock: dict[str, Any] = field(default_factory=dict)

    # 录屏配置
    recording_output_dir: str = DEFAULT_RECORDING_OUTPUT_DIR
    recording_default_fps: int = DEFAULT_RECORDING_FPS
    recording_max_timeout_ms: int = DEFAULT_RECORDING_MAX_TIMEOUT_MS

    # 本地任务和附件清理配置
    task_retention_hours: int = DEFAULT_TASK_RETENTION_HOURS
    artifact_retention_days: int = DEFAULT_ARTIFACT_RETENTION_DAYS
    cleanup_interval_hours: int = DEFAULT_CLEANUP_INTERVAL_HOURS

    # WebSocket 推流配置
    websocket_max_connections_per_device: int = DEFAULT_MAX_CONNECTIONS_PER_DEVICE
    websocket_send_timeout_seconds: int = DEFAULT_WEBSOCKET_SEND_TIMEOUT_SECONDS
    websocket_idle_timeout_seconds: int = DEFAULT_WEBSOCKET_IDLE_TIMEOUT_SECONDS
    websocket_streaming_fps: int = DEFAULT_WEBSOCKET_STREAMING_FPS
    websocket_streaming_codec: str = DEFAULT_WEBSOCKET_STREAMING_CODEC
    websocket_streaming_bitrate: int = DEFAULT_WEBSOCKET_STREAMING_BITRATE
    websocket_streaming_profile: int = DEFAULT_WEBSOCKET_STREAMING_PROFILE

    # 实时指针输入（浏览器 WS 上行 down/move/up/wheel，替代 mouseup 才发一条
    # REST 手势）。官方会话未就绪/不可用时 worker 自动退化为整手势合帧。
    realtime_input_enabled: bool = DEFAULT_REALTIME_INPUT_ENABLED

    # 鸿蒙 JPEG 推流独立参数（仅作用于实时推流，不影响 Windows/iOS/Android/Mac，
    # 也不影响截图/录屏——截图走设备端 snapshot_display，画质保持高清）
    harmony_streaming_fps: int = DEFAULT_HARMONY_STREAMING_FPS
    harmony_streaming_jpeg_quality: int = DEFAULT_HARMONY_STREAMING_JPEG_QUALITY
    # 鸿蒙推流长边上限，超过则等比缩小；<=0 表示不缩放。
    # 【默认 1600 降采样】WS 流开头已通过 meta 文本帧把真机原生分辨率
    # 下发给前端做坐标基准（与推流图像尺寸解耦，参考 Windows 的 H.264
    # SPS 带内自描述），故缩小推流不再影响 uitest 坐标。仅当 worker 成功
    # 拿到真机分辨率并下发 meta 后才实际启用缩放，否则自动回退为不缩放。
    harmony_streaming_max_long_edge: int = DEFAULT_HARMONY_STREAMING_MAX_LONG_EDGE

    # 文件接口固定参数，由代码统一维护。
    files_root: str | None = DEFAULT_FILES_ROOT
    files_download_rate_limit_mb: float = DEFAULT_FILES_DOWNLOAD_RATE_LIMIT_MB
    files_upload_rate_limit_mb: float = DEFAULT_FILES_UPLOAD_RATE_LIMIT_MB
    files_max_concurrent_downloads: int = DEFAULT_FILES_MAX_CONCURRENT_DOWNLOADS
    files_max_concurrent_uploads: int = DEFAULT_FILES_MAX_CONCURRENT_UPLOADS
    files_max_upload_size_mb: int = DEFAULT_FILES_MAX_UPLOAD_SIZE_MB

    # 配置版本号
    config_version: str | None = None

    @property
    def effective_upgrade_check_url(self) -> str:
        """升级检查 URL：显式配置优先，否则基于 platform_api 自动推导平台升级接口。

        平台接口：GET {platform_api}/api/core/env/upgrade/worker/info?device_type=<windows|mac>
        """
        if self.upgrade_check_url:
            return self.upgrade_check_url
        if self.platform_api:
            device_type = "mac" if sys.platform == "darwin" else "windows"
            base = self.platform_api.rstrip("/")
            return f"{base}/api/core/env/upgrade/worker/info?device_type={device_type}"
        return ""

    def __post_init__(self) -> None:
        self.namespace_overrides = {
            str(key).strip(): str(value).strip()
            for key, value in (self.namespace_overrides or {}).items()
            if str(key).strip() and str(value).strip()
        }

    def get_namespace(self, platform: str, device_id: str | None = None) -> str:
        """获取平台或设备实际注册的命名空间。"""
        if device_id:
            device_key = f"{platform}/{device_id}"
            namespace = self.namespace_overrides.get(device_key)
            if namespace:
                return namespace

        return self.namespace_overrides.get(platform) or self.namespace

    @classmethod
    def from_yaml(cls, path: str) -> "WorkerConfig":
        """从 YAML 文件加载配置。

        尝试多种编码读取，兼容不同来源的配置文件：
        - UTF-8：标准编码
        - GBK/GB18030：Windows 中文系统默认编码（如 Inno Setup 生成的配置）
        """
        # 尝试多种编码
        # Windows PowerShell 5.1 的旧安装器会将 Set-Content 默认写成 UTF-16。
        # 保留该编码兼容，避免旧安装包生成的配置被当成空配置读取。
        encodings = ["utf-8-sig", "utf-16", "gbk", "gb18030"]
        data = None

        for encoding in encodings:
            try:
                with open(path, encoding=encoding) as f:
                    data = yaml.safe_load(f) or {}
                break
            except UnicodeDecodeError:
                continue

        # 所有编码都失败时使用默认配置
        if data is None:
            data = {}

        data = _strip_removed_config_sections(data)

        worker_data = data.get("worker", {})
        external = data.get("external_services", {})
        platforms = data.get("platforms", {})
        harmony_official_cfg = data.get("harmony_official", {})
        unlock_cfg = data.get("unlock", {})
        if not isinstance(unlock_cfg, dict):
            unlock_cfg = {}

        return cls(
            id=worker_data.get("id") or _generate_worker_id(),
            ip=worker_data.get("ip"),
            port=worker_data.get("port", 8080),
            namespace=worker_data.get("namespace", "public"),
            namespace_overrides=worker_data.get("namespace_overrides") or {},
            discover_android_devices=worker_data.get("discover_android_devices", False),
            discover_ios_devices=worker_data.get("discover_ios_devices", False),
            discover_harmony_mobile_devices=bool(worker_data.get("discover_harmony_mobile_devices", False)),
            discover_harmony_pc_devices=bool(worker_data.get("discover_harmony_pc_devices", False)),
            platform_api=external.get("platform_api", "http://192.168.0.102:8000"),
            ocr_service=external.get("ocr_service", ""),
            platforms=platforms,
            harmony_official=harmony_official_cfg,
            unlock={"password": unlock_cfg.get("password", DEFAULT_UNLOCK_PASSWORD)},
        )

    def get_platform_config(self, platform: str) -> dict[str, Any]:
        """获取指定平台的配置。"""
        return self.platforms.get(platform, {})


@dataclass
class PlatformConfig:
    """平台通用配置。"""

    # Web 专用
    headless: bool = True
    browser_type: str = "chromium"
    timeout: int = 30000
    ignore_https_errors: bool = True
    permissions: list[str] = field(default_factory=lambda: ["camera", "microphone"])
    user_data_dir: str = "data/chrome_profile"

    # 启动前清理 Default 目录数据（保留 Cache 目录），避免账号缓存
    clear_profile_on_start: bool = True

    # 请求黑名单：拦截特定请求（如某些 JS 文件加载超时）
    # 格式：[{"pattern": "uba.js", "action": "abort"}, {"pattern": "tinyReporter.min.js", "action": "abort"}]
    # action 可选：abort（中止）、404（返回404）、empty（返回空响应）
    request_blacklist: list[dict[str, str]] = field(default_factory=list)

    # Web 专用 - Token 捕获
    token_headers: list[str] = field(default_factory=list)  # 要监听的 token header 名称列表

    # Web 专用 - Chromium 启动参数（字符串列表）
    browser_args: list[str] = field(default_factory=list)

    # iOS 专用
    wda_base_port: int = 8100
    wda_bundle_id: str = "com.facebook.WebDriverAgentRunner"
    wda_testrunner_bundle_id: str = ""  # go-ios runwda 需要
    wda_xctest_config: str = "WebDriverAgentRunner.xctest"  # go-ios runwda 需要
    go_ios_path: str = "tools/go-ios/ios.exe"
    agent_port: int = 60105                    # go-ios agent HTTP API 端口（默认 60105）
    mjpeg_base_port: int = 9100               # MJPEG 基础端口

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "PlatformConfig":
        """从字典创建配置。"""
        return cls(
            headless=data.get("headless", True),
            browser_type=data.get("browser_type", "chromium"),
            timeout=30000,
            permissions=data.get("permissions", ["camera", "microphone"]),
            request_blacklist=data.get("request_blacklist", []),
            token_headers=data.get("token_headers", []),
            browser_args=data.get("browser_args", []),
            wda_base_port=data.get("wda_base_port", 8100),
            wda_bundle_id=data.get("wda_bundle_id", "com.facebook.WebDriverAgentRunner"),
            wda_testrunner_bundle_id=data.get("wda_testrunner_bundle_id", ""),
            wda_xctest_config=data.get("wda_xctest_config", "WebDriverAgentRunner.xctest"),
            agent_port=data.get("agent_port", 60105),
            mjpeg_base_port=data.get("mjpeg_base_port", 9100),
        )


def _get_base_dir() -> str:
    """获取基础目录路径。

    返回打包环境下的 exe 所在目录，或开发环境下的项目根目录。

    Returns:
        str: 基础目录路径
    """
    return get_base_dir()


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
    if is_packaged():
        # 打包模式：模板在 _internal 目录下
        return os.path.join(_get_base_dir(), "_internal", "config", "worker.yaml")
    else:
        # 开发模式：用户配置和模板路径相同
        return os.path.join(_get_base_dir(), "config", "worker.yaml")


def get_log_level_path() -> str:
    """获取仅保存在本机的日志级别文件路径。"""
    return os.path.join(_get_base_dir(), "config", ".log_level")


def load_log_level() -> str:
    """读取本机日志级别；无效或缺失时使用代码默认值。"""
    try:
        with open(get_log_level_path(), encoding="utf-8-sig") as level_file:
            level = level_file.read().strip().upper()
        if level in LOG_LEVEL_OPTIONS:
            return level
    except OSError:
        pass
    return DEFAULT_LOG_LEVEL


def save_log_level(level: str) -> None:
    """保存设置界面选择的本机日志级别。"""
    normalized_level = str(level).strip().upper()
    if normalized_level not in LOG_LEVEL_OPTIONS:
        raise ValueError(f"不支持的日志级别: {level}")

    level_path = get_log_level_path()
    os.makedirs(os.path.dirname(level_path), exist_ok=True)
    with open(level_path, "w", encoding="utf-8") as level_file:
        level_file.write(normalized_level)


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


def _merge_missing_config(
    user_data: dict[str, Any],
    template_data: dict[str, Any]
) -> dict[str, Any]:
    """
    合并缺失配置项：保留用户配置，补充模板中新增的配置项。

    Args:
        user_data: 用户配置数据（基准）
        template_data: 模板配置数据（补充来源）

    Returns:
        dict: 合并后的配置数据
    """
    def deep_merge(base: dict[str, Any], supplement: dict[str, Any]) -> dict[str, Any]:
        """深度合并字典：base 保留，supplement 中新增的 key 补充到 base。"""
        result = base.copy()
        for key, value in supplement.items():
            if key not in result:
                # base 缺少此 key，从 supplement 补充
                result[key] = value
            elif isinstance(value, dict) and isinstance(result[key], dict):
                # 两个都是字典，递归合并
                result[key] = deep_merge(result[key], value)
        return result

    # 以用户配置为基准，从模板补充缺失项，并丢弃已固定到代码的旧配置段。
    return deep_merge(
        _strip_removed_config_sections(user_data),
        _strip_removed_config_sections(template_data),
    )


_REMOVED_CONFIG_SECTIONS = {
    "files",
    "logging",
    "recording",
    "storage",
    "upgrade",
    "websocket_streaming",
}

_REMOVED_WORKER_OPTIONS = {
    "device_check_interval",
    "service_retry_count",
    "service_retry_interval",
    "action_step_delay",
}

_REMOVED_PLATFORM_OPTIONS = {
    "web": {
        "timeout",
        "ignore_https_errors",
        "user_data_dir",
        "clear_profile_on_start",
        "cache_clear_enabled",
        "cache_clear_interval_hours",
        "cache_clear_clear_on_idle",
    },
    "ios": {"go_ios_path"},
    "harmony_mobile": {"hdc_path"},
    "harmony_pc": {"hdc_path"},
}


def _strip_removed_config_sections(data: dict[str, Any]) -> dict[str, Any]:
    """从内存中的配置数据剔除已固定到代码里的配置段。"""
    normalized = dict(data)
    for section in _REMOVED_CONFIG_SECTIONS:
        normalized.pop(section, None)

    worker_data = normalized.get("worker")
    if isinstance(worker_data, dict):
        worker_data = dict(worker_data)
        worker_data.pop("log_level", None)
        for option in _REMOVED_WORKER_OPTIONS:
            worker_data.pop(option, None)
        normalized["worker"] = worker_data

    if "unlock" in normalized:
        unlock_data = normalized.get("unlock")
        if isinstance(unlock_data, dict) and "password" in unlock_data:
            normalized["unlock"] = {"password": unlock_data["password"]}
        else:
            normalized["unlock"] = {}

    platforms = normalized.get("platforms")
    if isinstance(platforms, dict):
        platforms = dict(platforms)
        for platform_name, removed_options in _REMOVED_PLATFORM_OPTIONS.items():
            platform_config = platforms.get(platform_name)
            if isinstance(platform_config, dict):
                platform_config = dict(platform_config)
                for option in removed_options:
                    platform_config.pop(option, None)
                if platform_config:
                    platforms[platform_name] = platform_config
                else:
                    platforms.pop(platform_name, None)
            elif platform_name in {"harmony_mobile", "harmony_pc"}:
                platforms.pop(platform_name, None)
        normalized["platforms"] = platforms

    harmony_official = normalized.get("harmony_official")
    if isinstance(harmony_official, dict):
        normalized["harmony_official"] = (
            {"java_path": harmony_official["java_path"]}
            if "java_path" in harmony_official
            else {}
        )
    elif "harmony_official" in normalized:
        normalized["harmony_official"] = {}

    return normalized


def _remove_legacy_config_sections(path: str) -> None:
    """清理旧 YAML 中已固定到代码的配置段，并迁移本机日志级别。"""
    try:
        content = _read_file_with_encoding(path)
        try:
            old_data = yaml.safe_load(content) or {}
        except yaml.YAMLError:
            old_data = {}

        old_platforms = old_data.get("platforms", {}) if isinstance(old_data, dict) else {}
        empty_platform_sections = {
            platform_name
            for platform_name, removed_options in _REMOVED_PLATFORM_OPTIONS.items()
            if isinstance(old_platforms, dict)
            and isinstance(old_platforms.get(platform_name), dict)
            and set(old_platforms[platform_name]).issubset(removed_options)
        }

        old_logging = old_data.get("logging", {}) if isinstance(old_data, dict) else {}
        old_level = old_logging.get("level") if isinstance(old_logging, dict) else None
        level_path = get_log_level_path()
        if (
            not os.path.exists(level_path)
            and isinstance(old_level, str)
            and old_level.strip().upper() in LOG_LEVEL_OPTIONS
        ):
            save_log_level(old_level)

        lines = content.splitlines(keepends=True)
        root_key_pattern = re.compile(r"^([A-Za-z_][A-Za-z0-9_-]*):")
        section_pattern = re.compile(r"^([A-Za-z_][A-Za-z0-9_-]*):\s*(?:#.*)?(?:\r?\n)?$")
        output: list[str] = []
        removed_sections: set[str] = set()
        removed_unlock_options = False
        removed_fixed_options = False
        index = 0

        while index < len(lines):
            line = lines[index]
            section_match = section_pattern.match(line)
            section_name = section_match.group(1) if section_match else None

            if section_name in _REMOVED_CONFIG_SECTIONS:
                removed_sections.add(section_name)
                index += 1
                while index < len(lines):
                    candidate = lines[index]
                    if not candidate.strip() or candidate[0].isspace():
                        index += 1
                        continue
                    if candidate.startswith("#"):
                        output.append(candidate)
                        index += 1
                        continue
                    if root_key_pattern.match(candidate):
                        break
                    output.append(candidate)
                    index += 1
                continue

            if section_name == "unlock":
                output.append(line)
                index += 1
                while index < len(lines):
                    candidate = lines[index]
                    if not candidate.strip():
                        output.append(candidate)
                        index += 1
                        continue
                    if not candidate[0].isspace():
                        if root_key_pattern.match(candidate):
                            break
                        output.append(candidate)
                        index += 1
                        continue
                    if re.match(r"^  password\s*:", candidate):
                        output.append(candidate)
                    elif candidate.startswith("  #"):
                        output.append(candidate)
                    else:
                        removed_unlock_options = True
                    index += 1
                continue

            if not line.strip() or line.startswith("#"):
                lookahead = index
                while lookahead < len(lines):
                    candidate = lines[lookahead]
                    if candidate.strip() and not candidate.startswith("#"):
                        break
                    lookahead += 1
                next_section = (
                    section_pattern.match(lines[lookahead])
                    if lookahead < len(lines)
                    else None
                )
                if next_section and next_section.group(1) in _REMOVED_CONFIG_SECTIONS:
                    index += 1
                    continue

            output.append(line)
            index += 1

        # 只保留 harmony_official.java_path，其他鸿蒙运行参数固定由代码管理。
        cleaned_output: list[str] = []
        root_section: str | None = None
        platform_section: str | None = None
        skip_official_option = False
        for line in output:
            if not line.startswith((" ", "\t")) and line.strip() and not line.startswith("#"):
                root_match = root_key_pattern.match(line)
                root_section = root_match.group(1) if root_match else None
                platform_section = None
                skip_official_option = False
            elif root_section == "platforms":
                platform_match = re.match(r"^  ([A-Za-z_][A-Za-z0-9_-]*):", line)
                if platform_match:
                    platform_section = platform_match.group(1)

            worker_option_match = re.match(r"^  ([A-Za-z_][A-Za-z0-9_-]*)\s*:", line)
            platform_option_match = re.match(r"^    ([A-Za-z_][A-Za-z0-9_-]*)\s*:", line)
            if (
                root_section == "worker"
                and worker_option_match
                and worker_option_match.group(1) in _REMOVED_WORKER_OPTIONS
            ):
                removed_fixed_options = True
                continue

            if root_section == "harmony_official":
                official_option_match = re.match(r"^  ([A-Za-z_][A-Za-z0-9_-]*)\s*:", line)
                if official_option_match:
                    skip_official_option = official_option_match.group(1) != "java_path"
                    if skip_official_option:
                        removed_fixed_options = True
                        continue
                elif skip_official_option and (
                    not line.strip() or line.startswith(("    ", "  #"))
                ):
                    continue

            remove_line = (
                root_section == "platforms"
                and re.match(r"^  (harmony_mobile|harmony_pc):", line) is not None
                and platform_section in empty_platform_sections
            ) or (
                root_section == "platforms"
                and platform_section in _REMOVED_PLATFORM_OPTIONS
                and platform_option_match is not None
                and platform_option_match.group(1) in _REMOVED_PLATFORM_OPTIONS[platform_section]
            )
            if remove_line:
                removed_fixed_options = True
                continue
            cleaned_output.append(line)

        if removed_sections or removed_unlock_options or removed_fixed_options:
            updated = "".join(cleaned_output)
            with open(path, "w", encoding="utf-8", newline="") as config_file:
                config_file.write(updated)
            if removed_sections:
                logger.info("已从用户配置中移除固定配置段: %s", ", ".join(sorted(removed_sections)))
            if removed_unlock_options:
                logger.info("已从用户配置中移除旧版解锁参数")
            if removed_fixed_options:
                logger.info("已从用户配置中移除固定配置项")
    except OSError as e:
        logger.warning(f"清理旧版配置段失败，文件为 {path}: {e}")


def load_config() -> WorkerConfig:
    """加载 Worker 配置（含版本号和自动合并缺失配置）。"""
    user_config_path = get_user_config_path()
    default_template_path = get_default_template_path()

    # 优先读取用户配置
    if os.path.exists(user_config_path):
        logger.info(f"Loading user config: {user_config_path}")
        _remove_legacy_config_sections(user_config_path)
        user_data = _strip_removed_config_sections(
            yaml.safe_load(_read_file_with_encoding(user_config_path)) or {}
        )

        # 检查模板是否存在，合并缺失配置项
        if os.path.exists(default_template_path):
            template_data = _strip_removed_config_sections(
                yaml.safe_load(_read_file_with_encoding(default_template_path)) or {}
            )

            # 检查是否需要合并（模板配置比用户配置多）
            merged_data = _merge_missing_config(user_data, template_data)

            # 如果合并后有变化，保存回用户配置
            if merged_data != user_data:
                logger.info("Merging missing config items from template to user config")
                _save_user_config(merged_data, user_config_path)
                user_data = merged_data

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
    config.log_level = load_log_level()

    return config


def _read_file_with_encoding(path: str) -> str:
    """尝试多种编码读取文件。"""
    # 兼容旧安装器在 Windows PowerShell 5.1 下生成的 UTF-16 配置。
    encodings = ["utf-8-sig", "utf-16", "gbk", "gb18030"]
    for encoding in encodings:
        try:
            with open(path, encoding=encoding) as f:
                return f.read()
        except UnicodeDecodeError:
            continue
    return ""


def _save_user_config(data: dict[str, Any], path: str) -> None:
    """保存用户配置文件。"""
    data = _strip_removed_config_sections(data)
    config_yaml = yaml.dump(data, default_flow_style=False, allow_unicode=True, sort_keys=False)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(config_yaml)


def get_config_version_path() -> str:
    """获取配置版本文件路径。"""
    return os.path.join(_get_base_dir(), "config", ".config_version")


def load_config_version() -> str | None:
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


def merge_config_with_local_protection(
    new_config_yaml: str,
    existing_config_path: str = get_user_config_path()
) -> dict:
    """
    合并配置：保留本地特定字段。

    保留字段（不被远程配置覆盖）：
    - worker.ip: 本机 IP 地址
    - worker.discover_android_devices: Android 设备发现开关
    - worker.discover_ios_devices: iOS 设备发现开关
    - worker.discover_harmony_mobile_devices: 鸿蒙移动设备发现开关
    - worker.discover_harmony_pc_devices: 鸿蒙 PC 设备发现开关

    Why: 移动设备连接的机器是固定的，这些配置需要用户手动在机器上设置，
         共用一套模板时不应覆盖这些本地配置。

    Args:
        new_config_yaml: 新配置的 YAML 字符串
        existing_config_path: 现有配置文件路径

    Returns:
        dict: 合并后的配置数据
    """
    # 解析新配置并丢弃固定项，避免平台下发旧字段后再次写入本机配置。
    new_data = _strip_removed_config_sections(yaml.safe_load(new_config_yaml) or {})

    # 读取现有配置的保留字段
    if os.path.exists(existing_config_path):
        existing_data = _strip_removed_config_sections(
            yaml.safe_load(_read_file_with_encoding(existing_config_path)) or {}
        )
        existing_worker = existing_data.get("worker", {})
    else:
        existing_worker = {}

    # 保留字段列表（不被远程配置覆盖）
    preserved_fields = [
        "ip",
        "discover_android_devices",
        "discover_ios_devices",
        "discover_harmony_mobile_devices",
        "discover_harmony_pc_devices",
    ]

    # 合并：保留本地特定字段
    if "worker" in new_data:
        for field in preserved_fields:
            if field in existing_worker and existing_worker[field] is not None:
                new_data["worker"][field] = existing_worker[field]

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
    config_data = _strip_removed_config_sections(config_data)
    config_yaml = yaml.dump(config_data, default_flow_style=False, allow_unicode=True, sort_keys=False)

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
    """CLI 模式重启：拉起新进程后让当前进程优雅退出。

    之前直接在新线程里 ``sys.exit(0)``，只能退出该线程：旧进程继续
    占用端口和资源，新进程绑定失败退出，配置更新后的重启永远不会生效。

    现在的流程：
    1. 以 WORKER_RESTARTED=1 环境变量启动新进程（新进程在 main() 里
       会等待端口释放后再绑定）；
    2. 通知当前进程的 uvicorn 优雅停机，main() 的 finally 会完成
       worker.stop() 并退出进程，端口随之释放。
    """
    executable = sys.executable
    args = sys.argv

    logger.info(f"CLI mode: restarting with args={args}")

    try:
        env = dict(os.environ)
        env["WORKER_RESTARTED"] = "1"
        popen_cmd([executable] + args, env=env)
    except Exception as e:
        logger.error(f"CLI restart failed to launch new process: {e}")
        sys.exit(1)

    # 触发当前进程优雅退出（函数级导入避免循环依赖）
    try:
        from worker.server import request_uvicorn_shutdown
        request_uvicorn_shutdown()
    except Exception as e:
        logger.error(f"CLI restart failed to stop current server: {e}")
        sys.exit(1)
