"""PerfwinBackend 和 PerformanceCollector 的 mock 单测。"""

import sys
import threading
from unittest.mock import MagicMock, patch
import pytest

from worker.perf_backends.base import CollectBackend
from worker.perf_backends.perfharmony_backend import PerfharmonyBackend
from worker.perf_backends.perfwin_backend import PerfwinBackend
from worker.performance_monitor import (
    CollectStartRequest,
    CollectStopRequest,
    PerformanceCollector,
    TargetProcess,
)


def _make_mock_monitor(*, is_running=True, buffer_len=0, samples=None):
    """构造 mock perfwin.Monitor。"""
    m = MagicMock()
    m.is_running.return_value = is_running
    m.buffer_len.return_value = buffer_len
    result = MagicMock()
    result.samples = samples or []
    m.get_result.return_value = result
    return m


def _make_start_request(names=("test_proc",), interval=2, timeout=60):
    """构造采集开始请求。"""
    return CollectStartRequest(
        collect_id="test-collect-001",
        interval=interval,
        timeout=timeout,
        target_processes=[TargetProcess(name=n) for n in names],
    )


# ---------------------------------------------------------------------------
# PerfwinBackend 单测
# ---------------------------------------------------------------------------


class TestPerfwinBackend:
    """PerfwinBackend 测试套件。"""

    def test_backend_satisfies_protocol(self):
        """PerfwinBackend 实例满足 CollectBackend Protocol。"""
        backend = PerfwinBackend()
        assert isinstance(backend, CollectBackend)

    def test_start_creates_and_starts_monitor(self):
        """测试 start 创建并启动 Monitor。"""
        mock_monitor = _make_mock_monitor()
        mock_filter = MagicMock()
        mock_perfwin = MagicMock()
        mock_perfwin.Monitor.return_value = mock_monitor

        with patch.dict(sys.modules, {"perfwin": mock_perfwin}):
            backend = PerfwinBackend()
            backend.start(
                interval=2.0,
                duration=60.0,
                process_filter=mock_filter,
                top_n_cpu=10,
                top_n_gpu=10,
                enable_aggregation=True,
            )
        mock_perfwin.Monitor.assert_called_once()
        mock_monitor.start.assert_called_once()

    def test_stop_calls_monitor_stop(self):
        """测试 stop 调用 Monitor 的 stop 方法。"""
        mock_monitor = _make_mock_monitor()
        mock_perfwin = MagicMock()
        mock_perfwin.Monitor.return_value = mock_monitor

        with patch.dict(sys.modules, {"perfwin": mock_perfwin}):
            backend = PerfwinBackend()
            backend.start(
                interval=2.0,
                duration=60.0,
                process_filter=None,
                top_n_cpu=None,
                top_n_gpu=None,
                enable_aggregation=True,
            )
            backend.stop()
        mock_monitor.stop.assert_called_once()

    def test_is_running_delegates_to_monitor(self):
        """测试 is_running 委托给 Monitor。"""
        mock_monitor = _make_mock_monitor(is_running=True)
        mock_perfwin = MagicMock()
        mock_perfwin.Monitor.return_value = mock_monitor

        with patch.dict(sys.modules, {"perfwin": mock_perfwin}):
            backend = PerfwinBackend()
            backend.start(
                interval=2.0,
                duration=60.0,
                process_filter=None,
                top_n_cpu=None,
                top_n_gpu=None,
                enable_aggregation=True,
            )
            assert backend.is_running() is True

    def test_buffer_len_delegates_to_monitor(self):
        """测试 buffer_len 委托给 Monitor。"""
        mock_monitor = _make_mock_monitor(buffer_len=3)
        mock_perfwin = MagicMock()
        mock_perfwin.Monitor.return_value = mock_monitor

        with patch.dict(sys.modules, {"perfwin": mock_perfwin}):
            backend = PerfwinBackend()
            backend.start(
                interval=2.0,
                duration=60.0,
                process_filter=None,
                top_n_cpu=None,
                top_n_gpu=None,
                enable_aggregation=True,
            )
            assert backend.buffer_len() == 3

    def test_before_start_returns_safe_defaults(self):
        """测试 start 前返回安全默认值。"""
        backend = PerfwinBackend()
        assert not backend.is_running()
        assert backend.buffer_len() == 0
        result = backend.get_result()
        assert result.samples == []


# ---------------------------------------------------------------------------
# PerformanceCollector 单测（mock backend）
# ---------------------------------------------------------------------------


class TestPerformanceCollector:
    """PerformanceCollector 测试套件。"""

    def _make_collector_with_mock_backend(self, mock_monitor=None):
        """返回 (collector, mock_backend)，backend 已注入。"""
        mock_monitor = mock_monitor or _make_mock_monitor()
        mock_perfwin = MagicMock()
        mock_pf = MagicMock()
        mock_perfwin.ProcessFilter.return_value = mock_pf
        mock_perfwin.Monitor.return_value = mock_monitor

        collector = PerformanceCollector("test-device")
        collector.set_backend_host("http://mock-host")

        with patch.dict(sys.modules, {"perfwin": mock_perfwin}):
            request = _make_start_request()
            collector.start_collect(request)

        return collector, mock_monitor

    def test_start_collect_sets_collecting(self):
        """测试 start_collect 设置采集中状态。"""
        collector, _ = self._make_collector_with_mock_backend()
        status = collector.get_status()
        assert status.is_collecting

    def test_stop_collect_stops_monitor(self):
        """测试 stop_collect 停止 Monitor。"""
        mock_monitor = _make_mock_monitor(is_running=True)
        collector, _ = self._make_collector_with_mock_backend(mock_monitor)
        collector.stop_collect()
        mock_monitor.stop.assert_called()

    def test_duplicate_start_returns_already_started(self):
        """测试重复 start 返回已启动。"""
        collector, _ = self._make_collector_with_mock_backend()
        # 同一个 collect_id 再次调用
        mock_perfwin = MagicMock()
        mock_perfwin.ProcessFilter.return_value = MagicMock()
        mock_perfwin.Monitor.return_value = _make_mock_monitor()

        with patch.dict(sys.modules, {"perfwin": mock_perfwin}):
            result = collector.start_collect(_make_start_request())
        assert result["status"] == "already_started"

    def test_conflict_different_collect_id(self):
        """测试不同 collect_id 冲突。"""
        collector, _ = self._make_collector_with_mock_backend()
        mock_perfwin = MagicMock()
        mock_perfwin.ProcessFilter.return_value = MagicMock()
        mock_perfwin.Monitor.return_value = _make_mock_monitor()

        with patch.dict(sys.modules, {"perfwin": mock_perfwin}):
            other = CollectStartRequest(
                collect_id="other-id",
                interval=2,
                timeout=60,
                target_processes=[TargetProcess(name="proc")],
            )
            result = collector.start_collect(other)
        assert result["status"] == "conflict"

    def test_mixed_filter_raises_error(self):
        """测试混合筛选模式抛出错误。"""
        collector = PerformanceCollector("test-device")
        mock_perfwin = MagicMock()

        with patch.dict(sys.modules, {"perfwin": mock_perfwin}):
            req = CollectStartRequest(
                collect_id="mix-001",
                interval=2,
                timeout=60,
                target_processes=[
                    TargetProcess(name="proc1", pids=[100]),
                    TargetProcess(name="proc2"),  # 无 pids
                ],
            )
            result = collector.start_collect(req)
        assert result["status"] == "error"
        assert "混合" in result["message"]


class TestPerfharmonyBackend:
    """Perfharmony 后端的 mock 契约测试。"""

    def test_harmony_backend_does_not_import_perfwin(self):
        """鸿蒙后端只应创建 perfharmony.Monitor。"""
        mock_monitor = _make_mock_monitor()
        mock_perfharmony = MagicMock()
        mock_perfharmony.Monitor.return_value = mock_monitor
        original_perfwin = sys.modules.pop("perfwin", None)
        try:
            with patch.dict(sys.modules, {"perfharmony": mock_perfharmony}):
                backend = PerfharmonyBackend(udid="HDC-UDID-001", hdc_path="tools/hdc/hdc.exe")
                backend.start(
                    interval=2.0,
                    duration=60.0,
                    package="com.example.app",
                )
            mock_perfharmony.Monitor.assert_called_once()
            assert mock_perfharmony.Monitor.call_args.kwargs["udid"] == "HDC-UDID-001"
            assert mock_perfharmony.Monitor.call_args.kwargs["hdc_path"] == "tools/hdc/hdc.exe"
            assert mock_perfharmony.Monitor.call_args.kwargs["package"] == "com.example.app"
        finally:
            if original_perfwin is not None:
                sys.modules["perfwin"] = original_perfwin

    def test_empty_udid_is_rejected(self):
        """没有 HDC UDID 时不能创建鸿蒙后端。"""
        with pytest.raises(ValueError, match="device_sn/HDC UDID"):
            PerfharmonyBackend(udid=" ")


class TestHarmonyCollector:
    """Collector 的设备身份和样本兼容测试。"""

    def test_database_id_and_hdc_udid_are_independent(self):
        """平台设备 ID 与 HDC UDID 不相等时仍使用 HDC UDID 启动。"""
        mock_monitor = _make_mock_monitor()
        mock_perfharmony = MagicMock()
        mock_perfharmony.Monitor.return_value = mock_monitor
        request = CollectStartRequest(
            collect_id="harmony-001",
            interval=2,
            timeout=60,
            target_processes=[TargetProcess(name="com.example.app")],
            device_type="harmony_mobile",
            device_sn="HDC-UDID-001",
        )
        collector = PerformanceCollector("env-machine-id")
        with patch.dict(sys.modules, {"perfharmony": mock_perfharmony}):
            result = collector.start_collect(request)
        assert result["status"] == "started"
        assert mock_perfharmony.Monitor.call_args.kwargs["udid"] == "HDC-UDID-001"
        # 0.2.0：目标应用以 package 包名传入 SP_daemon -PKG
        assert mock_perfharmony.Monitor.call_args.kwargs["package"] == "com.example.app"
        collector.stop_collect()

    def test_harmony_sample_dict_is_converted(self):
        """perfharmony 返回的 dict 样本可转换为 Worker 上报格式。"""
        collector = PerformanceCollector("env-machine-id")
        collector.configure_device("harmony_pc", "HDC-UDID-001")
        collector._collect_id = "collect-001"
        sample = {
            "sequence": 1,
            "elapsed_ms": 1000,
            "timestamp": "2026-07-24T00:00:00Z",
            "system": {"cpu_percent": 25.0},
            "hwinfo_raw": {"Harmony CPU Usage": {"value": 25.0, "unit": "%"}},
            "processes": [{
                "pid": 200,
                "name": "com.example.app",
                "cpu_percent": 10.0,
                "working_set_mb": 12.0,
            }],
            "aggregated": [{
                "name": "com.example.app",
                "pids": [200],
                "cpu_percent_total": 10.0,
                "working_set_mb_total": 12.0,
            }],
            "top_n_cpu": None,
            "top_n_gpu": None,
        }
        converted = collector._convert_sample_to_report(sample)
        assert converted["sequence"] == 1
        assert converted["processes"][0]["pid"] == 200
        assert converted["aggregated"][0]["name"] == "com.example.app"
        assert converted["top_n_cpu"] is None

    def test_harmony_requires_device_sn(self):
        """鸿蒙采集缺少 UDID 时拒绝启动。"""
        collector = PerformanceCollector("env-machine-id")
        request = CollectStartRequest(
            collect_id="harmony-002",
            interval=2,
            timeout=60,
            target_processes=[],
            device_type="harmony_pc",
        )
        result = collector.start_collect(request)
        assert result["status"] == "error"
        assert "device_sn" in result["message"]

    def test_harmony_empty_targets_collect_system_only(self):
        """鸿蒙空目标列表只采系统指标，package 为 None。"""
        mock_monitor = _make_mock_monitor()
        mock_perfharmony = MagicMock()
        mock_perfharmony.Monitor.return_value = mock_monitor
        request = CollectStartRequest(
            collect_id="harmony-system-only",
            interval=2,
            timeout=60,
            target_processes=[],
            device_type="harmony_mobile",
            device_sn="HDC-UDID-001",
        )
        collector = PerformanceCollector("env-machine-id")
        with patch.dict(sys.modules, {"perfharmony": mock_perfharmony}):
            result = collector.start_collect(request)
        assert result["status"] == "started"
        assert mock_perfharmony.Monitor.call_args.kwargs["package"] is None
        collector.stop_collect()

    def test_harmony_rejects_multiple_target_apps(self):
        """0.2.0：鸿蒙一次采集仅支持一个应用。"""
        mock_perfharmony = MagicMock()
        request = CollectStartRequest(
            collect_id="harmony-multi-app",
            interval=2,
            timeout=60,
            target_processes=[
                TargetProcess(name="com.example.app"),
                TargetProcess(name="com.example.other"),
            ],
            device_type="harmony_mobile",
            device_sn="HDC-UDID-001",
        )
        collector = PerformanceCollector("env-machine-id")
        with patch.dict(sys.modules, {"perfharmony": mock_perfharmony}):
            result = collector.start_collect(request)
        assert result["status"] == "error"
        assert "一个应用" in result["message"]

    def test_harmony_rejects_pid_filter(self):
        """0.2.0：鸿蒙不支持按 PID 筛选。"""
        mock_perfharmony = MagicMock()
        request = CollectStartRequest(
            collect_id="harmony-pid-filter",
            interval=2,
            timeout=60,
            target_processes=[TargetProcess(name="com.example.app", pids=[123])],
            device_type="harmony_pc",
            device_sn="HDC-UDID-001",
        )
        collector = PerformanceCollector("env-machine-id")
        with patch.dict(sys.modules, {"perfharmony": mock_perfharmony}):
            result = collector.start_collect(request)
        assert result["status"] == "error"
        assert "PID" in result["message"]

    def test_backend_error_is_reported_as_failed_not_timeout(self):
        """后端因设备错误自停时应上报 failed，而不是 timed_out。"""
        notified = []
        mock_monitor = _make_mock_monitor(is_running=False)
        mock_monitor.last_error = "连续 3 轮未取得任何 P0 指标"
        mock_perfharmony = MagicMock()
        mock_perfharmony.Monitor.return_value = mock_monitor
        request = CollectStartRequest(
            collect_id="harmony-failed-001",
            interval=1,
            timeout=60,
            target_processes=[],
            device_type="harmony_pc",
            device_sn="HDC-UDID-001",
        )
        collector = PerformanceCollector("env-machine-id")
        collector._notify_terminal = lambda collect_id, status, message: notified.append(
            (collect_id, status, message)
        )
        with patch.dict(sys.modules, {"perfharmony": mock_perfharmony}):
            assert collector.start_collect(request)["status"] == "started"
            # 直接驱动一轮循环判断逻辑，避免依赖真实 sleep。
            collector._stop_event.set()
            # 恢复 stop 事件后手动执行自停分支。
            collector._stop_event.clear()
            backend = collector._backend
            assert backend is not None
            assert backend.is_running() is False
            assert backend.last_error() == "连续 3 轮未取得任何 P0 指标"
            collector._backend = None
            collector._notify_terminal(
                collector._collect_id,
                "failed" if backend.last_error() else "timed_out",
                backend.last_error() or "采集达到超时时间",
            )
        assert notified
        assert notified[-1][1] == "failed"
        assert "P0" in notified[-1][2]
        collector.stop_collect()

    def test_harmony_backend_last_error_property_and_callable(self):
        """兼容 Monitor.last_error 为属性或可调用两种形态。"""
        backend = PerfharmonyBackend(udid="HDC-1", hdc_path="tools/hdc/hdc.exe")
        backend._monitor = MagicMock()
        backend._monitor.last_error = "device offline"
        assert backend.last_error() == "device offline"
        backend._monitor.last_error = MagicMock(return_value="shell timeout")
        assert backend.last_error() == "shell timeout"


class TestHarmonyBundleCollapse:
    """鸿蒙进程列表按包名归组为应用粒度。"""

    def test_collapse_merges_colon_subprocesses(self):
        """主进程 + 冒号子进程归组为一行，PID 取主进程。"""
        rows = [
            (100, "com.huawei.it.works"),
            (101, "com.huawei.it.works:Native_libadapter0"),
            (102, "com.huawei.it.works:Native_libadapter1"),
            (103, "com.huawei.it.works:Native_libadapter2"),
            (104, "com.huawei.it.works:Native_libadapter3"),
            (200, "com.other.app"),
        ]
        result = PerformanceCollector._collapse_harmony_bundles(rows)
        assert result == [(100, "com.huawei.it.works"), (200, "com.other.app")]

    def test_collapse_prefers_main_pid_even_if_listed_later(self):
        """子进程行先出现时，后续主进程行的 PID 仍胜出。"""
        rows = [
            (301, "com.huawei.it.works:render"),
            (300, "com.huawei.it.works"),
        ]
        result = PerformanceCollector._collapse_harmony_bundles(rows)
        assert result == [(300, "com.huawei.it.works")]

    def test_collapse_keeps_non_bundle_names(self):
        """冒号前缀不含点号或含斜杠的进程不归组。"""
        rows = [
            (1, "kworker/0:1"),
            (2, "init:zygote"),
        ]
        result = PerformanceCollector._collapse_harmony_bundles(rows)
        assert result == rows

    def test_bundle_base_normalizes_subprocess_package(self):
        """传入子进程名时归一为包名（-PKG 兜底）。"""
        base = PerformanceCollector._harmony_bundle_base(
            "com.huawei.it.works:Native_libadapter0"
        )
        assert base == "com.huawei.it.works"
