# tests/files/test_files_upload.py
"""上传端点测试:成功、覆盖、409、413、非法文件名。"""

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from worker.config import WorkerConfig
from worker.files_api import router, set_files_config


@pytest.fixture()
def client(tmp_path):
    set_files_config(WorkerConfig(files_root=str(tmp_path)))
    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


def _root_of(client):
    from worker.files_api import get_files_settings

    return get_files_settings().root


def test_upload_ok(client):
    resp = client.post("/files/upload", params={"name": "a.log"}, content=b"log-content")
    assert resp.status_code == 200
    body = resp.json()
    assert body["name"] == "a.log" and body["size"] == len(b"log-content")
    assert (_root_of(client) / "a.log").read_bytes() == b"log-content"
    assert not (_root_of(client) / "a.log.part").exists()


def test_upload_into_subdir(client):
    (_root_of(client) / "d").mkdir()
    resp = client.post("/files/upload", params={"path": "d", "name": "n.bin"}, content=b"12")
    assert resp.status_code == 200
    assert (_root_of(client) / "d" / "n.bin").read_bytes() == b"12"


def test_upload_exists_409_then_overwrite(client):
    (_root_of(client) / "dup.txt").write_bytes(b"old")
    r1 = client.post("/files/upload", params={"name": "dup.txt"}, content=b"new")
    assert r1.status_code == 409
    assert r1.json()["detail"] == "file_exists"
    assert (_root_of(client) / "dup.txt").read_bytes() == b"old"
    r2 = client.post("/files/upload", params={"name": "dup.txt", "overwrite": "true"}, content=b"new")
    assert r2.status_code == 200
    assert (_root_of(client) / "dup.txt").read_bytes() == b"new"


def test_upload_too_large_413(client):
    set_files_config(WorkerConfig(files_root=str(_root_of(client)), files_max_upload_size_mb=1))
    resp = client.post("/files/upload", params={"name": "big.bin"}, content=b"z" * (1024 * 1024 + 1))
    assert resp.status_code == 413
    assert not (_root_of(client) / "big.bin").exists()
    assert not list(_root_of(client).glob("big.bin*.part"))


def test_upload_bad_name(client):
    for bad in ["../x", "a/b", "a\\b", "..", ".", "", "a\0b"]:
        resp = client.post("/files/upload", params={"name": bad}, content=b"x")
        assert resp.status_code == 400, bad


def test_upload_target_dir_missing(client):
    resp = client.post("/files/upload", params={"path": "nothere", "name": "x"}, content=b"x")
    assert resp.status_code == 404


def test_upload_disk_full_507(client, monkeypatch):
    import shutil as _shutil

    usage = _shutil.disk_usage(_root_of(client))._replace(free=0)
    monkeypatch.setattr(
        "worker.files_api.shutil.disk_usage", lambda _path: usage, raising=True
    )
    resp = client.post("/files/upload", params={"name": "x.bin"}, content=b"x")
    assert resp.status_code == 507
    assert not list(_root_of(client).glob("x.bin*"))


def test_concurrent_same_name_overwrite_not_corrupted(tmp_path):
    """同名 overwrite 并发上传:各自写独立 .part,最终文件必须是某一次
    上传的完整内容,而不是两次写入的交错;结束后无 .part 残留。"""
    import asyncio

    import httpx

    set_files_config(WorkerConfig(files_root=str(tmp_path)))
    app = FastAPI()
    app.include_router(router)

    payload_a = b"a" * (256 * 1024)
    payload_b = b"b" * (256 * 1024)

    async def run():
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:

            async def upload(content: bytes):
                r = await c.post(
                    "/files/upload",
                    params={"name": "same.bin", "overwrite": "true"},
                    content=content,
                )
                assert r.status_code == 200

            await asyncio.gather(upload(payload_a), upload(payload_b))

    asyncio.run(run())

    final = (tmp_path / "same.bin").read_bytes()
    assert final in (payload_a, payload_b)
    assert list(tmp_path.glob("*.part")) == []
