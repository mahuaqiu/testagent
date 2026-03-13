"""
示例：用户相关接口 Service 封装。

演示如何基于 BaseAPI 封装一组业务接口。
SKILL 生成新 Service 时参考此文件的结构和命名风格。

Usage:
    svc = UserService(base_url="https://api.example.com")
    svc.set_token("xxx")
    resp = svc.get_user(user_id=1)
"""

from common.base_api import BaseAPI


class UserService(BaseAPI):
    """用户模块接口封装。

    命名规范:
        - 查询: get_<资源>
        - 创建: create_<资源>
        - 更新: update_<资源>
        - 删除: delete_<资源>
        - 列表: list_<资源>s
    """

    def create_user(self, data: dict):
        """创建用户。

        Args:
            data: 用户信息字典，必须包含 name, phone, email。

        Returns:
            requests.Response

        接口: POST /api/users
        """
        return self.post("/api/users", json=data)

    def get_user(self, user_id: int):
        """查询单个用户详情。

        Args:
            user_id: 用户 ID。

        Returns:
            requests.Response

        接口: GET /api/users/{user_id}
        """
        return self.get(f"/api/users/{user_id}")

    def list_users(self, page: int = 1, size: int = 20):
        """分页查询用户列表。

        Args:
            page: 页码，从 1 开始。
            size: 每页条数。

        Returns:
            requests.Response

        接口: GET /api/users?page=1&size=20
        """
        return self.get("/api/users", params={"page": page, "size": size})

    def update_user(self, user_id: int, data: dict):
        """更新用户信息。

        Args:
            user_id: 用户 ID。
            data: 要更新的字段字典。

        Returns:
            requests.Response

        接口: PUT /api/users/{user_id}
        """
        return self.put(f"/api/users/{user_id}", json=data)

    def delete_user(self, user_id: int):
        """删除用户。

        Args:
            user_id: 用户 ID。

        Returns:
            requests.Response

        接口: DELETE /api/users/{user_id}
        """
        return self.delete(f"/api/users/{user_id}")
