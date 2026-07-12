"""任务执行内核清理验收测试。

新内核接入后，生产 Worker 不得继续引用旧 TaskStore、旧 TaskScheduler
或一次性查询语义。测试暂时以静态检查形式存在，接线完成后应通过。
"""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def test_worker_no_longer_uses_legacy_task_store():
    source = (ROOT / "worker" / "worker.py").read_text(encoding="utf-8")
    assert "from worker.task.store import" not in source
    assert "self.task_store" not in source
    assert "class TaskScheduler" not in source


def test_server_uses_idempotent_task_query_semantics():
    source = (ROOT / "worker" / "server.py").read_text(encoding="utf-8")
    assert "一次性查询" not in source
    assert "查询后任务从内存中销毁" not in source
    assert "取消正在执行的任务，销毁 task_id" not in source

