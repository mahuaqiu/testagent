"""
SessionManager —— 会话管理。

管理多个用户的测试会话，每个会话对应一个独立的 BrowserContext（用户隔离）。
支持会话超时自动清理。

Usage:
    from web.remote.browser import RemoteBrowser
    from web.remote.session import SessionManager

    browser = RemoteBrowser()
    browser.start()

    session_mgr = SessionManager(browser)
    session = session_mgr.create_session("user_001")
    page = session.context.new_page()
    # ... 执行操作 ...
    session_mgr.close_session(session.session_id)
"""

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Optional
import uuid
import threading

from playwright.sync_api import BrowserContext

from web.remote.browser import RemoteBrowser
from web.remote.result import TaskResult


@dataclass
class Session:
    """
    会话数据。

    Attributes:
        session_id: 会话唯一标识
        user_id: 用户标识
        context: 浏览器上下文
        created_at: 创建时间
        last_active: 最后活跃时间
        metadata: 扩展元数据
        result: 执行结果
    """

    session_id: str
    user_id: str
    context: BrowserContext
    created_at: datetime = field(default_factory=datetime.now)
    last_active: datetime = field(default_factory=datetime.now)
    metadata: dict = field(default_factory=dict)
    result: Optional[TaskResult] = None

    def touch(self) -> None:
        """更新最后活跃时间。"""
        self.last_active = datetime.now()

    def is_expired(self, timeout_seconds: int) -> bool:
        """检查会话是否超时。"""
        elapsed = datetime.now() - self.last_active
        return elapsed > timedelta(seconds=timeout_seconds)

    def to_dict(self) -> dict:
        """转换为字典。"""
        return {
            "session_id": self.session_id,
            "user_id": self.user_id,
            "created_at": self.created_at.isoformat(),
            "last_active": self.last_active.isoformat(),
            "metadata": self.metadata,
        }


class SessionManager:
    """
    会话管理器。

    管理多个用户的测试会话，每个会话对应一个独立的 BrowserContext。

    Attributes:
        _browser: RemoteBrowser 实例
        _sessions: 会话字典 session_id -> Session
        _session_timeout: 会话超时秒数
        _lock: 线程锁
    """

    def __init__(
        self,
        browser: RemoteBrowser,
        session_timeout: int = 300,  # 5 分钟
        context_options: Optional[dict] = None,
    ):
        """
        初始化会话管理器。

        Args:
            browser: RemoteBrowser 实例
            session_timeout: 会话超时秒数
            context_options: 创建上下文时的默认选项
        """
        self._browser = browser
        _sessions: dict[str, Session] = {}
        self._sessions = _sessions
        self._session_timeout = session_timeout
        self._context_options = context_options or {}
        self._lock = threading.Lock()

    @property
    def session_timeout(self) -> int:
        return self._session_timeout

    @session_timeout.setter
    def session_timeout(self, value: int) -> None:
        self._session_timeout = value

    def create_session(
        self,
        user_id: str,
        context_options: Optional[dict] = None,
        metadata: Optional[dict] = None,
    ) -> Session:
        """
        创建新会话。

        Args:
            user_id: 用户标识
            context_options: 创建上下文的选项（覆盖默认选项）
            metadata: 扩展元数据

        Returns:
            Session: 新创建的会话

        Raises:
            RuntimeError: 浏览器未连接
        """
        if not self._browser.is_connected:
            raise RuntimeError("Browser not connected. Call browser.start() first.")

        # 合并上下文选项
        options = {**self._context_options}
        if context_options:
            options.update(context_options)

        # 创建新的浏览器上下文
        context = self._browser.new_context(**options)

        # 生成会话 ID
        session_id = f"session_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:8]}"

        # 创建会话对象
        session = Session(
            session_id=session_id,
            user_id=user_id,
            context=context,
            created_at=datetime.now(),
            last_active=datetime.now(),
            metadata=metadata or {},
        )

        with self._lock:
            self._sessions[session_id] = session

        return session

    def get_session(self, session_id: str) -> Optional[Session]:
        """
        获取活跃会话。

        Args:
            session_id: 会话 ID

        Returns:
            Session 或 None
        """
        with self._lock:
            session = self._sessions.get(session_id)
            if session:
                session.touch()
            return session

    def get_session_by_user(self, user_id: str) -> Optional[Session]:
        """
        根据用户 ID 获取会话。

        Args:
            user_id: 用户 ID

        Returns:
            Session 或 None
        """
        with self._lock:
            for session in self._sessions.values():
                if session.user_id == user_id:
                    session.touch()
                    return session
        return None

    def close_session(self, session_id: str) -> bool:
        """
        关闭指定会话。

        Args:
            session_id: 会话 ID

        Returns:
            bool: 是否成功关闭
        """
        with self._lock:
            session = self._sessions.pop(session_id, None)
            if session is None:
                return False

        # 关闭浏览器上下文
        try:
            session.context.close()
        except Exception:
            pass

        return True

    def close_all_sessions(self) -> int:
        """
        关闭所有会话。

        Returns:
            int: 关闭的会话数
        """
        with self._lock:
            sessions = list(self._sessions.values())
            self._sessions.clear()

        count = 0
        for session in sessions:
            try:
                session.context.close()
                count += 1
            except Exception:
                pass

        return count

    def cleanup_expired(self) -> list[str]:
        """
        清理超时会话。

        Returns:
            list[str]: 被清理的会话 ID 列表
        """
        expired_ids = []

        with self._lock:
            for session_id, session in list(self._sessions.items()):
                if session.is_expired(self._session_timeout):
                    expired_ids.append(session_id)

        # 关闭超时会话
        for session_id in expired_ids:
            self.close_session(session_id)

        return expired_ids

    def get_active_sessions(self) -> list[Session]:
        """
        获取所有活跃会话。

        Returns:
            list[Session]: 活跃会话列表
        """
        with self._lock:
            return list(self._sessions.values())

    def get_active_count(self) -> int:
        """
        获取活跃会话数。

        Returns:
            int: 活跃会话数
        """
        with self._lock:
            return len(self._sessions)

    def get_all_user_ids(self) -> list[str]:
        """
        获取所有用户 ID。

        Returns:
            list[str]: 用户 ID 列表
        """
        with self._lock:
            return [s.user_id for s in self._sessions.values()]

    def __len__(self) -> int:
        return self.get_active_count()

    def __repr__(self) -> str:
        return (
            f"SessionManager(sessions={self.get_active_count()}, "
            f"timeout={self._session_timeout}s)"
        )