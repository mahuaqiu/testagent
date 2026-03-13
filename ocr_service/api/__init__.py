"""
API 模块。
"""

from ocr_service.api.routes import router
from ocr_service.api.schemas import (
    OCRRequest,
    OCRResponse,
    ImageMatchRequest,
    ImageMatchResponse,
    HealthResponse,
)

__all__ = [
    "router",
    "OCRRequest",
    "OCRResponse",
    "ImageMatchRequest",
    "ImageMatchResponse",
    "HealthResponse",
]