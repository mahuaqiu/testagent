"""
HTTP Server。

提供 RESTful API 接口供外部平台调用。
"""

import logging
import os
import sys
import threading
import time
from typing import Any

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import JSONResponse, PlainTextResponse
from pydantic import BaseModel, Field

from worker.upgrade import UpgradeError, UpgradeRequest, handle_upgrade
from worker.worker import TaskConflictError, Worker

logger = logging.getLogger(__name__)


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

    # 处理 screenshots 列表
    if log_result.get("screenshots"):
        screenshot_count = len(log_result["screenshots"])
        log_result["screenshots"] = f"<{screenshot_count} screenshot(s)>"

    # 处理 actions 中的截图字段
    if log_result.get("actions"):
        log_result["actions"] = _format_action_results(log_result["actions"])

    return log_result


# Pydantic 模型定义


class TaskRequest(BaseModel):
    """任务请求。"""

    platform: str = Field(..., description="目标平台: web/android/ios/windows/mac")
    actions: list[dict[str, Any]] = Field(..., description="动作列表")
    device_id: str | None = Field(None, description="设备 ID（移动端必填）")


def _format_request_for_log(request: TaskRequest) -> dict[str, Any]:
    """
    格式化原始请求用于日志输出，过滤 base64 数据。
    """
    log_request = {
        "platform": request.platform,
        "device_id": request.device_id,
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

# Worker 实例（在 main.py 中初始化）
worker: Worker | None = None


def set_worker(w: Worker) -> None:
    """设置 Worker 实例。"""
    global worker
    worker = w


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

    # 记录原始请求数据（过滤 base64）
    logger.info(f"Sync task raw request: {_format_request_for_log(request)}")

    # 同步执行（不生成 task_id）
    try:
        result = worker.execute_sync(
            platform=request.platform,
            actions=request.actions,
            device_id=request.device_id,
        )
    except Exception as e:
        logger.error(f"execute_sync failed: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Internal error: {e}")

    # 打印响应结果（排除 base64 数据）
    logger.info(f"Sync task response: {_format_result_for_log(result)}")

    return result


@app.post("/task/execute_async")
async def execute_task_async(request: TaskRequest):
    """
    异步执行任务。

    立即返回 task_id，任务在后台执行。

    Returns:
        Dict: {"task_id": "xxx", "status": "running"}

    Raises:
        HTTPException: 409 如果设备/平台正被占用
    """
    if not worker:
        raise HTTPException(status_code=503, detail="Worker not initialized")

    # 记录原始请求数据（过滤 base64）
    logger.info(f"Async task raw request: {_format_request_for_log(request)}")

    try:
        task_id, status = worker.execute_async(
            platform=request.platform,
            actions=request.actions,
            device_id=request.device_id,
        )

        # 记录任务提交结果
        logger.info(f"Async task submitted: task_id={task_id}, status={status}")

        return {"task_id": task_id, "status": status}

    except TaskConflictError as e:
        raise HTTPException(
            status_code=409,
            detail=str(e),
        )


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

    logger.info(f"Task result response: {_format_result_for_log(result)}")

    return result


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
    lines: int = Query(default=400, ge=1, le=2000, description="返回的日志行数"),
):
    """
    获取日志内容。

    返回最后 N 行日志纯文本。

    Args:
        lines: 返回的行数（默认 400，最大 2000）

    Returns:
        PlainTextResponse: 日志内容（UTF-8 编码）
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
        # 读取最后 N 行日志
        with open(log_path, encoding="utf-8", errors="replace") as f:
            all_lines = f.readlines()
            last_lines = all_lines[-lines:] if len(all_lines) > lines else all_lines
            content = "".join(last_lines)

        return PlainTextResponse(
            content=content,
            media_type="text/plain; charset=utf-8",
        )

    except Exception as e:
        logger.error(f"Failed to read log file: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to read log file: {e}")


@app.post("/worker/upgrade")
async def upgrade_worker(request: UpgradeRequest):
    """
    Worker 升级接口。

    接收平台下发的升级指令，下载安装包并执行静默安装。

    Args:
        request: 升级请求
            - version: 目标版本号（可选）
            - download_url: 安装包下载地址
            - force: 是否强制升级（可选，默认 true）

    Returns:
        Dict: 升级响应
            - status: skipped/upgrading/failed
            - message: 状态描述
            - current_version: 当前版本
            - target_version: 目标版本

    注意：升级成功后 Worker 会立即退出，由安装程序重启。
    """
    if not worker:
        raise HTTPException(status_code=503, detail="Worker not initialized")

    logger.info(
        f"Upgrade request: version={request.version}, "
        f"download_url={request.download_url}, force={request.force}"
    )

    try:
        result = await handle_upgrade(request)

        # 如果状态是 upgrading，Worker 立即退出
        if result.status == "upgrading":
            logger.info("Worker 即将退出以完成升级...")
            # 返回响应后退出
            # 注意：sys.exit() 在子线程中只会终止该线程，不会终止进程
            # 使用 os._exit() 强制终止整个进程
            def delayed_exit():
                time.sleep(0.5)
                logger.info("Worker 退出中...")
                os._exit(0)
            threading.Thread(target=delayed_exit, daemon=True).start()

        return result.to_dict()

    except UpgradeError as e:
        logger.error(f"Upgrade failed: {e}")
        return {
            "status": "failed",
            "message": str(e),
        }


# 异常处理
@app.exception_handler(Exception)
async def global_exception_handler(request, exc):
    logger.error(f"Unhandled exception: {exc}")
    return JSONResponse(
        status_code=500,
        content={"error": str(exc)},
    )
