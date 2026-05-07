"""
HTTP Server。

提供 RESTful API 接口供外部平台调用。
"""

import asyncio
import logging
import os
import re
import threading
from typing import Any

import yaml
from fastapi import FastAPI, HTTPException, Query, WebSocket, WebSocketDisconnect
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import JSONResponse, PlainTextResponse
from pydantic import BaseModel, Field

from common.request_context import clear_request_id, generate_request_id, set_request_id
from worker.config import load_config_version, merge_config_with_ip_protection, save_config_with_version
from worker.log_query import (
    LogQueryError,
    query_by_lines,
    query_by_request_id,
    query_by_time_range,
    validate_query_params,
)
from worker.performance_monitor import (
    CollectStartRequest,
    CollectStopRequest,
    get_collector,
)
from worker.tools import (
    get_script_version,
    save_script,
    script_exists,
    update_script_version,
    validate_script_name,
)
from worker.upgrade import UpgradeError, UpgradeRequest, get_upgrade_status, start_async_upgrade
from worker.worker import TaskConflictError, Worker

logger = logging.getLogger(__name__)

# WebSocket 连接计数器
_ws_connections: dict[str, int] = {}

# 默认 WebSocket 配置（会被 worker.config 覆盖）
DEFAULT_WS_MAX_CONNECTIONS = 3
DEFAULT_WS_SEND_TIMEOUT = 30


def _format_actions_summary(actions: list[dict[str, Any]], max_actions: int = 10) -> str:
    """
    格式化请求的 actions 列表为摘要字符串。

    - 每个 action 显示所有关键字段
    - 超长字符串截断
    - 超过 max_actions 时显示剩余数量
    """
    if not actions:
        return "[]"

    formatted = []
    for i, action in enumerate(actions[:max_actions]):
        # 显示所有字段（排除 image_base64）
        fields = {"number": i}
        for key, value in action.items():
            if key == "image_base64" and value:
                fields[key] = "<base64_data>"
            elif key == "value" and isinstance(value, str) and len(value) > 100:
                fields[key] = value[:97] + "..."
            else:
                fields[key] = value

        formatted.append(str(fields))

    remaining = len(actions) - max_actions
    if remaining > 0:
        formatted.append(f"... and {remaining} more action(s)")

    return "[" + ", ".join(formatted) + "]"


def _format_action_results(actions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """
    格式化响应中每个 action 的执行结果，排除 base64 数据。
    """
    if not actions:
        return []

    formatted = []
    for action in actions:
        result = action.copy()
        # 替换截图字段
        if result.get("screenshot"):
            result["screenshot"] = "<base64_data>"
        formatted.append(result)

    return formatted


def _format_result_for_log(result: dict[str, Any]) -> dict[str, Any]:
    """
    格式化结果用于日志输出，排除大数据字段。
    """
    if not result:
        return result

    log_result = result.copy()

    # 处理 error_screenshot
    if log_result.get("error_screenshot"):
        log_result["error_screenshot"] = "<base64_data>"

    # 处理 actions 中的截图字段
    if log_result.get("actions"):
        log_result["actions"] = _format_action_results(log_result["actions"])

    return log_result


# Pydantic 模型定义


class WindowSpec(BaseModel):
    """窗口定位参数（Windows 平台专用）。"""

    title: str | None = Field(None, description="窗口标题（包含匹配）")
    class_: str | None = Field(None, alias="class", description="窗口类名（精确匹配）")


class TaskRequest(BaseModel):
    """任务请求。"""

    platform: str = Field(..., description="目标平台: web/android/ios/windows/mac")
    actions: list[dict[str, Any]] = Field(..., description="动作列表")
    device_id: str | None = Field(None, description="设备 ID（移动端必填）")
    window: WindowSpec | None = Field(None, description="窗口定位参数（Windows 平台）")


class ConfigUpdateRequest(BaseModel):
    """配置更新请求。"""
    config_content: str = Field(..., description="完整的 YAML 配置文件内容")
    config_version: str = Field(..., description="配置版本号，格式：YYYYMMDD-HHMMSS")


class ScriptUpdateRequest(BaseModel):
    """脚本更新请求。"""
    name: str = Field(..., description="脚本名称，如 play_ppt.ps1")
    content: str = Field(..., description="脚本内容")
    version: str = Field(..., description="脚本版本号，格式：YYYYMMDD-HHMMSS")
    overwrite: bool = Field(True, description="是否覆盖已有脚本")


def _format_request_for_log(request: TaskRequest) -> dict[str, Any]:
    """
    格式化原始请求用于日志输出，过滤 base64 数据。
    """
    log_request = {
        "platform": request.platform,
        "device_id": request.device_id,
        "window": request.window.model_dump(by_alias=True) if request.window else None,
        "actions": [],
    }

    # 处理每个 action，过滤 image_base64
    for action in request.actions:
        log_action = {}
        for key, value in action.items():
            if key == "image_base64" and value:
                log_action[key] = "<base64_data>"
            elif key == "value" and isinstance(value, str) and len(value) > 100:
                log_action[key] = value[:97] + "..."
            else:
                log_action[key] = value
        log_request["actions"].append(log_action)

    return log_request


# FastAPI 应用
app = FastAPI(
    title="Test Worker API",
    description="多端自动化测试执行基建 API",
    version="3.0.0",
)

# 启用 GZip 压缩（超过 1KB 的响应自动压缩）
app.add_middleware(GZipMiddleware, minimum_size=1000)

# Worker 实例（在 main.py 中初始化）
worker: Worker | None = None

# 配置更新并发锁
_config_update_lock = threading.Lock()

# 脚本更新并发锁
_script_update_lock = threading.Lock()

# GUIApp 引用（用于触发重启）
gui_app: Any | None = None


def set_worker(w: Worker) -> None:
    """设置 Worker 实例。"""
    global worker
    worker = w


def set_gui_app(app: Any) -> None:
    """设置 GUIApp 实例。"""
    global gui_app
    gui_app = app


# ========== API 端点 ==========


@app.get("/worker_devices")
async def get_worker_devices():
    """获取 Worker 状态和设备信息（合并接口）。"""
    if not worker:
        raise HTTPException(status_code=503, detail="Worker not initialized")

    return worker.get_worker_devices()


@app.post("/task/execute")
async def execute_task(request: TaskRequest):
    """
    同步执行任务。

    执行完成后返回结果，不生成 task_id。

    Returns:
        Dict: 执行结果（不含 task_id）
    """
    if not worker:
        raise HTTPException(status_code=503, detail="Worker not initialized")

    # 生成 request-id
    request_id = generate_request_id()
    set_request_id(request_id)

    try:
        # 记录原始请求数据（过滤 base64）
        logger.info(f"Sync task raw request: {_format_request_for_log(request)}")

        # 同步执行（不生成 task_id）
        result = worker.execute_sync(
            platform=request.platform,
            actions=request.actions,
            device_id=request.device_id,
            window=request.window.model_dump(by_alias=True) if request.window else None,
        )

        # 添加 request_id 到返回结果
        result['request_id'] = request_id

        # 打印响应结果（排除 base64 数据）
        logger.info(f"Sync task response: {_format_result_for_log(result)}")

        return result

    except Exception as e:
        logger.error(f"execute_sync failed: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Internal error: {e}")

    finally:
        clear_request_id()


@app.post("/task/execute_async")
async def execute_task_async(request: TaskRequest):
    """
    异步执行任务。

    立即返回 task_id，任务在后台执行。

    Returns:
        Dict: {"task_id": "xxx", "status": "running", "request_id": "xxx"}

    Raises:
        HTTPException: 409 如果设备/平台正被占用
    """
    if not worker:
        raise HTTPException(status_code=503, detail="Worker not initialized")

    # 生成 request-id
    request_id = generate_request_id()
    set_request_id(request_id)

    try:
        # 记录原始请求数据（过滤 base64）
        logger.info(f"Async task raw request: {_format_request_for_log(request)}")

        task_id, status = worker.execute_async(
            platform=request.platform,
            actions=request.actions,
            device_id=request.device_id,
            window=request.window.model_dump(by_alias=True) if request.window else None,
        )

        # 记录任务提交结果
        logger.info(f"Async task submitted: task_id={task_id}, status={status}")

        return {"task_id": task_id, "status": status, "request_id": request_id}

    except TaskConflictError as e:
        raise HTTPException(
            status_code=409,
            detail=str(e),
        )

    finally:
        clear_request_id()


@app.get("/task/{task_id}")
async def get_task_result(task_id: str):
    """
    查询任务结果。

    一次性查询：查询后任务从内存中销毁，下次查询返回 404。

    Returns:
        Dict: 任务状态和结果

    Raises:
        HTTPException: 404 如果任务不存在
    """
    if not worker:
        raise HTTPException(status_code=503, detail="Worker not initialized")

    result = worker.get_task_result(task_id)

    if result is None:
        logger.info(f"Task result not found: task_id={task_id}")
        raise HTTPException(status_code=404, detail="Task not found")

    # 从任务结果中获取 request_id 并设置到当前线程
    request_id = result.get('request_id')
    if request_id:
        set_request_id(request_id)

    try:
        logger.info(f"Task result response: {_format_result_for_log(result)}")
        return result
    finally:
        if request_id:
            clear_request_id()


@app.delete("/task/{task_id}")
async def cancel_task(task_id: str):
    """
    取消任务。

    取消正在执行的任务，销毁 task_id。

    Returns:
        Dict: {"success": bool, "message": str}

    Raises:
        HTTPException: 404 如果任务不存在
    """
    if not worker:
        raise HTTPException(status_code=503, detail="Worker not initialized")

    success, message = worker.cancel_task(task_id)

    if not success and "not found" in message.lower():
        raise HTTPException(status_code=404, detail="Task not found")

    logger.info(f"Task cancelled: task_id={task_id}, success={success}")

    return {"success": success, "message": message}


@app.post("/devices/refresh")
async def refresh_devices():
    """刷新设备列表。"""
    if not worker:
        raise HTTPException(status_code=503, detail="Worker not initialized")

    return worker.refresh_devices()


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


@app.post("/worker/upgrade")
async def upgrade_worker(request: UpgradeRequest):
    """
    Worker 升级接口（异步）。

    立即返回 accepted 状态，升级在后台执行。
    使用 GET /worker/upgrade/status 查询进度。

    Args:
        request: 升级请求
            - version: 目标版本号（可选）
            - download_url: 安装包下载地址
            - force: 是否强制升级（可选，默认 true）

    Returns:
        Dict: 升级响应
            - status: accepted/skipped/rejected
            - message: 状态描述
            - current_version: 当前版本
            - target_version: 目标版本
    """
    if not worker:
        raise HTTPException(status_code=503, detail="Worker not initialized")

    logger.info(
        f"Upgrade request: version={request.version}, "
        f"download_url={request.download_url}, force={request.force}"
    )

    try:
        result = start_async_upgrade(request)
        return result.to_dict()

    except UpgradeError as e:
        logger.error(f"Upgrade rejected: {e}")
        return {
            "status": "rejected",
            "message": str(e),
        }


@app.get("/worker/upgrade/status")
async def upgrade_status():
    """
    查询升级状态。

    Returns:
        Dict: 升级状态信息
            - status: accepted/downloading/installing/completed/failed/none
            - target_version: 目标版本
            - current_version: 当前版本
            - download_progress: 下载进度百分比 (0-100)
            - downloaded_bytes: 已下载字节
            - total_bytes: 总字节
            - error: 错误信息（失败时）
            - started_at: 开始时间
            - completed_at: 完成时间
    """
    state = get_upgrade_status()

    if state is None:
        return {
            "status": "none",
            "message": "当前无升级任务",
        }

    return state.to_dict()


@app.post("/worker/config")
async def update_worker_config(request: ConfigUpdateRequest):
    """
    更新 Worker 配置。

    流程：
    1. 版本格式校验
    2. 并发保护（获取锁）
    3. 版本比较（相同则跳过）
    4. 配置合并（保留本地 IP）
    5. 保存配置（含版本文件）
    6. 返回响应
    7. 触发重启（异步）
    """
    if not worker:
        raise HTTPException(status_code=503, detail="Worker not initialized")

    # 请求接收日志
    logger.info(f"Config update request: version={request.config_version}")

    # 1. 版本格式校验
    if not re.match(r"^\d{8}-\d{6}$", request.config_version):
        return JSONResponse(
            status_code=400,
            content={"status": "error", "message": "版本号格式无效，应为 YYYYMMDD-HHMMSS"}
        )

    # 2. 并发保护（非阻塞）
    if not _config_update_lock.acquire(blocking=False):
        return JSONResponse(
            status_code=409,
            content={"status": "error", "message": "配置更新正在进行中，请稍后重试"}
        )

    try:
        # 3. 版本比较
        local_version = load_config_version()
        if local_version == request.config_version:
            logger.info(f"Config version unchanged: {request.config_version}")
            return {
                "status": "success",
                "message": "配置版本相同，无需更新",
                "updated": False,
                "config_version": request.config_version,
                "restart_triggered": False
            }

        # 4. 配置合并（保留本地 IP）
        try:
            merged_config = merge_config_with_ip_protection(request.config_content)
        except yaml.YAMLError as e:
            logger.warning(f"Config YAML parse failed: {e}")
            return JSONResponse(
                status_code=400,
                content={"status": "error", "message": f"配置内容无效: YAML 解析失败 - {e}"}
            )

        # 5. 保存配置（含版本文件）
        try:
            save_config_with_version(merged_config, request.config_version)
        except Exception as e:
            logger.error(f"Config save failed: {e}")
            return JSONResponse(
                status_code=500,
                content={"status": "error", "message": f"配置保存失败: {e}"}
            )

        # 6. 返回响应
        response = {
            "status": "success",
            "message": "配置更新成功",
            "updated": True,
            "config_version": request.config_version,
            "restart_triggered": True
        }

        # 配置更新成功日志
        logger.info(f"Config updated successfully: version={request.config_version}, triggering restart")

        # 7. 触发重启（响应返回后执行）
        _trigger_restart_after_response()

        return response

    finally:
        _config_update_lock.release()


@app.post("/worker/scripts")
async def update_worker_script(request: ScriptUpdateRequest):
    """
    更新 Worker 脚本。

    流程：
    1. 版本格式校验
    2. 脚本名称校验（扩展名 + 路径穿越）
    3. 并发保护
    4. 版本比较（相同则跳过）
    5. 覆盖检查
    6. 保存脚本
    7. 更新版本记录
    8. 返回响应（不重启）

    Returns:
        Dict: 更新结果
    """
    if not worker:
        raise HTTPException(status_code=503, detail="Worker not initialized")

    logger.info(f"Script update request: name={request.name}, version={request.version}")

    # 1. 版本格式校验
    if not re.match(r"^\d{8}-\d{6}$", request.version):
        return JSONResponse(
            status_code=400,
            content={"status": "error", "message": "版本号格式无效，应为 YYYYMMDD-HHMMSS"}
        )

    # 2. 脚本名称校验
    if not validate_script_name(request.name):
        return JSONResponse(
            status_code=400,
            content={"status": "error", "message": "脚本名称不合法，只允许 .ps1/.sh/.bat 扩展名，禁止路径穿越"}
        )

    # 3. 并发保护（非阻塞）
    if not _script_update_lock.acquire(blocking=False):
        return JSONResponse(
            status_code=409,
            content={"status": "error", "message": "脚本更新正在进行中，请稍后重试"}
        )

    try:
        # 4. 版本比较
        local_version = get_script_version(request.name)
        if local_version == request.version:
            logger.info(f"Script version unchanged: {request.name} -> {request.version}")
            return {
                "status": "success",
                "message": "脚本版本相同，无需更新",
                "name": request.name,
                "version": request.version,
                "updated": False,
            }

        # 5. 覆盖检查
        if not request.overwrite and script_exists(request.name):
            return JSONResponse(
                status_code=409,
                content={"status": "error", "message": f"脚本已存在且 overwrite=false: {request.name}"}
            )

        # 6. 保存脚本
        try:
            script_path = save_script(request.name, request.content)
            logger.info(f"Script saved: {script_path}")

            # 7. 更新版本记录
            update_script_version(request.name, request.version)
        except ValueError as e:
            logger.warning(f"Script save validation failed: {e}")
            return JSONResponse(
                status_code=400,
                content={"status": "error", "message": f"脚本保存失败: {e}"}
            )
        except OSError as e:
            logger.error(f"Script save IO failed: {e}")
            return JSONResponse(
                status_code=500,
                content={"status": "error", "message": f"脚本保存失败: {e}"}
            )

        # 8. 返回响应（不重启）
        logger.info(f"Script updated successfully: {request.name} -> {request.version}")

        return {
            "status": "success",
            "message": "脚本更新成功",
            "name": request.name,
            "version": request.version,
            "path": script_path,
            "updated": True,
        }

    finally:
        _script_update_lock.release()


def _trigger_restart_after_response():
    """在响应返回后触发重启。"""
    import time

    def _do_restart_async():
        # 等待一小段时间确保响应已返回
        time.sleep(0.5)

        if gui_app and hasattr(gui_app, 'ui_signals') and gui_app.ui_signals:
            # GUI 模式：通过信号触发重启
            gui_app.ui_signals.show_config_restart.emit()
        else:
            # CLI 模式：通过子进程重启
            from worker.config import cli_restart
            cli_restart()

    # 启动后台线程执行重启
    threading.Thread(target=_do_restart_async, daemon=True).start()


# ========== 性能监控 API 端点 ==========


@app.get("/api/worker/{device_id}/processes")
async def get_processes(
    device_id: str,
    search: str | None = Query(default=None, description="模糊搜索进程名"),
):
    """
    获取进程列表。

    用于"开始采集"弹窗中显示进程列表，用户勾选目标进程。

    Args:
        device_id: 设备ID
        search: 模糊搜索进程名（可选）

    Returns:
        Dict: 进程列表
    """
    if not worker:
        raise HTTPException(status_code=503, detail="Worker not initialized")

    collector = get_collector(device_id)
    processes = collector.get_processes(search)

    logger.info(f"Get processes: device_id={device_id}, search={search}, count={len(processes)}")

    return {"processes": [p.model_dump() for p in processes]}


@app.post("/api/worker/{device_id}/collect/start")
async def start_collect(device_id: str, request: CollectStartRequest):
    """
    开始性能数据采集。

    Worker 开始定时采集并上报数据。

    Args:
        device_id: 设备ID
        request: 开始采集请求
            - collect_id: 采集记录ID（由后端生成）
            - interval: 采集频率（秒）
            - target_processes: 目标进程列表

    Returns:
        Dict: {"status": "started", "message": "开始采集，频率X秒"}
    """
    if not worker:
        raise HTTPException(status_code=503, detail="Worker not initialized")

    collector = get_collector(device_id)

    # 设置后端地址（使用 platform_api）
    if worker.config and worker.config.platform_api:
        collector.set_backend_host(worker.config.platform_api)

    result = collector.start_collect(request)

    logger.info(
        f"Start collect: device_id={device_id}, "
        f"collect_id={request.collect_id}, "
        f"interval={request.interval}s, "
        f"target_processes={len(request.target_processes)}"
    )

    return result


@app.post("/api/worker/{device_id}/collect/stop")
async def stop_collect(device_id: str, request: CollectStopRequest | None = None):
    """
    停止性能数据采集。

    Args:
        device_id: 设备ID
        request: 停止采集请求（可选）
            - collect_id: 采集记录ID，不传则停止当前所有采集

    Returns:
        Dict: {"status": "stopped", "message": "采集已停止"}
    """
    if not worker:
        raise HTTPException(status_code=503, detail="Worker not initialized")

    collector = get_collector(device_id)
    result = collector.stop_collect(request)

    logger.info(f"Stop collect: device_id={device_id}, collect_id={request.collect_id if request else None}")

    return result


@app.get("/api/worker/{device_id}/collect/status")
async def get_collect_status(device_id: str):
    """
    获取采集状态。

    用于后端判断当前采集状态，Worker 重连后恢复采集。

    Args:
        device_id: 设备ID

    Returns:
        Dict: 采集状态信息
            - is_collecting: 是否正在采集
            - collect_id: 当前采集ID
            - interval: 采集频率（秒）
            - target_processes: 目标进程列表
            - start_time: 采集开始时间
            - elapsed_seconds: 已采集时长（秒）
    """
    if not worker:
        raise HTTPException(status_code=503, detail="Worker not initialized")

    collector = get_collector(device_id)
    status = collector.get_status()

    logger.debug(f"Get collect status: device_id={device_id}, is_collecting={status.is_collecting}")

    return status.model_dump()


# ========== WebSocket 請求 ==========


@app.websocket("/ws/screen/{platform}/{device_id}")
async def screen_stream(
    websocket: WebSocket,
    platform: str,
    device_id: str,
    monitor: int = 1  # 新增：屏幕索引，默认1=主显示器
):
    """实时屏幕推流（10fps）。

    Args:
        platform: 设备平台类型 (ios, android, windows, mac, web)
        device_id: 设备标识符
        monitor: 屏幕索引（mss索引：1=主显示器，2+=副显示器）
    """

    # 从配置读取参数（使用默认值作为 fallback）
    max_connections = DEFAULT_WS_MAX_CONNECTIONS
    send_timeout = DEFAULT_WS_SEND_TIMEOUT
    if worker and worker.config:
        max_connections = worker.config.websocket_max_connections_per_device
        send_timeout = worker.config.websocket_send_timeout_seconds

    # 连接计数和 ScreenManager key
    # 桌面端设备：key 包含 monitor 参数，支持多屏幕
    # 移动端设备：key 不包含 monitor，单屏幕
    if platform in ("windows", "mac"):
        conn_key = f"{platform}/{device_id}/{monitor}"
    else:
        conn_key = f"{platform}/{device_id}"

    current_count = _ws_connections.get(conn_key, 0)

    if current_count >= max_connections:
        # 超过限制，拒绝连接（WebSocket Policy Violation）
        await websocket.close(code=1008, reason="Max connections reached")
        return

    await websocket.accept()
    _ws_connections[conn_key] = current_count + 1

    # 日志显示 monitor 参数
    log_device = f"{device_id}/{monitor}" if platform in ("windows", "mac") else device_id
    logger.info(f"WebSocket connected: platform={platform}, device={log_device}, count={current_count + 1}")

    try:
        # 获取 ScreenManager
        from worker.screen.manager import _screen_managers, get_screen_manager

        # iOS/Android: 检查设备是否已注册（有 WDA/minicap 服务）
        if platform == "ios":
            if not worker or not worker.ios_manager:
                logger.warning("WebSocket rejected: iOS platform not initialized")
                await websocket.close(code=1008, reason="iOS platform not initialized")
                return
            wda_client = worker.ios_manager._device_clients.get(device_id)
            if not wda_client:
                logger.warning(f"WebSocket rejected: iOS device not registered: {device_id}")
                await websocket.close(code=1008, reason=f"iOS device not registered: {device_id}")
                return
        elif platform == "android":
            if not worker or not worker.android_manager:
                logger.warning("WebSocket rejected: Android platform not initialized")
                await websocket.close(code=1008, reason="Android platform not initialized")
                return
            minicap = worker.android_manager._minicap_instances.get(device_id)
            if not minicap:
                logger.warning(f"WebSocket rejected: Android device not registered: {device_id}")
                await websocket.close(code=1008, reason=f"Android device not registered: {device_id}")
                return

        # 根据 platform 创建对应的 FrameSource
        if conn_key not in _screen_managers:
            frame_source = _create_frame_source(platform, device_id, monitor)
            screen_manager = get_screen_manager(conn_key, frame_source)
        else:
            screen_manager = _screen_managers[conn_key]

        streamer = screen_manager.start_streaming()

        while streamer.is_running():
            frame = await streamer.get_frame_async()
            # 发送 JPEG 原始数据，带超时保护
            try:
                await asyncio.wait_for(
                    websocket.send_bytes(frame),
                    timeout=send_timeout
                )
            except asyncio.TimeoutError:
                logger.warning(f"WebSocket send timeout ({send_timeout}s), disconnecting: platform={platform}, device={device_id}")
                await websocket.close(code=1001, reason="Send timeout")
                break
            await asyncio.sleep(0.1)  # 10fps

    except WebSocketDisconnect:
        log_device = f"{device_id}/{monitor}" if platform in ("windows", "mac") else device_id
        logger.info(f"WebSocket disconnected: platform={platform}, device={log_device}")
    except Exception as e:
        log_device = f"{device_id}/{monitor}" if platform in ("windows", "mac") else device_id
        logger.error(f"WebSocket error: {e}")
    finally:
        # 确保减少连接计数
        _ws_connections[conn_key] = _ws_connections.get(conn_key, 1) - 1

        # 当连接计数降至 0 时，关闭 ScreenManager 以停止后台帧捕获线程
        if _ws_connections[conn_key] <= 0:
            del _ws_connections[conn_key]
            # 关闭 ScreenManager（停止后台线程，避免资源泄漏）
            from worker.screen.manager import close_screen_manager
            close_screen_manager(conn_key)
            log_device = f"{device_id}/{monitor}" if platform in ("windows", "mac") else device_id
            logger.info(f"ScreenManager closed: conn_key={conn_key}, last WebSocket disconnected")

        log_device = f"{device_id}/{monitor}" if platform in ("windows", "mac") else device_id
        logger.info(f"WebSocket connection closed: platform={platform}, device={log_device}")


def _create_frame_source(platform: str, device_id: str, monitor: int = 1):
    """根据平台类型创建对应的 FrameSource。

    Args:
        platform: 设备平台类型 (ios, android, windows, mac, web)
        device_id: 设备标识符
        monitor: 屏幕索引（mss索引：1=主显示器，2+=副显示器）

    Returns:
        FrameSource 实例
    """
    from worker.screen.frame_source import (
        MinicapFrameSource,
        MJPEGFrameSource,
        WindowsFrameSource,
    )

    if platform == "ios":
        # iOS: 使用 WDA MJPEG 流
        if worker and worker.ios_manager:
            wda_client = worker.ios_manager._device_clients.get(device_id)
            if wda_client:
                return MJPEGFrameSource(device_id, wda_client)
        # Fallback: 直接连接 WDA（假设本地 9100 端口）
        from worker.platforms.wda_client import WDAClient
        wda_client = WDAClient("http://127.0.0.1:8100")
        return MJPEGFrameSource(device_id, wda_client)

    elif platform == "android":
        # Android: 使用 minicap 流
        if worker and worker.android_manager:
            minicap = worker.android_manager._minicap_instances.get(device_id)
            if minicap:
                return MinicapFrameSource(device_id, minicap)
        # Fallback: 创建新的 minicap 实例
        from worker.platforms.minicap import Minicap
        minicap = Minicap(device_id)
        minicap.install()
        return MinicapFrameSource(device_id, minicap)

    elif platform in ("windows", "mac"):
        # Windows/Mac: 使用系统截屏（mss 支持跨平台），传递 monitor 参数
        return WindowsFrameSource(fps=10, monitor=monitor)

    elif platform == "web":
        # Web: 暂不支持 WebSocket 推流（需要 Playwright page 实例）
        raise ValueError("Web platform does not support WebSocket screen streaming")

    else:
        raise ValueError(f"Unsupported platform: {platform}")


# 异常处理
@app.exception_handler(Exception)
async def global_exception_handler(request, exc):
    logger.error(f"Unhandled exception: {exc}")
    return JSONResponse(
        status_code=500,
        content={"error": str(exc)},
    )
