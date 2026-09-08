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


