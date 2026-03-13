"""
HTTP 请求基础封装 —— 接口测试的父类。

所有 API Service 都应继承 BaseAPI，获得统一的请求方法和日志。

Usage:
    class UserService(BaseAPI):
        def get_user(self, user_id: int):
            return self.get(f"/api/users/{user_id}")

        def create_user(self, data: dict):
            return self.post("/api/users", json=data)

    svc = UserService(base_url="https://api.example.com")
    resp = svc.get_user(1)
"""

import requests
from typing import Optional


class BaseAPI:
    """HTTP 请求基础类，封装 GET / POST / PUT / DELETE 等常用方法。

    特性:
        - 自动拼接 base_url
        - 支持设置公共 headers（如 token）
        - 响应自动记录日志（打印 URL 和状态码）
        - 返回原生 requests.Response 对象

    Args:
        base_url: 接口根地址，如 "https://api.example.com"。
        headers: 公共请求头，会合并到每个请求中。
    """

    def __init__(self, base_url: str, headers: Optional[dict] = None):
        self.base_url = base_url.rstrip("/")
        self.session = requests.Session()
        if headers:
            self.session.headers.update(headers)

    def set_token(self, token: str):
        """设置 Bearer Token 到请求头。

        Args:
            token: JWT 或其他类型的 token 字符串。
        """
        self.session.headers["Authorization"] = f"Bearer {token}"

    def get(self, path: str, params: dict = None, **kwargs) -> requests.Response:
        """发送 GET 请求。

        Args:
            path: 接口路径，如 "/api/users"。
            params: URL 查询参数。

        Returns:
            requests.Response 对象。
        """
        url = f"{self.base_url}{path}"
        resp = self.session.get(url, params=params, **kwargs)
        self._log(resp)
        return resp

    def post(self, path: str, json: dict = None, data=None, **kwargs) -> requests.Response:
        """发送 POST 请求。

        Args:
            path: 接口路径。
            json: JSON 请求体（自动序列化）。
            data: 表单请求体。

        Returns:
            requests.Response 对象。
        """
        url = f"{self.base_url}{path}"
        resp = self.session.post(url, json=json, data=data, **kwargs)
        self._log(resp)
        return resp

    def put(self, path: str, json: dict = None, **kwargs) -> requests.Response:
        """发送 PUT 请求。

        Args:
            path: 接口路径。
            json: JSON 请求体。

        Returns:
            requests.Response 对象。
        """
        url = f"{self.base_url}{path}"
        resp = self.session.put(url, json=json, **kwargs)
        self._log(resp)
        return resp

    def delete(self, path: str, **kwargs) -> requests.Response:
        """发送 DELETE 请求。

        Args:
            path: 接口路径。

        Returns:
            requests.Response 对象。
        """
        url = f"{self.base_url}{path}"
        resp = self.session.delete(url, **kwargs)
        self._log(resp)
        return resp

    def _log(self, resp: requests.Response):
        """打印请求日志。"""
        print(f"  [{resp.request.method}] {resp.url} -> {resp.status_code}")
