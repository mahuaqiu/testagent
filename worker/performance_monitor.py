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
        self._target_processes: list[TargetProcess] = []
        self._start_time: datetime | None = None
        self._collecting: bool = False
        self._collect_thread: threading.Thread | None = None
        self._stop_event: threading.Event = threading.Event()
        self._lock: threading.Lock = threading.Lock()

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
                # 如果已有采集任务，先停止
                logger.warning(f"已有采集任务正在进行: {self._collect_id}, 将停止后重新开始")
                self._stop_collect_internal()

            # 记录采集配置
            self._collect_id = request.collect_id
            self._interval = request.interval
            self._target_processes = request.target_processes
            self._start_time = datetime.now(timezone.utc)
            self._collecting = True
            self._stop_event.clear()

            # 启动采集线程
            self._collect_thread = threading.Thread(
                target=self._collect_loop,
                daemon=True,
            )
            self._collect_thread.start()

            logger.info(
                f"开始采集: collect_id={self._collect_id}, "
                f"interval={self._interval}s, "
                f"target_processes={len(self._target_processes)}"
            )

            return {
                "status": "started",
                "message": f"开始采集，频率{self._interval}秒",
            }

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
            try:
                # 采集数据（占位实现）
                data = self._collect_data()

                # 上报数据（占位实现）
                self._report_data(data)

            except Exception as e:
                logger.error(f"采集异常: {e}", exc_info=True)

            # 等待下一次采集
            self._stop_event.wait(self._interval)

    def _collect_data(self) -> dict[str, Any]:
        """采集性能数据（占位实现）。"""
        # 计算相对时间
        relative_time = 0
        if self._start_time:
            relative_time = int((datetime.now(timezone.utc) - self._start_time).total_seconds())

        # 返回占位数据（实际采集逻辑后续实现）
        return {
            "collect_id": self._collect_id,
            "device_id": self.device_id,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "relative_time": relative_time,
            "system": {
                "cpu_usage": 0.0,
                "gpu_usage": 0.0,
                "commit_memory": 0.0,
                "memory_usage": 0.0,
                "power": 0,
                "cpu_speed": 0.0,
                "cpu_temp": 0,
                "process_handles": 0,
                "upload_speed": 0.0,
                "download_speed": 0.0,
            },
            "target_processes": [],
            "top10_cpu": [],
            "top10_gpu": [],
        }

    def _report_data(self, data: dict[str, Any]) -> None:
        """上报数据到后端（占位实现）。"""
        if not self._backend_host:
            logger.debug("后端地址未配置，跳过上报")
            return

        # 占位实现：实际 HTTP 调用后续实现
        logger.debug(f"上报数据: relative_time={data.get('relative_time')}")

    def get_processes(self, search: str | None = None) -> list[ProcessInfo]:
        """获取进程列表（占位实现）。

        Args:
            search: 模糊搜索进程名（可选）

        Returns:
            进程列表
        """
        # 返回占位数据（实际采集逻辑后续实现）
        # 返回一些示例进程
        sample_processes = [
            ProcessInfo(name="chrome.exe", pid=1234, cpu_usage=5.2, memory_usage=120.5, gpu_usage=8.5),
            ProcessInfo(name="chrome.exe", pid=2345, cpu_usage=4.8, memory_usage=95.2, gpu_usage=5.2),
            ProcessInfo(name="node.exe", pid=4567, cpu_usage=6.1, memory_usage=80.5, gpu_usage=0),
            ProcessInfo(name="python.exe", pid=7890, cpu_usage=3.2, memory_usage=50.5, gpu_usage=0),
            ProcessInfo(name="vscode.exe", pid=5678, cpu_usage=2.5, memory_usage=150.0, gpu_usage=5.2),
        ]

        # 搜索过滤
        if search:
            sample_processes = [
                p for p in sample_processes
                if search.lower() in p.name.lower()
            ]

        return sample_processes


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