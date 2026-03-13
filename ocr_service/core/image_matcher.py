"""
图像匹配引擎。

基于 OpenCV 实现模板匹配和特征匹配。
"""

import time
from typing import Optional

import cv2
import numpy as np

from ocr_service.config import ServiceConfig, get_config
from ocr_service.models.match_result import (
    BoundingBox,
    ImageMatchResult,
    MatchResult,
    Point,
)
from ocr_service.utils.image_utils import decode_image


class ImageMatcher:
    """
    图像匹配引擎。

    支持精确模板匹配和特征匹配（缩放/旋转场景）。

    Attributes:
        config: 服务配置。
    """

    def __init__(self, config: Optional[ServiceConfig] = None):
        """
        初始化图像匹配引擎。

        Args:
            config: 服务配置，默认使用全局配置。
        """
        self.config = config or get_config()

    def match_template(
        self,
        source_data: bytes | str,
        template_data: bytes | str,
        threshold: Optional[float] = None,
    ) -> ImageMatchResult:
        """
        精确模板匹配。

        Args:
            source_data: 源图像（大图）。
            template_data: 模板图像（小图）。
            threshold: 匹配阈值，默认使用配置中的阈值。

        Returns:
            ImageMatchResult: 匹配结果。
        """
        start_time = time.time()
        threshold = threshold or self.config.default_match_threshold

        try:
            # 解码图像
            source = decode_image(source_data)
            template = decode_image(template_data)

            # 转换为灰度图
            source_gray = cv2.cvtColor(source, cv2.COLOR_BGR2GRAY)
            template_gray = cv2.cvtColor(template, cv2.COLOR_BGR2GRAY)

            # 模板匹配
            result = cv2.matchTemplate(
                source_gray, template_gray, cv2.TM_CCOEFF_NORMED
            )

            # 查找最佳匹配
            min_val, max_val, min_loc, max_loc = cv2.minMaxLoc(result)

            matches = []
            if max_val >= threshold:
                h, w = template_gray.shape
                matches.append(
                    MatchResult(
                        confidence=max_val,
                        bbox=BoundingBox(
                            x=max_loc[0],
                            y=max_loc[1],
                            width=w,
                            height=h,
                        ),
                        center=Point(
                            x=max_loc[0] + w // 2,
                            y=max_loc[1] + h // 2,
                        ),
                    )
                )

            duration_ms = int((time.time() - start_time) * 1000)

            return ImageMatchResult(
                status="success",
                matches=matches,
                duration_ms=duration_ms,
            )

        except Exception as e:
            duration_ms = int((time.time() - start_time) * 1000)
            return ImageMatchResult(
                status="error",
                matches=[],
                duration_ms=duration_ms,
                error=str(e),
            )

    def match_all(
        self,
        source_data: bytes | str,
        template_data: bytes | str,
        threshold: Optional[float] = None,
    ) -> ImageMatchResult:
        """
        多目标模板匹配。

        在源图像中查找所有匹配的模板位置。

        Args:
            source_data: 源图像（大图）。
            template_data: 模板图像（小图）。
            threshold: 匹配阈值。

        Returns:
            ImageMatchResult: 匹配结果（可能包含多个匹配）。
        """
        start_time = time.time()
        threshold = threshold or self.config.default_match_threshold

        try:
            # 解码图像
            source = decode_image(source_data)
            template = decode_image(template_data)

            # 转换为灰度图
            source_gray = cv2.cvtColor(source, cv2.COLOR_BGR2GRAY)
            template_gray = cv2.cvtColor(template, cv2.COLOR_BGR2GRAY)

            # 模板匹配
            result = cv2.matchTemplate(
                source_gray, template_gray, cv2.TM_CCOEFF_NORMED
            )

            h, w = template_gray.shape
            matches = []

            # 查找所有超过阈值的位置
            locations = np.where(result >= threshold)

            # 使用非极大值抑制去除重叠的匹配
            rectangles = []
            for pt in zip(*locations[::-1]):
                rectangles.append([pt[0], pt[1], w, h, result[pt[1], pt[0]]])

            if rectangles:
                # 按 confidence 降序排序
                rectangles.sort(key=lambda x: x[4], reverse=True)

                # 简单的非极大值抑制
                picked = []
                for rect in rectangles:
                    x, y, rw, rh, conf = rect
                    overlap = False
                    for p in picked:
                        px, py, pw, ph, _ = p
                        # 计算 IoU
                        x1 = max(x, px)
                        y1 = max(y, py)
                        x2 = min(x + rw, px + pw)
                        y2 = min(y + rh, py + ph)
                        if x1 < x2 and y1 < y2:
                            overlap = True
                            break
                    if not overlap:
                        picked.append(rect)
                        matches.append(
                            MatchResult(
                                confidence=conf,
                                bbox=BoundingBox(x=x, y=y, width=rw, height=rh),
                                center=Point(x=x + rw // 2, y=y + rh // 2),
                            )
                        )

            duration_ms = int((time.time() - start_time) * 1000)

            return ImageMatchResult(
                status="success",
                matches=matches,
                duration_ms=duration_ms,
            )

        except Exception as e:
            duration_ms = int((time.time() - start_time) * 1000)
            return ImageMatchResult(
                status="error",
                matches=[],
                duration_ms=duration_ms,
                error=str(e),
            )

    def match_feature(
        self,
        source_data: bytes | str,
        template_data: bytes | str,
        threshold: Optional[float] = None,
    ) -> ImageMatchResult:
        """
        特征匹配（SIFT）。

        支持缩放和旋转场景。

        Args:
            source_data: 源图像（大图）。
            template_data: 模板图像（小图）。
            threshold: 匹配阈值。

        Returns:
            ImageMatchResult: 匹配结果。
        """
        start_time = time.time()
        threshold = threshold or self.config.default_match_threshold

        try:
            # 解码图像
            source = decode_image(source_data)
            template = decode_image(template_data)

            # 转换为灰度图
            source_gray = cv2.cvtColor(source, cv2.COLOR_BGR2GRAY)
            template_gray = cv2.cvtColor(template, cv2.COLOR_BGR2GRAY)

            # 创建 SIFT 检测器
            sift = cv2.SIFT_create()

            # 检测关键点和描述子
            kp1, des1 = sift.detectAndCompute(template_gray, None)
            kp2, des2 = sift.detectAndCompute(source_gray, None)

            if des1 is None or des2 is None:
                duration_ms = int((time.time() - start_time) * 1000)
                return ImageMatchResult(
                    status="success",
                    matches=[],
                    duration_ms=duration_ms,
                )

            # 使用 FLANN 匹配器
            FLANN_INDEX_KDTREE = 1
            index_params = dict(algorithm=FLANN_INDEX_KDTREE, trees=5)
            search_params = dict(checks=50)
            flann = cv2.FlannBasedMatcher(index_params, search_params)

            matches = flann.knnMatch(des1, des2, k=2)

            # 应用 Lowe's ratio test 筛选好的匹配
            good_matches = []
            for m, n in matches:
                if m.distance < 0.7 * n.distance:
                    good_matches.append(m)

            result_matches = []

            # 需要至少 4 个好的匹配点才能计算单应性矩阵
            if len(good_matches) >= 4:
                # 获取匹配点坐标
                src_pts = np.float32([kp1[m.queryIdx].pt for m in good_matches]).reshape(
                    -1, 1, 2
                )
                dst_pts = np.float32([kp2[m.trainIdx].pt for m in good_matches]).reshape(
                    -1, 1, 2
                )

                # 计算单应性矩阵
                M, mask = cv2.findHomography(src_pts, dst_pts, cv2.RANSAC, 5.0)

                if M is not None:
                    # 获取模板图像的角点
                    h, w = template_gray.shape
                    pts = np.float32([[0, 0], [0, h - 1], [w - 1, h - 1], [w - 1, 0]]).reshape(
                        -1, 1, 2
                    )

                    # 变换到源图像坐标系
                    dst = cv2.perspectiveTransform(pts, M)

                    # 计算边界框
                    x_coords = dst[:, 0, 0]
                    y_coords = dst[:, 0, 1]
                    x_min, x_max = int(x_coords.min()), int(x_coords.max())
                    y_min, y_max = int(y_coords.min()), int(y_coords.max())

                    # 计算置信度（基于内点比例）
                    confidence = len(good_matches) / max(len(matches), 1)

                    if confidence >= threshold:
                        result_matches.append(
                            MatchResult(
                                confidence=confidence,
                                bbox=BoundingBox(
                                    x=x_min,
                                    y=y_min,
                                    width=x_max - x_min,
                                    height=y_max - y_min,
                                ),
                                center=Point(
                                    x=(x_min + x_max) // 2,
                                    y=(y_min + y_max) // 2,
                                ),
                            )
                        )

            duration_ms = int((time.time() - start_time) * 1000)

            return ImageMatchResult(
                status="success",
                matches=result_matches,
                duration_ms=duration_ms,
            )

        except Exception as e:
            duration_ms = int((time.time() - start_time) * 1000)
            return ImageMatchResult(
                status="error",
                matches=[],
                duration_ms=duration_ms,
                error=str(e),
            )

    def match(
        self,
        source_data: bytes | str,
        template_data: bytes | str,
        threshold: Optional[float] = None,
        method: Optional[str] = None,
        multi_target: bool = False,
    ) -> ImageMatchResult:
        """
        通用匹配方法。

        Args:
            source_data: 源图像（大图）。
            template_data: 模板图像（小图）。
            threshold: 匹配阈值。
            method: 匹配方法，支持 template（精确）和 feature（特征）。
            multi_target: 是否多目标匹配。

        Returns:
            ImageMatchResult: 匹配结果。
        """
        method = method or self.config.default_match_method

        if method == "feature":
            return self.match_feature(source_data, template_data, threshold)
        elif multi_target:
            return self.match_all(source_data, template_data, threshold)
        else:
            return self.match_template(source_data, template_data, threshold)


# 全局匹配器实例
_matcher: Optional[ImageMatcher] = None


def get_image_matcher() -> ImageMatcher:
    """获取全局图像匹配器实例。"""
    global _matcher
    if _matcher is None:
        _matcher = ImageMatcher()
    return _matcher