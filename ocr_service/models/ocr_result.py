"""
OCR 结果模型。
"""

from dataclasses import dataclass, field
from typing import Optional


@dataclass
class Point:
    """坐标点。"""

    x: int
    y: int

    def to_dict(self) -> dict:
        return {"x": self.x, "y": self.y}


@dataclass
class TextBlock:
    """识别到的文字块。"""

    text: str
    confidence: float
    bbox: list[list[int]]  # 四角坐标 [[x1,y1], [x2,y2], [x3,y3], [x4,y4]]
    center: Point

    def to_dict(self) -> dict:
        return {
            "text": self.text,
            "confidence": round(self.confidence, 4),
            "bbox": self.bbox,
            "center": self.center.to_dict(),
        }

    @classmethod
    def from paddle_result(cls, result: tuple) -> "TextBlock":
        """从 PaddleOCR 结果创建实例。

        PaddleOCR 返回格式: [[[x1,y1],[x2,y2],[x3,y3],[x4,y4]], ('text', confidence)]
        """
        bbox = result[0]
        text, confidence = result[1]

        # 计算中心点
        x_coords = [p[0] for p in bbox]
        y_coords = [p[1] for p in bbox]
        center_x = sum(x_coords) // 4
        center_y = sum(y_coords) // 4

        return cls(
            text=text,
            confidence=confidence,
            bbox=[[int(p[0]), int(p[1])] for p in bbox],
            center=Point(x=center_x, y=center_y),
        )


@dataclass
class OCRResult:
    """OCR 识别结果。"""

    status: str
    texts: list[TextBlock] = field(default_factory=list)
    duration_ms: int = 0
    error: Optional[str] = None

    def to_dict(self) -> dict:
        result = {
            "status": self.status,
            "texts": [t.to_dict() for t in self.texts],
            "duration_ms": self.duration_ms,
        }
        if self.error:
            result["error"] = self.error
        return result