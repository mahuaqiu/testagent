"""产物文件管理 API。

平台经 /files/* 浏览、下载、上传、删除 Worker 上收集目录内的文件。
浏览范围锁定在配置的根目录内,拒绝一切路径穿越;限速通过分块 + 异步 sleep
实现,不阻塞事件循环,不影响测试任务执行。
"""

import asyncio
import os
from dataclasses import dataclass
from pathlib import Path
from typing import AsyncIterator
from urllib.parse import quote

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import StreamingResponse

from common.packaging import get_base_dir
from worker.config import WorkerConfig

router = APIRouter(prefix="/files", tags=["files"])

DEFAULT_ROOT_REL = os.path.join("data", "collected")
CHUNK_SIZE = 256 * 1024


@dataclass
class FilesSettings:
    """文件管理运行时设置(由 worker.yaml 的 files 段推导)。"""

    root: Path
    download_rate_limit_mb: float
    upload_rate_limit_mb: float
    max_concurrent_downloads: int
    max_upload_size_mb: int


_settings: FilesSettings | None = None
_download_semaphore: asyncio.Semaphore | None = None


def set_files_config(config: WorkerConfig) -> None:
    """由 server 启动流程(worker.set_worker)注入配置;测试亦由此注入临时根目录。"""
    global _settings, _download_semaphore
    root = config.files_root or os.path.join(get_base_dir(), DEFAULT_ROOT_REL)
    _settings = FilesSettings(
        root=Path(root),
        download_rate_limit_mb=config.files_download_rate_limit_mb,
        upload_rate_limit_mb=config.files_upload_rate_limit_mb,
        max_concurrent_downloads=max(1, config.files_max_concurrent_downloads),
        max_upload_size_mb=config.files_max_upload_size_mb,
    )
    _download_semaphore = asyncio.Semaphore(_settings.max_concurrent_downloads)


def get_files_settings() -> FilesSettings:
    if _settings is None:
        from worker.config import load_config

        set_files_config(load_config())
    assert _settings is not None
    return _settings


def _download_slots() -> asyncio.Semaphore:
    get_files_settings()
    assert _download_semaphore is not None
    return _download_semaphore


def _pacing_delay(size_bytes: int, rate_limit_mb: float) -> float:
    """该块应耗时多少秒(限速 MB/s;0 或负数 = 不限速)。"""
    if rate_limit_mb <= 0:
        return 0.0
    return size_bytes / (rate_limit_mb * 1024 * 1024)


def _content_disposition(name: str) -> str:
    return f"attachment; filename*=UTF-8''{quote(name)}"


def _validate_filename(name: str) -> str:
    name = (name or "").strip()
    if not name or "/" in name or "\\" in name or ".." in name or ":" in name:
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
    ):
        raise HTTPException(status_code=400, detail="非法路径")
    root_resolved = settings.root.resolve()
    target = (root_resolved / rel_clean).resolve()
    if target != root_resolved and root_resolved not in target.parents:
        raise HTTPException(status_code=400, detail="非法路径")
    return target


@router.get("/list")
async def list_files(path: str | None = Query(default=None)) -> dict:
    target = resolve_under_root(path)
    if not target.exists():
        raise HTTPException(status_code=404, detail="路径不存在")
    if not target.is_dir():
        raise HTTPException(status_code=400, detail="不是目录")
    entries = []
    for item in sorted(target.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower())):
        st = item.stat()
        entries.append(
            {
                "name": item.name,
                "is_dir": item.is_dir(),
                "size": st.st_size if item.is_file() else 0,
                "mtime": int(st.st_mtime),
            }
        )
    return {"path": (path or "").replace("\\", "/").strip("/"), "entries": entries}


@router.get("/download")
async def download_file(path: str = Query(...)):
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
    }
    return StreamingResponse(
        chunk_iter(), media_type="application/octet-stream", headers=headers
    )


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
    if dest.exists() and not overwrite:
        raise HTTPException(status_code=409, detail="file_exists")

    max_bytes = settings.max_upload_size_mb * 1024 * 1024
    tmp = dest.with_name(dest.name + ".part")
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
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise
    tmp.replace(dest)
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
