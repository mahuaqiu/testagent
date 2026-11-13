"""Worker 失败截图 helper 测试。

create_context 失败时也要有系统截图（挂到 TaskResult.error_screenshot 与 artifacts），
便于报告诊断——系统级截图不依赖 Playwright/设备连接，浏览器已关闭时仍然可用。
"""

import base64
from unittest.mock import MagicMock

from common.utils import compress_image_to_jpeg
from worker.task import Task
from worker.worker import Worker


def _make_worker(artifact_service: MagicMock) -> Worker:
    """绕过 __init__ 构造最小 Worker，只注入 artifact_service。"""
    worker = Worker.__new__(Worker)
    worker.artifact_service = artifact_service
    return worker


def _make_task() -> Task:
    return Task(task_id="task-1", platform="web", actions=[])


# 1x1 像素 PNG（最小合法图片，压缩器可处理）
_PNG_1PX = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
)


def _make_manager(screenshot_bytes: bytes) -> MagicMock:
    manager = MagicMock()
    manager.get_screenshot.return_value = screenshot_bytes
    return manager


def test_capture_error_screenshot_success() -> None:
    """截图与保存成功：返回 base64 与 artifact 引用。"""
    artifact_service = MagicMock()
    artifact_service.save_bytes.return_value.to_dict.return_value = {"id": "art-1"}
    worker = _make_worker(artifact_service)
    manager = _make_manager(_PNG_1PX)

    error_screenshot, artifacts = worker._capture_error_screenshot(manager, _make_task())

    assert error_screenshot is not None
    assert base64.b64decode(error_screenshot) == compress_image_to_jpeg(_PNG_1PX, quality=80)
    assert artifacts == [{"id": "art-1"}]
    # 系统级截图：不依赖 context
    manager.get_screenshot.assert_called_once_with(None)


def test_capture_error_screenshot_keeps_context_for_platform() -> None:
    """动作失败路径传入 context：保持原有行为（移动端需要 context 定位设备）。"""
    worker = _make_worker(MagicMock())
    manager = _make_manager(_PNG_1PX)

    worker._capture_error_screenshot(manager, _make_task(), context="ctx-object")

    manager.get_screenshot.assert_called_once_with("ctx-object")


def test_capture_error_screenshot_empty_bytes_returns_none() -> None:
    """平台返回空截图（如 sidecar 失败返回 b""）：不抛错，返回 (None, [])。"""
    worker = _make_worker(MagicMock())
    manager = _make_manager(b"")

    error_screenshot, artifacts = worker._capture_error_screenshot(manager, _make_task())

    assert error_screenshot is None
    assert artifacts == []


def test_capture_error_screenshot_manager_raises() -> None:
    """平台截图抛错：吞掉异常，返回 (None, [])，不影响任务结果。"""
    worker = _make_worker(MagicMock())
    manager = MagicMock()
    manager.get_screenshot.side_effect = RuntimeError("System-level operations not available")

    error_screenshot, artifacts = worker._capture_error_screenshot(manager, _make_task())

    assert error_screenshot is None
    assert artifacts == []


def test_capture_error_screenshot_artifact_save_raises() -> None:
    """artifact 保存失败：吞掉异常，截图 base64 仍然返回。"""
    artifact_service = MagicMock()
    artifact_service.save_bytes.side_effect = RuntimeError("disk full")
    worker = _make_worker(artifact_service)
    manager = _make_manager(_PNG_1PX)

    error_screenshot, artifacts = worker._capture_error_screenshot(manager, _make_task())

    assert error_screenshot is not None
    assert artifacts == []
