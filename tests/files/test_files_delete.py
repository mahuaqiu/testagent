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


def test_delete_empty_dir_ok_nonempty_recursive(client):
    """空目录直接删;非空目录连同全部内容递归删除(平台端有确认弹窗兜底)。"""
    d = _root_of(client) / "d"
    d.mkdir()
    assert client.delete("/files", params={"path": "d"}).status_code == 200
    d.mkdir()
    (d / "inner").write_text("x")
    (d / "sub").mkdir()
    (d / "sub" / "deep.log").write_text("y")
    resp = client.delete("/files", params={"path": "d"})
    assert resp.status_code == 200
    assert resp.json() == {"deleted": "d"}
    assert not d.exists()


def test_delete_nonempty_dir_locked_409(client, monkeypatch):
    """递归删除中目录被占用(Windows rmtree 拒绝访问)时返回 409。"""
    import shutil as _shutil

    d = _root_of(client) / "busy_dir"
    (d / "sub").mkdir(parents=True)
    (d / "sub" / "x.log").write_text("x")

    real_rmtree = _shutil.rmtree

    def _raise(path, *args, **kwargs):
        from pathlib import Path

        if Path(path).name == "busy_dir":
            raise PermissionError(5, "拒绝访问")
        return real_rmtree(path, *args, **kwargs)

    monkeypatch.setattr("worker.files_api.shutil.rmtree", _raise)
    resp = client.delete("/files", params={"path": "busy_dir"})
    assert resp.status_code == 409
    assert "占用" in resp.json()["detail"]
    assert d.exists()


def test_delete_root_rejected(client):
    resp = client.delete("/files", params={"path": ""})
    assert resp.status_code == 400


def test_delete_missing(client):
    resp = client.delete("/files", params={"path": "nope"})
    assert resp.status_code == 404


def test_delete_locked_file_409(client, monkeypatch):
    """文件正被下载/占用(Windows unlink 拒绝访问)时返回 409 而非 500。"""
    import pathlib

    f = _root_of(client) / "busy.log"
    f.write_text("x")

    def _raise(self, missing_ok=False):
        raise PermissionError(5, "拒绝访问")

    monkeypatch.setattr(pathlib.Path, "unlink", _raise)
    resp = client.delete("/files", params={"path": "busy.log"})
    assert resp.status_code == 409
    assert "占用" in resp.json()["detail"]
    assert f.exists()


def test_delete_traversal_rejected(client, tmp_path):
    """根目录之外(路径穿越/绝对路径)一律 400:删除只作用于根目录内。"""
    outside = tmp_path / "outside.txt"
    outside.write_text("x")
    for bad in ["../outside.txt", "a/../../outside.txt", "C:/Windows/notepad.exe"]:
        resp = client.delete("/files", params={"path": bad})
        assert resp.status_code == 400, bad
    assert outside.exists()
