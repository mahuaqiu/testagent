"""HDC 宿主进程归属和退出测试。"""

from types import SimpleNamespace

from worker.platforms import harmony_hdc_process


def test_register_launched_processes_ignores_unrelated_same_path_process(monkeypatch):
    """启动前已存在的同路径进程不能被登记。"""
    monkeypatch.setattr(
        harmony_hdc_process.psutil,
        "process_iter",
        lambda fields: iter([
            SimpleNamespace(info={
                "pid": 102,
                "ppid": 1,
                "exe": "D:/project/hdc.exe",
                "create_time": 30.0,
            })
        ]),
    )
    def fail_write(records):
        raise AssertionError("不应登记")

    monkeypatch.setattr(harmony_hdc_process, "_write_records", fail_write)

    harmony_hdc_process.register_launched_processes(
        root_pid=101,
        hdc_path="D:/project/hdc.exe",
        baseline_pids={102},
        launched_at=20.0,
    )


def test_stop_owned_hdc_processes_does_not_kill_external_process(monkeypatch):
    """外部已有 HDC 即使路径相同，也不应被项目退出逻辑终止。"""
    external = SimpleNamespace(pid=100, exe_path="D:/sdk/hdc.exe", create_time=10.0)
    monkeypatch.setattr(harmony_hdc_process, "_read_records", lambda: [
        harmony_hdc_process.HdcProcessRecord(
            external.pid, external.exe_path, external.create_time + 100
        )
    ])
    fake_process = SimpleNamespace(
        pid=external.pid,
        exe=lambda: external.exe_path,
        create_time=lambda: external.create_time,
        kill=lambda: (_ for _ in ()).throw(AssertionError("不应终止外部 HDC")),
    )
    monkeypatch.setattr(harmony_hdc_process.psutil, "Process", lambda pid: fake_process)
    monkeypatch.setattr(harmony_hdc_process, "_write_records", lambda records: None)

    assert harmony_hdc_process.stop_owned_hdc_processes() == 0


def test_stop_owned_hdc_processes_kills_matching_project_process(monkeypatch):
    """PID、路径和启动时间都匹配时，应终止项目启动的 HDC。"""
    killed = []
    monkeypatch.setattr(harmony_hdc_process, "_read_records", lambda: [
        harmony_hdc_process.HdcProcessRecord(101, "D:/project/hdc.exe", 20.0)
    ])
    fake_process = SimpleNamespace(
        pid=101,
        exe=lambda: "d:/project/hdc.exe",
        create_time=lambda: 20.5,
        kill=lambda: killed.append(101),
    )
    monkeypatch.setattr(harmony_hdc_process.psutil, "Process", lambda pid: fake_process)
    monkeypatch.setattr(harmony_hdc_process.psutil, "wait_procs", lambda processes, timeout: ([], []))
    monkeypatch.setattr(harmony_hdc_process, "_write_records", lambda records: None)

    assert harmony_hdc_process.stop_owned_hdc_processes() == 1
    assert killed == [101]


def test_worker_stop_cleans_hdc_when_startup_is_incomplete(monkeypatch):
    """Worker 启动未完成时，也必须执行 HDC 清理。"""
    from worker.worker import Worker

    cleaned = []
    monkeypatch.setattr(
        harmony_hdc_process,
        "stop_owned_hdc_processes",
        lambda: cleaned.append(True),
    )

    worker = object.__new__(Worker)
    worker._started = False
    worker.stop()

    assert cleaned == [True]
