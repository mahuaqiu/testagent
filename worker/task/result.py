"""
任务结果模型。
"""

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import List, Optional, Dict, Any


class TaskStatus(str, Enum):
    """任务状态。"""

    PENDING = "pending"
    RUNNING = "running"
    SUCCESS = "success"
    FAILED = "failed"
    TIMEOUT = "timeout"
    CANCELLED = "cancelled"


class ActionStatus(str, Enum):
    """动作状态。"""

    PENDING = "pending"
    RUNNING = "running"
    SUCCESS = "success"
    FAILED = "failed"
    SKIPPED = "skipped"


@dataclass
class ActionResult:
    """单个动作执行结果。"""

    number: int  # 动作在任务列表中的序号（第几个动作）
    action_type: str
    status: ActionStatus
    duration_ms: int = 0
    output: Optional[str] = None
    error: Optional[str] = None
    screenshot: Optional[str] = None  # base64 或文件路径
    context: Any = None  # 执行后更新的 context（如 start_app 后返回新 page）

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ActionResult":
        """从字典创建。"""
        return cls(
            number=data.get("number", 0),
            action_type=data.get("action_type", ""),
            status=ActionStatus(data.get("status", "pending")),
            duration_ms=data.get("duration_ms", 0),
            output=data.get("output"),
            error=data.get("error"),
            screenshot=data.get("screenshot"),
            context=data.get("context"),
        )

    def to_dict(self) -> Dict[str, Any]:
        """转换为字典。"""
        result = {
            "number": self.number,
            "action_type": self.action_type,
            "status": self.status.value,
            "duration_ms": self.duration_ms,
        }
        if self.output is not None:
            result["output"] = self.output
        if self.error is not None:
            result["error"] = self.error
        if self.screenshot is not None:
            result["screenshot"] = self.screenshot
        # context 不需要序列化到结果中
        return result


@dataclass
class ScreenshotInfo:
    """截图信息。"""

    name: str
    action_number: int  # 对应的动作序号
    data: Optional[str] = None  # base64 数据
    path: Optional[str] = None  # 文件路径

    def to_dict(self) -> Dict[str, Any]:
        """转换为字典。"""
        result = {
            "name": self.name,
            "action_number": self.action_number,
        }
        if self.data is not None:
            result["data"] = self.data
        if self.path is not None:
            result["path"] = self.path
        return result


@dataclass
class TaskResult:
    """
    任务执行结果。
    """

    task_id: Optional[str] = None  # 可选，同步执行不生成 task_id
    status: TaskStatus = TaskStatus.PENDING
    platform: str = ""

    started_at: Optional[datetime] = None
    finished_at: Optional[datetime] = None
    duration_ms: int = 0

    actions: List[ActionResult] = field(default_factory=list)
    screenshots: List[ScreenshotInfo] = field(default_factory=list)

    error: Optional[str] = None
    error_screenshot: Optional[str] = None  # 失败截图（base64 编码）
    metadata: Dict[str, Any] = field(default_factory=dict)

    @property
    def success(self) -> bool:
        """任务是否成功。"""
        return self.status == TaskStatus.SUCCESS

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "TaskResult":
        """从字典创建。"""
        return cls(
            task_id=data.get("task_id", ""),
            status=TaskStatus(data.get("status", "pending")),
            platform=data.get("platform", ""),
            started_at=datetime.fromisoformat(data["started_at"]) if data.get("started_at") else None,
            finished_at=datetime.fromisoformat(data["finished_at"]) if data.get("finished_at") else None,
            duration_ms=data.get("duration_ms", 0),
            actions=[ActionResult.from_dict(a) for a in data.get("actions", [])],
            screenshots=[ScreenshotInfo(**s) for s in data.get("screenshots", [])],
            error=data.get("error"),
            error_screenshot=data.get("error_screenshot"),
            metadata=data.get("metadata", {}),
        )

    def to_dict(self, include_task_id: bool = True) -> Dict[str, Any]:
        """转换为字典。

        Args:
            include_task_id: 是否包含 task_id 字段，默认 True
        """
        result = {
            "status": self.status.value,
            "platform": self.platform,
            "duration_ms": self.duration_ms,
            "actions": [a.to_dict() for a in self.actions],
            "screenshots": [s.to_dict() for s in self.screenshots],
            "metadata": self.metadata,
        }

        # task_id 可选输出
        if include_task_id and self.task_id:
            result["task_id"] = self.task_id

        if self.started_at is not None:
            result["started_at"] = self.started_at.isoformat()
        if self.finished_at is not None:
            result["finished_at"] = self.finished_at.isoformat()
        if self.error is not None:
            result["error"] = self.error
        if self.error_screenshot is not None:
            result["error_screenshot"] = self.error_screenshot

        return result