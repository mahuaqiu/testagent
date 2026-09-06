"""浏览器实时指针流注入器。

把屏幕 WebSocket 上行的 ``{"type":"input"}`` 事件变成设备端 down/move/up 流，
替代"鼠标抬起才发一条 click/swipe 的 REST 手势"。

设计约束（拖拽能否成功的关键）：
- down/up 是状态事件：绝不合并、绝不丢弃、严格保序；
- move 只保留最新（拖拽只关心当前指针位置，中间点合并掉反而降低延迟）；
- 注入在独立单线程串行执行，不阻塞 WS 收包循环，也不被画面推流阻塞；
- 连接关闭时按键仍处于按下状态，必须以最后坐标补发 up，防止设备端按键卡死。
"""

from __future__ import annotations

import logging
import threading
import time
from collections import deque
from typing import Any, Callable

logger = logging.getLogger(__name__)

# 队列深度上限；溢出时优先丢最旧的 move，绝不丢 down/up
_MAX_PENDING = 64
# 单事件注入耗时告警阈值
_SLOW_INJECT_MS = 50
# 合帧回退：位移低于该值（设备像素）视为点击
_CLICK_MAX_DISTANCE = 24.0

POINTER_ACTIONS = {"down", "move", "up", "wheel"}


def _event_coord(message: dict[str, Any]) -> tuple[int, int]:
    return int(message.get("x") or 0), int(message.get("y") or 0)


def _event_button(message: dict[str, Any], default: str = "left") -> str:
    return (str(message.get("button") or default)).lower()


class PointerInjector:
    """每条屏幕 WebSocket 一个实例；submit() 由 asyncio 侧调用，线程内串行执行。"""

    def __init__(self, dispatcher: Callable[[dict[str, Any]], None], label: str) -> None:
        self._dispatcher = dispatcher
        self._label = label
        self._cond = threading.Condition()
        self._items: deque[dict[str, Any] | None] = deque()
        self._pressed: str | None = None
        self._last_coord: tuple[int, int] | None = None
        self._thread = threading.Thread(
            target=self._run, name=f"pointer-injector-{label}", daemon=True
        )
        self._started = False
        self._closed = False

    def start(self) -> None:
        if not self._started:
            self._thread.start()
            self._started = True

    def submit(self, message: dict[str, Any]) -> None:
        """入队一条 input 消息；move 与队尾 move 抵尾合并，down/up 永不丢弃。"""
        if self._closed or message.get("action") not in POINTER_ACTIONS:
            return
        with self._cond:
            tail = self._items[-1] if self._items else None
            if (
                message.get("action") == "move"
                and tail is not None
                and tail.get("action") == "move"
            ):
                self._items[-1] = message
            else:
                self._items.append(message)
                if len(self._items) > _MAX_PENDING:
                    self._drop_oldest_move_locked()
            self._cond.notify()

    def close(self, timeout: float = 2.0) -> None:
        """停止注入线程；若按键仍按下，线程退出前以最后坐标补发 up。"""
        if self._closed:
            return
        self._closed = True
        with self._cond:
            self._items.append(None)  # 哨兵：排空队列后触发安全抬起并退出
            self._cond.notify()
        if self._started:
            self._thread.join(timeout=timeout)

    def _drop_oldest_move_locked(self) -> None:
        for index, item in enumerate(self._items):
            if item is not None and item.get("action") == "move":
                del self._items[index]
                return
        self._items.popleft()  # 队列里没有 move（理论不会发生）：丢最旧的一条

    def _run(self) -> None:
        while True:
            with self._cond:
                while not self._items:
                    self._cond.wait()
                message = self._items.popleft()
            if message is None:
                if self._pressed:
                    try:
                        self._safe_up(self._pressed)
                    except Exception as exc:  # noqa: BLE001 - 关闭路径必须走完
                        logger.warning("关闭时补发按键抬起失败: %s error=%s", self._label, exc)
                return
            try:
                self._execute(message)
            except Exception as exc:  # noqa: BLE001 - 单事件失败不影响后续事件
                logger.warning(
                    "指针注入异常: %s action=%s error=%s", self._label, message.get("action"), exc
                )

    def _execute(self, message: dict[str, Any]) -> None:
        action = message.get("action")
        started = time.perf_counter()
        try:
            if action == "down":
                if self._pressed:
                    # 前端状态异常（上一个按键未抬起又 down）：先补 up 保住设备状态
                    self._safe_up(self._pressed)
                self._dispatcher(message)
                self._pressed = _event_button(message).upper()
                # down 自身坐标也要记录：未经移动的按键同样可以被安全抬起
                self._last_coord = _event_coord(message)
            elif action == "up":
                self._dispatcher(message)
                self._pressed = None
            else:
                self._dispatcher(message)
                if action == "move":
                    self._last_coord = _event_coord(message)
        finally:
            elapsed_ms = (time.perf_counter() - started) * 1000
            if elapsed_ms > _SLOW_INJECT_MS:
                logger.warning(
                    "指针注入偏慢: %s action=%s %.1fms", self._label, action, elapsed_ms
                )

    def _safe_up(self, button: str) -> None:
        if self._last_coord is None:
            self._pressed = None
            return
        x, y = self._last_coord
        self._dispatcher({"action": "up", "button": button.lower(), "x": x, "y": y})
        self._pressed = None
        logger.info("指针注入器补发按键抬起: %s button=%s", self._label, button)


class HarmonyPointerDispatcher:
    """鸿蒙实时注入分发：官方会话就绪走逐事件原语，否则退化为整手势合帧。

    合帧回退的行为与旧版"mouseup 才发一条 REST 指令"一致，保证官方会话
    未就绪（解锁等待/冷启动）或不可用（旧系统版本）时基础操作不受影响。
    """

    def __init__(
        self,
        *,
        manager: Any,
        device_id: str,
        device_type: str,
        client: Any = None,
    ) -> None:
        self._manager = manager
        self._device_id = device_id
        self._is_pc = device_type == "harmony_pc"
        self._client = client
        self._fallback = _CoalescedGestureDispatcher(
            manager, device_id, client, self._is_pc
        )

    def __call__(self, message: dict[str, Any]) -> None:
        session = None
        try:
            session = self._manager.peek_official_session(self._device_id)
        except Exception:  # noqa: BLE001 - 会话管理器异常时走合帧回退
            session = None
        if session is not None and session.input_ready():
            self._realtime(session, message)
            return
        self._fallback.handle(message)

    def _realtime(self, session: Any, message: dict[str, Any]) -> None:
        action = message.get("action")
        x, y = _event_coord(message)
        if self._is_pc:
            raw_button = message.get("button")
            button = _event_button(message).upper() if raw_button else None
            if action == "down":
                session.mouse_down(button or "LEFT", x, y)
            elif action == "move":
                session.move_mouse(x, y, button)
            elif action == "up":
                session.mouse_up(button or "LEFT", x, y)
            elif action == "wheel":
                session.wheel((str(message.get("direction") or "up")).upper(), x, y)
            return
        # 移动端：触摸流
        if action == "down":
            session.touch_down(x, y)
        elif action == "move":
            session.touch_move(x, y)
        elif action == "up":
            session.touch_up(x, y)
        # wheel：移动端无对应注入，丢弃


class _CoalescedGestureDispatcher:
    """整手势合帧：down 记起点、move 记轨迹、up 时按位移合成 click/drag。"""

    def __init__(self, manager: Any, device_id: str, client: Any, is_pc: bool) -> None:
        self._manager = manager
        self._device_id = device_id
        self._client = client
        self._is_pc = is_pc
        self._lock = threading.Lock()
        self._start: tuple[int, int] | None = None
        self._button = "left"

    def handle(self, message: dict[str, Any]) -> None:
        action = message.get("action")
        x, y = _event_coord(message)
        if action == "down":
            with self._lock:
                self._start = (x, y)
                self._button = _event_button(message)
            return
        if action == "move":
            return  # 轨迹由平台侧 drag 插值生成，无需记录中间点
        if action == "up":
            with self._lock:
                start = self._start
                self._start = None
            if start is None:
                return
            self._emit(start, (x, y))
        # wheel：合帧模式没有低延迟滚轮通道，丢弃

    def _emit(self, start: tuple[int, int], end: tuple[int, int]) -> None:
        dx, dy = end[0] - start[0], end[1] - start[1]
        distance = (dx * dx + dy * dy) ** 0.5
        try:
            if distance < _CLICK_MAX_DISTANCE:
                if self._button == "right":
                    self._manager.right_click(start[0], start[1], context=self._client)
                else:
                    self._manager.click(start[0], start[1], context=self._client)
            else:
                self._manager.drag(
                    start[0], start[1], end[0], end[1], duration=500, context=self._client
                )
        except Exception as exc:  # noqa: BLE001 - 手势失败只告警，不中断事件流
            logger.warning(
                "合帧手势执行失败: device=%s button=%s distance=%.0f error=%s",
                self._device_id,
                self._button,
                distance,
                exc,
            )


class WindowsPointerDispatcher:
    """Windows 桌面实时注入（pyautogui 原语，坐标=截图像素空间）。"""

    def __init__(self, manager: Any, monitor: int = 1) -> None:
        self._manager = manager
        self._monitor = monitor

    def __call__(self, message: dict[str, Any]) -> None:
        action = message.get("action")
        x, y = _event_coord(message)
        button = _event_button(message)
        if action == "down":
            self._manager.mouse_down(x, y, button=button, monitor=self._monitor)
        elif action == "move":
            self._manager.move(x, y, monitor=self._monitor)
        elif action == "up":
            self._manager.mouse_up(x, y, button=button, monitor=self._monitor)
        elif action == "wheel":
            direction = str(message.get("direction") or "up").lower()
            amount = int(message.get("amount") or 3)
            self._manager.scroll(x, y, direction=direction, amount=amount, monitor=self._monitor)
