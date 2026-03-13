"""
Windows 端 conftest —— 提供 Windows 桌面相关的 fixtures。
"""

import pytest
from common.config import Config


@pytest.fixture(scope="session")
def driver(config: Config):
    """启动 Windows 桌面驱动。"""
    # TODO: 实现 Windows 驱动初始化
    # 可以使用 WinAppDriver 或 Playwright for Desktop
    raise NotImplementedError("Windows 端尚未实现")