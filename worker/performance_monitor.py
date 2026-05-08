"""
性能监控模块。

管理性能数据采集状态，提供采集控制接口。
"""

import logging
import threading
from datetime import datetime, timezone
from typing import Any

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


class ProcessInfo(BaseModel):
    """进程信息模型。"""

    name: str = Field(..., description="进程名")
    pid: int = Field(..., description="进程ID")
    cpu_usage: float = Field(0.0, description="CPU使用率 %")
    memory_usage: float = Field(0.0, description="内存使用 MB")
    gpu_usage: float = Field(0.0, description="GPU使用率 %")


class TargetProcess(BaseModel):
    """目标进程配置。"""

    name: str = Field(..., description="进程名")
    pids: list[int] | None = Field(None, description="指定PID列表，空则采集该进程名下所有实例")


class CollectStartRequest(BaseModel):
    """开始采集请求。"""

    collect_id: str = Field(..., description="采集记录ID（由后端生成）")
    interval: int = Field(5, description="采集频率（秒）", ge=1, le=60)
    timeout: int = Field(43200, description="采集超时时间（秒），默认12小时", ge=60, le=86400)
    target_processes: list[TargetProcess] = Field(..., description="目标进程列表")


class CollectStopRequest(BaseModel):
    """停止采集请求。"""

    collect_id: str | None = Field(None, description="采集记录ID，不传则停止当前所有采集")


class CollectStatus(BaseModel):
    """采集状态。"""

    is_collecting: bool = Field(..., description="是否正在采集")
    collect_id: str | None = Field(None, description="当前采集ID")
    interval: int | None = Field(None, description="采集频率（秒）")
    target_processes: list[TargetProcess] | None = Field(None, description="目标进程列表")
    start_time: datetime | None = Field(None, description="采集开始时间")
    elapsed_seconds: int | None = Field(None, description="已采集时长（秒）")


class PerformanceCollector:
    """性能数据采集器。

    管理采集状态和定时任务。
    """

    def __init__(self, device_id: str):
        """初始化采集器。

        Args:
            device_id: 设备ID
        """
        self.device_id = device_id
        self._collect_id: str | None = None
        self._interval: int = 5
        self._timeout: int = 43200  # 默认 12 小时
        self._target_processes: list[TargetProcess] = []
        self._start_time: datetime | None = None
        self._collecting: bool = False
        self._collect_thread: threading.Thread | None = None
        self._stop_event: threading.Event = threading.Event()
        self._lock: threading.Lock = threading.Lock()
        self._monitor: Any | None = None  # perfwin Monitor 实例

        # 后端上报地址（从配置获取）
        self._backend_host: str | None = None

    def set_backend_host(self, host: str) -> None:
        """设置后端上报地址。

        Args:
            host: 后端地址，如 http://192.168.1.100:8080
        """
        self._backend_host = host

    def get_status(self) -> CollectStatus:
        """获取当前采集状态。"""
        with self._lock:
            elapsed = None
            if self._start_time:
                elapsed = int((datetime.now(timezone.utc) - self._start_time).total_seconds())

            return CollectStatus(
                is_collecting=self._collecting,
                collect_id=self._collect_id,
                interval=self._interval,
                target_processes=self._target_processes if self._collecting else None,
                start_time=self._start_time,
                elapsed_seconds=elapsed,
            )

    def start_collect(self, request: CollectStartRequest) -> dict[str, Any]:
        """开始采集。

        Args:
            request: 开始采集请求

        Returns:
            响应结果
        """
        with self._lock:
            if self._collecting:
                # 检查是否是相同任务
                if self._is_same_task(request):
                    logger.info(f"任务已存在且参数相同: {request.collect_id}")
                    return {"status": "already_started", "message": "任务已开始"}
                # 参数不同，停止旧任务
                logger.info(f"新任务参数不同，停止旧任务: {self._collect_id}")
                self._stop_collect_internal()

            # 记录采集配置
            self._collect_id = request.collect_id
            self._interval = request.interval
            self._timeout = request.timeout
            self._target_processes = request.target_processes
            self._start_time = datetime.now(timezone.utc)
            self._collecting = True
            self._stop_event.clear()

            # 创建 perfwin Monitor
            try:
                self._create_monitor(request)
            except ValueError as e:
                self._collecting = False
                return {"status": "error", "message": str(e)}

            # 启动采集线程
            self._collect_thread = threading.Thread(
                target=self._collect_loop,
                daemon=True,
            )
            self._collect_thread.start()

            logger.info(
                f"开始采集: collect_id={self._collect_id}, "
                f"interval={self._interval}s, "
                f"timeout={self._timeout}s, "
                f"target_processes={len(self._target_processes)}"
            )

            return {
                "status": "started",
                "message": f"开始采集，频率{self._interval}秒，超时{self._timeout}秒",
            }

    def _is_same_task(self, request: CollectStartRequest) -> bool:
        """检查新请求是否与当前任务相同。

        Args:
            request: 新的采集请求

        Returns:
            是否相同
        """
        if self._collect_id != request.collect_id:
            return False
        if self._interval != request.interval:
            return False
        # timeout 不同可以接受（不影响采集逻辑）
        if len(self._target_processes) != len(request.target_processes):
            return False
        for old, new in zip(self._target_processes, request.target_processes):
            if old.name != new.name:
                return False
            old_pids = set(old.pids or [])
            new_pids = set(new.pids or [])
            if old_pids != new_pids:
                return False
        return True

    def _create_monitor(self, request: CollectStartRequest) -> None:
        """创建 perfwin Monitor 实例。

        Args:
            request: 采集请求

        Raises:
            ValueError: 不支持混合筛选模式
        """
        import perfwin

        # 根据 target_processes 构建 ProcessFilter（不支持混合模式）
        all_have_pids = all(tp.pids for tp in request.target_processes)
        all_no_pids = all(not tp.pids for tp in request.target_processes)

        if not all_have_pids and not all_no_pids:
            raise ValueError("不支持混合筛选模式，请统一指定 PID 或进程名")

        if all_have_pids:
            # Pids 模式：收集所有指定的 PID
            pids = []
            for tp in request.target_processes:
                pids.extend(tp.pids)
            process_filter = perfwin.ProcessFilter(pids=pids)
        else:
            # Names 模式：收集所有进程名
            names = [tp.name for tp in request.target_processes]
            process_filter = perfwin.ProcessFilter(names=names)

        # 设置 duration = timeout（超时后自动停止）
        self._monitor = perfwin.Monitor(
            interval=float(request.interval),
            duration=float(request.timeout),  # 超时后自动停止
            process_filter=process_filter,
            top_n_cpu=10,
            top_n_gpu=10,
            enable_aggregation=True,
        )
        self._monitor.start()

    def stop_collect(self, request: CollectStopRequest | None = None) -> dict[str, Any]:
        """停止采集。

        Args:
            request: 停止采集请求（可选）

        Returns:
            响应结果
        """
        with self._lock:
            if not self._collecting:
                return {
                    "status": "stopped",
                    "message": "当前无采集任务",
                }

            # 如果指定了 collect_id，检查是否匹配
            if request and request.collect_id and request.collect_id != self._collect_id:
                logger.warning(
                    f"停止采集 ID 不匹配: 请求={request.collect_id}, "
                    f"当前={self._collect_id}"
                )
                return {
                    "status": "error",
                    "message": f"采集ID不匹配，当前采集ID为 {self._collect_id}",
                }

            self._stop_collect_internal()

            return {
                "status": "stopped",
                "message": "采集已停止",
            }

    def _stop_collect_internal(self) -> None:
        """内部停止采集方法（不加锁）。"""
        self._collecting = False
        self._stop_event.set()

        # 停止 perfwin Monitor
        if self._monitor:
            try:
                self._monitor.stop()
            except Exception as e:
                logger.warning(f"停止 perfwin Monitor 异常: {e}")
            self._monitor = None

        # 等待线程结束
        if self._collect_thread and self._collect_thread.is_alive():
            self._collect_thread.join(timeout=2)

        # 清理状态
        self._collect_id = None
        self._target_processes = []
        self._start_time = None

        logger.info(f"采集已停止: device_id={self.device_id}")

    def _collect_loop(self) -> None:
        """采集循环（后台线程）。"""
        while not self._stop_event.is_set():
            # 等待采集间隔
            self._stop_event.wait(self._interval)

            if self._stop_event.is_set():
                break

            try:
                # 从 perfwin buffer 获取增量数据
                if self._monitor and self._monitor.buffer_len() > 0:
                    result = self._monitor.get_result()
                    samples = []
                    for sample in result.samples:
                        samples.append(self._convert_sample_to_report(sample))

                    # 上报数组格式
                    if samples:
                        self._report_samples(samples)

            except Exception as e:
                logger.error(f"采集异常: {e}", exc_info=True)

            # 检查 perfwin 是否仍在运行（timeout 后自动停止）
            if self._monitor and not self._monitor.is_running():
                logger.info(f"perfwin Monitor 已停止（timeout 达到）: {self._collect_id}")
                self._stop_collect_internal()
                break

    def _convert_sample_to_report(self, sample) -> dict:
        """将 perfwin Sample 直接转换为 dict 格式（透传）。

        Args:
            sample: perfwin v0.3.0 Sample 对象

        Returns:
            转换后的数据字典，结构完全透传 perfwin 原始数据
        """
        return {
            "timestamp": sample.timestamp,
            "hwinfo_raw": dict(sample.hwinfo_raw),
            "processes": self._convert_processes(sample.processes),
            "aggregated": self._convert_aggregated(sample.aggregated),
            "top_n_cpu": self._convert_processes(sample.top_n_cpu),
            "top_n_gpu": self._convert_processes(sample.top_n_gpu),
        }

    def _convert_processes(self, processes) -> list[dict] | None:
        """转换进程列表为 dict 格式。

        Args:
            processes: perfwin ProcessInfo 列表或 None

        Returns:
            转换后的列表或 None
        """
        if processes is None:
            return None

        return [
            {
                "pid": p.pid,
                "name": p.name,
                "cpu_percent": p.cpu_percent,
                "working_set_mb": p.working_set_mb,
                "committed_memory_mb": p.committed_memory_mb,
                "gpu_percent": p.gpu_percent,
                "gpu_memory_mb": p.gpu_memory_mb,
                "handle_count": p.handle_count,
            }
            for p in processes
        ]

    def _convert_aggregated(self, aggregated) -> list[dict] | None:
        """转换汇总列表为 dict 格式。

        Args:
            aggregated: perfwin AggregatedProcessInfo 列表或 None

        Returns:
            转换后的列表或 None
        """
        if aggregated is None:
            return None

        return [
            {
                "name": a.name,
                "pids": list(a.pids),
                "cpu_percent_total": a.cpu_percent_total,
                "working_set_mb_total": a.working_set_mb_total,
                "committed_memory_mb_total": a.committed_memory_mb_total,
                "gpu_percent_total": a.gpu_percent_total,
                "handle_count_total": a.handle_count_total,
                "process_count": a.process_count,
            }
            for a in aggregated
        ]

    def _report_samples(self, samples: list[dict]) -> None:
        """上报样本数组到后端或本地持久化。

        Args:
            samples: 样本数据列表
        """
        if not samples:
            return

        payload = {
            "collect_id": self._collect_id,
            "device_id": self.device_id,
            "samples": samples,
        }

        # 如果后端地址为空，直接持久化
        if not self._backend_host:
            self._persist_samples(payload)
            return

        # 尝试 HTTP 上报
        try:
            import requests
            url = f"{self._backend_host}/api/core/performance-monitor/report"
            response = requests.post(url, json=payload, timeout=10)
            if response.status_code != 200:
                logger.warning(f"上报失败: {response.status_code}")
                self._persist_samples(payload)
        except Exception as e:
            logger.warning(f"上报异常: {e}")
            self._persist_samples(payload)

    def _persist_samples(self, payload: dict) -> None:
        """持久化数据到本地文件。

        Args:
            payload: 上报数据
        """
        from worker.config import get_base_dir
        import os
        import json

        # 创建目录
        perf_dir = os.path.join(get_base_dir(), "data", "performance")
        os.makedirs(perf_dir, exist_ok=True)

        # 文件路径：{collect_id}.log
        file_path = os.path.join(perf_dir, f"{self._collect_id}.log")

        # JSON Lines 格式：每行一条上报数据
        with open(file_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(payload, ensure_ascii=False) + "\n")

        logger.debug(f"性能数据已持久化: {file_path}, 样本数: {len(payload['samples'])}")

    def get_processes(self, search: str | None = None) -> list[ProcessInfo]:
        """获取所有进程列表及其资源使用率。

        Args:
            search: 模糊搜索进程名（可选）

        Returns:
            进程列表
        """
        import perfwin

        # 使用 perfwin.list_processes() 获取所有进程的 PID 和名称
        all_processes = perfwin.list_processes()

        if not all_processes:
            return []

        # 转换为接口格式
        process_list = []
        for pid, name in all_processes:
            if search and search.lower() not in name.lower():
                continue
            process_list.append(ProcessInfo(
                name=name,
                pid=pid,
                cpu_usage=0.0,  # list_processes 不返回资源使用率，需要单独采集
                memory_usage=0.0,
                gpu_usage=0.0,
            ))

        return process_list


# 设备采集器管理（全局单例）
_collectors: dict[str, PerformanceCollector] = {}
_collectors_lock: threading.Lock = threading.Lock()


def get_collector(device_id: str) -> PerformanceCollector:
    """获取或创建设备采集器。

    Args:
        device_id: 设备ID

    Returns:
        PerformanceCollector 实例
    """
    with _collectors_lock:
        if device_id not in _collectors:
            _collectors[device_id] = PerformanceCollector(device_id)
        return _collectors[device_id]


def remove_collector(device_id: str) -> None:
    """移除设备采集器。

    Args:
        device_id: 设备ID
    """
    with _collectors_lock:
        if device_id in _collectors:
            collector = _collectors[device_id]
            collector.stop_collect()
            del _collectors[device_id]