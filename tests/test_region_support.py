"""OCR/Image action region 参数支持测试。"""

from worker.task.action import Action


class TestActionRegionField:
    """测试 Action 模型中 region 字段。"""

    def test_region_from_dict(self):
        """测试从字典解析 region。"""
        action = Action.from_dict({
            "action_type": "ocr_click",
            "value": "测试文字",
            "region": [100, 200, 500, 600],
        })
        assert action.region == [100, 200, 500, 600]

    def test_region_default_none(self):
        """测试 region 默认为 None。"""
        action = Action.from_dict({"action_type": "ocr_click"})
        assert action.region is None

    def test_region_to_dict(self):
        """测试序列化为字典。"""
        action = Action(
            action_type="ocr_click",
            value="测试文字",
            region=[0, 0, 640, 360],
        )
        result = action.to_dict()
        assert result["region"] == [0, 0, 640, 360]

    def test_region_to_dict_omits_none(self):
        """测试 region 为 None 时不序列化。"""
        action = Action(action_type="ocr_click")
        result = action.to_dict()
        assert "region" not in result


import io
import pytest
from PIL import Image
from worker.actions.base import BaseActionExecutor


class _ConcreteExecutor(BaseActionExecutor):
    """具体测试用执行器，实现抽象 execute 方法。"""
    name = "_test"
    def execute(self, platform, action, context=None):
        pass


class TestRegionCrop:
    """测试 region 裁剪逻辑。"""

    def test_crop_region(self):
        """测试按 region 裁剪图像。"""
        executor = _ConcreteExecutor()
        # 创建 200x200 红色图像
        img = Image.new("RGB", (200, 200), color="red")
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        image_bytes = buf.getvalue()

        # 裁剪右下区域 [100, 100, 200, 200]
        cropped = executor._crop_region(image_bytes, [100, 100, 200, 200])
        cropped_img = Image.open(io.BytesIO(cropped))
        assert cropped_img.size == (100, 100)

    def test_crop_region_returns_bytes(self):
        """测试裁剪后返回 bytes 类型。"""
        executor = _ConcreteExecutor()
        img = Image.new("RGB", (100, 100), color="blue")
        buf = io.BytesIO()
        img.save(buf, format="PNG")

        result = executor._crop_region(buf.getvalue(), [0, 0, 50, 50])
        assert isinstance(result, bytes)

    def test_crop_invalid_region_raises(self):
        """测试无效 region 抛出异常。"""
        executor = _ConcreteExecutor()
        img = Image.new("RGB", (100, 100), color="green")
        buf = io.BytesIO()
        img.save(buf, format="PNG")

        with pytest.raises(ValueError):
            executor._crop_region(buf.getvalue(), [100, 0, 50, 50])  # x1 > x2


class TestRegionOffset:
    """测试 region 坐标偏移逻辑。"""

    def test_offset_position(self):
        """测试坐标偏移计算。"""
        executor = _ConcreteExecutor()
        result = executor._offset_position((50, 30), [100, 200, 500, 600])
        assert result == (150, 230)

    def test_offset_position_zero_region(self):
        """测试零偏移 region。"""
        executor = _ConcreteExecutor()
        result = executor._offset_position((50, 30), [0, 0, 640, 480])
        assert result == (50, 30)
