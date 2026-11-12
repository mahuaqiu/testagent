"""OCRClient.last_failure 线程本地诊断测试。"""

import base64
import io

import httpx
from PIL import Image

from common.ocr_client import OCRClient


def _jpeg_b64() -> str:
    """真实 JPEG 字节，避免 _prepare_ocr_source 重编码分支出错。"""
    buf = io.BytesIO()
    Image.new("RGB", (8, 8), (255, 255, 255)).save(buf, format="JPEG")
    return base64.b64encode(buf.getvalue()).decode()


def _make_client(handler) -> OCRClient:
    client = OCRClient(base_url="http://test", timeout=5000, retry=0)
    client._client = httpx.Client(
        transport=httpx.MockTransport(handler), timeout=5, trust_env=False
    )
    return client


def test_service_error_recorded():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"status": "error", "error": "模型加载失败"})

    client = _make_client(handler)
    assert client.recognize(b"fake") == []
    failure = client.get_last_failure()
    assert failure["status"] == "error"
    assert failure["error"] == "模型加载失败"


def test_not_found_reason_recorded():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={
            "status": "success", "matches": [],
            "not_found_reason": "below_threshold", "best_confidence": 0.7231,
        })

    client = _make_client(handler)
    assert client.match_image(b"src", b"tpl", threshold=0.9) == []
    failure = client.get_last_failure()
    assert failure["not_found_reason"] == "below_threshold"
    assert failure["best_confidence"] == 0.7231


def test_http_error_records_status():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, json={"detail": "unavailable"})

    client = _make_client(handler)
    assert client.recognize(b"fake") == []
    failure = client.get_last_failure()
    assert failure["status"] == "error"
    assert failure["http_status"] == 503


def test_success_resets_failure():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={
            "status": "success",
            "texts": [{"text": "a", "confidence": 0.9,
                       "bbox": [[0, 0], [1, 0], [1, 1], [0, 1]],
                       "center": {"x": 0, "y": 0}}],
        })

    client = _make_client(handler)
    client.match_image(b"src", b"tpl")  # 先制造一次失败
    client.recognize(base64.b64decode(_jpeg_b64()))
    assert client.get_last_failure() is None
