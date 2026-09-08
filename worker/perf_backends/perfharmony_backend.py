"""鸿蒙性能采集后端，延迟加载独立 perfharmony 库。"""

from __future__ import annotations

import logging
import time
from types import SimpleNamespace
from typing import Any

logger = logging.getLogger(__name__)

# 精准模式 PID 复核周期（秒）；应用未启动时也按此节奏探测。
PID_CHECK_INTERVAL_SECS = 30.0


class _EmptyResult:
    """后端尚未启动时的空结果。"""

    samples: list = []

    def to_dicts(self) -> list:
        """返回空字典列表。"""
        return []


class PerfharmonyBackend:
    """通过 HDC UDID 采集 HarmonyOS 设备性能。

    match_mode：
    - fuzzy：设备端 SP_daemon -PKG 按包名采集（自动含主进程+子进程）；
    - exact：ps -ef 精确解析 PID 后 SP_daemon -PID 单进程采集；
      应用未启动时先仅采系统指标，heartbeat 每 30s 复核，
      PID 出现/变化时自动重启跟随（旧样本暂存不丢）。
    """

    def __init__(self, *, udid: str, hdc_path: str | None = None) -> None:
        if not udid or not udid.strip():
            raise ValueError("鸿蒙性能采集必须提供 device_sn/HDC UDID")
        self.udid = udid.strip()
        self.hdc_path = hdc_path or None
        self._monitor: Any | None = None
        self._package: str | None = None
        self._pid: int | None = None
        self._match_mode: str = "fuzzy"
        self._interval: float = 1.0
        self._duration: float | None = None
        self._last_pid_check: float = 0.0
        self._restarting: bool = False
        self._pending: list = []

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
        match_mode: str = "fuzzy",
    ) -> None:
        """创建并启动 Harmony Monitor。"""
        self._interval = interval
        self._duration = duration
        self._package = package
        self._match_mode = match_mode if match_mode in ("fuzzy", "exact") else "fuzzy"
        self._pid = None
        if self._match_mode == "exact" and package:
            self._pid = self._resolve_pid(package)
        self._last_pid_check = time.monotonic()
        self._start_monitor()

    def _start_monitor(self) -> None:
        """按当前 package/pid 组合启动 Monitor；exact 未命中 PID 时仅采系统指标。"""
        perfharmony = self._module()
        self._monitor = perfharmony.Monitor(
            udid=self.udid,
            hdc_path=self.hdc_path,
            interval=self._interval,
            duration=self._duration,
            package=self._package if self._match_mode == "fuzzy" else None,
            pid=self._pid,
        )
        self._monitor.start()

    def heartbeat(self) -> None:
        """精准模式下按 30s 节奏复核 PID；由采集循环在每轮排空前调用。"""
        if self._match_mode != "exact" or not self._package:
            return
        if time.monotonic() - self._last_pid_check < PID_CHECK_INTERVAL_SECS:
            return
        self._last_pid_check = time.monotonic()
        try:
            pid = self._resolve_pid(self._package)
        except Exception as error:
            logger.warning("鸿蒙精准采集 PID 复核失败: %s", error)
            return
        if pid == self._pid:
            return
        logger.info("鸿蒙精准采集 PID 变更: %s -> %s，重启采集流", self._pid, pid)
        old = self._monitor
        # 重启窗口期视为运行中，避免外层采集循环误判终态。
        self._restarting = True
        try:
            if old is not None:
                self._stash_pending(old)
                old.stop()
        finally:
            self._pid = pid
            self._last_pid_check = time.monotonic()
            try:
                self._start_monitor()
            finally:
                self._restarting = False

    def _resolve_pid(self, package: str) -> int | None:
        """按包名精确解析主进程 PID：进程名恰为包名优先，其次首个「包名:子进程」。"""
        values = self.list_processes(None)
        candidates = [
            (pid, name) for pid, name in values if self._bundle_base(name) == package
        ]
        if not candidates:
            return None
        for pid, name in candidates:
            if name == package:
                return pid
        return candidates[0][0]

    @staticmethod
    def _bundle_base(name: str) -> str:
        """与 Rust bundle_base_name/worker _harmony_bundle_base 语义一致。"""
        base, sep, _ = name.partition(":")
        if sep and "." in base and "/" not in base:
            return base
        return name

    def _stash_pending(self, monitor: Any) -> None:
        """重启前暂存旧 Monitor 未上报样本，避免换 PID 时丢数据。"""
        try:
            self._pending.extend(monitor.get_result().samples)
        except Exception as error:
            logger.warning("暂存旧 Monitor 样本失败: %s", error)

    def stop(self) -> None:
        """停止 Harmony Monitor。"""
        if self._monitor:
            self._monitor.stop()

    def is_running(self) -> bool:
        """返回采集线程是否运行（重启窗口期内视为运行）。"""
        return self._restarting or bool(self._monitor and self._monitor.is_running())

    def buffer_len(self) -> int:
        """返回待上报样本数。"""
        return self._monitor.buffer_len() if self._monitor else 0

    def get_result(self) -> Any:
        """读取并排空增量采样结果；含换 PID 前暂存的旧样本。"""
        if self._pending:
            pending, self._pending = self._pending, []
            current = self._monitor.get_result().samples if self._monitor else []
            return SimpleNamespace(samples=pending + current)
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
