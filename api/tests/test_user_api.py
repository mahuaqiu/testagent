"""
示例：用户接口测试用例。

演示 API 端标准的测试用例写法，SKILL 生成新用例时参考此风格。

约定:
    - 文件名: test_<模块>_api.py
    - 类名:   Test<模块>API
    - 函数名: test_<场景描述>
    - 必须标注 @pytest.mark.api
    - 使用 common.assertions 中的断言函数
"""

import pytest

from common.assertions import (
    assert_status_ok,
    assert_status,
    assert_json_contains,
    assert_json_key_exists,
    assert_list_not_empty,
)


@pytest.mark.api
class TestUserAPI:
    """用户接口测试集。"""

    def test_create_user(self, user_service, data_factory):
        """创建用户：传入合法数据，应返回 200 且包含用户 ID。"""
        user_data = data_factory.random_user()
        resp = user_service.create_user(user_data)
        assert_status_ok(resp)
        assert_json_key_exists(resp, "id")

    def test_get_user(self, user_service):
        """查询用户：传入有效 ID，应返回用户详情。"""
        resp = user_service.get_user(user_id=1)
        assert_status_ok(resp)
        assert_json_key_exists(resp, "id", "name", "phone")

    def test_list_users(self, user_service):
        """查询用户列表：应返回非空列表。"""
        resp = user_service.list_users(page=1, size=10)
        assert_status_ok(resp)
        assert_list_not_empty(resp, "data")

    def test_update_user(self, user_service):
        """更新用户：修改名称，应返回成功。"""
        resp = user_service.update_user(user_id=1, data={"name": "更新后的名字"})
        assert_status_ok(resp)

    def test_delete_user(self, user_service):
        """删除用户：传入有效 ID，应返回成功。"""
        resp = user_service.delete_user(user_id=9999)
        assert_status(resp, 200)

    def test_get_nonexistent_user(self, user_service):
        """异常场景：查询不存在的用户，应返回 404。"""
        resp = user_service.get_user(user_id=0)
        assert_status(resp, 404)
