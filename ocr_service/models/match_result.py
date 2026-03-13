"""
图像匹配结果模型。
"""

from dataclasses import dataclass, field
from typing import Optional


@dataclass
class BoundingBox:
    """边界框。"""

    x: int
    y: int
    width: int
    height: int

    def to_dict(self) -> dict:
        return {
            "x": self.x,
            "y": self.y,
            "width": self.width,
            "height": self.height,
        }


@dataclass
class Point:
    """坐标点。"""

    x: int
    y: int

    def to_dict(self) -> dict:
        return {"x": self.x, "y": self.y}


@dataclass
class MatchResult:
    """单个匹配结果。"""

    confidence: float
    bbox: BoundingBox
    center: Point

    def to_dict(self) -> dict:
        return {
            "confidence": round(self.confidence, 4),
            "bbox": self.bbox.to_dict(),
            "center": self.center.to_dict(),
        }


@dataclass
class ImageMatchResult:
    """图像匹配完整结果。"""

    status: str
    matches: list[MatchResult] = field(default_factory=list)
    duration_ms: int = 0
    error: Optional[str] = None

    def to_dict(self) -> dict:
        result = {
            "status": self.status,
            "matches": [m.to_dict() for m in self.matches],
            "duration_ms": self.duration_ms,
        }
        if self.error:
            result["error"] = self.error
        return result