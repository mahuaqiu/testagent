"""
任务模型和任务队列。

定义 Task、Action 等核心数据结构，以及 TaskQueue 任务队列。
"""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Optional
from collections import deque
import uuid
import time


@dataclass
class TaskConfig:
    """任务配置。"""

    timeout: int = 30000  # 整体超时（毫秒）
    screenshot_on_error: bool = True  # 失败时截图
    screenshot_on_success: bool = False  # 成功时截图
    slow_motion: int = 0  # 慢动作延迟（毫秒）
    retry_on_failure: int = 0  # 失败重试次数
    wait_after_action: int = 0  # 每个动作后等待时间（毫秒）


@dataclass
class Action:
    """
    单个操作动作。

    Attributes:
        action_type: 动作类型，如 navigate/click/fill/wait/assert/screenshot/select/hover/press
        selector: 元素选择器（click/fill/assert 等需要）
        value: 输入值（fill 需要文本，navigate 需要 URL，select 需要选项）
        wait: 等待时间（毫秒），用于 wait 动作或动作后等待
        expect: 期望结果，用于断言动作
        screenshot: 是否在该动作后截图
        timeout: 该动作的超时时间（毫秒）
    """

    action_type: str
    selector: Optional[str] = None
    value: Optional[str] = None
    wait: Optional[int] = None
    expect: Optional[str] = None
    screenshot: bool = False
    timeout: Optional[int] = None

    def to_dict(self) -> dict:
        """转换为字典。"""
        return {
            "action_type": self.action_type,
            "selector": self.selector,
            "value": self.value,
            "wait": self.wait,
            "expect": self.expect,
            "screenshot": self.screenshot,
            "timeout": self.timeout,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "Action":
        """从字典创建。"""
        return cls(
            action_type=data.get("action_type", ""),
            selector=data.get("selector"),
            value=data.get("value"),
            wait=data.get("wait"),
            expect=data.get("expect"),
            screenshot=data.get("screenshot", False),
            timeout=data.get("timeout"),
        )


@dataclass
class Task:
    """
    测试任务。

    Attributes:
        task_id: 任务唯一标识
        user_id: 执行用户标识
        actions: 动作列表
        config: 任务配置
        created_at: 创建时间
        priority: 优先级（越大越优先）
        callback_url: 回调地址（可选）
        metadata: 扩展元数据
    """

    task_id: str = field(default_factory=lambda: f"task_{timestamp()}_{uuid.uuid4().hex[:8]}")
    user_id: str = ""
    actions: list[Action] = field(default_factory=list)
    config: TaskConfig = field(default_factory=TaskConfig)
    created_at: datetime = field(default_factory=datetime.now)
    priority: int = 0
    callback_url: Optional[str] = None
    metadata: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        """转换为字典。"""
        return {
            "task_id": self.task_id,
            "user_id": self.user_id,
            "actions": [a.to_dict() for a in self.actions],
            "config": {
                "timeout": self.config.timeout,
                "screenshot_on_error": self.config.screenshot_on_error,
                "screenshot_on_success": self.config.screenshot_on_success,
                "slow_motion": self.config.slow_motion,
                "retry_on_failure": self.config.retry_on_failure,
                "wait_after_action": self.config.wait_after_action,
            },
            "created_at": self.created_at.isoformat(),
            "priority": self.priority,
            "callback_url": self.callback_url,
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "Task":
        """从字典创建。"""
        config_data = data.get("config", {})
        config = TaskConfig(
            timeout=config_data.get("timeout", 30000),
            screenshot_on_error=config_data.get("screenshot_on_error", True),
            screenshot_on_success=config_data.get("screenshot_on_success", False),
            slow_motion=config_data.get("slow_motion", 0),
            retry_on_failure=config_data.get("retry_on_failure", 0),
            wait_after_action=config_data.get("wait_after_action", 0),
        )

        actions = [Action.from_dict(a) for a in data.get("actions", [])]

        created_at = data.get("created_at")
        if isinstance(created_at, str):
            created_at = datetime.fromisoformat(created_at)
        elif created_at is None:
            created_at = datetime.now()

        return cls(
            task_id=data.get("task_id", f"task_{timestamp()}_{uuid.uuid4().hex[:8]}"),
            user_id=data.get("user_id", ""),
            actions=actions,
            config=config,
            created_at=created_at,
            priority=data.get("priority", 0),
            callback_url=data.get("callback_url"),
            metadata=data.get("metadata", {}),
        )


class TaskQueue:
    """
    任务队列（FIFO，支持优先级）。

    使用 deque 实现简单高效的队列操作。
    """

    def __init__(self):
        self._queue: deque[Task] = deque()
        self._results: dict[str, "TaskResult"] = {}  # task_id -> result

    def push(self, task: Task) -> None:
        """入队。"""
        # 按优先级插入（简化实现，优先级高的在前）
        if task.priority > 0:
            # 找到合适的位置插入
            inserted = False
            for i, t in enumerate(self._queue):
                if task.priority > t.priority:
                    self._queue.insert(i, task)
                    inserted = True
                    break
            if not inserted:
                self._queue.append(task)
        else:
            self._queue.append(task)

    def pop(self) -> Optional[Task]:
        """出队。"""
        if self._queue:
            return self._queue.popleft()
        return None

    def peek(self) -> Optional[Task]:
        """查看队首（不移除）。"""
        if self._queue:
            return self._queue[0]
        return None

    def is_empty(self) -> bool:
        """队列是否为空。"""
        return len(self._queue) == 0

    def size(self) -> int:
        """队列大小。"""
        return len(self._queue)

    def store_result(self, result: "TaskResult") -> None:
        """存储任务结果。"""
        self._results[result.task_id] = result

    def get_result(self, task_id: str) -> Optional["TaskResult"]:
        """获取任务结果。"""
        return self._results.get(task_id)

    def clear_results(self) -> None:
        """清空结果缓存。"""
        self._results.clear()


def timestamp() -> str:
    """返回时间戳字符串，格式: 20240101_120000。"""
    return time.strftime("%Y%m%d_%H%M%S")