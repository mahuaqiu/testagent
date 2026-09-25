# tests/files/test_server_registration.py
"""server.py 挂载 files 路由的冒烟检查。"""

import pytest


def test_server_includes_files_router():
    pytest.importorskip("worker.server")
    import worker.server as server

    paths = {getattr(r, "path", "") for r in server.app.routes}
    assert any(p.startswith("/files") for p in paths)
