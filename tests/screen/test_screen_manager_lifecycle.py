"""ScreenManager 缓存锁、JPEG 兜底与 MJPEG 端口修复测试。"""

import threading

import pytest

from worker.screen.frame_source import MJPEGFrameSource
from worker.screen.manager import (
    close_all_screen_managers,
    close_screen_manager,
    get_existing_screen_manager,
    get_screen_manager,
)


class StubFrameSource:
    """最小 FrameSource 替身（不走抽象基类避免无关约束）。"""

    def __init__(self):
        self.started = False
        self.stopped = False

    @property
    def prefers_latest_frame(self):
        return False

    def get_frame(self):
        return b"frame"

    def get_frame_bgra(self):
        raise NotImplementedError("StubFrameSource does not support BGRA frame")

    def get_screen_size(self):
        return (4, 4)

    def start(self):
        self.started = True

    def stop(self):
        self.stopped = True

    def get_blank_frame(self):
        return b"blank"


@pytest.fixture(autouse=True)
def _clean_manager_cache():
    close_all_screen_managers()
    yield
    close_all_screen_managers()


def test_get_screen_manager_is_thread_safe_same_instance():
    """并发 get_screen_manager 同一 key 只能创建一个实例。"""
    barrier = threading.Barrier(4)
    results = []
    lock = threading.Lock()

    def worker():
        barrier.wait(timeout=2)
        mgr = get_screen_manager("ios/dev-1", StubFrameSource())
        with lock:
            results.append(mgr)

    threads = [threading.Thread(target=worker) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=5)

    assert len(results) == 4
    assert all(mgr is results[0] for mgr in results)
    assert get_existing_screen_manager("ios/dev-1") is results[0]


def test_close_screen_manager_removes_and_stops():
    source = StubFrameSource()
    mgr = get_screen_manager("android/dev-2", source)
    close_screen_manager("android/dev-2")

    assert source.stopped
    assert get_existing_screen_manager("android/dev-2") is None

    # 关闭后重新获取会创建新实例
    fresh = get_screen_manager("android/dev-2", StubFrameSource())
    assert fresh is not mgr


def test_get_frame_jpeg_falls_back_to_frame_queue_without_bgra():
    """不支持 BGRA 的帧源（iOS/Android JPEG 流）不应抛 NotImplementedError。"""
    mgr = get_screen_manager("web/dev-3", StubFrameSource())
    with pytest.raises(NotImplementedError):
        mgr.get_frame_bgra()  # 保持原语义：BGRA 不支持

    # 直接打桩让 get_frame_bgra 立即抛错，避免测试空等队列
    import unittest.mock as mock

    with mock.patch.object(mgr, "get_frame_bgra", side_effect=NotImplementedError):
        assert mgr.get_frame_jpeg() == b"frame"


class _FakeWDAClient:
    base_url = "http://127.0.0.1:8100"


def test_mjpeg_frame_source_uses_injected_port():
    """MJPEG 帧源必须使用传入的设备端口，不能硬编码 9100。"""
    default_source = MJPEGFrameSource("dev-1", _FakeWDAClient())
    assert default_source.mjpeg_port == 9100
    assert default_source._mjpeg_url() == "http://127.0.0.1:9100"

    second_device = MJPEGFrameSource("dev-2", _FakeWDAClient(), mjpeg_port=9101)
    assert second_device._mjpeg_url() == "http://127.0.0.1:9101"


def test_mjpeg_frame_source_serializes_concurrent_stream_reads(monkeypatch):
    import requests
    import time

    frame_one = b"\xff\xd8frame-one\xff\xd9"
    frame_two = b"\xff\xd8frame-two\xff\xd9"

    def part(frame):
        return b"--BoundaryString\r\nContent-Length: " + str(len(frame)).encode() + b"\r\n\r\n" + frame + b"\r\n"

    class SlowIterator:
        def __init__(self, chunks):
            self.chunks = chunks
            self.index = 0
            self.active = 0
            self.max_active = 0
            self.lock = threading.Lock()

        def __iter__(self):
            return self

        def __next__(self):
            with self.lock:
                self.active += 1
                self.max_active = max(self.max_active, self.active)
            time.sleep(0.02)
            try:
                if self.index >= len(self.chunks):
                    raise StopIteration
                chunk = self.chunks[self.index]
                self.index += 1
                return chunk
            finally:
                with self.lock:
                    self.active -= 1

    iterator = SlowIterator([part(frame_one)[:12], part(frame_one)[12:], part(frame_two)[:10], part(frame_two)[10:]])

    class Response:
        def iter_content(self, chunk_size):
            return iterator

        def close(self):
            pass

    response = Response()
    get_calls = []
    monkeypatch.setattr(requests, "get", lambda *args, **kwargs: get_calls.append(args[0]) or response)
    source = MJPEGFrameSource("dev-1", _FakeWDAClient())
    results = []
    errors = []

    def read_frame():
        try:
            results.append(source.get_frame())
        except Exception as exc:
            errors.append(exc)

    readers = [threading.Thread(target=read_frame) for _ in range(2)]
    for reader in readers:
        reader.start()
    for reader in readers:
        reader.join(timeout=2)

    assert not errors
    assert set(results) == {frame_one, frame_two}
    assert iterator.max_active == 1
    assert len(get_calls) == 1
    source.stop()


def test_stop_and_start_capture_cannot_overlap_capture_threads():
    import time

    class BlockingFrameSource(StubFrameSource):
        def __init__(self):
            super().__init__()
            self.first_capture_started = threading.Event()
            self.release_first_capture = threading.Event()
            self.second_capture_started = threading.Event()
            self.capture_count = 0

        def get_frame(self):
            self.capture_count += 1
            if self.capture_count == 1:
                self.first_capture_started.set()
                self.release_first_capture.wait(timeout=2)
            else:
                self.second_capture_started.set()
            return b"frame"

    source = BlockingFrameSource()
    manager = get_screen_manager("ios/stop-start", source)
    assert source.first_capture_started.wait(timeout=1)

    stop_thread = threading.Thread(target=manager.stop)
    stop_thread.start()
    deadline = time.monotonic() + 1
    while not manager._stopping and time.monotonic() < deadline:
        time.sleep(0.005)

    start_thread = threading.Thread(target=manager.start_capture)
    start_thread.start()
    time.sleep(0.05)
    assert not source.second_capture_started.is_set()

    source.release_first_capture.set()
    stop_thread.join(timeout=2)
    start_thread.join(timeout=2)
    assert source.second_capture_started.wait(timeout=1)
    manager.stop()


def test_mjpeg_proxy_uses_injected_port(monkeypatch):
    captured = {}

    class FakeProxy:
        def __init__(self, host, port=9100):
            captured["host"] = host
            captured["port"] = port

        def start(self):
            pass

    import worker.screen.mjpeg_proxy as mjpeg_proxy_module

    monkeypatch.setattr(mjpeg_proxy_module, "MJPEGProxy", FakeProxy)
    source = MJPEGFrameSource("dev-2", _FakeWDAClient(), mjpeg_port=9101)
    source.start_mjpeg_proxy()
    assert captured == {"host": "127.0.0.1", "port": 9101}


def test_monitors_cache_invalidation():
    import worker.screen.monitor_utils as mu

    mu._monitors_cache = [{"left": 0, "top": 0, "width": 100, "height": 100}]
    mu.invalidate_monitors_cache()
    assert mu._monitors_cache is None
