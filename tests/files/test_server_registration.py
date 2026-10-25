# tests/files/test_server_registration.py
"""server.py 挂载 files 路由的冒烟检查。"""

import pytest


def test_server_includes_files_router():
    pytest.importorskip("worker.server")
    import worker.server as server

    paths = {getattr(r, "path", "") for r in server.app.routes}
    assert any(p.startswith("/files") for p in paths)


def test_server_allows_cross_origin_upload_preflight():
    """前端直连 /files/upload 上传,浏览器会先发 CORS 预检,必须放行。"""
    pytest.importorskip("worker.server")
    from fastapi.testclient import TestClient

    import worker.server as server

    client = TestClient(server.app)
    resp = client.options(
        "/files/upload",
        headers={
            "Origin": "http://platform.example",
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "content-type",
        },
    )
    assert resp.status_code in (200, 204)
    assert resp.headers["access-control-allow-origin"] == "*"
