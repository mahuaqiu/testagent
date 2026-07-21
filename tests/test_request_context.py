"""Request ID 上下文隔离测试。"""

import asyncio

from common.request_context import get_request_id, reset_request_id, set_request_id


async def _hold_context(request_id: str, ready: asyncio.Event, release: asyncio.Event):
    token = set_request_id(request_id)
    try:
        ready.set()
        await release.wait()
        return get_request_id()
    finally:
        reset_request_id(token)


async def test_concurrent_coroutines_keep_independent_request_ids():
    ready_a = asyncio.Event()
    ready_b = asyncio.Event()
    release = asyncio.Event()

    task_a = asyncio.create_task(_hold_context("request-a", ready_a, release))
    task_b = asyncio.create_task(_hold_context("request-b", ready_b, release))
    await ready_a.wait()
    await ready_b.wait()
    release.set()

    assert await task_a == "request-a"
    assert await task_b == "request-b"
    assert get_request_id() is None
