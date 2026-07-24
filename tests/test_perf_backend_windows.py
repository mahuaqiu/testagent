"""PerfwinBackend 和 PerformanceCollector 的 mock 单测。"""

import sys
import threading
from unittest.mock import MagicMock, patch
import pytest

from worker.perf_backends.base import CollectBackend
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
