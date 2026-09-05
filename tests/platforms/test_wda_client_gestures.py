"""WDAClient pinch/window_size 补齐测试。"""

from worker.platforms.wda_client import WDAClient


class _FakeResponse:
    def __init__(self, status_code=200, payload=None, text=""):
        self.status_code = status_code
        self._payload = payload or {}
        self.text = text

    def json(self):
        return self._payload


def _make_client(monkeypatch, posts=None, gets=None):
    client = WDAClient("http://127.0.0.1:8100")
    monkeypatch.setattr(client, "_get_session", lambda: "sid-1")

    def fake_post(url, json=None):
        if posts is not None:
            posts.append((url, json))
        return _FakeResponse(200)

    def fake_get(url):
        if gets is not None:
            gets.append(url)
            if url.endswith("/window/size"):
                return _FakeResponse(200, {"value": {"width": 390, "height": 844}})
        return _FakeResponse(200)

    monkeypatch.setattr(client.session, "post", fake_post)
    monkeypatch.setattr(client.session, "get", fake_get)
    return client


def test_pinch_posts_scale_and_velocity(monkeypatch):
    posts = []
    client = _make_client(monkeypatch, posts=posts)

    assert client.pinch(scale=0.5, duration=1.0)
    url, body = posts[0]
    assert url.endswith("/session/sid-1/wda/pinch")
    assert body["scale"] == 0.5
    # scale < 1 时 velocity 必须为负（XCUITest 契约）
    assert body["velocity"] < 0


def test_pinch_out_velocity_positive(monkeypatch):
    posts = []
    client = _make_client(monkeypatch, posts=posts)

    assert client.pinch(scale=2.0, duration=1.0)
    _, body = posts[0]
    assert body["scale"] == 2.0
    assert body["velocity"] > 0


def test_pinch_invalid_scale_is_clamped(monkeypatch):
    posts = []
    client = _make_client(monkeypatch, posts=posts)

    assert client.pinch(scale=-1, duration=1.0)
    _, body = posts[0]
    assert body["scale"] == 1.0


def test_pinch_http_failure_returns_false(monkeypatch):
    client = WDAClient("http://127.0.0.1:8100")
    monkeypatch.setattr(client, "_get_session", lambda: "sid-1")
    monkeypatch.setattr(
        client.session, "post", lambda url, json=None: _FakeResponse(404, text="not found")
    )
    assert not client.pinch(scale=1.5)


def test_window_size_returns_tuple(monkeypatch):
    gets = []
    client = _make_client(monkeypatch, gets=gets)
    assert client.window_size() == (390, 844)
    assert gets[0].endswith("/session/sid-1/window/size")
