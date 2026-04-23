# 日志查询接口扩展设计

## 概述

扩展 `/worker/logs` 接口，支持三种查询模式：
1. 按 lines 查询（原有功能）
2. 按 request_id 查询（新增）
3. 按时间区间查询（新增）

三种模式互斥，一次只能使用一种。

## 接口参数

| 参数 | 类型 | 必填 | 默认值 | 说明 |
|------|------|------|--------|------|
| `lines` | int | 否 | 400 | 返回最后 N 行（范围 1-2000） |
| `request_id` | string | 否 | - | 查询指定 request_id 的所有日志 |
| `start_time` | string | 否 | - | 时间区间起始（ISO 8601 格式） |
| `end_time` | string | 否 | - | 时间区间结束（ISO 8601 格式） |

### 互斥规则

- `lines` 单独使用 → 返回最后 N 行（原有功能）
- `request_id` 单独使用 → grep 搜索所有日志文件
- `start_time` + `end_time` 同时使用 → 时间区间过滤
- 其他组合（如同时传 `lines` 和 `request_id`）→ 返回 400 错误
- 无参数 → 使用默认 `lines=400`

### 时间格式

ISO 8601 格式，支持：
- `2026-04-24T10:00:00`
- `2026-04-24T10:00:00+08:00`

不带时区时默认使用本地时区。

## 搜索范围

日志文件：`worker.log` + `worker.log.1~5`（最多 6 个文件）

读取顺序：从新到旧（`worker.log` → `.1` → `.2` → `.3` → `.4` → `.5`）

返回顺序：按时间顺序（旧到新）

## 查询模式实现

### 模式一：lines 查询（原有）

读取当前 `worker.log` 文件末尾 N 行返回。

### 模式二：request_id 查询

**匹配方式**：
- 日志格式为 `%(asctime)s [%(request_id)s] %(levelname)s %(name)s: %(message)s`
- 匹配模式：`\[{request_id}\]`（精确匹配方括号内的 request_id）

**性能优化**：
- Windows: 使用 `findstr "[request_id]" log_path`
- Linux/Mac: 使用 `grep -F "[request_id]" log_path`
- 比纯 Python 逐行匹配快 2-5 倍

**停止搜索优化**：
- 找到该 request_id 的最早一条日志后，记录其时间
- 当遇到时间比"最早匹配时间 - 5 分钟"还早的行时，停止搜索更老的文件
- 逻辑：`日志时间 < (最早匹配时间 - 5分钟)` → 停止

**原因**：request_id 通常对应一个 HTTP 请求，生命周期在几分钟内。往前 5 分钟没有同个 request_id 的日志，可以认为请求已结束，更老的文件不会再有。

### 模式三：时间区间查询

**区间校验**：
- `start_time` 和 `end_time` 必须同时提供
- `end_time` 必须大于 `start_time`
- `end_time - start_time` ≤ 5 分钟（300 秒），否则返回 400 错误

**匹配方式**：
- 解析每行日志的 `asctime` 字段（格式如 `2026-04-24 10:00:00,123`）
- 过滤落在时间区间内的日志行

**停止搜索优化**：
- 按文件从新到旧读取
- 逐行检查时间，当遇到 `< start_time` 的行时停止
- 如果文件首行时间已 `< start_time`，跳过该文件及更老的文件

## 响应

**响应格式**：纯文本 `text/plain; charset=utf-8`

**响应头**：
- `X-Log-Count`: 返回的日志行数
- `X-Files-Scanned`: 扫描的文件数量（仅 request_id/时间区间模式）

## 错误处理

| 错误 | HTTP 状态码 | 说明 |
|------|-------------|------|
| 参数组合冲突 | 400 | "参数冲突：lines/request_id/start_time+end_time 三选一" |
| 时间区间超过 5 分钟 | 400 | "时间区间不能超过 5 分钟" |
| end_time < start_time | 400 | "end_time 必须大于 start_time" |
| request_id 未找到 | 200 | 返回空文本 + `X-Log-Count: 0` |
| 时间区间内无日志 | 200 | 返回空文本 + `X-Log-Count: 0` |
| 日志文件不存在 | 404 | "Log file not found" |
| Worker 未初始化 | 503 | "Worker not initialized" |

## 性能估算

假设 100MB 日志文件（约 50 万行）：

| 查询模式 | 预估时间 |
|----------|----------|
| `lines` | < 100ms |
| `request_id`（grep 优化） | 0.5-1.5 秒 |
| `时间区间` | 1-3 秒 |

影响因素：
- 磁盘 I/O（SSD vs HDD）
- request_id 匹配命中率
- 时间区间宽度

## 实现 Note

使用 `subprocess.run()` 调用系统命令进行 grep，处理跨平台差异：
- Windows 使用 `findstr`，设置 `encoding="utf-8"`
- Linux/Mac 使用 `grep -F`，固定字符串匹配

停止搜索优化需要在 grep 结果后额外检查时间戳，以确定是否继续搜索更老的文件。