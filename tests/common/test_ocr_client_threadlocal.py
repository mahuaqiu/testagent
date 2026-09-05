"""OCR 客户端线程隔离缓存测试。"""

import threading

from PIL import Image
from io import BytesIO

from common.ocr_client import OCRClient


def _tiny_jpeg() -> bytes:
    img = Image.new("RGB", (8, 8), "white")
    buf = BytesIO()
    img.save(buf, format="JPEG")
    return buf.getvalue()


def _make_client() -> OCRClient:
    return OCRClient(base_url="http://127.0.0.1:1", timeout=1000, retry=0)


def test_last_results_are_thread_isolated():
    """并发线程各自 recognize 后，"最后一次结果"不得跨线程串读。"""
    client = _make_client()
    jpeg = _tiny_jpeg()
    barrier = threading.Barrier(2)

    payloads = {
        "A": [{"text": "线程A文本", "confidence": 0.9, "bbox": [[0, 0], [1, 1], [1, 0], [0, 1]],
               "center": {"x": 1, "y": 1}}],
        "B": [{"text": "线程B文本", "confidence": 0.9, "bbox": [[0, 0], [1, 1], [1, 0], [0, 1]],
               "center": {"x": 2, "y": 2}}],
    }
    observed = {}

    def run(tag):
        response = {"status": "success", "texts": payloads[tag]}
        client._post = lambda path, data: response
        client.recognize(jpeg)
        barrier.wait(timeout=2)
        observed[tag] = [block.text for block in client.get_last_ocr_results()]

    threads = [threading.Thread(target=run, args=(tag,)) for tag in ("A", "B")]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=5)

    assert observed["A"] == ["线程A文本"]
    assert observed["B"] == ["线程B文本"]


def test_last_response_defaults_to_empty_dict_per_thread():
    client = _make_client()
    assert client.get_last_response() == {}
    client._post = lambda path, data: {"status": "error", "error": "boom"}
    # 不同线程互不影响
    client._last_response = {"status": "success"}
    results_holder = {}

    def check_other_thread():
        results_holder["response"] = client.get_last_response()

    t = threading.Thread(target=check_other_thread)
    t.start()
    t.join(timeout=2)
    assert results_holder["response"] == {}
