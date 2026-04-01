"""
HTTP Server。

提供 RESTful API 接口供外部平台调用。
"""

import logging
from typing import Optional, Dict, Any, List

from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from worker.worker import Worker, TaskConflictError
from worker.task import Task

logger = logging.getLogger(__name__)


def _format_actions_summary(actions: List[Dict[str, Any]], max_actions: int = 10) -> str:
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
        # 提取所有关键字段
        fields = {"number": i}
        for key in ["action_type", "value", "offset", "x", "y", "image_base64", "package_name", "bundle_id"]:
            if key in action:
                fields[key] = action[key]

        # 截断过长的 value
        value = fields.get("value")
        if isinstance(value, str) and len(value) > 100:
            fields["value"] = value[:97] + "..."

        formatted.append(str(fields))

    remaining = len(actions) - max_actions
    if remaining > 0:
        formatted.append(f"... and {remaining} more action(s)")

    return "[" + ", ".join(formatted) + "]"


def _format_action_results(actions: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
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


def _format_result_for_log(result: Dict[str, Any]) -> Dict[str, Any]:
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
    actions: List[Dict[str, Any]] = Field(..., description="动作列表")
    device_id: Optional[str] = Field(None, description="设备 ID（移动端必填）")


# FastAPI 应用
app = FastAPI(
    title="Test Worker API",
    description="多端自动化测试执行基建 API",
    version="3.0.0",
)

# Worker 实例（在 main.py 中初始化）
worker: Optional[Worker] = None


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

    # 记录任务请求
    logger.info(
        f"Sync task request: platform={request.platform}, "
        f"device_id={request.device_id}, actions_count={len(request.actions)}, "
        f"actions={_format_actions_summary(request.actions)}"
    )

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

    # 记录任务请求
    logger.info(
        f"Async task request: platform={request.platform}, "
        f"device_id={request.device_id}, actions_count={len(request.actions)}, "
        f"actions={_format_actions_summary(request.actions)}"
    )

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


# 异常处理
@app.exception_handler(Exception)
async def global_exception_handler(request, exc):
    logger.error(f"Unhandled exception: {exc}")
    return JSONResponse(
        status_code=500,
        content={"error": str(exc)},
    )