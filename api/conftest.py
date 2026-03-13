"""
API 端 conftest —— 提供接口测试相关的 fixtures。

这里的 fixture 只对 api/tests/ 下的用例生效。
"""

import pytest

from common.config import Config
from api.services.user_service import UserService
from api.steps.auth_steps import AuthSteps


@pytest.fixture(scope="session")
def api_base_url(config: Config) -> str:
    """获取 API base_url。"""
    return config.api_base_url


@pytest.fixture(scope="session")
def auth_steps(api_base_url: str) -> AuthSteps:
    """API 端认证流程 steps（会话级别，token 可缓存复用）。

    Returns:
        AuthSteps: 认证流程封装对象。
    """
    return AuthSteps(api_base_url=api_base_url)


@pytest.fixture
def user_service(api_base_url: str) -> UserService:
    """创建 UserService 实例（未认证）。

    Returns:
        UserService: 用户接口服务对象。
    """
    return UserService(base_url=api_base_url)


@pytest.fixture
def auth_user_service(api_base_url: str, auth_steps: AuthSteps) -> UserService:
    """创建已认证（带 token）的 UserService 实例。

    通过 auth_steps 自动完成登录并注入 token。

    Returns:
        UserService: 已认证的用户接口服务对象。
    """
    svc = UserService(base_url=api_base_url)
    auth_steps.login_and_set_token(svc)
    return svc
