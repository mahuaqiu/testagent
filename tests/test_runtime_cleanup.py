"""WorkerRuntime 后台清理生命周期测试。"""

import threading
import time

from worker.runtime import WorkerRuntime
from worker.task.result import TaskResult, TaskStatus


def test_runtime_start_does_not_wait_for_background_cleanup(tmp_path):
    cleanup_started = threading.Event()
    cleanup_finished = threading.Event()

    runtime = WorkerRuntime(
        lambda task, cancel_event: TaskResult(
            status=TaskStatus.SUCCESS,
            platform=task.platform,
        ),
        base_dir=tmp_path,
        cleanup_interval_hours=6,
    )

    def slow_cleanup():
        cleanup_started.set()
        time.sleep(0.2)
        cleanup_finished.set()

    runtime._run_cleanup = slow_cleanup
    started_at = time.monotonic()
    runtime.start()
    start_elapsed = time.monotonic() - started_at

    try:
        assert start_elapsed < 0.15
        assert cleanup_started.wait(1)
        assert cleanup_finished.wait(1)
        assert runtime._cleanup_interval == 6 * 3600
    finally:
        runtime.stop()
