"""
HTTP Server。

提供 RESTful API 接口供外部平台调用。
"""

import logging
from typing import Optional, Dict, Any, List

from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from worker.worker import Worker
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
    """同步执行任务。"""
    if not worker:
        raise HTTPException(status_code=503, detail="Worker not initialized")

    # 记录任务请求
    logger.info(
        f"Task request: platform={request.platform}, "
        f"device_id={request.device_id}, actions={len(request.actions)}"
    )

    # 创建任务对象
    task = Task.create(
        platform=request.platform,
        actions=request.actions,
        device_id=request.device_id,
        user_id=request.user_id,
        config=request.config,
        callback_url=request.callback_url,
        priority=request.priority,
    )

    # 执行任务
    result = worker.execute_task(task)

    # 记录任务结果
    logger.info(
        f"Task result: task_id={task.task_id}, status={result.status}, "
        f"duration={result.duration_ms}ms"
    )

    return result.to_dict()


@app.post("/task")
async def submit_task(request: TaskRequest):
    """提交任务到队列（异步）。"""
    if not worker:
        raise HTTPException(status_code=503, detail="Worker not initialized")

    # TODO: 实现任务队列
    raise HTTPException(status_code=501, detail="Task queue not implemented yet")


@app.get("/task/{task_id}")
async def get_task_result(task_id: str):
    """查询任务结果。"""
    if not worker:
        raise HTTPException(status_code=503, detail="Worker not initialized")

    # TODO: 实现任务结果存储
    raise HTTPException(status_code=501, detail="Task result storage not implemented yet")


@app.delete("/task/{task_id}")
async def cancel_task(task_id: str):
    """取消任务。"""
    if not worker:
        raise HTTPException(status_code=503, detail="Worker not initialized")

    # TODO: 实现任务取消
    raise HTTPException(status_code=501, detail="Task cancellation not implemented yet")


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