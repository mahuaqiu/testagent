"""error_type 字段序列化与任务透传测试。"""

from worker.task.result import ActionStatus, ActionResult, TaskResult, TaskStatus


def test_action_result_error_type_roundtrip():
    ar = ActionResult(number=0, action_type="image_click", status=ActionStatus.FAILED,
                      error="未找到目标图片", error_type="相似度不足")
    d = ar.to_dict()
    assert d["error_type"] == "相似度不足"
    assert ActionResult.from_dict(d).error_type == "相似度不足"


def test_action_result_none_error_type_not_serialized():
    ar = ActionResult(number=0, action_type="click", status=ActionStatus.SUCCESS)
    assert "error_type" not in ar.to_dict()


def test_task_result_error_type_roundtrip():
    tr = TaskResult(status=TaskStatus.FAILED, platform="windows", error="e", error_type="文本未找到")
    d = tr.to_dict()
    assert d["error_type"] == "文本未找到"
    assert TaskResult.from_dict(d).error_type == "文本未找到"
