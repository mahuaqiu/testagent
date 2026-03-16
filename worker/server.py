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

# Pydantic 模型定义


class TaskRequest(BaseModel):
    """任务请求。"""

    platform: str = Field(..., description="目标平台: web/android/ios/windows/mac")
    actions: List[Dict[str, Any]] = Field(..., description="动作列表")
    device_id: Optional[str] = Field(None, description="设备 ID（移动端必填）")
    user_id: Optional[str] = Field(None, description="用户标识")
    config: Optional[Dict[str, Any]] = Field(None, description="任务配置")
    callback_url: Optional[str] = Field(None, description="回调地址")
    priority: int = Field(0, description="优先级")


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


@app.get("/status")
async def get_status():
    """获取 Worker 状态。"""
    if not worker:
        raise HTTPException(status_code=503, detail="Worker not initialized")

    return worker.get_status()


@app.get("/devices")
async def get_devices():
    """获取设备信息。"""
    if not worker:
        raise HTTPException(status_code=503, detail="Worker not initialized")

    return worker.get_devices()


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
        f"device_id={request.device_id}, actions={len(request.actions)}"
    )

    # 同步执行（不生成 task_id）
    result = worker.execute_sync(
        platform=request.platform,
        actions=request.actions,
        device_id=request.device_id,
        user_id=request.user_id,
        config=request.config,
    )

    # 记录任务结果
    logger.info(
        f"Sync task result: status={result.get('status')}, "
        f"duration={result.get('duration_ms')}ms"
    )

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
        f"device_id={request.device_id}, actions={len(request.actions)}"
    )

    try:
        task_id, status = worker.execute_async(
            platform=request.platform,
            actions=request.actions,
            device_id=request.device_id,
            user_id=request.user_id,
            config=request.config,
        )

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
        raise HTTPException(status_code=404, detail="Task not found")

    logger.info(f"Task result queried: task_id={task_id}, status={result.get('status')}")

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