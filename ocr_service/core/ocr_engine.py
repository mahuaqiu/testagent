"""
OCR 引擎封装。

基于 PaddleOCR 实现文字识别，支持中英文等多语言。
"""

import re
import time
from typing import Optional

import numpy as np

from ocr_service.config import ServiceConfig, get_config
from ocr_service.models.ocr_result import TextBlock, OCRResult, Point
from ocr_service.utils.image_utils import decode_image


class OCREngine:
    """
    OCR 引擎类。

    封装 PaddleOCR，提供文字识别能力。

    Attributes:
        config: 服务配置。
        _ocr: PaddleOCR 实例（延迟加载）。
    """

    def __init__(self, config: Optional[ServiceConfig] = None):
        """
        初始化 OCR 引擎。

        Args:
            config: 服务配置，默认使用全局配置。
        """
        self.config = config or get_config()
        self._ocr = None

    @property
    def ocr(self):
        """延迟加载 PaddleOCR 实例。"""
        if self._ocr is None:
            from paddleocr import PaddleOCR

            self._ocr = PaddleOCR(
                use_angle_cls=True,
                lang=self.config.ocr_lang,
                use_gpu=self.config.ocr_use_gpu,
                show_log=False,
                # 禁用自动下载模型到用户目录，使用缓存目录
                model_storage_directory=self.config.cache_dir,
            )
        return self._ocr

    def recognize(
        self,
        image_data: bytes | str,
        lang: Optional[str] = None,
        confidence_threshold: float = 0.0,
    ) -> OCRResult:
        """
        识别图片中的所有文字。

        Args:
            image_data: 图像字节数据或 Base64 编码字符串。
            lang: 语言代码，默认使用配置中的语言。
            confidence_threshold: 置信度阈值，低于此值的结果将被过滤。

        Returns:
            OCRResult: 识别结果。
        """
        start_time = time.time()

        try:
            # 解码图像
            image = decode_image(image_data)

            # 执行 OCR
            result = self.ocr.ocr(image, cls=True)

            # 解析结果
            texts = []
            if result and result[0]:
                for item in result[0]:
                    text_block = TextBlock.from_paddle_result(item)
                    if text_block.confidence >= confidence_threshold:
                        texts.append(text_block)

            duration_ms = int((time.time() - start_time) * 1000)

            return OCRResult(
                status="success",
                texts=texts,
                duration_ms=duration_ms,
            )

        except Exception as e:
            duration_ms = int((time.time() - start_time) * 1000)
            return OCRResult(
                status="error",
                texts=[],
                duration_ms=duration_ms,
                error=str(e),
            )

    def find_text(
        self,
        image_data: bytes | str,
        target_text: str,
        match_mode: str = "exact",
        confidence_threshold: float = 0.0,
    ) -> Optional[TextBlock]:
        """
        在图片中查找指定文字。

        Args:
            image_data: 图像字节数据或 Base64 编码字符串。
            target_text: 目标文字。
            match_mode: 匹配模式，支持 exact（精确）、fuzzy（模糊）、regex（正则）。
            confidence_threshold: 置信度阈值。

        Returns:
            TextBlock | None: 找到的文字块，未找到返回 None。
        """
        result = self.recognize(image_data, confidence_threshold=confidence_threshold)

        if result.status != "success":
            return None

        for text_block in result.texts:
            if self._match_text(text_block.text, target_text, match_mode):
                return text_block

        return None

    def find_all_texts(
        self,
        image_data: bytes | str,
        target_text: str,
        match_mode: str = "exact",
        confidence_threshold: float = 0.0,
    ) -> list[TextBlock]:
        """
        在图片中查找所有匹配的文字。

        Args:
            image_data: 图像字节数据或 Base64 编码字符串。
            target_text: 目标文字。
            match_mode: 匹配模式。
            confidence_threshold: 置信度阈值。

        Returns:
            list[TextBlock]: 匹配的文字块列表。
        """
        result = self.recognize(image_data, confidence_threshold=confidence_threshold)

        if result.status != "success":
            return []

        matches = []
        for text_block in result.texts:
            if self._match_text(text_block.text, target_text, match_mode):
                matches.append(text_block)

        return matches

    def _match_text(self, text: str, target: str, mode: str) -> bool:
        """
        匹配文字。

        Args:
            text: 实际识别的文字。
            target: 目标文字。
            mode: 匹配模式。

        Returns:
            bool: 是否匹配。
        """
        if mode == "exact":
            return target in text
        elif mode == "fuzzy":
            # 模糊匹配：忽略标点和空白
            import unicodedata

            def normalize(s):
                # 移除标点和空白
                return "".join(
                    c
                    for c in unicodedata.normalize("NFKC", s)
                    if not unicodedata.category(c).startswith("P")
                    and not unicodedata.category(c).startswith("Z")
                )

            return normalize(target) in normalize(text)
        elif mode == "regex":
            try:
                return bool(re.search(target, text))
            except re.error:
                return False
        else:
            return target in text

    def get_text_center(
        self,
        image_data: bytes | str,
        target_text: str,
        match_mode: str = "exact",
    ) -> Optional[Point]:
        """
        获取指定文字的中心坐标。

        Args:
            image_data: 图像字节数据或 Base64 编码字符串。
            target_text: 目标文字。
            match_mode: 匹配模式。

        Returns:
            Point | None: 中心坐标，未找到返回 None。
        """
        text_block = self.find_text(image_data, target_text, match_mode)
        return text_block.center if text_block else None


# 全局引擎实例
_engine: Optional[OCREngine] = None


def get_ocr_engine() -> OCREngine:
    """获取全局 OCR 引擎实例。"""
    global _engine
    if _engine is None:
        _engine = OCREngine()
    return _engine