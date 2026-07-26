"""鸿蒙性能采集后端，延迟加载独立 perfharmony 库。"""

from __future__ import annotations

from typing import Any


class _EmptyResult:
    """后端尚未启动时的空结果。"""

    samples: list = []

    def to_dicts(self) -> list:
        """返回空字典列表。"""
        return []


class PerfharmonyBackend:
    """通过 HDC UDID 采集 HarmonyOS 设备性能。"""

    def __init__(self, *, udid: str, hdc_path: str | None = None) -> None:
        if not udid or not udid.strip():
            raise ValueError("鸿蒙性能采集必须提供 device_sn/HDC UDID")
        self.udid = udid.strip()
        self.hdc_path = hdc_path or None
        self._monitor: Any | None = None

    @staticmethod
    def _module():
        """按需导入，确保 Windows perfwin 路径不依赖 perfharmony。"""
        try:
            import perfharmony
        except ImportError as error:
            raise RuntimeError("未安装 perfharmony，请先安装对应的 wheel") from error
        return perfharmony

    def start(
        self,
        *,
        interval: float,
        duration: float | None,
        package: str | None = None,
    ) -> None:
        """创建并启动 Harmony Monitor。

        0.2.0：唯一数据源 SP_daemon，指标白名单制无逐项开关；
        一次采集限一个应用（-PKG 包名，含主进程+子进程）。
        """
        perfharmony = self._module()
        self._monitor = perfharmony.Monitor(
            udid=self.udid,
            hdc_path=self.hdc_path,
            interval=interval,
            duration=duration,
            package=package,
        )
        self._monitor.start()

    def stop(self) -> None:
        """停止 Harmony Monitor。"""
        if self._monitor:
            self._monitor.stop()

    def is_running(self) -> bool:
        """返回采集线程是否运行。"""
        return bool(self._monitor and self._monitor.is_running())

    def buffer_len(self) -> int:
        """返回待上报样本数。"""
        return self._monitor.buffer_len() if self._monitor else 0

    def get_result(self) -> Any:
        """读取并排空增量采样结果。"""
        return self._monitor.get_result() if self._monitor else _EmptyResult()

    def last_error(self) -> str | None:
        """读取 Monitor 最近一次设备/采集错误。"""
        if not self._monitor:
            return None
        error = getattr(self._monitor, "last_error", None)
        if callable(error):
            error = error()
        if error is None:
            return None
        text = str(error).strip()
        return text or None

    def list_processes(self, search: str | None = None) -> list[tuple[int, str]]:
        """读取 Harmony 设备进程列表。"""
        perfharmony = self._module()
        values = perfharmony.list_processes(self.udid, self.hdc_path)
        if not search:
            return values
        keyword = search.lower()
        return [(pid, name) for pid, name in values if keyword in name.lower()]
