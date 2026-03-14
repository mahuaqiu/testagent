"""
任务模型。
"""

import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import List, Optional, Dict, Any

from worker.task.action import Action
from worker.task.result import TaskResult, TaskStatus


@dataclass
class TaskConfig:
    """任务配置。"""

    timeout: int = 300000           # 总超时时间(ms)
    action_timeout: int = 30000     # 单个动作超时(ms)
    screenshot_on_error: bool = True
    slow_motion: int = 0            # 慢动作延迟(ms)
    retry_count: int = 0            # 失败重试次数

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "TaskConfig":
        """从字典创建。"""
        return cls(
            timeout=data.get("timeout", 300000),
            action_timeout=data.get("action_timeout", 30000),
            screenshot_on_error=data.get("screenshot_on_error", True),
            slow_motion=data.get("slow_motion", 0),
            retry_count=data.get("retry_count", 0),
        )


@dataclass
class Task:
    """
    任务模型。

    表示一个待执行的自动化测试任务。
    """

    task_id: str
    platform: str                          # web / android / ios / windows / mac
    actions: List[Action]
    device_id: Optional[str] = None        # 移动设备 UDID
    user_id: Optional[str] = None          # 用户标识
    session_id: Optional[str] = None       # 会话 ID（复用会话）
    config: TaskConfig = field(default_factory=TaskConfig)
    callback_url: Optional[str] = None     # 回调地址
    created_at: datetime = field(default_factory=datetime.now)
    priority: int = 0
    metadata: Dict[str, Any] = field(default_factory=dict)

    @classmethod
    def create(
        cls,
        platform: str,
        actions: List[Dict[str, Any]],
        device_id: Optional[str] = None,
        user_id: Optional[str] = None,
        session_id: Optional[str] = None,
        config: Optional[Dict[str, Any]] = None,
        callback_url: Optional[str] = None,
        priority: int = 0,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> "Task":
        """
        创建任务。

        Args:
            platform: 目标平台
            actions: 动作列表
            device_id: 设备 ID
            user_id: 用户 ID
            session_id: 会话 ID
            config: 任务配置
            callback_url: 回调地址
            priority: 优先级
            metadata: 元数据

        Returns:
            Task: 任务对象
        """
        task_id = f"task_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{str(uuid.uuid4())[:8]}"

        action_list = [Action.from_dict(a) for a in actions]

        task_config = TaskConfig.from_dict(config) if config else TaskConfig()

        return cls(
            task_id=task_id,
            platform=platform,
            actions=action_list,
            device_id=device_id,
            user_id=user_id,
            session_id=session_id,
            config=task_config,
            callback_url=callback_url,
            priority=priority,
            metadata=metadata or {},
        )

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Task":
        """从字典创建任务。"""
        return cls(
            task_id=data.get("task_id", str(uuid.uuid4())[:8]),
            platform=data.get("platform", ""),
            actions=[Action.from_dict(a) for a in data.get("actions", [])],
            device_id=data.get("device_id"),
            user_id=data.get("user_id"),
            session_id=data.get("session_id"),
            config=TaskConfig.from_dict(data.get("config", {})),
            callback_url=data.get("callback_url"),
            created_at=datetime.fromisoformat(data["created_at"]) if data.get("created_at") else datetime.now(),
            priority=data.get("priority", 0),
            metadata=data.get("metadata", {}),
        )

    def to_dict(self) -> Dict[str, Any]:
        """转换为字典。"""
        return {
            "task_id": self.task_id,
            "platform": self.platform,
            "actions": [a.to_dict() for a in self.actions],
            "device_id": self.device_id,
            "user_id": self.user_id,
            "session_id": self.session_id,
            "config": {
                "timeout": self.config.timeout,
                "action_timeout": self.config.action_timeout,
                "screenshot_on_error": self.config.screenshot_on_error,
                "slow_motion": self.config.slow_motion,
                "retry_count": self.config.retry_count,
            },
            "callback_url": self.callback_url,
            "created_at": self.created_at.isoformat(),
            "priority": self.priority,
            "metadata": self.metadata,
        }