# tests/files/test_files_download.py
"""下载端点测试:基本下载、限速、错误分支。"""
import time

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


def test_download_roundtrip_and_headers(client):
    content = b"hello log" * 100
    (_root_of(client) / "a.log").write_bytes(content)
    resp = client.get("/files/download", params={"path": "a.log"})
    assert resp.status_code == 200
    assert resp.content == content
    assert resp.headers["content-length"] == str(len(content))
    assert "a.log" in resp.headers["content-disposition"]


def test_download_subdir_path(client):
    root = _root_of(client)
    (root / "d").mkdir()
    (root / "d" / "x.bin").write_bytes(b"\x00\x01")
    resp = client.get("/files/download", params={"path": "d/x.bin"})
    assert resp.status_code == 200
    assert resp.content == b"\x00\x01"


def test_download_errors(client):
    assert client.get("/files/download", params={"path": "nope"}).status_code == 404
    root = _root_of(client)
    (root / "dir").mkdir()
    assert client.get("/files/download", params={"path": "dir"}).status_code == 400
    assert client.get("/files/download", params={"path": "../x"}).status_code == 400


def test_download_rate_limited(client):
    set_files_config(
        WorkerConfig(files_root=str(_root_of(client)), files_download_rate_limit_mb=0.1)
    )
    content = b"x" * (100 * 1024)  # 100KB,限速 0.1MB/s → 约 1s
    (_root_of(client) / "slow.log").write_bytes(content)
    start = time.monotonic()
    resp = client.get("/files/download", params={"path": "slow.log"})
    elapsed = time.monotonic() - start
    assert resp.status_code == 200 and resp.content == content
    assert 0.8 <= elapsed <= 8, f"elapsed={elapsed}"


def test_download_unlimited_fast(client):
    set_files_config(
        WorkerConfig(files_root=str(_root_of(client)), files_download_rate_limit_mb=0)
    )
    content = b"x" * (100 * 1024)
    (_root_of(client) / "fast.log").write_bytes(content)
    start = time.monotonic()
    resp = client.get("/files/download", params={"path": "fast.log"})
    elapsed = time.monotonic() - start
    assert resp.status_code == 200 and resp.content == content
    assert elapsed < 0.8, f"elapsed={elapsed}"
