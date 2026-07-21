"""Worker 日志查询测试。"""

from worker.log_query import collect_log_files, query_by_request_id, query_by_time_range


def _write(path, lines):
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def test_request_id_query_supports_long_lines_and_all_rotated_files(tmp_path):
    log_path = tmp_path / "worker.log"
    request_id = "request-123"
    _write(log_path, ["2026-07-21 10:10:00,000 [-] INFO current"])
    _write(tmp_path / "worker.log.1", [f"2026-07-21 10:09:00,000 [{request_id}] INFO {'x' * 10000}"])
    _write(tmp_path / "worker.log.6", [f"2026-07-21 10:01:00,000 [{request_id}] INFO oldest"])

    content, count, files_scanned = query_by_request_id(str(log_path), request_id)

    assert count == 2
    assert files_scanned == 3
    assert "oldest" in content
    assert "x" * 10000 in content
    assert collect_log_files(str(log_path))[-1].endswith("worker.log.6")


def test_time_range_continues_into_older_rotated_file(tmp_path):
    log_path = tmp_path / "worker.log"
    _write(log_path, ["2026-07-21 10:10:00,000 [-] INFO current"])
    _write(tmp_path / "worker.log.1", ["2026-07-21 10:00:30,000 [-] INFO target"])

    from datetime import datetime

    content, count, files_scanned = query_by_time_range(
        str(log_path),
        datetime(2026, 7, 21, 10, 0, 0),
        datetime(2026, 7, 21, 10, 1, 0),
    )

    assert count == 1
    assert files_scanned == 2
    assert "target" in content
