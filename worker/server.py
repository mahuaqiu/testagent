"""
HTTP Server。

提供 RESTful API 接口供外部平台调用。
"""

import asyncio
import io
import json
import logging
import os
import re
import threading
import time
from queue import Empty
from typing import Any

import yaml
from PIL import Image
from fastapi import FastAPI, Header, HTTPException, Query, WebSocket, WebSocketDisconnect
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import JSONResponse, PlainTextResponse
from pydantic import BaseModel, Field

from common.request_context import generate_request_id, reset_request_id, set_request_id
from worker.config import (
    load_config_version,
    merge_config_with_local_protection,
    save_config_with_version,
)
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
from worker.platforms.harmony_hdc import _find_hdc_path
from worker.tools import (
    get_script_version,
    save_script,
    script_exists,
    update_script_version,
    validate_script_name,
)
from worker.upgrade import UpgradeError, UpgradeRequest, get_upgrade_status, start_async_upgrade
from worker.errors import WorkerError
from worker.worker import Worker
from worker.screen.windows_sidecar import media_packet_to_websocket_frames

logger = logging.getLogger(__name__)

# WebSocket 连接计数器
_ws_connections: dict[str, int] = {}

# 默认 WebSocket 配置（会被 worker.config 覆盖）
DEFAULT_WS_MAX_CONNECTIONS = 3
DEFAULT_WS_SEND_TIMEOUT = 30
DEFAULT_WS_IDLE_TIMEOUT = 900
DEFAULT_WS_STREAMING_FPS = 10
DEFAULT_WS_STREAMING_BITRATE = 4000000  # H.264 平均码率 (4Mbps, VBR 瞬时突发可超)
DEFAULT_WS_STREAMING_PROFILE = 66  # H.264 profile: 66=Baseline, 77=Main, 100=High

# 鸿蒙 JPEG 推流重编码默认参数（会被 worker.config 覆盖）
DEFAULT_HARMONY_STREAMING_FPS = 10
DEFAULT_HARMONY_STREAMING_JPEG_QUALITY = 60
# 设备锁屏时保留 H.264 WebSocket，解锁后重新启动官方会话的最长等待时间。
DEFAULT_HARMONY_H264_UNLOCK_WAIT_TIMEOUT = 300.0
# 降采样长边上限：流开头已通过 meta 文本帧把真机分辨率下发给前端做坐标
# 基准（与推流尺寸解耦），故此处缩小不会影响坐标；<=0 表示不缩放。
DEFAULT_HARMONY_STREAMING_MAX_LONG_EDGE = 1600


async def _send_harmony_h264_fallback(websocket: WebSocket, reason: str) -> None:
    """通知前端鸿蒙 H.264 不可用，并请求切换到 JPEG。"""
    logger.warning("鸿蒙 H.264 推流降级 JPEG: %s", reason)
    try:
        await websocket.send_text(
            json.dumps(
                {"type": "codec_fallback", "codec": "jpeg", "reason": reason},
                ensure_ascii=False,
            )
        )
        await websocket.close(code=4001, reason="codec_fallback:jpeg")
    except Exception as exc:
        logger.debug("发送鸿蒙 H.264 降级通知失败: %s", exc)


def _recompress_jpeg(
    frame: bytes,
    quality: int,
    max_long_edge: int,
) -> tuple[bytes, tuple[int, int] | None, tuple[int, int] | None]:
    """鸿蒙实时推流帧重编码：解码 JPEG → 可选等比缩小 → 按低质量重编码，压带宽。

    仅用于 WS 实时推流（鸿蒙设备端 agent.so 出高画质大 JPEG，原样转发极费带宽）；
    截图/录屏不经过此函数（走设备端 snapshot_display），画质不受影响。
    重编码失败时回退原帧。

    Returns:
        (重编码后帧, 源尺寸, 目标尺寸)；失败时尺寸为 None。
    """
    try:
        with Image.open(io.BytesIO(frame)) as img:
            src_size = img.size
            # === DCT 域直接缩放（新增）===
            # draft 后直接 resize，省掉一半解码成本，画质几乎无损
            if max_long_edge and max_long_edge < max(src_size):
                ratio = max_long_edge / max(src_size)
                target_w = max(1, int(src_size[0] * ratio))
                target_h = max(1, int(src_size[1] * ratio))
                target_size = (target_w, target_h)
            else:
                target_size = src_size
            # draft() 原地设置 JPEG 解码提示，返回值不是图像对象。
            img.draft("RGB", target_size)
            img = img.resize(
                target_size,
                Image.BILINEAR,
            )
            dst_size = img.size
            out = io.BytesIO()
            img.save(out, format="JPEG", quality=quality)
            return out.getvalue(), src_size, dst_size
    except Exception as e:
        logger.error(f"鸿蒙推流帧重编码失败，回退原帧: {e}", exc_info=True)
        return frame, None, None


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


_PERFORMANCE_DEVICE_TYPES = {"windows", "harmony_pc", "harmony_mobile"}


def _resolve_performance_hdc_path(device_type: str) -> str | None:
    """把配置中的 HDC 路径解析成可执行文件。

    配置可能是 hdc.exe、tools/hdc 相对路径或 SDK 根目录；性能采集不能把
    未解析的目录字符串直接交给 perfharmony。未配置时由 _find_hdc_path
    自动搜索 tools/hdc 兜底。
    """
    if not worker or not worker.config:
        return _find_hdc_path(None)

    configured = worker.config.get_platform_config(device_type).get("hdc_path")
    return _find_hdc_path(configured)


def _prepare_performance_collector(
    device_id: str,
    device_type: str | None,
    device_sn: str | None,
    *,
    require_identity: bool = True,
):
    """校验性能采集身份并配置 Collector。

    ``device_id`` 是 ZQ 的 EnvMachine.id；鸿蒙设备必须用注册表中的 HDC
    UDID（device_sn）定位，不能从 URL 参数猜测或回退到 Windows。

    ``require_identity=False`` 用于 stop/status 兼容旧客户端：缺身份时只
    按 device_id 取已有 Collector，不把类型默认成 windows 后覆盖配置。
    """
    if not worker:
        raise HTTPException(status_code=503, detail="Worker not initialized")

    collector = get_collector(device_id)

    if not require_identity and not device_type and not device_sn:
        return collector, collector._device_type or "windows", collector._device_sn

    normalized_type = (device_type or "windows").strip().lower()
    if normalized_type not in _PERFORMANCE_DEVICE_TYPES:
        raise HTTPException(status_code=400, detail=f"不支持的性能采集设备类型: {normalized_type}")
    normalized_sn = device_sn.strip() if device_sn else None

    if normalized_type != "windows":
        if not normalized_sn:
            raise HTTPException(status_code=400, detail="鸿蒙性能采集必须提供 device_sn（HDC UDID）")
        record = worker.device_registry.get(normalized_type, normalized_sn)
        if record is None:
            raise HTTPException(status_code=404, detail="鸿蒙设备未在 Worker 注册表中")
        if (record.connection_status or "").lower() in {"disconnected", "offline", "faulty"}:
            raise HTTPException(status_code=409, detail="鸿蒙设备当前不在线")
        if (record.health_status or "").lower() in {"unhealthy", "faulty", "error"}:
            raise HTTPException(status_code=409, detail="鸿蒙设备当前不健康")

    hdc_path = None
    if normalized_type != "windows":
        hdc_path = _resolve_performance_hdc_path(normalized_type)
    try:
        collector.configure_device(normalized_type, normalized_sn, hdc_path)
    except ValueError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    return collector, normalized_type, normalized_sn


# ========== API 端点 ==========


@app.get("/worker_devices")
async def get_worker_devices():
    """获取 Worker 状态和设备信息（合并接口）。"""
    if not worker:
        raise HTTPException(status_code=503, detail="Worker not initialized")

    return worker.get_worker_devices()


@app.post("/task/execute")
async def execute_task(request: TaskRequest):
    """同步执行任务，等待统一任务服务返回结果。"""
    if not worker:
        raise HTTPException(status_code=503, detail="Worker not initialized")

    request_id = generate_request_id()
    request_id_token = set_request_id(request_id)
    try:
        logger.info(f"Sync task raw request: {_format_request_for_log(request)}")
        window_dict = request.window.model_dump(by_alias=True) if request.window else None
        result = await asyncio.to_thread(worker.execute_sync, request.platform, request.actions, request.device_id, window_dict)
        result["request_id"] = request_id
        logger.info(f"Sync task response: {_format_result_for_log(result)}")
        return result
    except WorkerError as exc:
        raise HTTPException(status_code=exc.http_status, detail=exc.to_dict()) from exc
    except Exception as exc:
        logger.error(f"execute_sync failed: {exc}", exc_info=True)
        raise HTTPException(status_code=500, detail={"code": "TASK_EXECUTION_FAILED", "message": str(exc), "retryable": True, "details": {}}) from exc
    finally:
        reset_request_id(request_id_token)


@app.post("/remote/execute")
async def execute_remote_task(request: TaskRequest):
    """同步执行远程桌面操作，和普通用例使用独立资源域。"""
    if not worker:
        raise HTTPException(status_code=503, detail="Worker not initialized")

    request_id = generate_request_id()
    request_id_token = set_request_id(request_id)
    try:
        logger.info(f"Remote task raw request: {_format_request_for_log(request)}")
        window_dict = request.window.model_dump(by_alias=True) if request.window else None
        result = await asyncio.to_thread(
            worker.execute_remote_sync,
            request.platform,
            request.actions,
            request.device_id,
            window_dict,
        )
        result["request_id"] = request_id
        logger.info(f"Remote task response: {_format_result_for_log(result)}")
        return result
    except WorkerError as exc:
        raise HTTPException(status_code=exc.http_status, detail=exc.to_dict()) from exc
    except Exception as exc:
        logger.error(f"execute_remote_sync failed: {exc}", exc_info=True)
        raise HTTPException(
            status_code=500,
            detail={
                "code": "REMOTE_TASK_EXECUTION_FAILED",
                "message": str(exc),
                "retryable": True,
                "details": {},
            },
        ) from exc
    finally:
        reset_request_id(request_id_token)


@app.post("/task/execute_async")
async def execute_task_async(request: TaskRequest, idempotency_key: str | None = Header(default=None, alias="Idempotency-Key")):
    """异步提交任务，支持幂等键重试。"""
    if not worker:
        raise HTTPException(status_code=503, detail="Worker not initialized")

    request_id = generate_request_id()
    request_id_token = set_request_id(request_id)
    try:
        logger.info(f"Async task raw request: {_format_request_for_log(request)}")
        task_id, status, task_request_id = worker.execute_async(platform=request.platform, actions=request.actions, device_id=request.device_id, window=request.window.model_dump(by_alias=True) if request.window else None, idempotency_key=idempotency_key)
        logger.info(f"Async task submitted: task_id={task_id}, status={status}")
        return {"task_id": task_id, "status": status, "request_id": task_request_id}
    except WorkerError as exc:
        raise HTTPException(status_code=exc.http_status, detail=exc.to_dict()) from exc
    finally:
        reset_request_id(request_id_token)


@app.get("/task/{task_id}")
async def get_task_result(task_id: str):
    """查询任务快照，允许重复轮询。"""
    if not worker:
        raise HTTPException(status_code=503, detail="Worker not initialized")

    result = worker.get_task_result(task_id)
    if result is None:
        raise HTTPException(status_code=404, detail={"code": "TASK_NOT_FOUND", "message": "Task not found", "retryable": False, "details": {"task_id": task_id}})
    request_id = result.get("request_id")
    request_id_token = None
    if request_id:
        request_id_token = set_request_id(request_id)
    try:
        logger.info(f"Task result response: {_format_result_for_log(result)}")
        return result
    finally:
        if request_id_token is not None:
            reset_request_id(request_id_token)


@app.delete("/task/{task_id}")
async def cancel_task(task_id: str):
    """请求取消任务，任务记录不会因取消请求被删除。"""
    if not worker:
        raise HTTPException(status_code=503, detail="Worker not initialized")
    snapshot = worker.get_task_result(task_id)
    request_id = snapshot.get("request_id") if snapshot else None
    request_id_token = set_request_id(request_id) if request_id else None
    try:
        result = worker.cancel_task(task_id)
        logger.info(f"Task cancellation requested: task_id={task_id}, status={result['status']}")
        return result
    except WorkerError as exc:
        raise HTTPException(status_code=exc.http_status, detail=exc.to_dict()) from exc
    finally:
        if request_id_token is not None:
            reset_request_id(request_id_token)

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

        # 执行查询 - 使用线程池避免阻塞事件循环
        if mode == "lines":
            content, log_count = await asyncio.to_thread(query_by_lines, log_path, lines_val)
            files_scanned = 1
        elif mode == "request_id":
            content, log_count, files_scanned = await asyncio.to_thread(
                query_by_request_id, log_path, request_id_val
            )
        else:  # time_range
            content, log_count, files_scanned = await asyncio.to_thread(
                query_by_time_range, log_path, start_dt, end_dt
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

        # 4. 配置合并（保留本地 IP 和设备发现配置）
        try:
            merged_config = merge_config_with_local_protection(request.config_content)
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

        # 9. 触发注册上报（通知平台脚本版本已更新）
        worker._report_devices()

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
    device_type: str | None = Query(default=None, description="设备类型"),
    device_sn: str | None = Query(default=None, description="设备物理标识，鸿蒙为 HDC UDID"),
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

    collector, normalized_type, normalized_sn = _prepare_performance_collector(
        device_id, device_type, device_sn
    )
    try:
        processes = collector.get_processes(search)
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error

    logger.info(
        "Get processes: device_id=%s device_type=%s device_sn=%s search=%s count=%s",
        device_id,
        normalized_type,
        normalized_sn,
        search,
        len(processes),
    )

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

    collector, normalized_type, normalized_sn = _prepare_performance_collector(
        device_id, request.device_type, request.device_sn
    )

    # 设置后端地址（使用 platform_api）
    if worker.config and worker.config.platform_api:
        collector.set_backend_host(worker.config.platform_api)

    request = request.model_copy(update={"device_type": normalized_type, "device_sn": normalized_sn})
    result = collector.start_collect(request)

    if result.get("status") == "conflict":
        raise HTTPException(status_code=409, detail=result)
    if result.get("status") == "error":
        raise HTTPException(status_code=503, detail=result)

    logger.info(
        f"Start collect: device_id={device_id}, device_type={normalized_type}, "
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

    request_type = request.device_type if request else None
    request_sn = request.device_sn if request else None
    collector, normalized_type, normalized_sn = _prepare_performance_collector(
        device_id,
        request_type,
        request_sn,
        require_identity=bool(request_type or request_sn),
    )
    result = collector.stop_collect(request)

    if result.get("status") == "error":
        raise HTTPException(status_code=409, detail=result)

    logger.info(
        "Stop collect: device_id=%s device_type=%s device_sn=%s collect_id=%s",
        device_id,
        normalized_type,
        normalized_sn,
        request.collect_id if request else None,
    )

    return result


@app.get("/api/worker/{device_id}/collect/status")
async def get_collect_status(
    device_id: str,
    device_type: str | None = Query(default=None, description="设备类型"),
    device_sn: str | None = Query(default=None, description="设备物理标识，鸿蒙为 HDC UDID"),
):
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

    collector, _, _ = _prepare_performance_collector(
        device_id,
        device_type,
        device_sn,
        require_identity=bool(device_type or device_sn),
    )
    status = collector.get_status()

    logger.debug(f"Get collect status: device_id={device_id}, is_collecting={status.is_collecting}")

    return status.model_dump()


# ========== WebSocket 請求 ==========


@app.websocket("/ws/screen/{platform}/{device_id}")
async def screen_stream(
    websocket: WebSocket,
    platform: str,
    device_id: str,
    monitor: int = 1
):
    """实时屏幕推流。

    Args:
        platform: 设备平台类型 (ios, android, windows, mac, web, harmony_mobile, harmony_pc)
        device_id: 设备标识符
        monitor: 屏幕索引（mss索引：1=主显示器，2+=副显示器）

    Query Parameters:
        codec: 推流编码格式 (jpeg/h264/mjpeg)，默认 jpeg
    """

    # 解析 codec 参数
    query_params = websocket.query_params
    codec = query_params.get("codec", "jpeg")
    valid_codecs = ["jpeg", "h264", "mjpeg"]
    if codec not in valid_codecs:
        await websocket.close(code=1008, reason=f"Invalid codec: {codec}")
        return

    # Windows 不支持 MJPEG
    if platform == "windows" and codec == "mjpeg":
        logger.error("Windows does not support MJPEG codec, falling back to jpeg")
        codec = "jpeg"

    # iOS/Android 不支持 H.264（当前版本）
    if platform in ("ios", "android") and codec == "h264":
        logger.error(f"{platform} does not support H.264 codec, falling back to jpeg")
        codec = "jpeg"

    # 鸿蒙官方会话支持原始 H.264；仅保留 MJPEG 的 JPEG 兼容处理。
    if platform in ("harmony_mobile", "harmony_pc") and codec == "mjpeg":
        logger.error(f"{platform} does not support MJPEG codec, falling back to jpeg")
        codec = "jpeg"

    # 从配置读取参数（使用默认值作为 fallback）
    max_connections = DEFAULT_WS_MAX_CONNECTIONS
    send_timeout = DEFAULT_WS_SEND_TIMEOUT
    streaming_fps = DEFAULT_WS_STREAMING_FPS
    streaming_bitrate = DEFAULT_WS_STREAMING_BITRATE
    streaming_profile = DEFAULT_WS_STREAMING_PROFILE
    idle_timeout = DEFAULT_WS_IDLE_TIMEOUT
    harmony_fps = DEFAULT_HARMONY_STREAMING_FPS
    harmony_jpeg_quality = DEFAULT_HARMONY_STREAMING_JPEG_QUALITY
    harmony_max_long_edge = DEFAULT_HARMONY_STREAMING_MAX_LONG_EDGE
    if worker and worker.config:
        max_connections = worker.config.websocket_max_connections_per_device
        send_timeout = worker.config.websocket_send_timeout_seconds
        streaming_fps = worker.config.websocket_streaming_fps
        streaming_bitrate = worker.config.websocket_streaming_bitrate
        streaming_profile = worker.config.websocket_streaming_profile
        idle_timeout = worker.config.websocket_idle_timeout_seconds
        harmony_fps = worker.config.harmony_streaming_fps
        harmony_jpeg_quality = worker.config.harmony_streaming_jpeg_quality
        harmony_max_long_edge = worker.config.harmony_streaming_max_long_edge

    # 鸿蒙推流使用独立帧率（前端只需 <=10fps），不影响其它平台
    is_harmony = platform in ("harmony_mobile", "harmony_pc")
    if is_harmony:
        streaming_fps = harmony_fps

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

    # 推流帧持续发送不能代表用户仍在操作。前端在用户操作时发送
    # {"type": "activity"}，Worker 同时保留服务端空闲超时作为兜底，
    # 防止浏览器定时器被节流或前端页面未正确卸载时推流资源长期占用。
    last_activity_at = time.monotonic()
    stream_stop_event = asyncio.Event()

    async def receive_activity() -> None:
        """接收前端活动信号；推流帧本身不刷新空闲时间。"""
        nonlocal last_activity_at
        while not stream_stop_event.is_set():
            try:
                message = await websocket.receive()
            except (WebSocketDisconnect, RuntimeError):
                stream_stop_event.set()
                return
            if message.get("type") == "websocket.disconnect":
                stream_stop_event.set()
                return
            text = message.get("text")
            if not text:
                continue
            try:
                payload = json.loads(text)
            except (TypeError, json.JSONDecodeError):
                continue
            if isinstance(payload, dict) and payload.get("type") == "activity":
                last_activity_at = time.monotonic()

    async def close_when_idle() -> None:
        """服务端空闲超时关闭 WebSocket，确保主循环进入 finally 清理资源。"""
        timeout = max(1.0, float(idle_timeout))
        while not stream_stop_event.is_set():
            await asyncio.sleep(min(1.0, timeout))
            if time.monotonic() - last_activity_at < timeout:
                continue
            logger.info(
                "WebSocket 因无用户操作自动断开: platform=%s, device=%s, idle_seconds=%.0f",
                platform,
                log_device,
                timeout,
            )
            stream_stop_event.set()
            try:
                # 使用正常关闭码，避免前端把服务端空闲回收误判为异常并自动重连。
                await websocket.close(code=1000, reason="idle_timeout")
            except Exception as exc:
                logger.debug("服务端空闲关闭 WebSocket 失败: %s", exc)
            return

    activity_task = asyncio.create_task(receive_activity())
    idle_watchdog_task = asyncio.create_task(close_when_idle())

    frame_source = None
    screen_manager = None
    direct_h264_frame_source = is_harmony and codec == "h264"
    h264_subscriber_id = None
    h264_queue = None
    h264_diag_started_at = None
    h264_diag_sent_packets = 0
    h264_diag_sent_bytes = 0
    h264_diag_first_packet_at = None
    h264_diag_first_video_at = None
    h264_diag_packet_types: list[str] = []
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
        elif platform in ("harmony_mobile", "harmony_pc"):
            harmony_manager = _get_harmony_manager(platform)
            if not harmony_manager:
                logger.warning(f"WebSocket rejected: {platform} platform not initialized")
                await websocket.close(code=1008, reason=f"{platform} platform not initialized")
                return
            if not harmony_manager._device_clients.get(device_id):
                # 未注册时尝试拉起设备服务（与 DeviceMonitor 同一入口）
                status, message = harmony_manager.ensure_device_service(device_id)
                if status != "online":
                    logger.warning(f"WebSocket rejected: Harmony device not available: {device_id}, {message}")
                    await websocket.close(code=1008, reason=f"Harmony device not available: {device_id}")
                    return

        if direct_h264_frame_source:
            # 官方 H.264 不需要 ScreenManager 的 JPEG 捕获线程；锁屏时保留
            # 独立帧源，等待解锁后再启动 Java Bridge，避免直接降级成旧 JPEG。
            frame_source = _create_frame_source(
                platform,
                device_id,
                monitor,
                use_official_h264=True,
            )
        elif platform == "windows":
            from worker.screen.windows_sidecar import get_windows_sidecar_manager

            screen_manager = get_windows_sidecar_manager(
                conn_key,
                monitor=monitor,
                idle_fps=1,
                active_fps=streaming_fps,
            )
        # 根据 platform 创建对应的 FrameSource
        elif conn_key not in _screen_managers:
            frame_source = _create_frame_source(platform, device_id, monitor)
            screen_manager = get_screen_manager(conn_key, frame_source)
        else:
            screen_manager = _screen_managers[conn_key]
            # 获取已存在的 frame_source
            frame_source = screen_manager._frame_source

        # iOS MJPEG 透传模式特殊处理
        if platform == "ios" and codec == "mjpeg":
            # 使用 MJPEG 透传
            mjpeg_proxy = frame_source.start_mjpeg_proxy()
            await mjpeg_proxy.proxy_to_websocket(websocket)
            mjpeg_proxy.stop()
            return

        # 根据 codec 配置帧源（透传 bitrate/profile 给 H.264 推流；jpeg/mjpeg 忽略）
        # Windows H.264 优先使用 RSM1 二进制媒体通道；如果当前客户端或 sidecar
        # 不支持，会在 manager 内部保留旧的文本/base64 兼容路径。
        use_binary_media = platform == "windows" and codec == "h264"
        stream_options = {
            "codec": codec,
            "bitrate": streaming_bitrate,
            "profile": streaming_profile,
        }
        if use_binary_media:
            stream_options["binary"] = True
        streamer = None
        if not direct_h264_frame_source:
            streamer = screen_manager.start_streaming(**stream_options)

        # 鸿蒙官方 H.264 直接转发 Java SDK 的 Annex-B 数据，不经过
        # ScreenManager 的 JPEG 队列和 Pillow 重编码。
        if is_harmony and codec == "h264":
            if not getattr(frame_source, "supports_h264", False):
                await _send_harmony_h264_fallback(
                    websocket,
                    f"{platform} 当前帧源不支持 H.264",
                )
                return
            h264_subscriber_id = f"{conn_key}/h264/{id(websocket)}"
            h264_wait_deadline = time.monotonic() + DEFAULT_HARMONY_H264_UNLOCK_WAIT_TIMEOUT
            h264_wait_logged = False
            while True:
                try:
                    h264_queue = await asyncio.to_thread(
                        frame_source.subscribe_h264,
                        h264_subscriber_id,
                    )
                    break
                except ConnectionError as exc:
                    if (
                        getattr(frame_source, "h264_waiting_for_unlock", lambda: False)()
                        and time.monotonic() < h264_wait_deadline
                    ):
                        if not h264_wait_logged:
                            logger.info(
                                "screen_stream: 鸿蒙设备锁屏，等待解锁后启动 H.264: conn_key=%s",
                                conn_key,
                            )
                            h264_wait_logged = True
                        await asyncio.sleep(1.0)
                        continue
                    await _send_harmony_h264_fallback(websocket, str(exc))
                    return
                except Exception as exc:
                    await _send_harmony_h264_fallback(websocket, str(exc))
                    return
            h264_diag_started_at = time.monotonic()
            logger.info("screen_stream: 使用鸿蒙官方 H.264 直通通道, conn_key=%s", conn_key)

        # Windows H.264 推流使用推模式
        if platform == "windows" and codec == "h264" and streamer.uses_binary_media:
            logger.info("screen_stream: 使用 Windows RSM1 二进制媒体通道, conn_key=%s", conn_key)
            import time as _stream_diag_time
            _diag_started = _stream_diag_time.monotonic()
            _diag_window_started = _diag_started
            _diag_packets = 0
            _diag_bytes = 0
            _diag_max_send_ms = 0.0
            _diag_last_sequence = None
            try:
                while streamer.is_running() and not stream_stop_event.is_set():
                    packet = await streamer.get_media_packet_async()
                    if not packet:
                        continue
                    _send_started = _stream_diag_time.monotonic()
                    _frames = media_packet_to_websocket_frames(packet)
                    for _frame in _frames:
                        await asyncio.wait_for(websocket.send_bytes(_frame), timeout=send_timeout)
                    _sent_at = _stream_diag_time.monotonic()
                    _send_ms = (_sent_at - _send_started) * 1000
                    _diag_packets += 1
                    _diag_bytes += len(_frame)
                    _diag_max_send_ms = max(_diag_max_send_ms, _send_ms)
                    _sequence = int(packet.get("sequence", -1))
                    _flags = int(packet.get("flags", 0))
                    _gap = 0 if _diag_last_sequence is None else _sequence - _diag_last_sequence
                    _diag_last_sequence = _sequence
                    if _gap != 1:
                        logger.debug("[stream-diag] websocket packet conn_key=%s sequence=%d gap=%d pts_100ns=%s flags=%d bytes=%d reader_count=%s connected_ms=%.1f read_ms=%.1f buffered_bytes=%s send_ms=%.1f relay_ms=%.1f elapsed_ms=%.1f", conn_key, _sequence, _gap, packet.get("pts_100ns"), _flags, len(_frame), getattr(getattr(streamer, "_media_reader", None), "last_diagnostics", {}).get("_packet_count"), getattr(getattr(streamer, "_media_reader", None), "last_diagnostics", {}).get("_connected_ms", 0.0), getattr(getattr(streamer, "_media_reader", None), "last_diagnostics", {}).get("_read_ms", 0.0), getattr(getattr(streamer, "_media_reader", None), "last_diagnostics", {}).get("_buffered_bytes"), _send_ms, (_sent_at - getattr(getattr(streamer, "_media_reader", None), "last_diagnostics", {}).get("_received_monotonic", _sent_at)) * 1000, (_sent_at - _diag_started) * 1000)
                    if _send_ms >= 50:
                        logger.warning("[stream-diag] websocket slow send conn_key=%s sequence=%d send_ms=%.1f bytes=%d", conn_key, _sequence, _send_ms, len(_frame))
                    if _sent_at - _diag_window_started >= 5.0:
                        logger.debug("[stream-diag] websocket summary conn_key=%s packets=%d bytes=%d last_sequence=%d max_send_ms=%.1f elapsed_ms=%.1f", conn_key, _diag_packets, _diag_bytes, _sequence, _diag_max_send_ms, (_sent_at - _diag_started) * 1000)
                        _diag_window_started = _sent_at
                        _diag_packets = 0
                        _diag_bytes = 0
                        _diag_max_send_ms = 0.0
            finally:
                streamer.stop()
        elif platform == "windows" and codec == "h264" and streamer.codec == "h264":
            from worker.screen.windows_sidecar import PushFrameReader

            logger.info("screen_stream: 开始 Windows H.264 推流, conn_key=%s", conn_key)

            # 获取 client 引用
            client = screen_manager._client
            reader = PushFrameReader(client, session_id=conn_key)
            logger.info("screen_stream: PushFrameReader 已创建, 开始启动推流")
            reader.start_push(fps=streaming_fps)
            logger.info("screen_stream: push 已启动，等待 SPS+PPS")

            # 先发送 SPS+PPS（它们会先到达，需要等待两者都收到）
            sps_data = None
            pps_data = None
            wait_count = 0
            sps_pps_deadline = 5.0  # 最多等待 5 秒
            import time as _time
            _sps_pps_start = _time.monotonic()

            # 等待 SPS 和 PPS 都收到（带总超时，单次 0.5s 超时不立即放弃）
            while (sps_data is None or pps_data is None) and not stream_stop_event.is_set():
                wait_count += 1
                frame_type, frame_data = await reader.get_frame()
                logger.info("screen_stream: get_frame() 返回: type=%s, data=%s, wait_count=%d", frame_type, "None" if frame_data is None else f"{len(frame_data)} bytes", wait_count)
                if frame_data is None:
                    # 单次超时：检查是否超过总时限，是才退出
                    if _time.monotonic() - _sps_pps_start > sps_pps_deadline:
                        logger.warning("screen_stream: 等待 SPS+PPS 超时(%ds)，退出等待循环, wait_count=%d", sps_pps_deadline, wait_count)
                        break
                    continue
                if frame_type == 'sps':
                    sps_data = frame_data
                    logger.info("screen_stream: 收到 SPS, size=%d", len(sps_data))
                elif frame_type == 'pps':
                    pps_data = frame_data
                    logger.info("screen_stream: 收到 PPS, size=%d", len(pps_data))

            # 合并发送 SPS+PPS（格式：[1字节前缀 0x01][SPS Annex-B][PPS Annex-B]）
            # sps_data/pps_data 各自已带 00 00 00 01 起始码，直接串联即可，
            # 不在中间再加 0x01 之类分隔符（会被 jmuxer 当成 SPS NAL 的尾部数据破坏解析）。
            if sps_data and pps_data:
                combined = bytes([0x01]) + sps_data + pps_data
                await websocket.send_bytes(combined)

            # 主循环：从推模式读取器获取帧并发送
            try:
                # ★ 限速兜底：用 streaming_fps 控制转发节奏，防 Rust 突发产帧
                # （瞬时多产 IDR+P / 编码器突发）导致前端 frame_queue 积压丢旧帧 + 带宽不可预测。
                # 常态下 Rust 真实捕获 ~8fps（低于配置 10fps），此限速基本 no-op；
                # 不期待它降延迟（延迟由冲刷 + GOP/码控负责），仅压住瞬时突发。
                frame_interval = 1.0 / streaming_fps if streaming_fps > 0 else 0.0
                import time as _t
                last_send = _t.monotonic()
                while reader.is_running() and not stream_stop_event.is_set():
                    frame_type, frame_data = await reader.get_frame()
                    if not frame_data:
                        continue
                    if frame_type in ('idr', 'p'):
                        # ws 协议契约（与前端 useMseDecoder 一致）：每帧前 1 字节类型前缀
                        # 0x02=IDR, 0x03=P；Annex-B NAL 原样追加在后。
                        # 不加前缀会被前端 detectFrameType 判为 Unknown 走 JPEG 分支，永远不出画面。
                        prefix = b'\x02' if frame_type == 'idr' else b'\x03'
                        _t0 = _t.monotonic()
                        await asyncio.wait_for(
                            websocket.send_bytes(prefix + frame_data),
                            timeout=send_timeout
                        )
                        _dt = (_t.monotonic() - _t0) * 1000
                        # 仅在 send 明显变慢时记录（>100ms 视为潜在客户端反压）
                        if _dt > 100:
                            logger.warning("[consume] send 慢 %s size=%d send=%.1fms", frame_type, len(frame_data), _dt)
                        # 限速兜底：发送间隔不足 frame_interval 则补 sleep；超时则不 sleep（已落后）
                        if frame_interval > 0:
                            elapsed = _t.monotonic() - last_send
                            if elapsed < frame_interval:
                                await asyncio.sleep(frame_interval - elapsed)
                        last_send = _t.monotonic()
                    # sps/pps 不限速，确保尽快送达（已在 SPS+PPS 阶段提前发送，主循环偶发的 sps/pps 也应立即转发）
            finally:
                # 停止推流模式
                reader.stop_push()
        elif is_harmony and codec == "h264":
            # 鸿蒙官方 H.264：订阅队列中的协议帧并直接转发。
            while frame_source.h264_stream_running() and not stream_stop_event.is_set():
                try:
                    frame = await asyncio.to_thread(h264_queue.get, timeout=1.0)
                except Empty:
                    # 官方 HOScrcpy 在静止画面下可能只回调 READY，不持续产生 onData。
                    # 空队列不代表会话失败，只有 Bridge/帧源明确停止时才结束连接。
                    continue
                h264_diag_sent_packets += 1
                h264_diag_sent_bytes += len(frame)
                frame_type = {
                    0x01: "config",
                    0x02: "idr",
                    0x03: "p",
                }.get(frame[0] if frame else -1, "unknown")
                if len(h264_diag_packet_types) < 8:
                    h264_diag_packet_types.append(frame_type)
                if h264_diag_first_packet_at is None:
                    h264_diag_first_packet_at = time.monotonic()
                    logger.info(
                        "鸿蒙 H.264 WebSocket 首包: conn_key=%s, frame_type=%s, bytes=%d, elapsed_ms=%.1f",
                        conn_key,
                        frame_type,
                        len(frame),
                        (h264_diag_first_packet_at - h264_diag_started_at) * 1000
                        if h264_diag_started_at is not None
                        else -1,
                    )
                if frame and frame[0] in (0x02, 0x03) and h264_diag_first_video_at is None:
                    h264_diag_first_video_at = time.monotonic()
                    logger.info(
                        "鸿蒙 H.264 WebSocket 首个视频包: conn_key=%s, frame_type=%s, bytes=%d, elapsed_ms=%.1f",
                        conn_key,
                        frame_type,
                        len(frame),
                        (h264_diag_first_video_at - h264_diag_started_at) * 1000
                        if h264_diag_started_at is not None
                        else -1,
                    )
                await asyncio.wait_for(
                    websocket.send_bytes(frame),
                    timeout=send_timeout,
                )

            if not frame_source.h264_stream_running() and not stream_stop_event.is_set():
                await _send_harmony_h264_fallback(
                    websocket,
                    "鸿蒙官方 H.264 会话已停止",
                )
        else:
            # 非 Windows/鸿蒙 H.264 之外的路径使用 JPEG/MJPEG 拉模式。
            # 鸿蒙：设备端 agent.so 出高画质大 JPEG，转发前在主机重编码（缩小+降质）压带宽
            harmony_recompress = is_harmony and codec == "jpeg" and harmony_jpeg_quality > 0

            # 鸿蒙降采样会改变推流图像尺寸；前端若仍用推流尺寸做坐标基准会错位。
            # 参考 Windows（H.264 SPS 带内自描述原生分辨率）的思路：在流开头发一条
            # JSON 文本帧，把真机原生分辨率带给前端做坐标基准，与推流图像尺寸解耦。
            # 【安全约束】仅当成功拿到真机分辨率(>0)时才允许降采样；否则强制不缩放，
            # 宁可多占带宽也绝不让坐标错位。
            harmony_downscale = 0
            if harmony_recompress:
                try:
                    real_w, real_h = await asyncio.to_thread(frame_source.get_screen_size)
                except Exception as e:
                    real_w, real_h = 0, 0
                    logger.error(f"鸿蒙获取真机分辨率失败，禁用降采样: {e}", exc_info=True)
                if real_w > 0 and real_h > 0:
                    await websocket.send_text(
                        json.dumps({"type": "meta", "width": real_w, "height": real_h})
                    )
                    harmony_downscale = harmony_max_long_edge
                    logger.info(
                        "鸿蒙推流已下发真机分辨率 meta: %dx%d, 降采样长边上限=%d",
                        real_w, real_h, harmony_downscale,
                    )
                else:
                    logger.error("鸿蒙未取得真机分辨率，禁用降采样(坐标安全优先)，仅降质")
            _logged_reencode = False
            while streamer.is_running() and not stream_stop_event.is_set():
                # 先 sleep 控制帧率（发送完上一帧后不要立即请求下一帧）
                await asyncio.sleep(1.0 / streaming_fps)

                frame = await streamer.get_frame_async()

                if not frame:
                    continue

                if harmony_recompress:
                    # 重编码放在线程中执行，避免阻塞事件循环
                    frame, src_size, dst_size = await asyncio.to_thread(
                        _recompress_jpeg, frame, harmony_jpeg_quality, harmony_downscale
                    )
                    if not _logged_reencode and src_size:
                        logger.info(
                            "鸿蒙推流重编码: 源 %sx%s → 目标 %sx%s, quality=%d, fps=%d, 帧大小=%d bytes",
                            src_size[0], src_size[1], dst_size[0], dst_size[1],
                            harmony_jpeg_quality, streaming_fps, len(frame),
                        )
                        _logged_reencode = True

                try:
                    await asyncio.wait_for(
                        websocket.send_bytes(frame),
                        timeout=send_timeout
                    )
                except asyncio.TimeoutError:
                    logger.warning(f"WebSocket send timeout ({send_timeout}s), disconnecting: platform={platform}, device={device_id}")
                    await websocket.close(code=1001, reason="Send timeout")
                    break

    except WebSocketDisconnect:
        log_device = f"{device_id}/{monitor}" if platform in ("windows", "mac") else device_id
        logger.info(f"WebSocket disconnected: platform={platform}, device={log_device}")
    except Exception as e:
        log_device = f"{device_id}/{monitor}" if platform in ("windows", "mac") else device_id
        winerror = getattr(e, "winerror", None)
        expected_disconnect = isinstance(
            e,
            (ConnectionAbortedError, ConnectionResetError, BrokenPipeError),
        ) or winerror in (10053, 10054)
        if expected_disconnect:
            logger.debug(f"WebSocket client disconnected: platform={platform}, device={log_device}, error={e}")
        else:
            logger.error(f"WebSocket error: {e}")
    finally:
        stream_stop_event.set()
        for background_task in (activity_task, idle_watchdog_task):
            if not background_task.done():
                background_task.cancel()
        await asyncio.gather(activity_task, idle_watchdog_task, return_exceptions=True)
        if h264_queue is not None:
            logger.info(
                "鸿蒙 H.264 WebSocket 发送统计: conn_key=%s, packets=%d, bytes=%d, "
                "first_packet_ms=%s, first_video_ms=%s, first_types=%s",
                conn_key,
                h264_diag_sent_packets,
                h264_diag_sent_bytes,
                f"{(h264_diag_first_packet_at - h264_diag_started_at) * 1000:.1f}"
                if h264_diag_started_at is not None and h264_diag_first_packet_at is not None
                else "none",
                f"{(h264_diag_first_video_at - h264_diag_started_at) * 1000:.1f}"
                if h264_diag_started_at is not None and h264_diag_first_video_at is not None
                else "none",
                ",".join(h264_diag_packet_types) or "none",
            )
        if h264_subscriber_id and frame_source is not None:
            try:
                frame_source.unsubscribe_h264(h264_subscriber_id)
            except Exception as exc:
                logger.warning("取消鸿蒙 H.264 订阅失败: %s", exc)
        if direct_h264_frame_source and frame_source is not None:
            try:
                frame_source.stop()
            except Exception as exc:
                logger.debug("停止鸿蒙 H.264 帧源失败: %s", exc)
        # 确保减少连接计数
        _ws_connections[conn_key] = _ws_connections.get(conn_key, 1) - 1

        # 当连接计数降至 0 时，关闭 ScreenManager 以停止后台帧捕获线程
        if _ws_connections[conn_key] <= 0:
            del _ws_connections[conn_key]
            if direct_h264_frame_source:
                # 官方 H.264 使用独立帧源，不创建 ScreenManager。
                pass
            elif platform == "windows":
                from worker.screen.windows_sidecar import close_windows_sidecar_manager

                close_windows_sidecar_manager(conn_key)
            else:
                # 关闭 ScreenManager（停止后台线程，避免资源泄漏）
                from worker.screen.manager import close_screen_manager

                close_screen_manager(conn_key)
            if not direct_h264_frame_source:
                log_device = f"{device_id}/{monitor}" if platform in ("windows", "mac") else device_id
                logger.info(f"ScreenManager closed: conn_key={conn_key}, last WebSocket disconnected")

        log_device = f"{device_id}/{monitor}" if platform in ("windows", "mac") else device_id
        logger.info(f"WebSocket connection closed: platform={platform}, device={log_device}")


def _get_harmony_manager(platform: str):
    """按平台类型获取对应的鸿蒙平台管理器。"""
    if not worker:
        return None
    if platform == "harmony_mobile":
        return worker.harmony_mobile_manager
    if platform == "harmony_pc":
        return worker.harmony_pc_manager
    return None


def _create_frame_source(
    platform: str,
    device_id: str,
    monitor: int = 1,
    *,
    use_official_h264: bool = False,
):
    """根据平台类型创建对应的 FrameSource。

    Args:
        platform: 设备平台类型 (ios, android, windows, mac, web, harmony_mobile, harmony_pc)
        device_id: 设备标识符
        monitor: 屏幕索引（mss索引：1=主显示器，2+=副显示器）

    Returns:
        FrameSource 实例
    """
    from worker.screen.frame_source import (
        HarmonyFrameSource,
        HarmonyOfficialFrameSource,
        MacFrameSource,
        MinicapFrameSource,
        MJPEGFrameSource,
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

    elif platform in ("harmony_mobile", "harmony_pc"):
        # 只有 H.264 直通推流使用官方 Java 会话；截图和 JPEG 推流统一走
        # HDC/agent 帧流，避免依赖本地 H.264 解码旁路。
        harmony_manager = _get_harmony_manager(platform)
        if use_official_h264 and harmony_manager:
            return HarmonyOfficialFrameSource(device_id, harmony_manager)
        hdc_client = (
            harmony_manager._device_clients.get(device_id)
            if harmony_manager
            else None
        )
        if hdc_client is None:
            from worker.platforms.harmony_hdc import HarmonyHdcWrapper

            hdc_client = HarmonyHdcWrapper(device_id)
        return HarmonyFrameSource(device_id, hdc_client)

    elif platform == "mac":
        # Mac: 使用 pyautogui 截屏
        return MacFrameSource(fps=10, monitor=monitor)

    elif platform == "windows":
        # Windows: 不使用此函数创建 FrameSource，应该使用 get_windows_sidecar_manager
        raise ValueError("Windows platform should use get_windows_sidecar_manager instead")

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
