"""
API 端认证相关业务流程封装。

将"登录获取 token → 设置到 service header"这个高频前置动作封装为 step。

Usage:
    def test_create_order(self, auth_steps, order_service):
        auth_steps.login_and_set_token(order_service)
        resp = order_service.create_order(...)
"""

from common.base_api import BaseAPI


class AuthSteps:
    """API 端认证流程封装。

    封装级别说明：
        - UserService.post("/api/auth/login", ...) → 单个接口调用
        - AuthSteps.login_and_set_token(svc) → 登录 + 提取 token + 注入到 service

    Args:
        api_base_url: API 根地址。
    """

    def __init__(self, api_base_url: str):
        self.api_base_url = api_base_url
        self._api = BaseAPI(base_url=api_base_url)
        self._cached_token: str | None = None

    def get_token(self, username: str = "admin", password: str = "admin123") -> str:
        """登录并返回 token。

        如果已缓存且未过期则直接返回缓存的 token。

        Args:
            username: 登录账号。
            password: 登录密码。

        Returns:
            str: 认证 token。
        """
        if self._cached_token:
            return self._cached_token

        resp = self._api.post("/api/auth/login", json={
            "username": username,
            "password": password,
        })
        assert resp.status_code == 200, f"登录失败: {resp.text}"
        self._cached_token = resp.json()["data"]["token"]
        return self._cached_token

    def login_and_set_token(self, service: BaseAPI, username: str = "admin", password: str = "admin123"):
        """登录获取 token 并注入到指定 service。

        这是最常用的方法 —— 让任意 service 获得认证能力。

        Args:
            service: 需要认证的 Service 实例（如 UserService, OrderService）。
            username: 登录账号。
            password: 登录密码。

        Usage:
            auth_steps.login_and_set_token(user_service)
            auth_steps.login_and_set_token(order_service, username="vip", password="vip123")
        """
        token = self.get_token(username, password)
        service.set_token(token)

    def clear_cache(self):
        """清除缓存的 token（用于测试 token 过期场景）。"""
        self._cached_token = None
