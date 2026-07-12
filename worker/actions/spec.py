"""Action 统一能力描述和可取消执行控制。"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable


class ActionCancelled(Exception):
    """Action 被任务取消。"""


class ActionTimedOut(Exception):
    """Action 达到任务或动作截止时间。"""


@dataclass
class ExecutionControl:
    """为 Action 提供统一的剩余时间和取消检查。"""

    deadline_monotonic: float | None = None
    cancel_event: threading.Event = field(default_factory=threading.Event)

    def checkpoint(self) -> None:
        """在阻塞操作前后检查取消和截止时间。"""
        if self.cancel_event.is_set():
            raise ActionCancelled("Task cancelled by user")
        if self.deadline_monotonic is not None and time.monotonic() >= self.deadline_monotonic:
            raise ActionTimedOut("Task timeout")

    def remaining_seconds(self) -> float | None:
        """返回剩余秒数。"""
        self.checkpoint()
        if self.deadline_monotonic is None:
            return None
        return max(0.0, self.deadline_monotonic - time.monotonic())

    def wait(self, seconds: float) -> None:
        """可取消地等待指定秒数。"""
        if seconds <= 0:
            self.checkpoint()
            return
        remaining = self.remaining_seconds()
        timeout = seconds if remaining is None else min(seconds, remaining)
        if self.cancel_event.wait(timeout):
            raise ActionCancelled("Task cancelled by user")
        self.checkpoint()


@dataclass(frozen=True)
class ActionSpec:
    """单个 Action 的能力描述。"""

    name: str
    executor: Any
    supported_platforms: frozenset[str] = frozenset()
    requires_context: bool = True
    requires_device_service: bool = False
    default_timeout_ms: int = 30000
    interruptible: bool = True
    security_level: str = "normal"
    validator: Callable[[Any], None] | None = None
