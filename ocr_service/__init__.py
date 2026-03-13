"""
OCR 服务模块入口。

提供基于 PaddleOCR 的文字识别和 OpenCV 图像匹配能力。
"""

from ocr_service.server import create_app

__version__ = "1.0.0"
__all__ = ["create_app"]