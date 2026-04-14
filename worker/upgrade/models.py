"""
升级模块数据模型。
"""

from dataclasses import dataclass
from enum import Enum


class UpgradeStatus(Enum):
    """升级状态。"""
    SKIPPED = "skipped"           # 无需升级
    DOWNLOADING = "downloading"   # 正在下载
    INSTALLING = "installing"     # 正在安装
    COMPLETED = "completed"       # 升级完成
    FAILED = "failed"             # 升级失败


@dataclass
class UpgradeRequest:
    """升级请求。"""
    download_url: str                       # 安装包下载地址
    version: str | None = None           # 目标版本号
    force: bool = True                      # 是否强制升级


@dataclass
class UpgradeResponse:
    """升级响应。"""
    status: str
    message: str
    current_version: str | None = None
    target_version: str | None = None

    def to_dict(self) -> dict:
        return {
            "status": self.status,
            "message": self.message,
            "current_version": self.current_version,
            "target_version": self.target_version,
        }


@dataclass
class UpgradeState:
    """升级状态（持久化）。"""
    status: str
    target_version: str
    current_version: str
    download_url: str
    started_at: str
    completed_at: str | None = None
    error: str | None = None
    # 下载进度字段
    download_progress: int | None = None       # 下载百分比 (0-100)
    downloaded_bytes: int | None = None       # 已下载字节
    total_bytes: int | None = None            # 总字节

    def to_dict(self) -> dict:
        return {
            "status": self.status,
            "target_version": self.target_version,
            "current_version": self.current_version,
            "download_url": self.download_url,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
            "error": self.error,
            "download_progress": self.download_progress,
            "downloaded_bytes": self.downloaded_bytes,
            "total_bytes": self.total_bytes,
        }
