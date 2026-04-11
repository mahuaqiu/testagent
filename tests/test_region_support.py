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
