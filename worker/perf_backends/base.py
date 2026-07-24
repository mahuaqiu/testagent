"""采集后端抽象接口。"""

from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class CollectBackend(Protocol):
    """性能采集后端 Protocol。"""

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
            process_filter: 进程筛选器
            top_n_cpu: CPU top N 个进程数量（可选）
            top_n_gpu: GPU top N 个进程数量（可选）
            enable_aggregation: 是否启用聚合统计
        """
        ...

    def stop(self) -> None:
        """停止采集。"""
        ...

    def is_running(self) -> bool:
        """检查采集是否在运行。

        Returns:
            是否运行中
        """
        ...

    def buffer_len(self) -> int:
        """获取缓冲区长度。

        Returns:
            缓冲区中的样本数量
        """
        ...

    def get_result(self) -> Any:
        """获取采集结果。

        返回对象应包含 .samples 属性。

        Returns:
            采集结果对象
        """
        ...
