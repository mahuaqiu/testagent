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
    state: str = Field("idle", description="采集状态")
    last_sequence: int | None = Field(None, description="最后已读取的采样序号")
    last_elapsed_ms: int | None = Field(None, description="最后样本相对时间（毫秒）")


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
        self._stopping: bool = False
        self._collect_thread: threading.Thread | None = None
        self._stop_event: threading.Event = threading.Event()
        self._lock: threading.Lock = threading.Lock()
        self._monitor: Any | None = None  # perfwin Monitor 实例

        # 后端上报地址（从配置获取）
        self._backend_host: str | None = None
        self._report_lock = threading.Lock()
        self._last_sequence: int | None = None
        self._last_elapsed_ms: int | None = None
        # GPU 回退日志节流，避免每个采样周期刷屏
        self._gpu_fallback_logged: bool = False
        self._gpu_source_last: str | None = None

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
                state="running" if self._collecting else "idle",
                last_sequence=self._last_sequence,
                last_elapsed_ms=self._last_elapsed_ms,
            )

    def start_collect(self, request: CollectStartRequest) -> dict[str, Any]:
        """开始采集。

        Args:
            request: 开始采集请求

        Returns:
            响应结果
        """
        self._flush_spool()

        with self._lock:
            if self._stopping:
                logger.warning("拒绝在采集停止期间启动新任务: current=%s, requested=%s", self._collect_id, request.collect_id)
                return {"status": "conflict", "message": "设备正在停止上一采集任务"}
            if self._collecting:
                # 检查是否是相同任务
                if self._is_same_task(request):
                    logger.info(f"任务已存在且参数相同: {request.collect_id}")
                    return {"status": "already_started", "message": "任务已开始"}
                # 同一设备不允许用新任务静默覆盖旧任务
                logger.warning("拒绝覆盖正在运行的采集任务: current=%s, requested=%s", self._collect_id, request.collect_id)
                return {"status": "conflict", "message": f"设备已有采集任务运行: {self._collect_id}"}
            self._start_time = datetime.now(timezone.utc)
            self._collecting = True
            self._stopping = False
            self._stop_event.clear()
            self._last_sequence = None
            self._last_elapsed_ms = None
            self._collect_id = request.collect_id
            self._interval = request.interval
            self._timeout = request.timeout
            self._target_processes = list(request.target_processes)
            self._gpu_fallback_logged = False
            self._gpu_source_last = None

            # 创建 perfwin Monitor
            try:
                self._create_monitor(request)
            except Exception as e:
                self._collecting = False
                self._collect_id = None
                self._target_processes = []
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

            if request and request.collect_id and request.collect_id != self._collect_id:
                logger.warning(
                    f"停止采集 ID 不匹配: 请求={request.collect_id}, "
                    f"当前={self._collect_id}"
                )
                return {
                    "status": "error",
                    "message": f"采集ID不匹配，当前采集ID为 {self._collect_id}",
                }

            # 先标记停止，避免新任务在清理期间覆盖当前任务。
            self._collecting = False
            self._stopping = True

        # 停止 Monitor、等待线程和上报终态都在锁外执行。
        self._stop_collect_internal()

        return {
            "status": "stopped",
            "message": "采集已停止",
        }

    def _stop_collect_internal(self) -> None:
        """内部停止采集方法（不加锁）。"""
        self._collecting = False
        self._stop_event.set()

        # 先停止 perfwin 采集线程，确保不会再产生新样本。
        monitor = self._monitor
        if monitor:
            try:
                monitor.stop()
            except Exception as e:
                logger.warning(f"停止 perfwin Monitor 异常: {e}")
            self._monitor = None

        # 主线程等待采集循环退出；采集线程不能 join 自己。
        if (
            self._collect_thread
            and self._collect_thread.is_alive()
            and threading.current_thread() is not self._collect_thread
        ):
            self._collect_thread.join(timeout=2)

        # 停止后再排空缓冲区，避免最后一个采样周期丢失。
        self._drain_monitor_buffer(monitor)
        self._collect_thread = None

        collect_id = self._collect_id
        self._notify_terminal(collect_id, "stopped", "用户停止采集")

        # 清理状态
        self._collect_id = None
        self._stopping = False
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
                # 先发送历史 spool，再读取本轮增量数据。
                self._flush_spool()
                self._drain_monitor_buffer()
            except Exception as error:
                collect_id = self._collect_id
                logger.error(f"采集线程异常: {error}", exc_info=True)
                self._stop_event.set()
                monitor = self._monitor
                self._monitor = None
                if monitor:
                    try:
                        monitor.stop()
                    except Exception as stop_error:
                        logger.warning(f"异常清理 perfwin Monitor 失败: {stop_error}")
                    try:
                        self._drain_monitor_buffer(monitor)
                    except Exception as drain_error:
                        logger.warning(f"异常清理采样缓冲区失败: {drain_error}")
                self._notify_terminal(collect_id, "failed", str(error))
                with self._lock:
                    self._collecting = False
                    self._stopping = False
                    self._collect_thread = None
                    self._collect_id = None
                    self._target_processes = []
                    self._start_time = None
                break

            # 检查 perfwin 是否仍在运行（timeout 后自动停止）
            if self._monitor and not self._monitor.is_running():
                collect_id = self._collect_id
                logger.info("perfwin Monitor 已停止（timeout 达到）: %s", collect_id)
                self._stop_event.set()
                monitor = self._monitor
                self._monitor = None
                self._drain_monitor_buffer(monitor)
                self._notify_terminal(collect_id, "timed_out", "采集达到超时时间")
                with self._lock:
                    self._collecting = False
                    self._stopping = False
                    self._collect_thread = None
                    self._collect_id = None
                    self._target_processes = []
                    self._start_time = None
                break

    def _drain_monitor_buffer(self, monitor=None) -> None:
        """读取并上报当前 Monitor 缓冲区中的增量样本。"""
        monitor = monitor or self._monitor
        if not monitor or monitor.buffer_len() <= 0:
            return
        result = monitor.get_result()
        samples = [self._convert_sample_to_report(sample) for sample in result.samples]
        if samples:
            self._last_sequence = samples[-1]["sequence"]
            self._last_elapsed_ms = samples[-1]["elapsed_ms"]
            self._report_samples(samples)

    def _convert_sample_to_report(self, sample) -> dict:
        """将 perfwin Sample 直接转换为 dict 格式。"""
        system = dict(sample.system)
        hwinfo_raw = dict(sample.hwinfo_raw)
        # PDH 可能返回 gpu_percent=0 且 gpu_adapters=[]（假空闲/解析失败），
        # 此时用 HWiNFO 传感器做运行时回退，避免前端 GPU 曲线全 0。
        original_source = system.get("gpu_source")
        system = self._enrich_gpu_with_hwinfo(system, hwinfo_raw)
        self._log_gpu_source(system, original_source)
        sequence = int(sample.sequence)
        elapsed_ms = int(sample.elapsed_ms)
        return {
            "sample_key": f"{self._collect_id}:{sequence}",
            "sequence": sequence,
            "elapsed_ms": elapsed_ms,
            "timestamp": sample.timestamp,
            "system": system,
            "hwinfo_raw": hwinfo_raw,
            "processes": self._convert_processes(sample.processes),
            "aggregated": self._convert_aggregated(sample.aggregated),
            "top_n_cpu": self._convert_aggregated(sample.top_n_cpu),
            "top_n_gpu": self._convert_aggregated(sample.top_n_gpu),
        }

    def _log_gpu_source(self, system: dict, original_source: str | None) -> None:
        """记录 GPU 来源切换/回退，定位为何没用上 Rust PDH。"""
        source = system.get("gpu_source")
        gpu_percent = system.get("gpu_percent")
        adapters = system.get("gpu_adapters") or []
        if source != self._gpu_source_last:
            if source == "rust_pdh":
                logger.info(
                    "GPU 主路径: rust_pdh, gpu_percent=%s, adapters=%s",
                    gpu_percent,
                    len(adapters),
                )
            elif source == "hwinfo_fallback":
                logger.error(
                    "GPU 回退到 HWiNFO: original_source=%s, gpu_percent=%s, adapters=%s "
                    "(Rust PDH 未给出有效适配器数据)",
                    original_source,
                    gpu_percent,
                    len(adapters),
                )
            else:
                logger.error(
                    "GPU 来源异常/不可用: source=%s, original_source=%s, gpu_percent=%s, adapters=%s",
                    source,
                    original_source,
                    gpu_percent,
                    len(adapters),
                )
            self._gpu_source_last = source
            self._gpu_fallback_logged = source == "hwinfo_fallback"
        elif source == "hwinfo_fallback" and not self._gpu_fallback_logged:
            logger.error(
                "GPU 持续使用 HWiNFO 回退: gpu_percent=%s",
                gpu_percent,
            )
            self._gpu_fallback_logged = True

    def _enrich_gpu_with_hwinfo(self, system: dict, hwinfo_raw: dict | None) -> dict:
        """当 PDH GPU 为空/假 0 时，用 HWiNFO 字段回填 system.gpu_percent。"""
        if not hwinfo_raw:
            return system

        gpu_percent = system.get("gpu_percent")
        adapters = system.get("gpu_adapters") or []
        source = system.get("gpu_source")

        # 已有真实适配器 → 保留 Rust 主路径
        if adapters:
            return system
        # rust_pdh 且非 0，即使 adapters 暂时为空也保留
        if source == "rust_pdh" and gpu_percent not in (None, 0, 0.0):
            return system
        # 仅当无适配器且值为 None/0 时才考虑回退
        if gpu_percent not in (None, 0, 0.0):
            return system

        candidates = (
            "GPU D3D Usage",
            "GPU Core Load",
            "GPU Usage",
            "GPU Utilization",
            "Total GPU Usage",
            "GPU Load",
        )
        for key in candidates:
            item = hwinfo_raw.get(key)
            if not isinstance(item, dict):
                continue
            value = item.get("value")
            if value is None:
                continue
            try:
                num = float(value)
            except (TypeError, ValueError):
                continue
            if not (0 <= num <= 100):
                continue
            enriched = dict(system)
            enriched["gpu_percent"] = num
            enriched["gpu_source"] = "hwinfo_fallback"
            if not self._gpu_fallback_logged:
                logger.error(
                    "Worker GPU 回退: rust_pdh 无效(original_source=%s, gpu_percent=%s, adapters=%s) "
                    "-> HWiNFO[%s]=%s",
                    source,
                    gpu_percent,
                    len(adapters),
                    key,
                    num,
                )
                self._gpu_fallback_logged = True
            return enriched
        return system

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

    def _spool_path(self, collect_id: str | None = None) -> str | None:
        """返回当前采集任务的可靠本地队列路径。"""
        from worker.config import get_base_dir
        import os

        collect_id = collect_id or self._collect_id
        if not collect_id:
            return None
        perf_dir = os.path.join(get_base_dir(), "data", "performance")
        os.makedirs(perf_dir, exist_ok=True)
        return os.path.join(perf_dir, f"{collect_id}.spool")

    def _append_spool(self, payload: dict) -> None:
        """将未成功上报的批次追加到本地队列并持久化。"""
        import json
        import os

        path = self._spool_path(payload.get("collect_id"))
        if not path:
            logger.error("无法持久化性能数据：缺少 collect_id")
            return
        with open(path, "a", encoding="utf-8") as file:
            file.write(json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n")
            file.flush()
            os.fsync(file.fileno())
        logger.info("性能数据已进入本地重试队列: %s, 样本数: %s", path, len(payload["samples"]))

    def _terminal_spool_path(self, collect_id: str) -> str:
        """返回终态事件的本地队列路径。"""
        path = self._spool_path(collect_id)
        if not path:
            raise ValueError("缺少 collect_id")
        return f"{path}.terminal"

    def _append_terminal_spool(self, payload: dict) -> None:
        """持久化未成功发送的终态事件。"""
        import json
        import os

        path = self._terminal_spool_path(payload["collect_id"])
        with open(path, "w", encoding="utf-8") as file:
            file.write(json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n")
            file.flush()
            os.fsync(file.fileno())
        logger.info("性能终态事件已进入本地重试队列: %s", path)

    def _post_terminal_payload(self, payload: dict) -> bool:
        """发送一个终态事件。

        返回 True 表示该事件无需再重试（成功，或平台确认记录已不存在）。
        """
        if not self._backend_host:
            return False
        import requests

        response = requests.post(
            f"{self._backend_host}/api/core/performance-monitor/collect/worker-event",
            json=payload,
            timeout=10,
        )
        if response.status_code in (200, 201, 202):
            return True
        # 404：采集记录已被删除/对账中断后清理，继续重试只会刷日志
        if response.status_code == 404:
            logger.warning(
                "性能终态事件对应采集记录不存在，丢弃本地队列: collect_id=%s body=%s",
                payload.get("collect_id"),
                response.text,
            )
            return True
        logger.warning("性能终态事件上报失败: status=%s body=%s", response.status_code, response.text)
        return False

    def _flush_terminal_spool_unlocked(self) -> None:
        """重试本地终态事件队列。"""
        if not self._backend_host:
            return
        import glob
        import json
        import os
        from worker.config import get_base_dir

        perf_dir = os.path.join(get_base_dir(), "data", "performance")
        for path in sorted(glob.glob(os.path.join(perf_dir, "*.spool.terminal"))):
            try:
                with open(path, "r", encoding="utf-8") as file:
                    payload = json.load(file)
                if self._post_terminal_payload(payload):
                    os.remove(path)
            except FileNotFoundError:
                continue
            except Exception as error:
                logger.warning("性能终态事件重试异常: %s", error)

    def _post_payload(self, payload: dict) -> bool:
        """发送一个批次，返回服务端是否接受。"""
        if not self._backend_host:
            return False
        import requests

        url = f"{self._backend_host}/api/core/performance-monitor/report"
        response = requests.post(url, json=payload, timeout=10)
        if response.status_code in (200, 201, 202):
            try:
                body = response.json()
            except ValueError:
                body = {}
            if body.get("status") in (None, "success", "accepted", "partial"):
                return True
            logger.warning("性能数据上报业务失败: body=%s", body)
            return False
        logger.warning("性能数据上报失败: status=%s", response.status_code)
        return False

    def _flush_spool(self) -> None:
        """在线程安全的上下文中重试所有本地队列。"""
        with self._report_lock:
            self._flush_spool_unlocked()
            self._flush_terminal_spool_unlocked()
    def _flush_spool_unlocked(self) -> None:
        """在已持有上报锁时重试本地队列。"""
        if not self._backend_host:
            return
        import glob
        import json
        import os
        from worker.config import get_base_dir

        perf_dir = os.path.join(get_base_dir(), "data", "performance")
        paths = sorted(glob.glob(os.path.join(perf_dir, "*.spool")))
        for path in paths:
            with open(path, "r", encoding="utf-8") as file:
                lines = file.readlines()

            remaining: list[str] = []
            for index, line in enumerate(lines):
                if not line.strip():
                    continue
                try:
                    payload = json.loads(line)
                except json.JSONDecodeError:
                    logger.error("性能 spool 存在损坏记录，保留原文: %s", path)
                    remaining.extend(lines[index:])
                    break
                try:
                    accepted = self._post_payload(payload)
                except Exception as error:
                    logger.warning("性能 spool 重试异常: %s", error)
                    accepted = False
                if not accepted:
                    remaining.extend(lines[index:])
                    break

            temp_path = f"{path}.tmp"
            if remaining:
                with open(temp_path, "w", encoding="utf-8") as file:
                    file.writelines(remaining)
                    file.flush()
                    os.fsync(file.fileno())
                os.replace(temp_path, path)
            else:
                try:
                    os.remove(path)
                except FileNotFoundError:
                    pass

    def _report_samples(self, samples: list[dict]) -> None:
        """上报样本；网络失败时进入可靠本地队列。"""
        if not samples:
            return

        with self._report_lock:
            collect_id = self._collect_id
            payload = {
                "collect_id": collect_id,
                "device_id": self.device_id,
                "batch_id": f"{collect_id}:{samples[0]['sequence']}:{samples[-1]['sequence']}",
                "samples": samples,
            }

            if not self._backend_host:
                self._append_spool(payload)
                return

            try:
                self._flush_spool_unlocked()
                if self._post_payload(payload):
                    logger.info("性能数据上报成功: 样本数=%s, collect_id=%s", len(samples), collect_id)
                else:
                    self._append_spool(payload)
            except Exception as error:
                logger.warning("性能数据上报异常: %s", error)
                self._append_spool(payload)

    def _notify_terminal(self, collect_id: str | None, status: str, message: str) -> None:
        """通知平台采集进入终态，失败时进入可靠本地队列。"""
        if not collect_id:
            return
        payload = {
            "collect_id": collect_id,
            "device_id": self.device_id,
            "status": status,
            "message": message,
            "last_sequence": self._last_sequence,
            "last_elapsed_ms": self._last_elapsed_ms,
        }
        with self._report_lock:
            if not self._backend_host:
                self._append_terminal_spool(payload)
                return
            try:
                self._flush_spool_unlocked()
                self._flush_terminal_spool_unlocked()
                if not self._post_terminal_payload(payload):
                    self._append_terminal_spool(payload)
            except Exception as error:
                logger.warning("平台终态通知异常: %s", error)
                self._append_terminal_spool(payload)
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
