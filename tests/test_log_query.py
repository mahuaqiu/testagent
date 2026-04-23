"""
日志查询模块测试。
"""

import os
import tempfile
from datetime import datetime, timedelta

import pytest

from worker.log_query import (
    collect_log_files,
    parse_log_time,
    parse_iso_time,
    query_by_lines,
    query_by_request_id,
    query_by_time_range,
    validate_query_params,
    LogQueryError,
)


@pytest.fixture
def temp_log_dir():
    """创建临时日志目录。"""
    with tempfile.TemporaryDirectory() as tmpdir:
        yield tmpdir


@pytest.fixture
def sample_log_file(temp_log_dir):
    """创建示例日志文件。"""
    log_path = os.path.join(temp_log_dir, "worker.log")
    content = """2026-04-24 10:00:00,123 [req-001] INFO logger: message 1
2026-04-24 10:01:00,456 [req-002] INFO logger: message 2
2026-04-24 10:02:00,789 [req-001] INFO logger: message 3
2026-04-24 10:03:00,111 [req-003] INFO logger: message 4
"""
    with open(log_path, "w", encoding="utf-8") as f:
        f.write(content)
    return log_path


# ========== 时间解析测试 ==========


def test_parse_log_time():
    """测试日志时间解析。"""
    line = "2026-04-24 10:00:00,123 [req-001] INFO logger: message"
    result = parse_log_time(line)
    assert result == datetime(2026, 4, 24, 10, 0, 0)


def test_parse_log_time_with_different_format():
    """测试不同格式的日志时间。"""
    line = "2026-04-24 10:30:45 [req-001] INFO logger: message"
    result = parse_log_time(line)
    assert result == datetime(2026, 4, 24, 10, 30, 45)


def test_parse_log_time_invalid():
    """测试无效日志时间。"""
    line = "invalid line"
    result = parse_log_time(line)
    assert result is None


def test_parse_log_time_short_line():
    """测试过短的日志行。"""
    line = "short"
    result = parse_log_time(line)
    assert result is None


def test_parse_iso_time():
    """测试 ISO 时间解析。"""
    result = parse_iso_time("2026-04-24T10:00:00")
    assert result == datetime(2026, 4, 24, 10, 0, 0)


def test_parse_iso_time_with_timezone():
    """测试带时区的 ISO 时间解析。"""
    result = parse_iso_time("2026-04-24T10:00:00+08:00")
    # 转换为本地时间后验证
    assert result.hour == 10


def test_parse_iso_time_invalid():
    """测试无效 ISO 时间。"""
    with pytest.raises(ValueError, match="无法解析时间"):
        parse_iso_time("invalid-time")


# ========== 文件收集测试 ==========


def test_collect_log_files(sample_log_file):
    """测试日志文件收集。"""
    # 创建备份文件
    backup_path = f"{sample_log_file}.1"
    with open(backup_path, "w", encoding="utf-8") as f:
        f.write("backup content")

    files = collect_log_files(sample_log_file)
    assert len(files) == 2
    assert sample_log_file in files
    assert backup_path in files


def test_collect_log_files_order(sample_log_file):
    """测试日志文件收集顺序（从新到旧）。"""
    for i in range(1, 4):
        backup_path = f"{sample_log_file}.{i}"
        with open(backup_path, "w", encoding="utf-8") as f:
            f.write(f"backup {i}")

    files = collect_log_files(sample_log_file)
    # worker.log 应该是第一个
    assert files[0] == sample_log_file
    # worker.log.1 应该是第二个
    assert files[1] == f"{sample_log_file}.1"


def test_collect_log_files_no_backup(sample_log_file):
    """测试没有备份文件的收集。"""
    files = collect_log_files(sample_log_file)
    assert len(files) == 1
    assert files[0] == sample_log_file


def test_collect_log_files_nonexistent():
    """测试不存在的日志文件路径。"""
    files = collect_log_files("/nonexistent/path/worker.log")
    assert len(files) == 0


# ========== lines 查询测试 ==========


def test_query_by_lines(sample_log_file):
    """测试 lines 查询。"""
    content, count = query_by_lines(sample_log_file, 2)
    assert count == 2
    # 最后两行是 req-001 (message 3) 和 req-003 (message 4)
    assert "req-001" in content
    assert "req-003" in content


def test_query_by_lines_all(sample_log_file):
    """测试 lines 查询所有行。"""
    content, count = query_by_lines(sample_log_file, 10)
    assert count == 4  # 文件只有 4 行
    assert "req-001" in content
    assert "req-002" in content
    assert "req-003" in content


def test_query_by_lines_one(sample_log_file):
    """测试 lines 查询一行。"""
    content, count = query_by_lines(sample_log_file, 1)
    assert count == 1
    assert "req-003" in content  # 最后一行


# ========== request_id 查询测试 ==========


def test_query_by_request_id(sample_log_file):
    """测试 request_id 查询。"""
    content, count, files_scanned = query_by_request_id(sample_log_file, "req-001")
    assert count == 2
    assert "req-001" in content
    assert files_scanned == 1


def test_query_by_request_id_not_found(sample_log_file):
    """测试 request_id 未找到。"""
    content, count, files_scanned = query_by_request_id(sample_log_file, "not-exist")
    assert count == 0
    assert content == ""


def test_query_by_request_id_multiple_files(temp_log_dir):
    """测试 request_id 跨多个文件查询。"""
    log_path = os.path.join(temp_log_dir, "worker.log")

    # 当前日志文件
    with open(log_path, "w", encoding="utf-8") as f:
        f.write("2026-04-24 10:05:00,123 [req-001] INFO logger: recent\n")

    # 备份文件（更早）
    backup_path = f"{log_path}.1"
    with open(backup_path, "w", encoding="utf-8") as f:
        f.write("2026-04-24 10:00:00,123 [req-001] INFO logger: old\n")

    content, count, files_scanned = query_by_request_id(log_path, "req-001")
    assert count == 2
    assert "old" in content
    assert "recent" in content
    # 结果按时间排序（旧到新）
    lines = content.split("\n")
    assert "old" in lines[0]
    assert "recent" in lines[1]


# ========== 时间区间查询测试 ==========


def test_query_by_time_range(sample_log_file):
    """测试时间区间查询。"""
    start = datetime(2026, 4, 24, 10, 0, 0)
    end = datetime(2026, 4, 24, 10, 1, 30)

    content, count, files_scanned = query_by_time_range(
        sample_log_file, start, end
    )
    assert count == 2  # 10:00:00 和 10:01:00
    assert "req-001" in content
    assert "req-002" in content


def test_query_by_time_range_full(sample_log_file):
    """测试时间区间查询全部。"""
    start = datetime(2026, 4, 24, 10, 0, 0)
    end = datetime(2026, 4, 24, 10, 5, 0)

    content, count, files_scanned = query_by_time_range(
        sample_log_file, start, end
    )
    assert count == 4


def test_query_by_time_range_no_match(sample_log_file):
    """测试时间区间无匹配。"""
    start = datetime(2026, 4, 24, 11, 0, 0)
    end = datetime(2026, 4, 24, 11, 5, 0)

    content, count, files_scanned = query_by_time_range(
        sample_log_file, start, end
    )
    assert count == 0
    assert content == ""


def test_query_by_time_range_order(sample_log_file):
    """测试时间区间查询结果排序。"""
    start = datetime(2026, 4, 24, 9, 0, 0)
    end = datetime(2026, 4, 24, 11, 0, 0)

    content, count, files_scanned = query_by_time_range(
        sample_log_file, start, end
    )
    # 结果按时间排序（旧到新）
    lines = content.split("\n")
    assert "req-001" in lines[0]  # 10:00:00
    assert "req-003" in lines[3]  # 10:03:00


# ========== 参数校验测试 ==========


def test_validate_params_default():
    """测试默认参数。"""
    mode, lines, rid, start, end = validate_query_params(None, None, None, None)
    assert mode == "lines"
    assert lines == 400


def test_validate_params_lines():
    """测试 lines 参数。"""
    mode, lines, rid, start, end = validate_query_params(100, None, None, None)
    assert mode == "lines"
    assert lines == 100


def test_validate_params_lines_invalid():
    """测试 lines 参数范围无效。"""
    with pytest.raises(LogQueryError, match="lines 范围"):
        validate_query_params(0, None, None, None)

    with pytest.raises(LogQueryError, match="lines 范围"):
        validate_query_params(3000, None, None, None)


def test_validate_params_request_id():
    """测试 request_id 参数。"""
    mode, lines, rid, start, end = validate_query_params(None, "req-001", None, None)
    assert mode == "request_id"
    assert rid == "req-001"


def test_validate_params_request_id_empty():
    """测试 request_id 为空。"""
    with pytest.raises(LogQueryError, match="request_id 不能为空"):
        validate_query_params(None, "", None, None)


def test_validate_params_time_range():
    """测试时间区间参数。"""
    mode, lines, rid, start, end = validate_query_params(
        None, None,
        "2026-04-24T10:00:00",
        "2026-04-24T10:05:00"
    )
    assert mode == "time_range"
    assert start == datetime(2026, 4, 24, 10, 0, 0)
    assert end == datetime(2026, 4, 24, 10, 5, 0)


def test_validate_params_time_range_missing_end():
    """测试时间区间缺少 end_time。"""
    with pytest.raises(LogQueryError, match="需要同时提供"):
        validate_query_params(None, None, "2026-04-24T10:00:00", None)


def test_validate_params_time_range_missing_start():
    """测试时间区间缺少 start_time。"""
    with pytest.raises(LogQueryError, match="需要同时提供"):
        validate_query_params(None, None, None, "2026-04-24T10:05:00")


def test_validate_params_conflict_lines_and_request_id():
    """测试参数冲突（lines + request_id）。"""
    with pytest.raises(LogQueryError, match="参数冲突"):
        validate_query_params(100, "req-001", None, None)


def test_validate_params_conflict_lines_and_time():
    """测试参数冲突（lines + time_range）。"""
    with pytest.raises(LogQueryError, match="参数冲突"):
        validate_query_params(100, None, "2026-04-24T10:00:00", "2026-04-24T10:05:00")


def test_validate_params_conflict_request_id_and_time():
    """测试参数冲突（request_id + time_range）。"""
    with pytest.raises(LogQueryError, match="参数冲突"):
        validate_query_params(None, "req-001", "2026-04-24T10:00:00", "2026-04-24T10:05:00")


def test_validate_params_time_range_exceed():
    """测试时间区间超过 5 分钟。"""
    with pytest.raises(LogQueryError, match="时间区间不能超过 5 分钟"):
        validate_query_params(
            None, None,
            "2026-04-24T10:00:00",
            "2026-04-24T10:10:00"  # 10 分钟
        )


def test_validate_params_time_range_invalid_order():
    """测试时间顺序无效。"""
    with pytest.raises(LogQueryError, match="end_time 必须大于 start_time"):
        validate_query_params(
            None, None,
            "2026-04-24T10:05:00",
            "2026-04-24T10:00:00"
        )


def test_validate_params_time_range_invalid_format():
    """测试时间格式无效。"""
    with pytest.raises(LogQueryError, match="时间格式无效"):
        validate_query_params(
            None, None,
            "invalid-time",
            "2026-04-24T10:05:00"
        )