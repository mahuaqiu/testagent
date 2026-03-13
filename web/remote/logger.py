"""
操作日志记录器。

记录每个操作的详细日志，支持结构化输出。
"""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional
import json


@dataclass
class LogEntry:
    """
    日志条目。

    Attributes:
        timestamp: 时间戳
        level: 日志级别 (info/warning/error/debug)
        action: 动作类型
        message: 日志消息
        detail: 详细信息
        duration_ms: 耗时（毫秒）
    """

    timestamp: datetime = field(default_factory=datetime.now)
    level: str = "info"
    action: str = ""
    message: str = ""
    detail: dict = field(default_factory=dict)
    duration_ms: Optional[int] = None

    def to_dict(self) -> dict:
        """转换为字典。"""
        return {
            "timestamp": self.timestamp.isoformat(),
            "level": self.level,
            "action": self.action,
            "message": self.message,
            "detail": self.detail,
            "duration_ms": self.duration_ms,
        }


class ActionLogger:
    """
    操作日志记录器。

    记录每个操作的详细日志，支持不同级别、结构化输出。

    Usage:
        logger = ActionLogger(session_id="session_001")
        logger.log_navigation("https://example.com", 200)
        logger.log_click("button.submit", success=True)
        logs = logger.get_logs()
    """

    def __init__(self, session_id: str = ""):
        self._session_id = session_id
        self._logs: list[LogEntry] = []

    @property
    def session_id(self) -> str:
        return self._session_id

    @session_id.setter
    def session_id(self, value: str) -> None:
        self._session_id = value

    def log(
        self,
        action: str,
        message: str = "",
        detail: dict = None,
        level: str = "info",
        duration_ms: Optional[int] = None,
    ) -> None:
        """
        记录日志。

        Args:
            action: 动作类型
            message: 日志消息
            detail: 详细信息
            level: 日志级别
            duration_ms: 耗时（毫秒）
        """
        if detail is None:
            detail = {}
        self._logs.append(
            LogEntry(
                timestamp=datetime.now(),
                level=level,
                action=action,
                message=message,
                detail=detail,
                duration_ms=duration_ms,
            )
        )

    def log_navigation(self, url: str, status: int = 200, duration_ms: Optional[int] = None) -> None:
        """记录导航日志。"""
        self.log(
            action="navigate",
            message=f"Navigated to {url}",
            detail={"url": url, "status": status},
            level="info",
            duration_ms=duration_ms,
        )

    def log_click(
        self,
        selector: str,
        success: bool = True,
        error: Optional[str] = None,
        duration_ms: Optional[int] = None,
    ) -> None:
        """记录点击日志。"""
        level = "info" if success else "error"
        message = f"Clicked {selector}" if success else f"Failed to click {selector}"
        detail = {"selector": selector, "success": success}
        if error:
            detail["error"] = error
        self.log(action="click", message=message, detail=detail, level=level, duration_ms=duration_ms)

    def log_fill(
        self,
        selector: str,
        value: str,
        success: bool = True,
        error: Optional[str] = None,
        duration_ms: Optional[int] = None,
    ) -> None:
        """记录填充日志。"""
        level = "info" if success else "error"
        # 隐藏敏感信息
        display_value = value if len(value) < 50 else value[:47] + "..."
        message = f"Filled {selector}" if success else f"Failed to fill {selector}"
        detail = {"selector": selector, "value_length": len(value), "success": success}
        if error:
            detail["error"] = error
        self.log(action="fill", message=message, detail=detail, level=level, duration_ms=duration_ms)

    def log_wait(
        self,
        selector: str,
        timeout: int,
        success: bool = True,
        duration_ms: Optional[int] = None,
    ) -> None:
        """记录等待日志。"""
        level = "info" if success else "warning"
        message = f"Waited for {selector}" if success else f"Timeout waiting for {selector}"
        self.log(
            action="wait",
            message=message,
            detail={"selector": selector, "timeout": timeout, "success": success},
            level=level,
            duration_ms=duration_ms,
        )

    def log_assertion(
        self,
        assertion_type: str,
        expected: str,
        actual: Optional[str] = None,
        success: bool = True,
        duration_ms: Optional[int] = None,
    ) -> None:
        """记录断言日志。"""
        level = "info" if success else "error"
        message = f"Assertion passed: {assertion_type}" if success else f"Assertion failed: {assertion_type}"
        detail = {
            "assertion_type": assertion_type,
            "expected": expected,
            "actual": actual,
            "success": success,
        }
        self.log(action="assert", message=message, detail=detail, level=level, duration_ms=duration_ms)

    def log_screenshot(self, name: str, success: bool = True, duration_ms: Optional[int] = None) -> None:
        """记录截图日志。"""
        level = "info" if success else "warning"
        message = f"Screenshot saved: {name}" if success else f"Failed to save screenshot: {name}"
        self.log(
            action="screenshot",
            message=message,
            detail={"name": name, "success": success},
            level=level,
            duration_ms=duration_ms,
        )

    def log_error(self, action: str, error: Exception, detail: dict = None) -> None:
        """记录错误日志。"""
        if detail is None:
            detail = {}
        detail["error_type"] = type(error).__name__
        detail["error_message"] = str(error)
        self.log(
            action=action,
            message=f"Error in {action}: {error}",
            detail=detail,
            level="error",
        )

    def log_info(self, action: str, message: str, detail: dict = None) -> None:
        """记录信息日志。"""
        self.log(action=action, message=message, detail=detail or {}, level="info")

    def log_warning(self, action: str, message: str, detail: dict = None) -> None:
        """记录警告日志。"""
        self.log(action=action, message=message, detail=detail or {}, level="warning")

    def get_logs(self) -> list[LogEntry]:
        """获取所有日志。"""
        return self._logs.copy()

    def get_logs_as_dicts(self) -> list[dict]:
        """获取所有日志（字典格式）。"""
        return [log.to_dict() for log in self._logs]

    def clear(self) -> None:
        """清空日志。"""
        self._logs.clear()

    def to_json(self) -> str:
        """转换为 JSON 字符串。"""
        return json.dumps(self.get_logs_as_dicts(), ensure_ascii=False, indent=2)

    def __len__(self) -> int:
        return len(self._logs)

    def __repr__(self) -> str:
        return f"ActionLogger(session_id={self._session_id!r}, logs_count={len(self._logs)})"