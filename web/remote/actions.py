"""
ActionExecutor —— 动作执行器。

根据 Action 定义执行对应的页面操作，返回 ActionResult。
支持扩展自定义动作类型。

Usage:
    from web.remote.actions import ActionExecutor
    from web.remote.task import Action
    from web.remote.page import RemotePage

    executor = ActionExecutor(page)
    action = Action(action_type="click", selector="button.submit")
    result = executor.execute(action)
"""

from typing import Optional, Callable, Any
import time

from web.remote.task import Action
from web.remote.result import ActionResult
from web.remote.page import RemotePage
from web.remote.logger import ActionLogger
from web.remote.screenshot import ScreenshotManager


class ActionExecutor:
    """
    动作执行器。

    根据 Action 定义执行对应的页面操作，返回 ActionResult。
    支持扩展自定义动作类型。

    Attributes:
        page: RemotePage 实例
        logger: 操作日志记录器
        screenshot_manager: 截图管理器
        custom_handlers: 自定义动作处理器字典
    """

    # 内置动作类型
    BUILTIN_ACTIONS = {
        "navigate",
        "click",
        "fill",
        "type",
        "wait",
        "wait_for",
        "screenshot",
        "assert_visible",
        "assert_text",
        "assert_url",
        "hover",
        "select",
        "check",
        "uncheck",
        "press",
        "get_text",
        "get_value",
        "evaluate",
        "scroll",
        "goto",
    }

    def __init__(
        self,
        page: RemotePage,
        logger: Optional[ActionLogger] = None,
        screenshot_manager: Optional[ScreenshotManager] = None,
    ):
        """
        初始化动作执行器。

        Args:
            page: RemotePage 实例
            logger: 操作日志记录器
            screenshot_manager: 截图管理器
        """
        self._page = page
        self._logger = logger or page.logger
        self._screenshot_manager = screenshot_manager or ScreenshotManager()
        self._custom_handlers: dict[str, Callable] = {}
        self._action_index = 0

    @property
    def page(self) -> RemotePage:
        return self._page

    def register_handler(self, action_type: str, handler: Callable[[Action], ActionResult]) -> None:
        """
        注册自定义动作处理器。

        Args:
            action_type: 动作类型名称
            handler: 处理函数，接收 Action，返回 ActionResult
        """
        self._custom_handlers[action_type] = handler

    def unregister_handler(self, action_type: str) -> bool:
        """
        注销自定义动作处理器。

        Args:
            action_type: 动作类型名称

        Returns:
            bool: 是否成功注销
        """
        return self._custom_handlers.pop(action_type, None) is not None

    def execute(self, action: Action, index: Optional[int] = None) -> ActionResult:
        """
        执行单个动作。

        Args:
            action: 动作定义
            index: 动作索引

        Returns:
            ActionResult: 执行结果
        """
        start_time = time.time()
        output = None
        error = None
        screenshot_data = None
        status = "success"

        try:
            # 检查自定义处理器
            if action.action_type in self._custom_handlers:
                result = self._custom_handlers[action.action_type](action)
                return result

            # 执行内置动作
            output = self._execute_builtin(action)

            # 动作后等待
            if action.wait:
                self._page.wait_for_timeout(action.wait)

        except Exception as e:
            status = "failed"
            error = str(e)
            self._logger.log_error(action.action_type, e, {"selector": action.selector})

        # 计算耗时
        duration_ms = int((time.time() - start_time) * 1000)

        # 截图（如果需要或失败时）
        if action.screenshot or (status == "failed"):
            try:
                screenshot_data = self._page.screenshot(
                    name=f"action_{action.action_type}_{index or self._action_index}"
                ).data
            except Exception:
                pass

        # 更新索引
        if index is not None:
            self._action_index = index + 1
        else:
            self._action_index += 1

        return ActionResult(
            action=action,
            status=status,
            output=output,
            screenshot=screenshot_data,
            duration_ms=duration_ms,
            error=error,
            index=index if index is not None else self._action_index - 1,
        )

    def execute_sequence(self, actions: list[Action]) -> list[ActionResult]:
        """
        执行动作序列。

        Args:
            actions: 动作列表

        Returns:
            list[ActionResult]: 结果列表
        """
        results = []
        for i, action in enumerate(actions):
            result = self.execute(action, index=i)
            results.append(result)
            if result.status == "failed":
                # 失败时停止执行后续动作
                break
        return results

    def _execute_builtin(self, action: Action) -> Any:
        """
        执行内置动作。

        Args:
            action: 动作定义

        Returns:
            Any: 动作输出
        """
        action_type = action.action_type
        selector = action.selector
        value = action.value
        timeout = action.timeout or 30000

        if action_type == "navigate" or action_type == "goto":
            return self._page.navigate(value, wait_until="load")

        elif action_type == "click":
            self._page.click(selector, timeout=timeout)
            return None

        elif action_type == "fill":
            self._page.fill(selector, value, timeout=timeout)
            return None

        elif action_type == "type":
            self._page.type(selector, value, timeout=timeout)
            return None

        elif action_type == "wait":
            wait_time = int(value) if value else 1000
            self._page.wait_for_timeout(wait_time)
            return None

        elif action_type == "wait_for":
            self._page.wait_for(selector, timeout=timeout)
            return None

        elif action_type == "screenshot":
            screenshot = self._page.screenshot(name=value)
            return screenshot.name

        elif action_type == "assert_visible":
            self._page.expect_visible(selector, timeout=timeout)
            return True

        elif action_type == "assert_text":
            self._page.expect_text(selector, action.expect or value, timeout=timeout)
            return True

        elif action_type == "assert_url":
            self._page.expect_url(action.expect or value)
            return True

        elif action_type == "hover":
            self._page.hover(selector, timeout=timeout)
            return None

        elif action_type == "select":
            self._page.select_option(selector, value=value, timeout=timeout)
            return None

        elif action_type == "check":
            self._page.check(selector, timeout=timeout)
            return None

        elif action_type == "uncheck":
            self._page.uncheck(selector, timeout=timeout)
            return None

        elif action_type == "press":
            self._page.press(selector, value or "Enter", timeout=timeout)
            return None

        elif action_type == "get_text":
            return self._page.get_text(selector, timeout=timeout)

        elif action_type == "get_value":
            return self._page.get_value(selector, timeout=timeout)

        elif action_type == "evaluate":
            return self._page.evaluate(value)

        elif action_type == "scroll":
            # 滚动到元素
            script = f"document.querySelector('{selector}').scrollIntoView()"
            self._page.evaluate(script)
            return None

        else:
            raise ValueError(f"Unknown action type: {action_type}")

    def get_supported_actions(self) -> list[str]:
        """
        获取支持的动作类型列表。

        Returns:
            list[str]: 动作类型列表
        """
        return list(self.BUILTIN_ACTIONS) + list(self._custom_handlers.keys())

    def __repr__(self) -> str:
        return f"ActionExecutor(actions={len(self.get_supported_actions())})"