"""鸿蒙采集 match_mode 协议字段与 PerfharmonyBackend 精准模式测试。

全部通过注入假 perfharmony 模块运行，不依赖真机。
"""

import sys
import types
from datetime import datetime, timezone

import pytest

from worker.perf_backends.perfharmony_backend import PerfharmonyBackend
from worker.performance_monitor import CollectStartRequest, PerformanceCollector, TargetProcess

# ---------------------------------------------------------------------------
# CollectStartRequest.match_mode 协议契约
# ---------------------------------------------------------------------------


def test_match_mode_defaults_to_fuzzy():
    request = CollectStartRequest(collect_id="c1", device_type="harmony_pc", device_sn="SN1")
    assert request.match_mode == "fuzzy"


def test_match_mode_accepts_exact():
    request = CollectStartRequest(
        collect_id="c1", device_type="harmony_pc", device_sn="SN1", match_mode="exact"
    )
    assert request.match_mode == "exact"


def test_match_mode_rejects_unknown_value():
    with pytest.raises(Exception):
        CollectStartRequest(collect_id="c1", match_mode="regex")


def test_same_task_requires_same_match_mode():
    collector = PerformanceCollector("dev1")
    base = dict(collect_id="c1", interval=5, device_type="harmony_pc", device_sn="SN1")
    request_a = CollectStartRequest(
        **base, target_processes=[TargetProcess(name="com.app")], match_mode="fuzzy"
    )
    request_b = CollectStartRequest(
        **base, target_processes=[TargetProcess(name="com.app")], match_mode="exact"
    )
    collector._collect_id = "c1"
    collector._interval = 5
    collector._device_type = "harmony_pc"
    collector._device_sn = "SN1"
    collector._match_mode = "fuzzy"
    collector._target_processes = [TargetProcess(name="com.app")]
    assert collector._is_same_task(request_a) is True
    assert collector._is_same_task(request_b) is False


# ---------------------------------------------------------------------------
# PerfharmonyBackend 精准模式（PID 发现 + 30s 跟随）
# ---------------------------------------------------------------------------


class _FakeResult:
    def __init__(self, samples):
        self.samples = samples


class FakeMonitor:
    """记录构造参数与启停；样本由测试用例通过类变量注入。"""

    created = []
    next_samples = []
    ps_list = []
    def __init__(self, *, udid, hdc_path=None, interval=1.0, duration=None,
                 package=None, pid=None):
        self.kwargs = dict(
            udid=udid, hdc_path=hdc_path, interval=interval, duration=duration,
            package=package, pid=pid,
        )
        self.started = False
        self.stopped = False
        FakeMonitor.created.append(self)

    def start(self):
        self.started = True

    def stop(self):
        self.stopped = True

    def is_running(self):
        return self.started and not self.stopped

    def buffer_len(self):
        return len(FakeMonitor.next_samples)

    def get_result(self):
        samples, FakeMonitor.next_samples = FakeMonitor.next_samples, []
        return _FakeResult(samples)

    def last_error(self):
        return None


@pytest.fixture()
def fake_module(monkeypatch):
    module = types.ModuleType("perfharmony")
    module.Monitor = FakeMonitor
    module.list_processes = staticmethod(lambda udid, hdc_path=None: list(FakeMonitor.ps_list))
    monkeypatch.setitem(sys.modules, "perfharmony", module)
    FakeMonitor.created = []
    FakeMonitor.next_samples = []
    FakeMonitor.ps_list = []
    return module


def _sample(seq=1):
    return types.SimpleNamespace(
        sequence=seq,
        elapsed_ms=seq * 1000,
        timestamp=datetime(2026, 9, 8, tzinfo=timezone.utc),
        system={},
        hwinfo_raw={},
        processes=[],
        aggregated=[],
        top_n_cpu=None,
        top_n_gpu=None,
    )


def test_exact_mode_resolves_pid_at_start(fake_module):
    FakeMonitor.ps_list = [(100, "com.app"), (200, "com.app:render")]
    backend = PerfharmonyBackend(udid="SN1")
    backend.start(interval=5, duration=3600, package="com.app", match_mode="exact")
    assert FakeMonitor.created[0].kwargs["pid"] == 100
    assert FakeMonitor.created[0].kwargs["package"] is None


def test_exact_mode_prefers_main_process_pid(fake_module):
    # ps -ef 先出现子进程行时，仍应优先选进程名恰为包名的主进程。
    FakeMonitor.ps_list = [(300, "com.app:worker"), (100, "com.app")]
    backend = PerfharmonyBackend(udid="SN1")
    backend.start(interval=5, duration=3600, package="com.app", match_mode="exact")
    assert FakeMonitor.created[0].kwargs["pid"] == 100


def test_exact_mode_system_only_when_app_absent(fake_module):
    FakeMonitor.ps_list = []
    backend = PerfharmonyBackend(udid="SN1")
    backend.start(interval=5, duration=3600, package="com.app", match_mode="exact")
    assert FakeMonitor.created[0].kwargs["pid"] is None
    assert FakeMonitor.created[0].kwargs["package"] is None


def test_exact_mode_follows_pid_change(fake_module):
    FakeMonitor.ps_list = []
    backend = PerfharmonyBackend(udid="SN1")
    backend.start(interval=5, duration=3600, package="com.app", match_mode="exact")
    # 强制到期，模拟 30s 后复核。
    backend._last_pid_check = 0.0
    FakeMonitor.ps_list = [(100, "com.app")]
    FakeMonitor.next_samples = [_sample(1)]
    backend.heartbeat()
    assert FakeMonitor.created[0].stopped is True  # 旧 Monitor 已停
    assert FakeMonitor.created[1].kwargs["pid"] == 100  # 新 Monitor 带新 PID
    # 旧样本不丢：暂存后随 get_result 一并返回。
    result = backend.get_result()
    assert [s.sequence for s in result.samples] == [1]
    # PID 未变化时 heartbeat 不重启。
    FakeMonitor.created.clear()
    backend._last_pid_check = 0.0
    backend.heartbeat()
    assert FakeMonitor.created == []


def test_fuzzy_mode_heartbeat_is_noop(fake_module):
    backend = PerfharmonyBackend(udid="SN1")
    backend.start(interval=5, duration=3600, package="com.app", match_mode="fuzzy")
    assert FakeMonitor.created[0].kwargs["package"] == "com.app"
    backend._last_pid_check = 0.0
    backend.heartbeat()
    assert len(FakeMonitor.created) == 1




# ---------------------------------------------------------------------------
# 换 PID 重启的三个边界：剩余时长、stop 竞态、样本序号单调
# ---------------------------------------------------------------------------


def _install_fake_clock(monkeypatch, start=1000.0):
    """为 perfharmony_backend 注入可控单调时钟。"""
    import worker.perf_backends.perfharmony_backend as backend_module

    clock = {"now": start}
    monkeypatch.setattr(
        backend_module,
        "time",
        types.SimpleNamespace(monotonic=lambda: clock["now"]),
    )
    return clock


def test_exact_follow_restart_uses_remaining_duration(fake_module, monkeypatch):
    """PID 跟随重启应使用剩余 timeout，而不是重新计时全额 duration。"""
    clock = _install_fake_clock(monkeypatch)
    FakeMonitor.ps_list = [(100, "com.app")]
    backend = PerfharmonyBackend(udid="SN1")
    backend.start(interval=5, duration=3600, package="com.app", match_mode="exact")
    assert FakeMonitor.created[0].kwargs["duration"] == pytest.approx(3600, abs=1)

    clock["now"] += 1800  # 采集半小时后应用重启、PID 变化
    FakeMonitor.ps_list = [(200, "com.app")]
    backend._last_pid_check = 0.0
    backend.heartbeat()
    assert FakeMonitor.created[1].kwargs["duration"] == pytest.approx(1800, abs=1)


def test_exact_follow_skips_restart_after_timeout_exhausted(fake_module, monkeypatch):
    """已到 timeout 时 PID 变化不再重启，采集按到期正常收尾。"""
    clock = _install_fake_clock(monkeypatch)
    FakeMonitor.ps_list = []
    backend = PerfharmonyBackend(udid="SN1")
    backend.start(interval=5, duration=60, package="com.app", match_mode="exact")

    clock["now"] += 120
    FakeMonitor.ps_list = [(100, "com.app")]
    backend._last_pid_check = 0.0
    backend.heartbeat()
    assert len(FakeMonitor.created) == 1
    assert backend.is_running() is False


def test_stop_in_follow_window_does_not_start_new_monitor(fake_module):
    """stop_collect 与 PID 跟随重启并发时，不得泄漏无人停止的新 Monitor。"""
    FakeMonitor.ps_list = []
    backend = PerfharmonyBackend(udid="SN1")
    backend.start(interval=5, duration=3600, package="com.app", match_mode="exact")
    backend._last_pid_check = 0.0
    FakeMonitor.ps_list = [(100, "com.app")]
    assert backend._check_pid_due_and_prepare() is True  # 旧流已停，重启动作待执行
    backend.stop()  # 模拟 stop_collect 恰在此窗口进入
    backend._start_monitor()  # 采集线程在竞态窗口中继续补上重启
    assert len(FakeMonitor.created) == 1
    assert backend.is_running() is False


def test_collector_renumbers_samples_across_backend_restart(monkeypatch):
    """换 PID 重启后新 Monitor 序号从头计数，必须单调续编避免 sample_key 撞车。"""
    from types import SimpleNamespace as NS

    collector = PerformanceCollector("dev1")
    collector._collect_id = "c1"
    batches: list = []
    monkeypatch.setattr(collector, "_report_samples", lambda samples: batches.append(samples))
    collector._backend = NS(
        buffer_len=lambda: 2,
        get_result=lambda: NS(samples=[_sample(1), _sample(2)]),
    )
    collector._drain_backend_buffer()
    # 模拟换 PID 重启：新 Monitor 的序号又从 1 开始。
    collector._backend = NS(
        buffer_len=lambda: 2,
        get_result=lambda: NS(samples=[_sample(1), _sample(2)]),
    )
    collector._drain_backend_buffer()

    assert [s["sequence"] for s in batches[0]] == [1, 2]
    assert [s["sequence"] for s in batches[1]] == [3, 4]
    keys = [s["sample_key"] for batch in batches for s in batch]
    assert keys == ["c1:1", "c1:2", "c1:3", "c1:4"]
    assert collector._last_sequence == 4
