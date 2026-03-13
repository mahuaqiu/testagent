"""
API 请求/响应模型（Pydantic）。
"""

from typing import Optional

from pydantic import BaseModel, Field


# ==================== 通用模型 ====================

class PointModel(BaseModel):
    """坐标点。"""

    x: int
    y: int


class TextBlockModel(BaseModel):
    """识别到的文字块。"""

    text: str
    confidence: float
    bbox: list[list[int]]
    center: PointModel


class BoundingBoxModel(BaseModel):
    """边界框。"""

    x: int
    y: int
    width: int
    height: int


class MatchItemModel(BaseModel):
    """单个匹配结果。"""

    confidence: float
    bbox: BoundingBoxModel
    center: PointModel


# ==================== OCR 接口 ====================

class OCRRequest(BaseModel):
    """OCR 识别请求。"""

    image: str = Field(..., description="Base64 编码的图像数据")
    lang: str = Field(default="ch", description="语言代码，默认中文")
    filter_text: Optional[str] = Field(default=None, description="过滤关键词，只返回包含此文字的结果")
    confidence_threshold: float = Field(default=0.0, ge=0.0, le=1.0, description="置信度阈值")


class OCRResponse(BaseModel):
    """OCR 识别响应。"""

    status: str
    texts: list[TextBlockModel] = []
    duration_ms: int = 0
    error: Optional[str] = None


# ==================== 图像匹配接口 ====================

class ImageMatchRequest(BaseModel):
    """图像匹配请求。"""

    source_image: str = Field(..., description="源图像（大图）Base64 编码")
    template_image: str = Field(..., description="模板图像（小图）Base64 编码")
    confidence_threshold: float = Field(
        default=0.8, ge=0.0, le=1.0, description="匹配阈值"
    )
    method: str = Field(
        default="template", description="匹配方法: template(精确) / feature(特征)"
    )
    multi_target: bool = Field(default=False, description="是否多目标匹配")


class ImageMatchResponse(BaseModel):
    """图像匹配响应。"""

    status: str
    matches: list[MatchItemModel] = []
    duration_ms: int = 0
    error: Optional[str] = None


# ==================== 健康检查 ====================

class HealthResponse(BaseModel):
    """健康检查响应。"""

    status: str
    version: str