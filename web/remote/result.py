"""
任务结果模型和结果构建器。

定义 TaskResult、ActionResult 等结果数据结构。
"""

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Optional
import base64
import time

from web.remote.task import Action
from web.remote.logger import LogEntry


class TaskStatus(str, Enum):
    """任务状态枚举。"""

    PENDING = "pending"
    RUNNING = "running"
    SUCCESS = "success"
    FAILED = "failed"
    TIMEOUT = "timeout"
    CANCELLED = "cancelled"


@dataclass
class Screenshot:
    """
    截图数据。

    Attributes:
        name: 截图名称
        data: 图片二进制数据
        timestamp: 截图时间
        action_index: 对应的动作索引（可选）
    """

    name: str
    data: bytes
    timestamp: datetime = field(default_factory=datetime.now)
    action_index: Optional[int] = None

    def to_dict(self, include_data: bool = True) -> dict:
        """转换为字典。

        Args:
            include_data: 是否包含图片数据（Base64 编码）
        """
        result = {
            "name": self.name,
            "timestamp": self.timestamp.isoformat(),
            "action_index": self.action_index,
        }
        if include_data:
            result["data"] = base64.b64encode(self.data).decode("utf-8")
        return result


@dataclass
class ActionResult:
    """
    单个动作的执行结果。

    Attributes:
        action: 原始动作
        status: 执行状态 (success/failed)
        output: 输出值（如 get_text 的结果）
        screenshot: 该动作的截图
        duration_ms: 耗时（毫秒）
        error: 错误信息
        index: 动作在任务中的索引
    """

    action: Action
    status: str = "success"
    output: Any = None
    screenshot: Optional[bytes] = None
    duration_ms: int = 0
    error: Optional[str] = None
    index: int = 0

    def to_dict(self, include_screenshot: bool = True) -> dict:
        """转换为字典。"""
        result = {
            "index": self.index,
            "action_type": self.action.action_type,
            "selector": self.action.selector,
            "value": self.action.value,
            "status": self.status,
            "output": self.output,
            "duration_ms": self.duration_ms,
            "error": self.error,
        }
        if include_screenshot and self.screenshot:
            result["screenshot"] = base64.b64encode(self.screenshot).decode("utf-8")
        return result


@dataclass
class TaskResult:
    """
    任务执行结果。

    Attributes:
        task_id: 任务 ID
        status: 任务状态
        actions: 每个动作的执行结果
        screenshots: 截图列表
        logs: 操作日志
        error: 错误信息
        started_at: 开始时间
        finished_at: 结束时间
        duration_ms: 总耗时（毫秒）
        metadata: 扩展字段
    """

    task_id: str
    status: TaskStatus = TaskStatus.PENDING
    actions: list[ActionResult] = field(default_factory=list)
    screenshots: list[Screenshot] = field(default_factory=list)
    logs: list[LogEntry] = field(default_factory=list)
    error: Optional[str] = None
    started_at: Optional[datetime] = None
    finished_at: Optional[datetime] = None
    duration_ms: int = 0
    metadata: dict = field(default_factory=dict)

    def to_dict(self, include_screenshots: bool = True) -> dict:
        """转换为字典。"""
        return {
            "task_id": self.task_id,
            "status": self.status.value,
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "finished_at": self.finished_at.isoformat() if self.finished_at else None,
            "duration_ms": self.duration_ms,
            "actions": [a.to_dict(include_screenshot=False) for a in self.actions],
            "screenshots": [s.to_dict(include_data=include_screenshots) for s in self.screenshots]
            if include_screenshots
            else [],
            "logs": [l.to_dict() for l in self.logs],
            "error": self.error,
            "metadata": self.metadata,
        }

    def to_json(self) -> str:
        """转换为 JSON 字符串。"""
        import json

        return json.dumps(self.to_dict(), ensure_ascii=False, indent=2)


class ResultBuilder:
    """
    结果构建器。

    用于在任务执行过程中逐步构建 TaskResult。
    """

    def __init__(self, task_id: str):
        self._task_id = task_id
        self._status = TaskStatus.PENDING
        self._actions: list[ActionResult] = []
        self._screenshots: list[Screenshot] = []
        self._logs: list[LogEntry] = []
        self._error: Optional[str] = None
        self._started_at: Optional[datetime] = None
        self._finished_at: Optional[datetime] = None
        self._metadata: dict = {}
        self._action_index = 0

    def start_task(self) -> None:
        """开始任务。"""
        self._started_at = datetime.now()
        self._status = TaskStatus.RUNNING

    def add_action_result(self, result: ActionResult) -> None:
        """添加动作结果。"""
        result.index = self._action_index
        self._actions.append(result)
        self._action_index += 1

    def add_screenshot(self, name: str, data: bytes, action_index: Optional[int] = None) -> None:
        """添加截图。"""
        self._screenshots.append(
            Screenshot(name=name, data=data, action_index=action_index)
        )

    def add_log(
        self,
        action: str,
        detail: dict,
        level: str = "info",
        duration_ms: Optional[int] = None,
    ) -> None:
        """添加日志。"""
        self._logs.append(
            LogEntry(
                timestamp=datetime.now(),
                level=level,
                action=action,
                detail=detail,
                duration_ms=duration_ms,
            )
        )

    def set_error(self, error: str) -> None:
        """设置错误信息。"""
        self._error = error

    def set_metadata(self, key: str, value: Any) -> None:
        """设置元数据。"""
        self._metadata[key] = value

    def finish_task(self, status: TaskStatus = TaskStatus.SUCCESS) -> TaskResult:
        """完成任务并构建结果。"""
        self._finished_at = datetime.now()
        self._status = status

        # 计算总耗时
        duration_ms = 0
        if self._started_at and self._finished_at:
            duration_ms = int((self._finished_at - self._started_at).total_seconds() * 1000)

        return TaskResult(
            task_id=self._task_id,
            status=self._status,
            actions=self._actions,
            screenshots=self._screenshots,
            logs=self._logs,
            error=self._error,
            started_at=self._started_at,
            finished_at=self._finished_at,
            duration_ms=duration_ms,
            metadata=self._metadata,
        )