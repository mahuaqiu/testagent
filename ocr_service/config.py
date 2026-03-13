"""
OCR 服务配置。
"""

import os
from dataclasses import dataclass


@dataclass
class ServiceConfig:
    """OCR 服务配置。"""

    # 服务配置
    host: str = "0.0.0.0"
    port: int = 8081
    debug: bool = False

    # OCR 引擎配置
    ocr_lang: str = "ch"  # 默认中文
    ocr_use_gpu: bool = False
    ocr_model_dir: str | None = None  # 自定义模型目录

    # 图像匹配配置
    default_match_threshold: float = 0.8
    default_match_method: str = "template"  # template / feature

    # 缓存目录
    cache_dir: str = "cache"

    @classmethod
    def from_env(cls) -> "ServiceConfig":
        """从环境变量加载配置。"""
        return cls(
            host=os.getenv("OCR_HOST", "0.0.0.0"),
            port=int(os.getenv("OCR_PORT", "8081")),
            debug=os.getenv("OCR_DEBUG", "false").lower() == "true",
            ocr_lang=os.getenv("OCR_LANG", "ch"),
            ocr_use_gpu=os.getenv("OCR_USE_GPU", "false").lower() == "true",
            ocr_model_dir=os.getenv("OCR_MODEL_DIR") or None,
            default_match_threshold=float(os.getenv("OCR_MATCH_THRESHOLD", "0.8")),
            default_match_method=os.getenv("OCR_MATCH_METHOD", "template"),
            cache_dir=os.getenv("OCR_CACHE_DIR", "cache"),
        )


# 全局配置实例
_config: ServiceConfig | None = None


def get_config() -> ServiceConfig:
    """获取配置实例。"""
    global _config
    if _config is None:
        _config = ServiceConfig.from_env()
    return _config


def set_config(config: ServiceConfig) -> None:
    """设置配置实例。"""
    global _config
    _config = config