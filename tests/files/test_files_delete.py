# tests/files/test_files_delete.py
"""删除端点测试。"""

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


def test_delete_file(client):
    f = _root_of(client) / "x.log"
    f.write_text("x")
    resp = client.delete("/files", params={"path": "x.log"})
    assert resp.status_code == 200
    assert resp.json() == {"deleted": "x.log"}
    assert not f.exists()


def test_delete_empty_dir_ok_nonempty_rejected(client):
    d = _root_of(client) / "d"
    d.mkdir()
    assert client.delete("/files", params={"path": "d"}).status_code == 200
    d.mkdir()
    (d / "inner").write_text("x")
    resp = client.delete("/files", params={"path": "d"})
    assert resp.status_code == 400
    assert d.exists()


def test_delete_root_rejected(client):
    resp = client.delete("/files", params={"path": ""})
    assert resp.status_code == 400


def test_delete_missing(client):
    resp = client.delete("/files", params={"path": "nope"})
    assert resp.status_code == 404
