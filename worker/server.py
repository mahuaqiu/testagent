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
    session_id: Optional[str] = Field(None, description="会话 ID（复用会话）")
    config: Optional[Dict[str, Any]] = Field(None, description="任务配置")
    callback_url: Optional[str] = Field(None, description="回调地址")
    priority: int = Field(0, description="优先级")


class SessionRequest(BaseModel):
    """会话请求。"""

    platform: str = Field(..., description="平台: web/android/ios/windows/mac")
    device_id: Optional[str] = Field(None, description="设备 ID（移动端必填）")
    options: Optional[Dict[str, Any]] = Field(None, description="会话选项")


# FastAPI 应用
app = FastAPI(
    title="Test Worker API",
    description="多端自动化测试执行基建 API",
    version="2.0.0",
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
    """获取 Worker 完整状态信息。"""
    if not worker:
        raise HTTPException(status_code=503, detail="Worker not initialized")

    status = worker.get_status()
    devices = worker.get_devices()

    return {
        "worker_id": status.worker_id,
        "status": status.status,
        "started_at": status.started_at.isoformat(),
        "port": worker.port,
        "hostname": worker.host_info.hostname if worker.host_info else "unknown",
        "ip_addresses": worker.host_info.ip_addresses if worker.host_info else [],
        "os_type": worker.host_info.os_type if worker.host_info else "unknown",
        "os_version": worker.host_info.os_version if worker.host_info else "unknown",
        "supported_platforms": status.supported_platforms,
        "active_sessions": status.active_sessions,
        "devices": devices,
    }


@app.post("/task/execute")
async def execute_task(request: TaskRequest):
    """同步执行任务。"""
    if not worker:
        raise HTTPException(status_code=503, detail="Worker not initialized")

    # 创建任务对象
    task = Task.create(
        platform=request.platform,
        actions=request.actions,
        device_id=request.device_id,
        user_id=request.user_id,
        session_id=request.session_id,
        config=request.config,
        callback_url=request.callback_url,
        priority=request.priority,
    )

    # 执行任务
    result = worker.execute_task(task)

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


@app.post("/session")
async def create_session(request: SessionRequest):
    """创建会话。"""
    if not worker:
        raise HTTPException(status_code=503, detail="Worker not initialized")

    try:
        session = worker.create_session(
            platform=request.platform,
            device_id=request.device_id,
            options=request.options,
        )
        return {
            "session_id": session.session_id,
            "platform": session.platform,
            "device_id": session.device_id,
            "created_at": session.created_at.isoformat(),
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/session/{session_id}")
async def get_session(session_id: str, platform: str):
    """获取会话状态。"""
    if not worker:
        raise HTTPException(status_code=503, detail="Worker not initialized")

    session = worker.get_session(platform, session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    return {
        "session_id": session.session_id,
        "platform": session.platform,
        "device_id": session.device_id,
        "created_at": session.created_at.isoformat(),
        "last_active": session.last_active.isoformat(),
    }


@app.delete("/session/{session_id}")
async def close_session(session_id: str, platform: str):
    """关闭会话。"""
    if not worker:
        raise HTTPException(status_code=503, detail="Worker not initialized")

    success = worker.close_session(platform, session_id)
    return {"closed": success}


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