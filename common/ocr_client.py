"""
OCR 服务 HTTP 客户端。

供各端测试框架调用，实现基于视觉感知的元素定位。

Usage:
    from common.ocr_client import get_ocr_client

    client = get_ocr_client()

    # 文字识别
    texts = client.recognize(screenshot_bytes)
    for t in texts:
        print(f"{t.text} at ({t.center.x}, {t.center.y})")

    # 文字查找
    text_block = client.find_text(screenshot_bytes, "登录")
    if text_block:
        print(f"Found at ({text_block.center.x}, {text_block.center.y})")

    # 图像匹配
    matches = client.match_image(screenshot_bytes, template_bytes)
    for m in matches:
        print(f"Match at ({m.center.x}, {m.center.y})")
"""

import base64
import time
from dataclasses import dataclass
from typing import Optional

import httpx

from common.config import Config


@dataclass
class TextBlock:
    """识别到的文字块。"""

    text: str
    confidence: float
    bbox: list[list[int]]  # 四角坐标
    center_x: int
    center_y: int

    @property
    def center(self) -> tuple[int, int]:
        return (self.center_x, self.center_y)


@dataclass
class MatchResult:
    """图像匹配结果。"""

    confidence: float
    x: int
    y: int
    width: int
    height: int
    center_x: int
    center_y: int

    @property
    def center(self) -> tuple[int, int]:
        return (self.center_x, self.center_y)


class OCRClient:
    """
    OCR 服务 HTTP 客户端。

    封装 OCR 服务的 HTTP 调用，提供文字识别和图像匹配能力。

    Attributes:
        base_url: OCR 服务地址。
        timeout: 请求超时时间（毫秒）。
        retry: 重试次数。
    """

    def __init__(
        self,
        base_url: str,
        timeout: int = 30000,
        retry: int = 1,
        lang: str = "ch",
    ):
        """
        初始化 OCR 客户端。

        Args:
            base_url: OCR 服务地址。
            timeout: 请求超时时间（毫秒）。
            retry: 重试次数。
            lang: 默认语言。
        """
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout / 1000  # 转换为秒
        self.retry = retry
        self.lang = lang
        self._client = httpx.Client(timeout=self.timeout, trust_env=True)

    def recognize(
        self,
        image_bytes: bytes,
        lang: Optional[str] = None,
        filter_text: Optional[str] = None,
        confidence_threshold: float = 0.0,
    ) -> list[TextBlock]:
        """
        识别图片中的所有文字。

        Args:
            image_bytes: 图像字节数据。
            lang: 语言代码，默认使用客户端配置。
            filter_text: 过滤关键词，只返回包含此文字的结果。
            confidence_threshold: 置信度阈值。

        Returns:
            list[TextBlock]: 识别到的文字块列表。
        """
        image_base64 = base64.b64encode(image_bytes).decode("utf-8")

        response = self._post("/ocr/get_ocr_infos", {
            "image": image_base64,
            "lang": lang or self.lang,
            "filter_text": filter_text,
            "confidence_threshold": confidence_threshold,
        })

        if response.get("status") != "success":
            return []

        return [
            TextBlock(
                text=t["text"],
                confidence=t["confidence"],
                bbox=t["bbox"],
                center_x=t["center"]["x"],
                center_y=t["center"]["y"],
            )
            for t in response.get("texts", [])
        ]

    def find_text(
        self,
        image_bytes: bytes,
        target_text: str,
        match_mode: str = "exact",
        confidence_threshold: float = 0.0,
    ) -> Optional[TextBlock]:
        """
        在图片中查找指定文字。

        Args:
            image_bytes: 图像字节数据。
            target_text: 目标文字。
            match_mode: 匹配模式（exact/fuzzy/regex），正则表达式以 "reg_" 开头。
            confidence_threshold: 置信度阈值。

        Returns:
            TextBlock | None: 找到的文字块，未找到返回 None。
        """
        # 使用新的 /ocr/get_coord_by_text API 直接查找
        image_base64 = base64.b64encode(image_bytes).decode("utf-8")

        # 处理正则表达式模式
        filter_text = target_text
        if match_mode == "regex":
            filter_text = f"reg_{target_text}"

        response = self._post("/ocr/get_coord_by_text", {
            "image": image_base64,
            "filter_text": filter_text,
            "confidence_threshold": confidence_threshold,
        })

        if response.get("status") != "success":
            return None

        texts = response.get("texts", [])
        if not texts:
            return None

        t = texts[0]
        return TextBlock(
            text=t["text"],
            confidence=t["confidence"],
            bbox=t["bbox"],
            center_x=t["center"]["x"],
            center_y=t["center"]["y"],
        )

    def find_all_texts(
        self,
        image_bytes: bytes,
        target_text: str,
        confidence_threshold: float = 0.0,
    ) -> list[TextBlock]:
        """
        在图片中查找所有匹配的文字。

        Args:
            image_bytes: 图像字节数据。
            target_text: 目标文字。
            confidence_threshold: 置信度阈值。

        Returns:
            list[TextBlock]: 匹配的文字块列表。
        """
        return self.recognize(
            image_bytes,
            filter_text=target_text,
            confidence_threshold=confidence_threshold,
        )

    def get_texts(
        self,
        image_bytes: bytes,
        lang: Optional[str] = None,
        separator: str = "\n",
        confidence_threshold: float = 0.0,
    ) -> str:
        """
        获取图片中的所有文本（拼接后的纯文本）。

        Args:
            image_bytes: 图像字节数据。
            lang: 语言代码，默认使用客户端配置。
            separator: 文本分隔符，默认换行。
            confidence_threshold: 置信度阈值。

        Returns:
            str: 拼接后的文本字符串。
        """
        image_base64 = base64.b64encode(image_bytes).decode("utf-8")

        response = self._post("/ocr/get_ocr_texts", {
            "image": image_base64,
            "lang": lang or self.lang,
            "separator": separator,
            "confidence_threshold": confidence_threshold,
        })

        if response.get("status") != "success":
            return ""

        return response.get("text", "")

    def match_image(
        self,
        source_bytes: bytes,
        template_bytes: bytes,
        threshold: float = 0.8,
        method: str = "template",
        multi_target: bool = False,
    ) -> list[MatchResult]:
        """
        图像匹配。

        在源图像中查找模板图像的位置。

        Args:
            source_bytes: 源图像字节数据。
            template_bytes: 模板图像字节数据。
            threshold: 匹配阈值（0-1）。
            method: 匹配方法（template/feature）。
            multi_target: 是否多目标匹配。

        Returns:
            list[MatchResult]: 匹配结果列表。
        """
        source_base64 = base64.b64encode(source_bytes).decode("utf-8")
        template_base64 = base64.b64encode(template_bytes).decode("utf-8")

        response = self._post("/image/match", {
            "source_image": source_base64,
            "template_image": template_base64,
            "confidence_threshold": threshold,
            "method": method,
            "multi_target": multi_target,
        })

        if response.get("status") != "success":
            return []

        return [
            MatchResult(
                confidence=m["confidence"],
                x=m["bbox"]["x"],
                y=m["bbox"]["y"],
                width=m["bbox"]["width"],
                height=m["bbox"]["height"],
                center_x=m["center"]["x"],
                center_y=m["center"]["y"],
            )
            for m in response.get("matches", [])
        ]

    def find_image(
        self,
        source_bytes: bytes,
        template_bytes: bytes,
        threshold: float = 0.8,
        method: str = "template",
    ) -> Optional[MatchResult]:
        """
        在源图像中查找模板图像（返回第一个匹配）。

        Args:
            source_bytes: 源图像字节数据。
            template_bytes: 模板图像字节数据。
            threshold: 匹配阈值。
            method: 匹配方法。

        Returns:
            MatchResult | None: 匹配结果，未找到返回 None。
        """
        matches = self.match_image(source_bytes, template_bytes, threshold, method)
        return matches[0] if matches else None

    def match_near_text(
        self,
        image_bytes: bytes,
        target_image_bytes: bytes,
        filter_text: str,
        max_distance: int = 500,
        threshold: float = 0.8,
        method: str = "template",
    ) -> Optional[MatchResult]:
        """
        查找文本附近最近的图片。

        在源图像中查找目标文字位置，然后查找距离文字最近的模板图像位置。

        Args:
            image_bytes: 源图像字节数据。
            target_image_bytes: 模板图像字节数据。
            filter_text: 目标文字（以 reg_ 开头表示正则表达式）。
            max_distance: 最大搜索距离（像素）。
            threshold: 匹配阈值。
            method: 匹配方法（template/feature）。

        Returns:
            MatchResult | None: 匹配结果，未找到返回 None。
        """
        image_base64 = base64.b64encode(image_bytes).decode("utf-8")
        target_image_base64 = base64.b64encode(target_image_bytes).decode("utf-8")

        response = self._post("/image/match_near_text", {
            "image": image_base64,
            "target_image": target_image_base64,
            "filter_text": filter_text,
            "max_distance": max_distance,
            "confidence_threshold": threshold,
            "method": method,
        })

        if response.get("status") != "success":
            return None

        match = response.get("match")
        if not match:
            return None

        return MatchResult(
            confidence=match["confidence"],
            x=match["bbox"]["x"],
            y=match["bbox"]["y"],
            width=match["bbox"]["width"],
            height=match["bbox"]["height"],
            center_x=match["center"]["x"],
            center_y=match["center"]["y"],
        )

    def health_check(self) -> bool:
        """
        检查服务健康状态。

        Returns:
            bool: 服务是否健康。
        """
        try:
            response = self._client.get(f"{self.base_url}/health")
            return response.status_code == 200
        except Exception:
            return False

    def _post(self, path: str, data: dict) -> dict:
        """
        发送 POST 请求（带重试）。

        Args:
            path: API 路径。
            data: 请求数据。

        Returns:
            dict: 响应数据。
        """
        last_error = None

        for attempt in range(self.retry + 1):
            try:
                response = self._client.post(
                    f"{self.base_url}{path}",
                    json=data,
                )
                response.raise_for_status()
                return response.json()
            except Exception as e:
                last_error = e
                if attempt < self.retry:
                    time.sleep(0.5 * (attempt + 1))

        return {"status": "error", "error": str(last_error)}

    def close(self):
        """关闭客户端连接。"""
        self._client.close()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()


# 全局客户端实例
_client: Optional[OCRClient] = None


def get_ocr_client(config: Optional[Config] = None) -> OCRClient:
    """
    获取全局 OCR 客户端实例。

    Args:
        config: 配置对象，默认使用全局配置。

    Returns:
        OCRClient: 客户端实例。
    """
    global _client

    if _client is None:
        if config is None:
            from common.config import Config
            config = Config()

        ocr_config = config.get("ocr_service", {})
        _client = OCRClient(
            base_url=ocr_config.get("base_url", "http://127.0.0.1:8081"),
            timeout=ocr_config.get("timeout", 30000),
            retry=ocr_config.get("retry", 2),
            lang=ocr_config.get("lang", "ch"),
        )

    return _client


def reset_ocr_client():
    """重置全局客户端实例（用于测试）。"""
    global _client
    if _client:
        _client.close()
    _client = None