# tests/files/test_files_api.py
"""files_api 路径安全与列表接口测试。"""
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


def test_list_empty_root(client):
    resp = client.get("/files/list")
    assert resp.status_code == 200
    assert resp.json() == {"path": "", "entries": []}


def test_list_dirs_first_and_sorted(client):
    root_path = _root_of(client)
    (root_path / "b_dir").mkdir()
    (root_path / "a_file.log").write_text("x" * 10)
    (root_path / "A_DIR2").mkdir()
    resp = client.get("/files/list")
    names = [e["name"] for e in resp.json()["entries"]]
    assert names[0] == "A_DIR2" and names[1] == "b_dir" and "a_file.log" in names
    entry = next(e for e in resp.json()["entries"] if e["name"] == "a_file.log")
    assert entry["is_dir"] is False and entry["size"] == 10 and entry["mtime"] > 0


def test_list_subdir(client):
    root_path = _root_of(client)
    (root_path / "sub").mkdir()
    (root_path / "sub" / "inner.log").write_text("hi")
    resp = client.get("/files/list", params={"path": "sub"})
    assert resp.status_code == 200
    assert resp.json()["entries"][0]["name"] == "inner.log"


def test_list_traversal_rejected(client):
    for bad in ["../x", "a/../../x", "C:/Windows", "a\\..\\..\\x"]:
        resp = client.get("/files/list", params={"path": bad})
        assert resp.status_code == 400, bad


def test_list_missing(client):
    resp = client.get("/files/list", params={"path": "nope"})
    assert resp.status_code == 404


def test_list_file_as_path(client):
    root_path = _root_of(client)
    (root_path / "f.log").write_text("x")
    resp = client.get("/files/list", params={"path": "f.log"})
    assert resp.status_code == 400
