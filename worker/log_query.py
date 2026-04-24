"""
日志查询模块。

提供三种查询模式：
1. lines - 返回最后 N 行
2. request_id - grep 搜索所有日志文件
3. time_range - 时间区间过滤
"""

import logging
import os
import platform
import subprocess
from datetime import datetime, timedelta
from typing import Optional

logger = logging.getLogger(__name__)

# 日志时间格式
LOG_TIME_FORMAT = "%Y-%m-%d %H:%M:%S"

# 停止搜索的时间阈值（request_id 模式）
STOP_SEARCH_THRESHOLD_SECONDS = 300  # 5 分钟


class LogQueryError(Exception):
    """日志查询参数错误。"""
    pass


def collect_log_files(log_path: str) -> list[str]:
    """
    收集日志文件列表（当前 + 轮转备份）。

    按从新到旧排序：worker.log -> worker.log.1 -> ... -> worker.log.5

    Args:
        log_path: 当前日志文件路径（如 worker.log）

    Returns:
        存在的日志文件路径列表，从新到旧排序
    """
    files = []

    # 当前日志文件
    if os.path.exists(log_path):
        files.append(log_path)

    # 轮转备份文件
    for i in range(1, 6):
        backup_path = f"{log_path}.{i}"
        if os.path.exists(backup_path):
            files.append(backup_path)

    return files


def parse_log_time(line: str) -> Optional[datetime]:
    """
    解析日志行的时间戳。

    日志格式：2026-04-24 10:00:00,123 [request_id] LEVEL name: message

    Args:
        line: 日志行

    Returns:
        datetime 对象，解析失败返回 None
    """
    # 取前 19 个字符作为日期时间部分
    if len(line) < 19:
        return None

    dt_str = line[:19]

    try:
        return datetime.strptime(dt_str, LOG_TIME_FORMAT)
    except ValueError:
        return None


def query_by_lines(log_path: str, lines: int) -> tuple[str, int]:
    """
    按 lines 查询日志。

    Args:
        log_path: 日志文件路径
        lines: 返回行数

    Returns:
        (日志内容, 行数)
    """
    with open(log_path, encoding="utf-8", errors="replace") as f:
        all_lines = f.readlines()
        last_lines = all_lines[-lines:] if len(all_lines) > lines else all_lines
        content = "".join(last_lines)

    return content, len(last_lines)


def grep_request_id_in_file(file_path: str, request_id: str) -> list[str]:
    """
    使用系统命令在单个文件中搜索 request_id。

    Args:
        file_path: 日志文件路径
        request_id: 要搜索的 request_id

    Returns:
        匹配的日志行列表
    """
    # 搜索模式：[request_id]（方括号内精确匹配）
    pattern = f"[{request_id}]"

    try:
        if platform.system() == "Windows":
            # Windows 使用 findstr /C: 进行字面字符串匹配
            # /C: 后紧跟搜索字符串，方括号作为字面值而非正则字符集
            result = subprocess.run(
                ["findstr", f"/C:{pattern}", file_path],
                capture_output=True,
                text=True,
                encoding="utf-8",
                timeout=30,
            )
        else:
            # Linux/Mac 使用 grep -F（固定字符串匹配）
            result = subprocess.run(
                ["grep", "-F", pattern, file_path],
                capture_output=True,
                text=True,
                timeout=30,
            )

        if result.stdout:
            return result.stdout.splitlines()
        return []

    except subprocess.TimeoutExpired:
        logger.warning(f"Grep timeout for file: {file_path}")
        return []
    except Exception as e:
        logger.error(f"Grep failed for file {file_path}: {e}")
        return []


def query_by_request_id(log_path: str, request_id: str) -> tuple[str, int, int]:
    """
    按 request_id 查询日志。

    搜索所有日志文件，带停止优化：
    - 找到最早匹配时间后，往前 5 分钟无该 request_id 则停止

    Args:
        log_path: 当前日志文件路径
        request_id: 要搜索的 request_id

    Returns:
        (日志内容, 行数, 扫描文件数)
    """
    log_files = collect_log_files(log_path)

    all_matches = []
    earliest_time: Optional[datetime] = None
    files_scanned = 0

    for file_path in log_files:
        files_scanned += 1
        matches = grep_request_id_in_file(file_path, request_id)

        if not matches:
            continue

        # 解析每行时间，更新最早时间
        for line in matches:
            line_time = parse_log_time(line)
            if line_time:
                if earliest_time is None or line_time < earliest_time:
                    earliest_time = line_time

        all_matches.extend(matches)

        # 停止优化检查
        if earliest_time:
            # 检查该文件最后一行的时间
            # 如果最后一行时间 < earliest_time - 5分钟，停止搜索
            if matches:
                last_line_time = parse_log_time(matches[-1])
                if last_line_time:
                    threshold = earliest_time - timedelta(seconds=STOP_SEARCH_THRESHOLD_SECONDS)
                    if last_line_time < threshold:
                        logger.debug(
                            f"Stop searching older files: last_line_time={last_line_time}, "
                            f"threshold={threshold}"
                        )
                        break

    # 按时间排序（旧到新）
    if all_matches:
        all_matches.sort(key=lambda line: parse_log_time(line) or datetime.min)

    content = "\n".join(all_matches)
    return content, len(all_matches), files_scanned


def query_by_time_range(
    log_path: str,
    start_time: datetime,
    end_time: datetime,
) -> tuple[str, int, int]:
    """
    按时间区间查询日志。

    Args:
        log_path: 当前日志文件路径
        start_time: 起始时间
        end_time: 结束时间

    Returns:
        (日志内容, 行数, 扫描文件数)
    """
    log_files = collect_log_files(log_path)

    all_matches = []
    files_scanned = 0
    should_stop = False

    for file_path in log_files:
        if should_stop:
            break

        files_scanned += 1

        with open(file_path, encoding="utf-8", errors="replace") as f:
            for line in f:
                line_time = parse_log_time(line.rstrip("\n\r"))

                if line_time is None:
                    continue

                # 时间区间过滤
                if start_time <= line_time <= end_time:
                    all_matches.append(line.rstrip("\n\r"))

                # 停止优化：日志文件是从旧到新追加写入的
                # 当遇到晚于 end_time 的行时，已经过了查询区间上限，后续所有行都更晚
                if line_time > end_time:
                    should_stop = True
                    break

    # 重新排序为整体旧到新
    all_matches.sort(key=lambda line: parse_log_time(line) or datetime.min)

    content = "\n".join(all_matches)
    return content, len(all_matches), files_scanned


def parse_iso_time(time_str: str) -> datetime:
    """
    解析 ISO 8601 时间字符串。

    支持格式：
    - 2026-04-24T10:00:00
    - 2026-04-24T10:00:00+08:00

    Args:
        time_str: 时间字符串

    Returns:
        datetime 对象

    Raises:
        ValueError: 无法解析时间
    """
    # 尝试带时区格式
    try:
        return datetime.fromisoformat(time_str)
    except ValueError:
        pass

    # 尝试不带时区格式（使用本地时区）
    try:
        return datetime.strptime(time_str, "%Y-%m-%dT%H:%M:%S")
    except ValueError:
        raise ValueError(f"无法解析时间：{time_str}")


def validate_query_params(
    lines: Optional[int],
    request_id: Optional[str],
    start_time: Optional[str],
    end_time: Optional[str],
) -> tuple[str, Optional[int], Optional[str], Optional[datetime], Optional[datetime]]:
    """
    校验查询参数，返回查询模式和和解后的参数。

    Args:
        lines: 行数参数
        request_id: request_id 参数
        start_time: 起始时间字符串
        end_time: 结束时间字符串

    Returns:
        (查询模式, lines, request_id, start_time_dt, end_time_dt)

    Raises:
        LogQueryError: 参数校验失败
    """
    # 判断查询模式
    has_lines = lines is not None
    has_request_id = request_id is not None
    has_time_range = start_time is not None or end_time is not None

    # 互斥校验
    modes = sum([has_lines, has_request_id, has_time_range])
    if modes > 1:
        raise LogQueryError("参数冲突：lines/request_id/start_time+end_time 三选一")

    # 默认使用 lines 模式
    if modes == 0:
        return "lines", 400, None, None, None

    # lines 模式
    if has_lines:
        if lines < 1 or lines > 2000:
            raise LogQueryError("lines 范围应为 1-2000")
        return "lines", lines, None, None, None

    # request_id 模式
    if has_request_id:
        if not request_id:
            raise LogQueryError("request_id 不能为空")
        return "request_id", None, request_id, None, None

    # 时间区间模式
    if has_time_range:
        if not start_time or not end_time:
            raise LogQueryError("时间区间查询需要同时提供 start_time 和 end_time")

        # 解析时间
        try:
            start_dt = parse_iso_time(start_time)
            end_dt = parse_iso_time(end_time)
        except ValueError as e:
            raise LogQueryError(f"时间格式无效：{e}")

        # 校验时间顺序
        if end_dt <= start_dt:
            raise LogQueryError("end_time 必须大于 start_time")

        # 校验时间区间（最多 5 分钟）
        duration = (end_dt - start_dt).total_seconds()
        if duration > 300:
            raise LogQueryError("时间区间不能超过 5 分钟")

        return "time_range", None, None, start_dt, end_dt

    # 不应该到达这里
    raise LogQueryError("未知查询模式")