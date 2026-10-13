"""产物文件管理 API。

平台经 /files/* 浏览、下载、上传、删除 Worker 上收集目录内的文件。
浏览范围锁定在配置的根目录内,拒绝一切路径穿越;限速通过分块 + 异步 sleep
实现,不阻塞事件循环,不影响测试任务执行。下载不支持 Range 断点续传,
HEAD 请求返回 405,前端如需大小应走 /files/list。
"""

import asyncio
import errno
import heapq
import logging
import os
import shutil
from collections.abc import AsyncIterator
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import quote
from uuid import uuid4

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import StreamingResponse

from common.packaging import get_base_dir
from worker.config import WorkerConfig
from worker.constants import DEFAULT_FILES_MAX_UPLOAD_SIZE_MB, DEFAULT_FILES_ROOT_REL

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/files", tags=["files"])

DEFAULT_ROOT_REL = os.path.join(*DEFAULT_FILES_ROOT_REL.split("/"))
DEFAULT_MAX_UPLOAD_SIZE_MB = DEFAULT_FILES_MAX_UPLOAD_SIZE_MB
CHUNK_SIZE = 256 * 1024
# 目录列表条目上限:超大目录截断返回(truncated=true),避免巨量条目拖垮
# worker 与平台前端表格
MAX_LIST_ENTRIES = 2000


@dataclass
class FilesSettings:
    """文件管理运行时固定参数。"""

    root: Path
    download_rate_limit_mb: float
    upload_rate_limit_mb: float
    max_concurrent_downloads: int
    max_concurrent_uploads: int
    max_upload_size_mb: int


_settings: FilesSettings | None = None
_download_semaphore: asyncio.Semaphore | None = None
_upload_semaphore: asyncio.Semaphore | None = None


def set_files_config(config: WorkerConfig, *, create_root: bool = True) -> None:
    """由 server 启动流程(worker.set_worker)注入配置;测试亦由此注入临时根目录。

    create_root=True 时顺带创建根目录:全新安装时平台首个请求即可用,
    无需人工 mkdir;创建失败仅告警,由各端点的 404 自然兜底。
    """
    global _settings, _download_semaphore, _upload_semaphore
    root = Path(config.files_root or os.path.join(get_base_dir(), DEFAULT_ROOT_REL))
    if create_root:
        try:
            root.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            logger.warning("文件根目录创建失败 %s: %s", root, e)
    upload_size_mb = config.files_max_upload_size_mb
    if upload_size_mb <= 0:
        logger.warning(
            "文件上传大小上限 %sMB 非法（需为正数），回退默认 %sMB",
            upload_size_mb,
            DEFAULT_MAX_UPLOAD_SIZE_MB,
        )
        upload_size_mb = DEFAULT_MAX_UPLOAD_SIZE_MB
    _settings = FilesSettings(
        root=root,
        download_rate_limit_mb=max(0.0, config.files_download_rate_limit_mb),
        upload_rate_limit_mb=max(0.0, config.files_upload_rate_limit_mb),
        max_concurrent_downloads=max(1, config.files_max_concurrent_downloads),
        max_concurrent_uploads=max(1, config.files_max_concurrent_uploads),
        max_upload_size_mb=upload_size_mb,
    )
    _download_semaphore = asyncio.Semaphore(_settings.max_concurrent_downloads)
    _upload_semaphore = asyncio.Semaphore(_settings.max_concurrent_uploads)


def get_files_settings() -> FilesSettings:
    if _settings is None:
        # 兜底:仅出现在未走 worker.set_worker 的场景(如裸路由冒烟)。
        # 不创建根目录,避免未经启动流程就触碰真实磁盘状态。
        logger.warning("文件管理参数未经启动流程注入，使用代码默认值")
        set_files_config(WorkerConfig(), create_root=False)
    assert _settings is not None
    return _settings


def _download_slots() -> asyncio.Semaphore:
    get_files_settings()
    assert _download_semaphore is not None
    return _download_semaphore


def _upload_slots() -> asyncio.Semaphore:
    get_files_settings()
    assert _upload_semaphore is not None
    return _upload_semaphore


def _pacing_delay(size_bytes: int, rate_limit_mb: float) -> float:
    """该块应耗时多少秒(限速 MB/s;0 或负数 = 不限速)。"""
    if rate_limit_mb <= 0:
        return 0.0
    return size_bytes / (rate_limit_mb * 1024 * 1024)


def _content_disposition(name: str) -> str:
    return f"attachment; filename*=UTF-8''{quote(name)}"


def _validate_filename(name: str) -> str:
    name = (name or "").strip()
    if (
        not name
        or name in (".", "..")
        or "/" in name
        or "\\" in name
        or ".." in name
        or ":" in name
        or "\0" in name
    ):
        # "." 经 pathlib 会被折叠为目录本身,replace 时报 PermissionError
        # 并泄漏 .part;\0 则会让 resolve() 抛 ValueError 变 500
        raise HTTPException(status_code=400, detail="非法文件名")
    return name


def resolve_under_root(rel: str | None) -> Path:
    """把相对路径解析到根目录内;拒绝绝对路径、盘符与 .. 穿越。"""
    settings = get_files_settings()
    rel_clean = (rel or "").strip().replace("\\", "/").strip("/")
    if not rel_clean or rel_clean == ".":
        return settings.root.resolve()
    if (
        rel_clean == ".."
        or rel_clean.startswith("../")
        or "/../" in rel_clean
        or Path(rel_clean).is_absolute()
        or ":" in rel_clean
        or "\0" in rel_clean
    ):
        raise HTTPException(status_code=400, detail="非法路径")
    root_resolved = settings.root.resolve()
    target = (root_resolved / rel_clean).resolve()
    if target != root_resolved and root_resolved not in target.parents:
        raise HTTPException(status_code=400, detail="非法路径")
    return target


def _scan_dir(target: Path) -> tuple[list[dict], bool]:
    # 只保留排序靠前的上限 + 1 项，超大目录不再把所有条目读入内存。
    items = heapq.nsmallest(
        MAX_LIST_ENTRIES + 1,
        target.iterdir(),
        key=lambda item: (not item.is_dir(), item.name.lower()),
    )
    truncated = len(items) > MAX_LIST_ENTRIES
    entries = []
    for item in items[:MAX_LIST_ENTRIES]:
        st = item.stat()
        entries.append(
            {
                "name": item.name,
                "is_dir": item.is_dir(),
                "size": st.st_size if item.is_file() else 0,
                "mtime": int(st.st_mtime),
            }
        )
    return entries, truncated


@router.get("/list")
async def list_files(path: str | None = Query(default=None)) -> dict:
    target = resolve_under_root(path)
    if not target.exists():
        raise HTTPException(status_code=404, detail="路径不存在")
    if not target.is_dir():
        raise HTTPException(status_code=400, detail="不是目录")
    # iterdir/stat 是阻塞 IO,放线程池执行,避免大目录拖住事件循环上的流式任务
    entries, truncated = await asyncio.to_thread(_scan_dir, target)
    return {
        "path": (path or "").replace("\\", "/").strip("/"),
        "entries": entries,
        "truncated": truncated,
    }


@router.get("/download")
async def download_file(path: str = Query(...)):
    """流式下载单文件。

    Content-Length 在开始流式前定格:若文件仍被运行中的任务写入,实际
    字节数可能超出声明值,客户端会报传输错误——产物文件应在任务结束后
    再下载。
    """
    settings = get_files_settings()
    target = resolve_under_root(path)
    if not target.exists():
        raise HTTPException(status_code=404, detail="文件不存在")
    if not target.is_file():
        raise HTTPException(status_code=400, detail="不是文件")

    async def chunk_iter() -> AsyncIterator[bytes]:
        # 并发槽在生成器内获取:响应头立即返回,排队者的 body 延迟产出(平台 read 无超时)
        async with _download_slots():
            with open(target, "rb") as f:
                while True:
                    chunk = await asyncio.to_thread(f.read, CHUNK_SIZE)
                    if not chunk:
                        break
                    yield chunk
                    delay = _pacing_delay(len(chunk), settings.download_rate_limit_mb)
                    if delay > 0:
                        await asyncio.sleep(delay)

    headers = {
        "Content-Length": str(target.stat().st_size),
        "Content-Disposition": _content_disposition(target.name),
        # 声明 identity 阻止 GZipMiddleware 重压缩二进制流:
        # 压缩会丢掉 Content-Length,浏览器原生下载进度条依赖它
        "Content-Encoding": "identity",
    }
    return StreamingResponse(chunk_iter(), media_type="application/octet-stream", headers=headers)


@router.post("/upload")
async def upload_file(
    request: Request,
    path: str | None = Query(default=None),
    name: str = Query(...),
    overwrite: bool = Query(default=False),
):
    settings = get_files_settings()
    target_dir = resolve_under_root(path)
    if not target_dir.is_dir():
        raise HTTPException(status_code=404, detail="目标目录不存在")
    filename = _validate_filename(name)
    dest = target_dir / filename

    max_bytes = settings.max_upload_size_mb * 1024 * 1024
    # 按请求声明的体积预检；未知长度的流在实际写入时由磁盘错误兜底。
    free = (await asyncio.to_thread(shutil.disk_usage, str(settings.root))).free
    content_length = request.headers.get("content-length")
    expected_bytes = None
    if content_length is not None:
        try:
            expected_bytes = int(content_length)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail="无效的 Content-Length") from exc
        if expected_bytes < 0:
            raise HTTPException(status_code=400, detail="无效的 Content-Length")
        if expected_bytes > max_bytes:
            raise HTTPException(status_code=413, detail="文件超过大小限制")
    if free < (expected_bytes if expected_bytes is not None else 1):
        raise HTTPException(status_code=507, detail="磁盘空间不足")

    if dest.exists() and not overwrite:
        raise HTTPException(status_code=409, detail="file_exists")

    # 并发槽与下载对齐:防止并发大文件上传占满磁盘与带宽
    async with _upload_slots():
        # .part 名带 uuid:同名并发上传不能共用临时文件,否则写入交错、
        # replace 发布出损坏文件(overwrite 场景 409 检查帮不上忙)
        tmp = dest.with_name(f"{dest.name}.{uuid4().hex}.part")
        written = 0
        try:
            with open(tmp, "wb") as f:
                async for chunk in request.stream():
                    written += len(chunk)
                    if written > max_bytes:
                        raise HTTPException(status_code=413, detail="文件超过大小限制")
                    await asyncio.to_thread(f.write, chunk)
                    delay = _pacing_delay(len(chunk), settings.upload_rate_limit_mb)
                    if delay > 0:
                        await asyncio.sleep(delay)
            if overwrite:
                tmp.replace(dest)
            else:
                try:
                    # 硬链接以原子方式占用目标名，消除预检查与发布之间的覆盖竞态。
                    os.link(tmp, dest)
                except FileExistsError as exc:
                    raise HTTPException(status_code=409, detail="file_exists") from exc
                tmp.unlink()
        except BaseException as exc:
            # 覆盖读流中断与文件发布失败都要清掉残留 .part
            tmp.unlink(missing_ok=True)
            if isinstance(exc, OSError) and exc.errno in (errno.ENOSPC, errno.EDQUOT):
                raise HTTPException(status_code=507, detail="磁盘空间不足") from exc
            raise
    rel_path = dest.relative_to(settings.root.resolve()).as_posix()
    return {"name": filename, "size": written, "rel_path": rel_path}


@router.delete("")
async def delete_file(path: str = Query(...)):
    settings = get_files_settings()
    target = resolve_under_root(path)
    if target == settings.root.resolve():
        raise HTTPException(status_code=400, detail="不能删除根目录")
    if not target.exists():
        raise HTTPException(status_code=404, detail="路径不存在")
    if target.is_dir():
        if any(target.iterdir()):
            raise HTTPException(status_code=400, detail="目录非空,无法删除")
        await asyncio.to_thread(target.rmdir)
    else:
        await asyncio.to_thread(target.unlink)
    return {"deleted": path}
