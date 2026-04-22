"""截图/录屏/推流统一模块。"""

from worker.screen.manager import (
    ScreenManager,
    get_screen_manager,
    close_screen_manager,
    close_all_screen_managers,
)

__all__ = [
    "ScreenManager",
    "get_screen_manager",
    "close_screen_manager",
    "close_all_screen_managers",
]