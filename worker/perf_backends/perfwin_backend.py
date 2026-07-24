"""Windows 性能采集后端（perfwin）。"""

from typing import Any


class _EmptyResult:
    """空的采集结果对象。"""

    @property
    def samples(self) -> list:
        """返回空样本列表。"""
        return []

    def to_dicts(self) -> list:
        """转换为字典列表。"""
        return []


class PerfwinBackend:
    """基于 perfwin 库的 Windows 性能采集后端。"""

    def __init__(self) -> None:
        """初始化后端。"""
        self._monitor: Any | None = None

    def start(
        self,
        *,
        interval: float,
        duration: float | None,
        process_filter: Any,
        top_n_cpu: int | None,
        top_n_gpu: int | None,
        enable_aggregation: bool,
    ) -> None:
        """启动采集。

        Args:
            interval: 采集间隔（秒）
            duration: 采集持续时间（秒），None 表示无限期
            process_filter: 进程筛选器（perfwin.ProcessFilter 实例或 None）
            top_n_cpu: CPU top N 个进程数量（可选）
            top_n_gpu: GPU top N 个进程数量（可选）
            enable_aggregation: 是否启用聚合统计
        """
        import perfwin

        # 创建并启动 Monitor
        self._monitor = perfwin.Monitor(
            interval=interval,
            duration=duration,
            process_filter=process_filter,
            top_n_cpu=top_n_cpu,
            top_n_gpu=top_n_gpu,
            enable_aggregation=enable_aggregation,
        )
        self._monitor.start()

    def stop(self) -> None:
        """停止采集。"""
        if self._monitor:
            self._monitor.stop()

    def is_running(self) -> bool:
        """检查采集是否在运行。

        Returns:
            是否运行中
        """
        return bool(self._monitor and self._monitor.is_running())

    def buffer_len(self) -> int:
        """获取缓冲区长度。

        Returns:
            缓冲区中的样本数量
        """
        return self._monitor.buffer_len() if self._monitor else 0

    def get_result(self) -> Any:
        """获取采集结果。

        Returns:
            采集结果对象（含 .samples 属性）
        """
        if self._monitor:
            return self._monitor.get_result()
        return _EmptyResult()
