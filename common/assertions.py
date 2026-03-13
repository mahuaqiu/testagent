"""
自定义断言模块 —— 提供语义化的断言函数，替代裸 assert。

在测试用例中使用这些断言，失败时会给出更清晰的错误信息。

Usage:
    from common.assertions import assert_status_ok, assert_json_contains

    resp = api.get("/users/1")
    assert_status_ok(resp)
    assert_json_contains(resp, {"name": "张三"})
"""

import requests


def assert_status_ok(resp: requests.Response):
    """断言 HTTP 状态码为 200。

    Args:
        resp: requests.Response 对象。

    Raises:
        AssertionError: 状态码不是 200 时抛出，附带详细信息。
    """
    assert resp.status_code == 200, (
        f"期望状态码 200，实际 {resp.status_code}。"
        f"\nURL: {resp.url}"
        f"\n响应体: {resp.text[:500]}"
    )


def assert_status(resp: requests.Response, expected: int):
    """断言 HTTP 状态码为指定值。

    Args:
        resp: requests.Response 对象。
        expected: 期望的状态码，如 201、404。

    Raises:
        AssertionError: 状态码不匹配时抛出。
    """
    assert resp.status_code == expected, (
        f"期望状态码 {expected}，实际 {resp.status_code}。"
        f"\nURL: {resp.url}"
        f"\n响应体: {resp.text[:500]}"
    )


def assert_json_contains(resp: requests.Response, expected: dict):
    """断言响应 JSON 包含期望的键值对（子集匹配）。

    Args:
        resp: requests.Response 对象。
        expected: 期望包含的键值对字典。

    Raises:
        AssertionError: 缺少期望的键或值不匹配时抛出。

    Examples:
        >>> assert_json_contains(resp, {"code": 0, "msg": "success"})
    """
    body = resp.json()
    for key, value in expected.items():
        assert key in body, f"响应 JSON 中缺少 key: {key}。\n实际: {body}"
        assert body[key] == value, (
            f"key '{key}' 值不匹配: 期望 {value!r}，实际 {body[key]!r}。"
        )


def assert_json_key_exists(resp: requests.Response, *keys: str):
    """断言响应 JSON 中包含指定的 key。

    Args:
        resp: requests.Response 对象。
        *keys: 一个或多个需要存在的 key 名。

    Examples:
        >>> assert_json_key_exists(resp, "id", "name", "email")
    """
    body = resp.json()
    for key in keys:
        assert key in body, f"响应 JSON 中缺少 key: {key}。\n实际 keys: {list(body.keys())}"


def assert_list_not_empty(resp: requests.Response, list_key: str = None):
    """断言响应中的列表不为空。

    Args:
        resp: requests.Response 对象。
        list_key: JSON 中列表字段的 key。为 None 则认为响应体本身是列表。

    Examples:
        >>> assert_list_not_empty(resp, "data")  # {"data": [...]}
        >>> assert_list_not_empty(resp)           # [...]
    """
    body = resp.json()
    target = body[list_key] if list_key else body
    assert isinstance(target, list), f"期望列表类型，实际: {type(target)}"
    assert len(target) > 0, "列表为空"
