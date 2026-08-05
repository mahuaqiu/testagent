"""HDC 宿主进程归属和退出管理。

HDC 命令可能会拉起一个长期运行的 hdc.exe 服务。仅在命令进程结束时
调用 wait() 无法回收这个服务，而且按进程名杀进程会误伤用户自己启动的 HDC。
这里记录本项目实际启动的进程身份，退出和安装时只回收记录中仍然匹配的 PID。
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
import threading
from dataclasses import asdict, dataclass
from typing import Any

import psutil

from common.packaging import get_base_dir

logger = logging.getLogger(__name__)

_REGISTRY_LOCK = threading.RLock()
_CREATE_TIME_TOLERANCE_SECONDS = 2.0


@dataclass(frozen=True)
class HdcProcessRecord:
    """可用于确认进程身份的最小信息。"""

    pid: int
    exe_path: str
    create_time: float


def get_registry_path() -> str:
    """返回 HDC 进程归属文件路径。"""
    return os.path.join(get_base_dir(), "data", "hdc_processes.json")


def _normalise_path(path: str | None) -> str:
    """规范化 Windows 路径，避免大小写和分隔符造成误判。"""
    if not path:
        return ""
    return os.path.normcase(os.path.normpath(os.path.abspath(path)))


def _read_records() -> list[HdcProcessRecord]:
    """读取记录；文件损坏时返回空列表并保留运行能力。"""
    try:
        with open(get_registry_path(), encoding="utf-8") as file:
            payload = json.load(file)
    except (FileNotFoundError, OSError, json.JSONDecodeError, TypeError, ValueError):
        return []

    records: list[HdcProcessRecord] = []
    for item in payload if isinstance(payload, list) else []:
        try:
            records.append(
                HdcProcessRecord(
                    pid=int(item["pid"]),
                    exe_path=str(item["exe_path"]),
                    create_time=float(item["create_time"]),
                )
            )
        except (KeyError, TypeError, ValueError):
            logger.warning("忽略无效的 HDC 进程归属记录: %r", item)
    return records


def _write_records(records: list[HdcProcessRecord]) -> None:
    """原子写入记录，避免升级或异常退出时留下半个 JSON 文件。"""
    path = get_registry_path()
    directory = os.path.dirname(path)
    try:
        os.makedirs(directory, exist_ok=True)
        fd, temp_path = tempfile.mkstemp(prefix="hdc_processes.", suffix=".tmp", dir=directory)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as file:
                json.dump([asdict(record) for record in records], file, ensure_ascii=False, indent=2)
                file.flush()
                os.fsync(file.fileno())
            os.replace(temp_path, path)
        finally:
            if os.path.exists(temp_path):
                os.remove(temp_path)
    except OSError as exc:
        logger.warning("保存 HDC 进程归属失败: %s", exc)


def _get_process_record(process: psutil.Process, fallback_path: str) -> HdcProcessRecord | None:
    """读取进程身份；进程已结束时返回 None。"""
    try:
        exe_path = process.exe() or fallback_path
        return HdcProcessRecord(
            pid=process.pid,
            exe_path=exe_path,
            create_time=process.create_time(),
        )
    except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess, OSError):
        return None


def _append_record(record: HdcProcessRecord) -> None:
    """添加记录并去重。"""
    with _REGISTRY_LOCK:
        records = _read_records()
        records = [
            existing
            for existing in records
            if existing.pid != record.pid
            or abs(existing.create_time - record.create_time) > _CREATE_TIME_TOLERANCE_SECONDS
        ]
        records.append(record)
        _write_records(records)


def register_process(process: Any, hdc_path: str) -> None:
    """登记本项目启动的 HDC 命令进程。"""
    try:
        pid = int(process.pid)
        record = _get_process_record(psutil.Process(pid), hdc_path)
    except (AttributeError, TypeError, ValueError, psutil.Error):
        record = None
    if record is not None:
        _append_record(record)


def _is_descendant(pid: int, root_pid: int, parent_by_pid: dict[int, int]) -> bool:
    """判断 PID 是否属于 root_pid 的进程树。"""
    visited: set[int] = set()
    current = pid
    while current and current not in visited:
        if current == root_pid:
            return True
        visited.add(current)
        current = parent_by_pid.get(current, 0)
    return False


def register_launched_processes(
    root_pid: int,
    hdc_path: str,
    baseline_pids: set[int],
    launched_at: float,
) -> None:
    """登记本次命令进程树中的同路径 HDC 进程。

    HDC 服务可能在客户端退出后脱离父进程树，因此同时使用启动前 PID 快照、
    创建时间和完整路径。无法证明是在本次命令之后启动的进程不登记。
    """
    expected_path = _normalise_path(hdc_path)
    try:
        processes = list(psutil.process_iter(["pid", "ppid", "exe", "create_time"]))
    except psutil.Error as exc:
        logger.debug("扫描 HDC 子进程失败: %s", exc)
        return

    parent_by_pid = {
        int(process.info["pid"]): int(process.info.get("ppid") or 0)
        for process in processes
        if process.info.get("pid") is not None
    }
    records: list[HdcProcessRecord] = []
    for process in processes:
        info = process.info
        pid = int(info.get("pid") or 0)
        create_time = info.get("create_time")
        is_new_same_path = (
            bool(pid)
            and pid not in baseline_pids
            and create_time is not None
            and float(create_time) >= launched_at - _CREATE_TIME_TOLERANCE_SECONDS
            and _normalise_path(info.get("exe")) == expected_path
        )
        if not pid or not (_is_descendant(pid, root_pid, parent_by_pid) or is_new_same_path):
            continue
        if _normalise_path(info.get("exe")) != expected_path:
            continue
        if create_time is None:
            continue
        records.append(HdcProcessRecord(pid, str(info.get("exe") or hdc_path), float(create_time)))

    if not records:
        return
    with _REGISTRY_LOCK:
        existing = _read_records()
        identities = {(record.pid, record.create_time) for record in existing}
        existing.extend(record for record in records if (record.pid, record.create_time) not in identities)
        _write_records(existing)


def _record_matches_process(record: HdcProcessRecord, process: psutil.Process) -> bool:
    """严格校验 PID 当前是否仍是被记录的那个进程。"""
    try:
        current_path = _normalise_path(process.exe())
        return (
            current_path == _normalise_path(record.exe_path)
            and abs(process.create_time() - record.create_time) <= _CREATE_TIME_TOLERANCE_SECONDS
        )
    except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess, OSError):
        return False


def stop_owned_hdc_processes() -> int:
    """停止并清理本项目启动的 HDC 进程，返回实际终止的进程数。"""
    with _REGISTRY_LOCK:
        records = _read_records()
        matched: dict[int, HdcProcessRecord] = {}
        processes: list[psutil.Process] = []
        for record in records:
            try:
                process = psutil.Process(record.pid)
            except psutil.NoSuchProcess:
                continue
            if not _record_matches_process(record, process):
                logger.warning("HDC PID 身份已变化，跳过回收: pid=%s", record.pid)
                continue
            matched[process.pid] = record
            processes.append(process)

        killed = 0
        failed: set[int] = set()
        for process in processes:
            try:
                process.kill()
                killed += 1
            except psutil.NoSuchProcess:
                pass
            except (psutil.AccessDenied, psutil.ZombieProcess, OSError) as exc:
                logger.warning("终止项目启动的 HDC 失败: pid=%s, %s", process.pid, exc)
                failed.add(process.pid)

        alive: list[psutil.Process] = []
        if processes:
            try:
                _, alive = psutil.wait_procs(processes, timeout=3)
            except psutil.Error as exc:
                logger.debug("等待 HDC 进程退出失败: %s", exc)
                alive = processes

        # 终止失败或超时的记录保留给安装器兜底；身份不匹配的记录直接丢弃，避免 PID 重用误杀。
        remaining = [matched[process.pid] for process in alive if process.pid in matched]
        remaining.extend(
            matched[pid]
            for pid in failed
            if pid in matched and all(record.pid != pid for record in remaining)
        )
        _write_records(remaining)
        return killed


def capture_hdc_processes_before_launch(hdc_path: str) -> set[int]:
    """返回启动命令前的同路径 PID，用于排除外部已有 HDC。"""
    expected_path = _normalise_path(hdc_path)
    pids: set[int] = set()
    try:
        for process in psutil.process_iter(["pid", "exe"]):
            if _normalise_path(process.info.get("exe")) == expected_path:
                pids.add(int(process.info["pid"]))
    except psutil.Error as exc:
        logger.debug("扫描启动前 HDC 进程失败: %s", exc)
    return pids


def cleanup_stale_records() -> None:
    """清除已经退出或身份变化的历史记录。"""
    with _REGISTRY_LOCK:
        active: list[HdcProcessRecord] = []
        for record in _read_records():
            try:
                process = psutil.Process(record.pid)
            except psutil.NoSuchProcess:
                continue
            if _record_matches_process(record, process):
                active.append(record)
        _write_records(active)
