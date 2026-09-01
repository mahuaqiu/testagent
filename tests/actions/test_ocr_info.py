"""OCR/图像识别 Action 的 ocr_info 返回测试。"""

from types import SimpleNamespace

from worker.actions.position import OcrGetPositionExecutor
from worker.config import PlatformConfig
from worker.platforms.base import PlatformManager
from worker.task import Action, ActionStatus


class _FakeOcrClient:
    def __init__(self) -> None:
        self.ocr_info = [{"text": "设置", "confidence": 0.99}]

    def get_last_ocr_info(self) -> list[dict]:
        return self.ocr_info


class _FakePlatform(PlatformManager):
    """只实现位置 Action 测试需要的最小平台能力。"""

    def __init__(self, ocr_client=None) -> None:
        super().__init__(PlatformConfig(), ocr_client=ocr_client)

    @property
    def platform(self) -> str:
        return "test"

    def start(self) -> None:
        pass

    def stop(self) -> None:
        pass

    def is_available(self) -> bool:
        return True

    def create_context(self, device_id=None, options=None):
        return SimpleNamespace()

    def close_context(self, context, close_session=False) -> None:
        pass

    def execute_action(self, context, action):
        raise NotImplementedError

    def get_screenshot(self, context) -> bytes:
        return b"screenshot"

    def click(self, x, y, duration=0, context=None) -> None:
        pass

    def double_click(self, x, y, context=None) -> None:
        pass

    def move(self, x, y, context=None) -> None:
        pass

    def input_text(self, text, context=None) -> None:
        pass

    def press(self, key, context=None) -> None:
        pass

    def swipe(self, start_x, start_y, end_x, end_y, duration=500, steps=None, context=None) -> None:
        pass

    def take_screenshot(self, context=None) -> bytes:
        return b"screenshot"

    def _find_all_text_positions(self, image_bytes, text):
        return [(10, 20)]

    def _find_all_image_positions(self, source_bytes, template_base64, threshold=0.9):
        return [(30, 40)]


def test_ocr_get_position_returns_ocr_info() -> None:
    client = _FakeOcrClient()
    platform = _FakePlatform(client)

    result = OcrGetPositionExecutor().execute(
        platform,
        Action.from_dict({"action_type": "ocr_get_position", "value": "设置"}),
    )

    assert result.status == ActionStatus.SUCCESS
    assert result.output == {"positions": [[10, 20]]}
    assert result.ocr_info == client.ocr_info
