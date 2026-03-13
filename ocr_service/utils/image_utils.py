"""
图像处理工具函数。
"""

import base64
from io import BytesIO
from typing import Optional, Tuple

import cv2
import numpy as np
from PIL import Image


def decode_image(image_data: bytes | str) -> np.ndarray:
    """解码图像数据为 OpenCV 格式。

    Args:
        image_data: 图像字节数据或 Base64 编码字符串。

    Returns:
        np.ndarray: OpenCV 图像数组 (BGR 格式)。
    """
    if isinstance(image_data, str):
        # Base64 解码
        image_data = base64.b64decode(image_data)

    # 转换为 numpy 数组
    np_array = np.frombuffer(image_data, np.uint8)
    image = cv2.imdecode(np_array, cv2.IMREAD_COLOR)

    if image is None:
        raise ValueError("Failed to decode image")

    return image


def encode_image(image: np.ndarray, format: str = ".png") -> str:
    """将 OpenCV 图像编码为 Base64 字符串。

    Args:
        image: OpenCV 图像数组。
        format: 图像格式，如 '.png', '.jpg'。

    Returns:
        str: Base64 编码的图像字符串。
    """
    success, buffer = cv2.imencode(format, image)
    if not success:
        raise ValueError("Failed to encode image")

    return base64.b64encode(buffer).decode("utf-8")


def resize_image(
    image: np.ndarray,
    max_width: Optional[int] = None,
    max_height: Optional[int] = None,
    scale: Optional[float] = None,
) -> np.ndarray:
    """调整图像大小。

    Args:
        image: OpenCV 图像数组。
        max_width: 最大宽度。
        max_height: 最大高度。
        scale: 缩放比例。

    Returns:
        np.ndarray: 调整后的图像。
    """
    if scale is not None:
        new_width = int(image.shape[1] * scale)
        new_height = int(image.shape[0] * scale)
        return cv2.resize(image, (new_width, new_height))

    if max_width is None and max_height is None:
        return image

    h, w = image.shape[:2]

    if max_width is not None and w > max_width:
        scale = max_width / w
        w = max_width
        h = int(h * scale)

    if max_height is not None and h > max_height:
        scale = max_height / h
        h = max_height
        w = int(w * scale)

    return cv2.resize(image, (w, h))


def convert_to_grayscale(image: np.ndarray) -> np.ndarray:
    """将图像转换为灰度图。

    Args:
        image: OpenCV 图像数组。

    Returns:
        np.ndarray: 灰度图像。
    """
    if len(image.shape) == 3:
        return cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    return image


def get_image_size(image: np.ndarray) -> Tuple[int, int]:
    """获取图像尺寸。

    Args:
        image: OpenCV 图像数组。

    Returns:
        Tuple[int, int]: (width, height)
    """
    h, w = image.shape[:2]
    return w, h