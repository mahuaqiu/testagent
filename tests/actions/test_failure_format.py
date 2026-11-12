"""format_find_failure 文案与类型测试。"""

from worker.actions.failure import format_find_failure


def test_below_threshold_message_with_numbers():
    msg, err_type = format_find_failure(
        {"status": "success", "not_found_reason": "below_threshold", "best_confidence": 0.72},
        target="images/web/会中_挂断.png", threshold=0.9, kind="image",
    )
    assert msg == '未找到目标图片"images/web/会中_挂断.png"：最高相似度 0.72，低于阈值 0.90'
    assert err_type == "相似度不足"


def test_color_check_failed():
    msg, err_type = format_find_failure(
        {"status": "success", "not_found_reason": "color_check_failed", "best_confidence": 0.95},
        target="tpl.png", threshold=0.9, kind="image",
    )
    assert "灰度相似度 0.95 达标" in msg and "颜色校验未通过" in msg
    assert err_type == "颜色校验未通过"


def test_text_not_found():
    msg, err_type = format_find_failure(
        {"status": "success", "not_found_reason": "text_not_found"},
        target="密码", kind="text",
    )
    assert msg == '未找到文本 "密码"'
    assert err_type == "文本未找到"


def test_no_text_recognized():
    msg, err_type = format_find_failure(
        {"status": "success", "not_found_reason": "no_text_recognized"},
        target="密码", kind="text",
    )
    assert "屏幕上未识别到任何文字" in msg
    assert err_type == "文本未找到"


def test_distance_exceeded():
    msg, err_type = format_find_failure(
        {"status": "success", "not_found_reason": "distance_exceeded", "nearest_distance": 812.4},
        target="btn.png", kind="image",
    )
    assert "812" in msg and "距离超限" == err_type


def test_service_error():
    msg, err_type = format_find_failure(
        {"status": "error", "error": "模型加载失败"}, target="密码", kind="text",
    )
    assert msg == "OCR 服务错误: 模型加载失败"
    assert err_type == "OCR服务错误"


def test_http_error():
    msg, err_type = format_find_failure(
        {"status": "error", "error": "Connection refused", "http_status": 503},
        target="密码", kind="text",
    )
    assert "HTTP 503" in msg
    assert err_type == "OCR请求失败"


def test_index_suffix():
    msg, _ = format_find_failure(
        {"status": "success", "not_found_reason": "text_not_found"},
        target="密码", index=2, kind="text",
    )
    assert msg.endswith(" at index 2")


def test_none_failure_fallback():
    """拿不到诊断时文本类兜底为"文本未找到"（确实没找到，原因未知）。"""
    msg, err_type = format_find_failure(None, target="密码", kind="text")
    assert "密码" in msg
    assert err_type == "文本未找到"


def test_find_image_position_logs_structured_summary(caplog):
    """find 助手失败日志是一行结构化摘要，不再倾倒 ocr_response 整包。"""
    import logging
    from types import SimpleNamespace

    from worker.actions.image import ImageClickAction

    class _StubClient:
        def get_last_failure(self):
            return {"status": "success", "not_found_reason": "below_threshold",
                    "best_confidence": 0.72, "error": None}

        def get_last_ocr_info(self):
            return []

    class _StubPlatform:
        ocr_client = _StubClient()

        def _find_image_position(self, *args, **kwargs):
            return None

    executor = ImageClickAction()
    with caplog.at_level(logging.WARNING):
        assert executor._find_image_position(_StubPlatform(), b"src", b"tpl", 0.9, 0) is None

    joined = " ".join(r.getMessage() for r in caplog.records)
    assert "reason=below_threshold" in joined
    assert "best=0.72" in joined
    assert "ocr_response" not in joined
