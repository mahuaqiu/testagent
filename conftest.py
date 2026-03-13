"""
全局 conftest —— 提供跨端共享的 fixtures。

所有端（web / app / api）都能使用这里定义的 fixture。
端专属的 fixture 请放到各端目录下的 conftest.py。
"""

import pytest
import yaml
import os

from common.config import Config
from common.data_factory import DataFactory


# ──────────────────────────────────────────────
# 配置相关
# ──────────────────────────────────────────────

@pytest.fixture(scope="session")
def config():
    """加载全局配置，整个测试会话只加载一次。

    配置文件路径: config/settings.yaml
    可通过环境变量 ENV 切换环境，默认 dev。

    Returns:
        Config: 配置对象，支持 config.get("key") 访问。
    """
    env = os.getenv("ENV", "dev")
    cfg = Config(env=env)
    return cfg


@pytest.fixture(scope="session")
def data_factory():
    """测试数据工厂，用于生成各类随机/模板测试数据。

    Returns:
        DataFactory: 数据工厂实例。
    """
    return DataFactory()


# ──────────────────────────────────────────────
# 日志 / 报告
# ──────────────────────────────────────────────

@pytest.fixture(autouse=True)
def _log_test_name(request):
    """自动打印当前执行的用例名称，方便调试。"""
    print(f"\n▶ Running: {request.node.nodeid}")
    yield
    print(f"  ✔ Finished: {request.node.nodeid}")
