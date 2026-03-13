"""
API 路由定义。
"""

from fastapi import APIRouter, HTTPException

from ocr_service import __version__
from ocr_service.api.schemas import (
    HealthResponse,
    ImageMatchRequest,
    ImageMatchResponse,
    OCRRequest,
    OCRResponse,
    TextBlockModel,
    PointModel,
    BoundingBoxModel,
    MatchItemModel,
)
from ocr_service.core.ocr_engine import get_ocr_engine
from ocr_service.core.image_matcher import get_image_matcher

router = APIRouter()


# ==================== OCR 接口 ====================

@router.get("/health", response_model=HealthResponse, tags=["Health"])
async def health_check():
    """
    健康检查。

    Returns:
        HealthResponse: 服务状态。
    """
    return HealthResponse(status="healthy", version=__version__)


@router.post("/ocr/recognize", response_model=OCRResponse, tags=["OCR"])
async def ocr_recognize(request: OCRRequest):
    """
    文字识别。

    识别图片中的所有文字，返回文字内容和坐标。

    Args:
        request: OCR 请求，包含 Base64 编码的图像。

    Returns:
        OCRResponse: 识别结果，包含文字列表和坐标。
    """
    engine = get_ocr_engine()
    result = engine.recognize(
        image_data=request.image,
        lang=request.lang,
        confidence_threshold=request.confidence_threshold,
    )

    # 过滤文字
    texts = result.texts
    if request.filter_text:
        texts = [t for t in texts if request.filter_text in t.text]

    return OCRResponse(
        status=result.status,
        texts=[
            TextBlockModel(
                text=t.text,
                confidence=t.confidence,
                bbox=t.bbox,
                center=PointModel(x=t.center.x, y=t.center.y),
            )
            for t in texts
        ],
        duration_ms=result.duration_ms,
        error=result.error,
    )


@router.post("/ocr/find", response_model=OCRResponse, tags=["OCR"])
async def ocr_find_text(request: OCRRequest):
    """
    查找指定文字。

    在图片中查找指定的文字，返回匹配的文字和坐标。

    Args:
        request: OCR 请求，filter_text 为必填字段。

    Returns:
        OCRResponse: 匹配结果。
    """
    if not request.filter_text:
        raise HTTPException(status_code=400, detail="filter_text is required")

    engine = get_ocr_engine()
    text_block = engine.find_text(
        image_data=request.image,
        target_text=request.filter_text,
        confidence_threshold=request.confidence_threshold,
    )

    if text_block is None:
        return OCRResponse(
            status="success",
            texts=[],
            duration_ms=0,
        )

    return OCRResponse(
        status="success",
        texts=[
            TextBlockModel(
                text=text_block.text,
                confidence=text_block.confidence,
                bbox=text_block.bbox,
                center=PointModel(x=text_block.center.x, y=text_block.center.y),
            )
        ],
        duration_ms=0,
    )


# ==================== 图像匹配接口 ====================

@router.post("/image/match", response_model=ImageMatchResponse, tags=["Image"])
async def image_match(request: ImageMatchRequest):
    """
    图像匹配。

    在源图像中查找模板图像的位置。

    Args:
        request: 匹配请求。

    Returns:
        ImageMatchResponse: 匹配结果，包含坐标和置信度。
    """
    matcher = get_image_matcher()
    result = matcher.match(
        source_data=request.source_image,
        template_data=request.template_image,
        threshold=request.confidence_threshold,
        method=request.method,
        multi_target=request.multi_target,
    )

    return ImageMatchResponse(
        status=result.status,
        matches=[
            MatchItemModel(
                confidence=m.confidence,
                bbox=BoundingBoxModel(
                    x=m.bbox.x,
                    y=m.bbox.y,
                    width=m.bbox.width,
                    height=m.bbox.height,
                ),
                center=PointModel(x=m.center.x, y=m.center.y),
            )
            for m in result.matches
        ],
        duration_ms=result.duration_ms,
        error=result.error,
    )