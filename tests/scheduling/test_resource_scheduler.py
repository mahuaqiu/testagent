"""本机资源调度测试。"""

from worker.scheduling.scheduler import ResourceScheduler


def test_scheduler_rejects_same_resource_and_allows_distinct_devices():
    scheduler = ResourceScheduler()
    first = scheduler.try_acquire("android", "device-1", "task-1")
    assert first is not None
    assert scheduler.try_acquire("android", "device-1", "task-2") is None
    assert scheduler.try_acquire("android", "device-2", "task-2") is not None
    assert scheduler.get_busy_task_id("android", "device-1") == "task-1"


def test_scheduler_separates_harmony_platforms():
    scheduler = ResourceScheduler()
    mobile = scheduler.try_acquire("harmony_mobile", "same-id", "task-1")
    pc = scheduler.try_acquire("harmony_pc", "same-id", "task-2")
    assert mobile is not None
    assert pc is not None
    assert scheduler.active_count() == 2


def test_old_lease_cannot_release_new_lease():
    scheduler = ResourceScheduler()
    first = scheduler.try_acquire("web", None, "task-1")
    assert first is not None
    assert scheduler.release_lease(first)
    second = scheduler.try_acquire("web", None, "task-2")
    assert second is not None
    assert not scheduler.release_lease(first)
    assert scheduler.get_busy_task_id("web") == "task-2"


def test_scheduler_supports_explicit_resource_key():
    scheduler = ResourceScheduler()
    command = scheduler.try_acquire_key("host:command", "command-1")
    web = scheduler.try_acquire("web", None, "web-1")

    assert command is not None
    assert web is not None
    assert scheduler.try_acquire_key("host:command", "command-2") is None
    assert scheduler.get_busy_task_id_by_key("host:command") == "command-1"
