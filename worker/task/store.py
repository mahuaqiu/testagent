"""
任务存储模块。

提供线程安全的内存任务存储，支持任务状态管理和冲突检测。
"""

import threading
from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, Optional, Any

from worker.task.task import Task
from worker.task.result import TaskResult, TaskStatus


@dataclass
class TaskEntry:
    """
    任务存储条目。

    存储在内存中的任务信息，包括任务定义、执行状态、结果和取消信号。

    Attributes:
        task_id: 任务唯一标识
        task: 任务定义
        status: 任务状态
        result: 执行结果（完成后填充）
        thread: 执行线程
        cancel_event: 取消信号事件
        created_at: 创建时间
    """

    task_id: str
    task: Task
    status: TaskStatus
    result: Optional[TaskResult] = None
    thread: Optional[threading.Thread] = None
    cancel_event: threading.Event = field(default_factory=threading.Event)
    request_id: str | None = None  # 用于异步任务传递 request-id
    created_at: datetime = field(default_factory=datetime.now)

    def to_dict(self) -> Dict[str, Any]:
        """转换为字典（用于 API 返回）。"""
        data = {
            "task_id": self.task_id,
            "status": self.status.value,
        }
        if self.result:
            data["platform"] = self.result.platform
            data["duration_ms"] = self.result.duration_ms
            data["actions"] = [a.to_dict() for a in self.result.actions]
            if self.result.error:
                data["error"] = self.result.error
            if self.result.error_screenshot:
                data["error_screenshot"] = self.result.error_screenshot
        return data


class TaskStore:
    """
    线程安全的内存任务存储。

    管理异步任务的生命周期，支持任务存储、查询、删除和冲突检测。

    Attributes:
        _tasks: 任务字典（task_id -> TaskEntry）
        _lock: 线程锁
        _busy: 忙碌索引（key -> task_id），用于快速检测冲突
    """

    def __init__(self):
        """初始化任务存储。"""
        self._tasks: Dict[str, TaskEntry] = {}
        self._lock = threading.Lock()
        # 忙碌索引：key 为 platform 或 device_id，value 为 task_id
        self._busy: Dict[str, str] = {}

    def store(self, entry: TaskEntry) -> None:
        """
        存储任务。

        Args:
            entry: 任务条目
        """
        with self._lock:
            self._tasks[entry.task_id] = entry
            # 标记忙碌状态
            key = self._get_busy_key(entry.task.platform, entry.task.device_id)
            self._busy[key] = entry.task_id

    def get(self, task_id: str) -> Optional[TaskEntry]:
        """
        获取任务（不删除）。

        Args:
            task_id: 任务 ID

        Returns:
            任务条目，不存在则返回 None
        """
        with self._lock:
            return self._tasks.get(task_id)

    def pop(self, task_id: str) -> Optional[TaskEntry]:
        """
        获取并删除任务（一次性查询）。

        Args:
            task_id: 任务 ID

        Returns:
            任务条目，不存在则返回 None
        """
        with self._lock:
            entry = self._tasks.pop(task_id, None)
            if entry:
                # 清理忙碌索引
                key = self._get_busy_key(entry.task.platform, entry.task.device_id)
                self._busy.pop(key, None)
            return entry

    def remove(self, task_id: str) -> None:
        """
        删除任务。

        Args:
            task_id: 任务 ID
        """
        self.pop(task_id)

    def is_busy(self, platform: str, device_id: Optional[str]) -> bool:
        """
        检查平台/设备是否忙碌。

        Args:
            platform: 平台名称
            device_id: 设备 ID（可选）

        Returns:
            是否忙碌
        """
        with self._lock:
            key = self._get_busy_key(platform, device_id)
            return key in self._busy

    def get_busy_task_id(self, platform: str, device_id: Optional[str]) -> Optional[str]:
        """
        获取占用平台/设备的任务 ID。

        Args:
            platform: 平台名称
            device_id: 设备 ID（可选）

        Returns:
            任务 ID，无则返回 None
        """
        with self._lock:
            key = self._get_busy_key(platform, device_id)
            return self._busy.get(key)

    def clear_busy(self, platform: str, device_id: Optional[str]) -> None:
        """
        清除平台/设备的忙碌状态。

        Args:
            platform: 平台名称
            device_id: 设备 ID（可选）
        """
        with self._lock:
            key = self._get_busy_key(platform, device_id)
            self._busy.pop(key, None)

    def update_status(self, task_id: str, status: TaskStatus, result: Optional[TaskResult] = None) -> None:
        """
        更新任务状态。

        Args:
            task_id: 任务 ID
            status: 新状态
            result: 执行结果（可选）
        """
        with self._lock:
            entry = self._tasks.get(task_id)
            if entry:
                entry.status = status
                if result:
                    entry.result = result
                # 如果任务完成，清理忙碌状态
                if status in [TaskStatus.SUCCESS, TaskStatus.FAILED, TaskStatus.CANCELLED, TaskStatus.TIMEOUT]:
                    key = self._get_busy_key(entry.task.platform, entry.task.device_id)
                    self._busy.pop(key, None)

    def _get_busy_key(self, platform: str, device_id: Optional[str]) -> str:
        """
        生成忙碌索引的 key。

        规则：移动端使用 device_id，桌面端/Web 使用 platform。

        Args:
            platform: 平台名称
            device_id: 设备 ID（可选）

        Returns:
            索引 key
        """
        # 移动端按设备区分，桌面端/Web 按平台区分
        if device_id:
            return f"device:{device_id}"
        return f"platform:{platform}"