"""
数据模型模块。
"""

from ocr_service.models.ocr_result import TextBlock, OCRResult
from ocr_service.models.match_result import MatchResult, ImageMatchResult

__all__ = [
    "TextBlock",
    "OCRResult",
    "MatchResult",
    "ImageMatchResult",
]