# 日志查询接口扩展实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 扩展 `/worker/logs` 接口，支持三种查询模式：lines、request_id、时间区间

**Architecture:** 创建独立的 `worker/log_query.py` 模块处理日志查询逻辑，server.py 只负责参数校验和调用。使用系统 grep/findstr 命令优化 request_id 查询性能。

**Tech Stack:** Python, FastAPI, subprocess (grep/findstr)

---

## 文件结构

| 文件 | 职责 |
|------|------|
| `worker/log_query.py` | 日志查询核心逻辑（新建） |
| `worker/server.py` | 接口参数校验和调用（修改 get_logs 函数） |
| `tests/test_log_query.py` | 单元测试（新建） |

---

## Task 1: 创建日志查询核心模块

**Files:**
- Create: `worker/log_query.py`

- [ ] **Step 1: 创建模块骨架和日志文件收集函数**

```python
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
LOG_TIME_FORMAT_WITH_MS = "%Y-%m-%d %H:%M:%S,%f"

# 停止搜索的时间阈值（request_id 模式）
STOP_SEARCH_THRESHOLD_SECONDS = 300  # 5 分钟


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
```

- [ ] **Step 2: 添加日志时间解析函数**

```python
def parse_log_time(line: str) -> Optional[datetime]:
    """
    解析日志行的时间戳。

    日志格式：2026-04-24 10:00:00,123 [request_id] LEVEL name: message

    Args:
        line: 日志行

    Returns:
        datetime 对象，解析失败返回 None
    """
    # 尝试匹配时间戳前缀
    # 格式1: 2026-04-24 10:00:00,123
    # 格式2: 2026-04-24 10:00:00
    prefix = line[:25] if len(line) >= 25 else line

    # 尝试带毫秒格式
    try:
        # 取前 19 个字符作为日期时间部分
        dt_str = prefix[:19]
        return datetime.strptime(dt_str, LOG_TIME_FORMAT)
    except ValueError:
        pass

    return None
```

- [ ] **Step 3: 添加 lines 查询函数**

```python
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
```

- [ ] **Step 4: 添加 request_id grep 函数**

```python
def grep_request_id_in_file(file_path: str, request_id: str) -> list[str]:
    """
    使用系统命令在单个文件中搜索 request_id。

    Args:
        file_path: 日志文件路径
        request_id: 要搜索的 request_id

    Returns:
        匹配的日志行列表
    """
    pattern = f"[{request_id}]"

    try:
        if platform.system() == "Windows":
            # Windows 使用 findstr
            result = subprocess.run(
                ["findstr", pattern, file_path],
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
```

- [ ] **Step 5: 添加 request_id 查询函数（带停止优化）**

```python
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
```

- [ ] **Step 6: 添加时间区间查询函数**

```python
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
                line_time = parse_log_time(line.rstrip("\n"))

                if line_time is None:
                    continue

                # 时间区间过滤
                if start_time <= line_time <= end_time:
                    all_matches.append(line.rstrip("\n"))

                # 停止优化：遇到早于 start_time 的行，后面的文件都不需要了
                if line_time < start_time:
                    should_stop = True
                    break

    # 已按时间顺序（从新文件到旧文件读取，但每个文件内是旧到新）
    # 需要重新排序为整体旧到新
    all_matches.sort(key=lambda line: parse_log_time(line) or datetime.min)

    content = "\n".join(all_matches)
    return content, len(all_matches), files_scanned
```

- [ ] **Step 7: 添加参数校验函数**

```python
class LogQueryError(Exception):
    """日志查询错误。"""
    pass


def validate_query_params(
    lines: Optional[int],
    request_id: Optional[str],
    start_time: Optional[str],
    end_time: Optional[str],
) -> tuple[str, Optional[int], Optional[str], Optional[datetime], Optional[datetime]]:
    """
    校验查询参数，返回查询模式和解析后的参数。

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
```

- [ ] **Step 8: 提交核心模块**

```bash
git add worker/log_query.py
git commit -m "feat: add log_query module with three query modes"
```

---

## Task 2: 编写单元测试

**Files:**
- Create: `tests/test_log_query.py`

- [ ] **Step 1: 创建测试骨架和 fixtures**

```python
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
```

- [ ] **Step 2: 编写时间解析测试**

```python
def test_parse_log_time():
    """测试日志时间解析。"""
    line = "2026-04-24 10:00:00,123 [req-001] INFO logger: message"
    result = parse_log_time(line)
    assert result == datetime(2026, 4, 24, 10, 0, 0)


def test_parse_log_time_invalid():
    """测试无效日志时间。"""
    line = "invalid line"
    result = parse_log_time(line)
    assert result is None


def test_parse_iso_time():
    """测试 ISO 时间解析。"""
    result = parse_iso_time("2026-04-24T10:00:00")
    assert result == datetime(2026, 4, 24, 10, 0, 0)


def test_parse_iso_time_with_timezone():
    """测试带时区的 ISO 时间解析。"""
    result = parse_iso_time("2026-04-24T10:00:00+08:00")
    # 转换为本地时间进行比较
    assert result.hour == 10
```

- [ ] **Step 3: 编写文件收集测试**

```python
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
```

- [ ] **Step 4: 编写 lines 查询测试**

```python
def test_query_by_lines(sample_log_file):
    """测试 lines 查询。"""
    content, count = query_by_lines(sample_log_file, 2)
    assert count == 2
    assert "req-002" in content
    assert "req-003" in content


def test_query_by_lines_more_than_file(sample_log_file):
    """测试 lines 超过文件行数。"""
    content, count = query_by_lines(sample_log_file, 100)
    assert count == 4  # 文件只有 4 行
```

- [ ] **Step 5: 编写 request_id 查询测试**

```python
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
```

- [ ] **Step 6: 编写时间区间查询测试**

```python
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


def test_query_by_time_range_no_match(sample_log_file):
    """测试时间区间无匹配。"""
    start = datetime(2026, 4, 24, 11, 0, 0)
    end = datetime(2026, 4, 24, 11, 5, 0)

    content, count, files_scanned = query_by_time_range(
        sample_log_file, start, end
    )
    assert count == 0
```

- [ ] **Step 7: 编写参数校验测试**

```python
def test_validate_params_default():
    """测试默认参数。"""
    mode, lines, rid, start, end = validate_query_params(None, None, None, None)
    assert mode == "lines"
    assert lines == 400


def test_validate_params_conflict():
    """测试参数冲突。"""
    with pytest.raises(LogQueryError, match="参数冲突"):
        validate_query_params(100, "req-001", None, None)


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
```

- [ ] **Step 8: 运行测试验证**

```bash
pytest tests/test_log_query.py -v
```

- [ ] **Step 9: 提交测试**

```bash
git add tests/test_log_query.py
git commit -m "test: add unit tests for log_query module"
```

---

## Task 3: 修改 server.py 接口

**Files:**
- Modify: `worker/server.py:361-402`

- [ ] **Step 1: 添加导入**

在 server.py 顶部添加导入：

```python
from worker.log_query import (
    query_by_lines,
    query_by_request_id,
    query_by_time_range,
    validate_query_params,
    LogQueryError,
)
```

- [ ] **Step 2: 修改 get_logs 函数**

替换原有 `get_logs` 函数（361-402 行）：

```python
@app.get("/worker/logs", response_class=PlainTextResponse)
async def get_logs(
    lines: int | None = Query(default=None, ge=1, le=2000, description="返回的日志行数"),
    request_id: str | None = Query(default=None, description="查询指定 request_id 的日志"),
    start_time: str | None = Query(default=None, description="时间区间起始（ISO 8601 格式）"),
    end_time: str | None = Query(default=None, description="时间区间结束（ISO 8601 格式）"),
):
    """
    获取日志内容。

    支持三种查询模式（互斥）：
    - lines: 返回最后 N 行（默认 400）
    - request_id: grep 搜索所有日志文件
    - start_time + end_time: 时间区间过滤（最多 5 分钟）

    Args:
        lines: 返回行数（范围 1-2000）
        request_id: 查询指定 request_id 的所有日志
        start_time: 时间区间起始（ISO 8601）
        end_time: 时间区间结束（ISO 8601）

    Returns:
        PlainTextResponse: 日志内容，带响应头 X-Log-Count 和 X-Files-Scanned
    """
    if not worker:
        raise HTTPException(status_code=503, detail="Worker not initialized")

    log_path = worker.log_path
    if not log_path:
        logger.warning(f"Log path not set, worker.log_path={log_path}")
        raise HTTPException(status_code=404, detail="Log path not configured")

    if not os.path.exists(log_path):
        logger.warning(f"Log file not found: {log_path}")
        raise HTTPException(status_code=404, detail=f"Log file not found: {log_path}")

    try:
        # 参数校验
        mode, lines_val, request_id_val, start_dt, end_dt = validate_query_params(
            lines, request_id, start_time, end_time
        )

        # 执行查询
        if mode == "lines":
            content, log_count = query_by_lines(log_path, lines_val)
            files_scanned = 1
        elif mode == "request_id":
            content, log_count, files_scanned = query_by_request_id(
                log_path, request_id_val
            )
        else:  # time_range
            content, log_count, files_scanned = query_by_time_range(
                log_path, start_dt, end_dt
            )

        # 构建响应
        response = PlainTextResponse(
            content=content,
            media_type="text/plain; charset=utf-8",
        )
        response.headers["X-Log-Count"] = str(log_count)
        response.headers["X-Files-Scanned"] = str(files_scanned)

        return response

    except LogQueryError as e:
        logger.warning(f"Log query validation failed: {e}")
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"Failed to query logs: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to query logs: {e}")
```

- [ ] **Step 3: 提交接口修改**

```bash
git add worker/server.py
git commit -m "feat: extend /worker/logs API with request_id and time_range query modes"
```

---

## Task 4: 集成测试和验证

- [ ] **Step 1: 启动 Worker 测试接口**

```bash
python -m worker.main
```

- [ ] **Step 2: 测试 lines 模式**

```bash
curl http://localhost:8000/worker/logs?lines=100
```

- [ ] **Step 3: 测试 request_id 模式**

```bash
curl http://localhost:8000/worker/logs?request_id=<某个request_id>
```

- [ ] **Step 4: 测试时间区间模式**

```bash
curl "http://localhost:8000/worker/logs?start_time=2026-04-24T10:00:00&end_time=2026-04-24T10:05:00"
```

- [ ] **Step 5: 测试参数冲突**

```bash
curl "http://localhost:8000/worker/logs?lines=100&request_id=test"
# 应返回 400 错误
```

- [ ] **Step 6: 测试时间区间超过 5 分钟**

```bash
curl "http://localhost:8000/worker/logs?start_time=2026-04-24T10:00:00&end_time=2026-04-24T10:10:00"
# 应返回 400 错误
```

- [ ] **Step 7: 最终提交（如有修复）**

```bash
git add -A
git commit -m "fix: resolve integration test issues for log query API"
```