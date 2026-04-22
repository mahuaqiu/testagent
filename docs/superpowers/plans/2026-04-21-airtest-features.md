# Airtest 借鉴功能实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 实现从 Airtest 借鉴的 pinch 双指缩放手势、录屏功能、WebSocket 屏幕推流三大功能。

**Architecture:** 新增 `worker/screen/` 模块统一管理帧获取、录屏、推流。FrameSource 抽象层实现各平台帧获取逻辑，ScreenManager 统一管理帧队列和并发控制。ScreenRecorder 使用 FFmpeg image2pipe 输入实现高效编码。WebSocketStreamer 提供 10fps 实时推流。pinch 手势通过 Android uiautomator2 和 iOS WDA 多点触控 API 实现。

**Tech Stack:** Python threading/asyncio、FFmpeg、mss、uiautomator2、WDA、FastAPI WebSocket

---

## 文件结构

### 新增文件

```
worker/screen/
├── __init__.py            # 模块入口，导出 ScreenManager 工厂函数
├── frame_source.py        # FrameSource 抽象基类 + 各平台实现
├── manager.py             # ScreenManager 统一管理器 + 全局缓存
├── recorder.py            # ScreenRecorder FFmpeg 录屏器
└── streamer.py            # WebSocketStreamer 推流器

worker/actions/
├── gesture.py             # pinch 动作处理器
└── recording.py           # start_recording/stop_recording 动作处理器

tests/unit/screen/
├── test_frame_source.py   # FrameSource 单元测试
├── test_manager.py        # ScreenManager 单元测试
├── test_recorder.py       # ScreenRecorder 单元测试
└── test_streamer.py       # WebSocketStreamer 单元测试
```

### 修改文件

```
worker/platforms/base.py           # 新增 pinch() 抽象方法
worker/platforms/android.py        # 实现 pinch + 录屏帧源集成
worker/platforms/ios.py            # 实现 pinch + 录屏帧源集成
worker/platforms/windows.py        # 录屏帧源集成
worker/device_monitor.py          # 设备离线时关闭 ScreenManager
worker/server.py                   # 新增 WebSocket 路由
worker/actions/__init__.py         # 注册新动作处理器
worker/config.py                   # 新增录屏/推流配置项
config/worker.yaml                 # 新增配置示例
pyproject.toml                     # 新增依赖项说明（已有 mss）
```

---

## Task 1: FrameSource 抽象层

**Files:**
- Create: `worker/screen/__init__.py`
- Create: `worker/screen/frame_source.py`
- Create: `tests/unit/screen/test_frame_source.py`

- [ ] **Step 1: 写失败测试 - FrameSource 基类**

```python
# tests/unit/screen/test_frame_source.py
"""FrameSource 单元测试。"""

import pytest
from worker.screen.frame_source import (
    FrameSource,
    MinicapFrameSource,
    MJPEGFrameSource,
    WindowsFrameSource,
)


class TestFrameSourceAbstract:
    """测试 FrameSource 抽象基类。"""

    def test_cannot_instantiate_abstract_class(self):
        """不能直接实例化抽象基类。"""
        with pytest.raises(TypeError):
            FrameSource()

    def test_subclass_must_implement_all_methods(self):
        """子类必须实现所有抽象方法。"""
        class IncompleteFrameSource(FrameSource):
            def get_frame(self) -> bytes:
                return b""

        with pytest.raises(TypeError):
            IncompleteFrameSource()
```

- [ ] **Step 2: 运行测试验证失败**

Run: `pytest tests/unit/screen/test_frame_source.py -v`
Expected: FAIL with "Can't instantiate abstract class"

- [ ] **Step 3: 实现 FrameSource 抽象基类**

```python
# worker/screen/__init__.py
"""截图/录屏/推流统一模块。"""

from worker.screen.manager import (
    ScreenManager,
    get_screen_manager,
    close_screen_manager,
    close_all_screen_managers,
)

__all__ = [
    "ScreenManager",
    "get_screen_manager",
    "close_screen_manager",
    "close_all_screen_managers",
]
```

```python
# worker/screen/frame_source.py
"""FrameSource 帧获取抽象层。"""

import io
import logging
import time
from abc import ABC, abstractmethod
from typing import Optional

import numpy
from PIL import Image

logger = logging.getLogger(__name__)


class FrameSource(ABC):
    """帧获取抽象基类。"""

    MAX_RECONNECT_ATTEMPTS = 3
    RECONNECT_INTERVAL = 1  # 秒

    @abstractmethod
    def get_frame(self) -> bytes:
        """获取单帧（JPEG 格式）。"""
        pass

    @abstractmethod
    def get_screen_size(self) -> tuple[int, int]:
        """获取屏幕尺寸 (width, height)。"""
        pass

    @abstractmethod
    def start(self) -> None:
        """启动帧源（如建立 socket 连接）。"""
        pass

    @abstractmethod
    def stop(self) -> None:
        """停止帧源。"""
        pass

    @abstractmethod
    def get_blank_frame(self) -> bytes:
        """获取空白帧（连接失败时返回）。"""
        pass

    def get_frame_with_reconnect(self) -> bytes:
        """获取帧（带自动重连）。"""
        for attempt in range(self.MAX_RECONNECT_ATTEMPTS + 1):
            try:
                return self.get_frame()
            except ConnectionError:
                if attempt < self.MAX_RECONNECT_ATTEMPTS:
                    logger.warning(f"Frame source disconnected, reconnecting (attempt {attempt + 1})")
                    self.stop()
                    time.sleep(self.RECONNECT_INTERVAL)
                    self.start()
                else:
                    logger.error("Frame source reconnect failed, returning blank frame")
                    return self.get_blank_frame()

    def _img_to_jpeg(self, img_array: numpy.ndarray, quality: int = 80) -> bytes:
        """将 numpy 数组转换为 JPEG。"""
        img = Image.fromarray(img_array)
        buffer = io.BytesIO()
        img.save(buffer, format="JPEG", quality=quality)
        return buffer.getvalue()


class MinicapFrameSource(FrameSource):
    """Android: minicap socket 流（占位实现）。"""

    def __init__(self, device_id: str, minicap_instance):
        self.device_id = device_id
        self.minicap = minicap_instance
        self._screen_size: Optional[tuple[int, int]] = None

    def get_frame(self) -> bytes:
        """从 minicap 获取帧（JPEG 格式）。"""
        # 实际实现使用 minicap.get_frame()
        raise ConnectionError("Not implemented - placeholder")

    def get_screen_size(self) -> tuple[int, int]:
        """获取屏幕尺寸。"""
        if self._screen_size:
            return self._screen_size
        # 从 minicap 获取
        # self._screen_size = self.minicap.get_screen_size()
        return (1080, 1920)  # 默认值

    def start(self) -> None:
        """启动 minicap 流。"""
        pass  # minicap 由平台管理器启动

    def stop(self) -> None:
        """停止 minicap 流。"""
        pass

    def get_blank_frame(self) -> bytes:
        """返回黑屏 JPEG 帧。"""
        width, height = self.get_screen_size()
        img = numpy.zeros((height, width, 3), dtype=numpy.uint8)
        return self._img_to_jpeg(img)


class MJPEGFrameSource(FrameSource):
    """iOS: WDA 9100 MJPEG 流（占位实现）。"""

    def __init__(self, device_id: str, wda_client):
        self.device_id = device_id
        self.wda_client = wda_client
        self._screen_size: Optional[tuple[int, int]] = None

    def get_frame(self) -> bytes:
        """从 WDA MJPEG 流获取帧。"""
        raise ConnectionError("Not implemented - placeholder")

    def get_screen_size(self) -> tuple[int, int]:
        """获取屏幕尺寸。"""
        if self._screen_size:
            return self._screen_size
        return (375, 667)  # iPhone 8 默认逻辑分辨率

    def start(self) -> None:
        """启动 MJPEG 流。"""
        pass

    def stop(self) -> None:
        """停止 MJPEG 流。"""
        pass

    def get_blank_frame(self) -> bytes:
        """返回黑屏 JPEG 帧。"""
        width, height = self.get_screen_size()
        img = numpy.zeros((height, width, 3), dtype=numpy.uint8)
        return self._img_to_jpeg(img)


class WindowsFrameSource(FrameSource):
    """Windows: mss 截屏。"""

    def __init__(self, fps: int = 10, monitor: int = 1):
        self.fps = fps
        self.monitor = monitor
        self._screen_size: Optional[tuple[int, int]] = None

    def get_frame(self) -> bytes:
        """使用 mss 截屏，转为 JPEG。"""
        import mss

        with mss.mss() as sct:
            monitor_config = sct.monitors[self.monitor]
            screenshot = sct.grab(monitor_config)
            img = Image.frombytes("RGB", screenshot.size, screenshot.rgb)
            buffer = io.BytesIO()
            img.save(buffer, format="JPEG", quality=80)
            return buffer.getvalue()

    def get_screen_size(self) -> tuple[int, int]:
        """获取显示器尺寸。"""
        import mss

        with mss.mss() as sct:
            monitor_config = sct.monitors[self.monitor]
            return (monitor_config["width"], monitor_config["height"])

    def start(self) -> None:
        """mss 不需要启动。"""
        pass

    def stop(self) -> None:
        """mss 不需要停止。"""
        pass

    def get_blank_frame(self) -> bytes:
        """返回黑屏 JPEG 帧。"""
        width, height = self.get_screen_size()
        img = numpy.zeros((height, width, 3), dtype=numpy.uint8)
        return self._img_to_jpeg(img)
```

- [ ] **Step 4: 运行测试验证通过**

Run: `pytest tests/unit/screen/test_frame_source.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add worker/screen/__init__.py worker/screen/frame_source.py tests/unit/screen/test_frame_source.py
git commit -m "feat(screen): add FrameSource abstract base class and platform implementations"
```

---

## Task 2: FrameSource 平台实现测试

**Files:**
- Modify: `tests/unit/screen/test_frame_source.py`

- [ ] **Step 1: 写失败测试 - WindowsFrameSource**

```python
# tests/unit/screen/test_frame_source.py (追加)
class TestWindowsFrameSource:
    """测试 WindowsFrameSource。"""

    def test_get_screen_size_returns_tuple(self):
        """get_screen_size 返回尺寸元组。"""
        source = WindowsFrameSource()
        width, height = source.get_screen_size()
        assert isinstance(width, int)
        assert isinstance(height, int)
        assert width > 0
        assert height > 0

    def test_get_frame_returns_jpeg_bytes(self):
        """get_frame 返回 JPEG 字节数据。"""
        source = WindowsFrameSource()
        frame = source.get_frame()
        assert isinstance(frame, bytes)
        assert len(frame) > 0
        # JPEG 以 FFD8 开头，FFD9 结尾
        assert frame[:2] == b'\xff\xd8'
        assert frame[-2:] == b'\xff\xd9'

    def test_get_blank_frame_returns_jpeg(self):
        """get_blank_frame 返回有效 JPEG。"""
        source = WindowsFrameSource()
        frame = source.get_blank_frame()
        assert isinstance(frame, bytes)
        assert frame[:2] == b'\xff\xd8'
```

- [ ] **Step 2: 运行测试验证通过**

Run: `pytest tests/unit/screen/test_frame_source.py::TestWindowsFrameSource -v`
Expected: PASS (WindowsFrameSource 已实现)

- [ ] **Step 3: Commit**

```bash
git add tests/unit/screen/test_frame_source.py
git commit -m "test(screen): add WindowsFrameSource unit tests"
```

---

## Task 3: ScreenManager 统一管理器

**Files:**
- Create: `worker/screen/manager.py`
- Create: `tests/unit/screen/test_manager.py`

- [ ] **Step 1: 写失败测试 - ScreenManager 基础功能**

```python
# tests/unit/screen/test_manager.py
"""ScreenManager 单元测试。"""

import pytest
from unittest.mock import MagicMock, patch
from queue import Queue
from threading import Thread, Event
import time

from worker.screen.manager import ScreenManager
from worker.screen.frame_source import WindowsFrameSource


class TestScreenManager:
    """测试 ScreenManager。"""

    def test_init_creates_frame_queue(self):
        """初始化创建帧队列。"""
        source = WindowsFrameSource()
        manager = ScreenManager(source)
        assert manager._frame_queue is not None
        assert manager._frame_queue.maxsize == 30

    def test_get_frame_returns_frame(self):
        """get_frame 从队列获取帧。"""
        source = WindowsFrameSource()
        manager = ScreenManager(source)

        # 手动放入测试帧
        test_frame = b"test_frame_data"
        manager._frame_queue.put(test_frame)

        frame = manager.get_frame()
        assert frame == test_frame

    def test_get_frame_returns_blank_when_queue_empty(self):
        """队列空时返回空白帧。"""
        source = MagicMock()
        source.get_blank_frame.return_value = b"blank_frame"
        manager = ScreenManager(source)

        # 等待超时返回空白帧
        frame = manager.get_frame(timeout=0.1)
        assert frame == b"blank_frame"

    def test_start_recording_returns_false_when_already_recording(self):
        """已有录屏时 start_recording 返回 False。"""
        source = MagicMock()
        manager = ScreenManager(source)
        manager._is_recording = True

        result = manager.start_recording("test.mp4")
        assert result is False

    def test_stop_recording_returns_empty_when_not_recording(self):
        """无录屏时 stop_recording 返回空字符串。"""
        source = MagicMock()
        manager = ScreenManager(source)
        manager._is_recording = False

        result = manager.stop_recording()
        assert result == ""
```

- [ ] **Step 2: 运行测试验证失败**

Run: `pytest tests/unit/screen/test_manager.py -v`
Expected: FAIL with "module 'worker.screen' has no attribute 'manager'"

- [ ] **Step 3: 实现 ScreenManager**

```python
# worker/screen/manager.py
"""ScreenManager 统一管理器。"""

import logging
import threading
from queue import Queue, Empty
from typing import Optional

from worker.screen.frame_source import FrameSource

logger = logging.getLogger(__name__)

# 全局缓存
_screen_managers: dict[str, "ScreenManager"] = {}


def get_screen_manager(device_id: str, frame_source: FrameSource) -> "ScreenManager":
    """获取或创建 ScreenManager（按设备 ID 缓存）。"""
    if device_id not in _screen_managers:
        manager = ScreenManager(frame_source)
        manager.start_capture()
        _screen_managers[device_id] = manager
        logger.info(f"ScreenManager created for device: {device_id}")
    return _screen_managers[device_id]


def close_screen_manager(device_id: str) -> None:
    """关闭指定设备的 ScreenManager。"""
    if device_id in _screen_managers:
        manager = _screen_managers[device_id]
        manager.stop()
        del _screen_managers[device_id]
        logger.info(f"ScreenManager closed for device: {device_id}")


def close_all_screen_managers() -> None:
    """关闭所有 ScreenManager（Worker 停止时调用）。"""
    for device_id in list(_screen_managers.keys()):
        close_screen_manager(device_id)
    logger.info("All ScreenManagers closed")


class ScreenManager:
    """统一管理截图/录屏/推流。"""

    def __init__(self, frame_source: FrameSource):
        self._frame_source = frame_source
        self._frame_queue: Queue[bytes] = Queue(maxsize=30)
        self._capture_thread: Optional[threading.Thread] = None
        self._running: bool = False
        self._is_recording: bool = False
        self._recording_lock: threading.Lock = threading.Lock()
        self._recorder: Optional["ScreenRecorder"] = None
        self._streamer: Optional["WebSocketStreamer"] = None

    def start_capture(self) -> None:
        """启动后台截图线程。"""
        if self._running:
            return

        self._running = True
        self._capture_thread = threading.Thread(target=self._capture_loop, daemon=True)
        self._capture_thread.start()
        logger.info("Frame capture thread started")

    def stop(self) -> None:
        """停止所有资源（截图线程、录屏、推流）。"""
        self._running = False
        if self._capture_thread:
            self._capture_thread.join(timeout=5)
        self.stop_recording()
        if self._streamer:
            self._streamer.stop()
        self._frame_source.stop()
        logger.info("ScreenManager stopped")

    def get_frame(self, timeout: float = 1.0) -> bytes:
        """获取单帧（供录屏和推流共享）。"""
        try:
            return self._frame_queue.get(timeout=timeout)
        except Empty:
            # 队列空时返回空白帧
            return self._frame_source.get_blank_frame()

    def _capture_loop(self) -> None:
        """后台截图循环（队列满时丢弃旧帧）。"""
        while self._running:
            try:
                frame = self._frame_source.get_frame_with_reconnect()
                if self._frame_queue.full():
                    # 队列满时丢弃最旧的帧
                    try:
                        self._frame_queue.get_nowait()
                    except Empty:
                        pass
                self._frame_queue.put(frame, timeout=1)
            except Exception as e:
                logger.warning(f"Frame capture error: {e}")

    def start_recording(self, output_path: str, fps: int = 10,
                        timeout_ms: int = 7200000) -> bool:
        """启动录屏。

        Args:
            output_path: 输出文件路径
            fps: 帧率
            timeout_ms: 超时时间（毫秒），默认 2 小时

        Returns:
            bool: 是否成功启动（False 表示已有录屏进行中）
        """
        from worker.screen.recorder import ScreenRecorder

        with self._recording_lock:
            if self._is_recording:
                logger.warning("Recording already in progress")
                return False

            timeout_sec = timeout_ms // 1000
            self._recorder = ScreenRecorder(self, output_path, fps, timeout_sec)
            self._recorder.start()
            self._is_recording = True
            logger.info(f"Recording started: {output_path}")
            return True

    def stop_recording(self) -> str:
        """停止录屏，返回文件路径。"""
        with self._recording_lock:
            if not self._is_recording or not self._recorder:
                return ""

            output_path = self._recorder.stop()
            self._recorder = None
            self._is_recording = False
            logger.info(f"Recording stopped: {output_path}")
            return output_path

    def start_streaming(self) -> "WebSocketStreamer":
        """启动 WebSocket 推流。"""
        from worker.screen.streamer import WebSocketStreamer

        if not self._streamer:
            self._streamer = WebSocketStreamer(self)
            self._streamer.start()
            logger.info("WebSocket streaming started")
        return self._streamer
```

- [ ] **Step 4: 运行测试验证通过**

Run: `pytest tests/unit/screen/test_manager.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add worker/screen/manager.py tests/unit/screen/test_manager.py
git commit -m "feat(screen): add ScreenManager unified manager with global cache"
```

---

## Task 4: ScreenRecorder 录屏器

**Files:**
- Create: `worker/screen/recorder.py`
- Create: `tests/unit/screen/test_recorder.py`

- [ ] **Step 1: 写失败测试 - ScreenRecorder 基础功能**

```python
# tests/unit/screen/test_recorder.py
"""ScreenRecorder 单元测试。"""

import pytest
import subprocess
import os
import tempfile
from unittest.mock import MagicMock, patch
import threading

from worker.screen.recorder import ScreenRecorder


class TestScreenRecorder:
    """测试 ScreenRecorder。"""

    def test_init_sets_parameters(self):
        """初始化设置参数。"""
        manager = MagicMock()
        manager._frame_source.get_screen_size.return_value = (1920, 1080)

        recorder = ScreenRecorder(
            screen_manager=manager,
            output_path="/tmp/test.mp4",
            fps=10,
            timeout_sec=60
        )

        assert recorder.output_path == "/tmp/test.mp4"
        assert recorder.fps == 10
        assert recorder.timeout_sec == 60

    def test_start_creates_timeout_timer(self):
        """启动时创建超时定时器。"""
        manager = MagicMock()
        manager._frame_source.get_screen_size.return_value = (1920, 1080)

        recorder = ScreenRecorder(
            screen_manager=manager,
            output_path="/tmp/test.mp4",
            fps=10,
            timeout_sec=60
        )

        recorder.start()

        assert recorder._timeout_timer is not None
        assert recorder._timeout_timer.is_alive()

        # 清理
        recorder.stop()

    def test_stop_cancels_timer(self):
        """停止时取消定时器。"""
        manager = MagicMock()
        manager._frame_source.get_screen_size.return_value = (1920, 1080)

        recorder = ScreenRecorder(
            screen_manager=manager,
            output_path="/tmp/test.mp4",
            fps=10,
            timeout_sec=60
        )

        recorder.start()
        recorder.stop()

        # 定时器应该被取消
        assert not recorder._timeout_timer.is_alive()

    @patch('subprocess.Popen')
    def test_stop_closes_ffmpeg_process(self, mock_popen):
        """停止时关闭 FFmpeg 进程。"""
        manager = MagicMock()
        manager._frame_source.get_screen_size.return_value = (1920, 1080)
        manager.get_frame.return_value = b"\xff\xd8\xff\xd9"  # 最小 JPEG

        mock_process = MagicMock()
        mock_process.stdin = MagicMock()
        mock_process.wait = MagicMock()
        mock_popen.return_value = mock_process

        recorder = ScreenRecorder(
            screen_manager=manager,
            output_path="/tmp/test.mp4",
            fps=10,
            timeout_sec=60
        )

        recorder.start()
        # 等待线程启动
        threading.Event().wait(0.1)
        recorder.stop()

        # FFmpeg stdin 应被关闭
        mock_process.stdin.close.assert_called()
```

- [ ] **Step 2: 运行测试验证失败**

Run: `pytest tests/unit/screen/test_recorder.py -v`
Expected: FAIL with "No module named 'worker.screen.recorder'"

- [ ] **Step 3: 实现 ScreenRecorder**

```python
# worker/screen/recorder.py
"""ScreenRecorder FFmpeg 录屏器。"""

import logging
import subprocess
import threading
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from worker.screen.manager import ScreenManager

logger = logging.getLogger(__name__)


class ScreenRecorder:
    """FFmpeg 录屏器（队列缓冲 + 双线程）。"""

    def __init__(
        self,
        screen_manager: "ScreenManager",
        output_path: str,
        fps: int = 10,
        timeout_sec: int = 7200,
    ):
        """
        Args:
            screen_manager: ScreenManager 实例
            output_path: 输出文件路径
            fps: 帧率
            timeout_sec: 超时时间（秒）
        """
        self.screen_manager = screen_manager
        self.output_path = output_path
        self.fps = fps
        self.timeout_sec = timeout_sec
        self._stop_event = threading.Event()
        self._timeout_timer: threading.Timer | None = None
        self._ffmpeg_process: subprocess.Popen | None = None
        self._write_thread: threading.Thread | None = None

    def start(self) -> None:
        """启动录屏。"""
        # 启动超时定时器
        self._timeout_timer = threading.Timer(self.timeout_sec, self.stop)
        self._timeout_timer.start()

        # 启动 FFmpeg 写入线程
        self._write_thread = threading.Thread(target=self._write_loop, daemon=True)
        self._write_thread.start()

        logger.info(f"Recording started: {self.output_path}, fps={self.fps}, timeout={self.timeout_sec}s")

    def _write_loop(self) -> None:
        """FFmpeg 编码写入线程。"""
        width, height = self.screen_manager._frame_source.get_screen_size()

        # FFmpeg 命令：使用 image2pipe 输入 JPEG 序列
        ffmpeg_cmd = [
            "ffmpeg",
            "-y",  # 覆盖输出文件
            "-f", "image2pipe",
            "-vcodec", "mjpeg",
            "-r", str(self.fps),
            "-i", "-",  # 从 stdin 读取
            "-c:v", "libx264",
            "-preset", "ultrafast",
            "-pix_fmt", "yuv420p",
            "-s", f"{width}x{height}",
            self.output_path,
        ]

        try:
            self._ffmpeg_process = subprocess.Popen(
                ffmpeg_cmd,
                stdin=subprocess.PIPE,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )

            while not self._stop_event.is_set():
                frame = self.screen_manager.get_frame()
                if frame and self._ffmpeg_process and self._ffmpeg_process.stdin:
                    try:
                        self._ffmpeg_process.stdin.write(frame)
                    except BrokenPipeError:
                        logger.warning("FFmpeg stdin pipe broken")
                        break

        except FileNotFoundError:
            logger.error("FFmpeg not found, please install ffmpeg and add to PATH")
        except Exception as e:
            logger.error(f"FFmpeg error: {e}")
        finally:
            logger.debug("Write loop ended")

    def stop(self) -> str:
        """停止录屏，返回文件路径。"""
        self._stop_event.set()

        # 取消超时定时器
        if self._timeout_timer:
            self._timeout_timer.cancel()
            self._timeout_timer = None

        # 关闭 FFmpeg 进程
        if self._ffmpeg_process:
            try:
                if self._ffmpeg_process.stdin:
                    self._ffmpeg_process.stdin.close()
                self._ffmpeg_process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self._ffmpeg_process.terminate()
                self._ffmpeg_process.wait(timeout=2)
            except Exception as e:
                logger.warning(f"FFmpeg cleanup error: {e}")
            self._ffmpeg_process = None

        # 等待写入线程结束
        if self._write_thread:
            self._write_thread.join(timeout=5)
            self._write_thread = None

        logger.info(f"Recording stopped: {self.output_path}")
        return self.output_path
```

- [ ] **Step 4: 运行测试验证通过**

Run: `pytest tests/unit/screen/test_recorder.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add worker/screen/recorder.py tests/unit/screen/test_recorder.py
git commit -m "feat(screen): add ScreenRecorder with FFmpeg image2pipe input"
```

---

## Task 5: WebSocketStreamer 推流器

**Files:**
- Create: `worker/screen/streamer.py`
- Create: `tests/unit/screen/test_streamer.py`

- [ ] **Step 1: 写失败测试 - WebSocketStreamer**

```python
# tests/unit/screen/test_streamer.py
"""WebSocketStreamer 单元测试。"""

import pytest
import asyncio
from unittest.mock import MagicMock, AsyncMock, patch

from worker.screen.streamer import WebSocketStreamer


class TestWebSocketStreamer:
    """测试 WebSocketStreamer。"""

    def test_init_sets_manager(self):
        """初始化设置 ScreenManager。"""
        manager = MagicMock()
        streamer = WebSocketStreamer(manager)

        assert streamer.screen_manager == manager
        assert streamer._running is False

    def test_start_sets_running_flag(self):
        """start 设置运行标志。"""
        manager = MagicMock()
        streamer = WebSocketStreamer(manager)

        streamer.start()
        assert streamer._running is True

    def test_stop_clears_running_flag(self):
        """stop 清除运行标志。"""
        manager = MagicMock()
        streamer = WebSocketStreamer(manager)

        streamer.start()
        streamer.stop()
        assert streamer._running is False

    @pytest.mark.asyncio
    async def test_get_frame_async_returns_frame(self):
        """get_frame_async 异步返回帧。"""
        manager = MagicMock()
        manager.get_frame.return_value = b"test_frame"
        streamer = WebSocketStreamer(manager)

        frame = await streamer.get_frame_async()
        assert frame == b"test_frame"
```

- [ ] **Step 2: 运行测试验证失败**

Run: `pytest tests/unit/screen/test_streamer.py -v`
Expected: FAIL with "No module named 'worker.screen.streamer'"

- [ ] **Step 3: 实现 WebSocketStreamer**

```python
# worker/screen/streamer.py
"""WebSocketStreamer 推流器。"""

import asyncio
import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from worker.screen.manager import ScreenManager

logger = logging.getLogger(__name__)


class WebSocketStreamer:
    """WebSocket 屏幕推流器。"""

    def __init__(self, screen_manager: "ScreenManager"):
        self.screen_manager = screen_manager
        self._running = False

    def start(self) -> None:
        """启动推流。"""
        self._running = True
        logger.info("WebSocket streamer started")

    def stop(self) -> None:
        """停止推流。"""
        self._running = False
        logger.info("WebSocket streamer stopped")

    async def get_frame_async(self) -> bytes:
        """异步获取帧（避免阻塞 WebSocket）。"""
        return await asyncio.to_thread(self.screen_manager.get_frame)

    def is_running(self) -> bool:
        """检查是否正在运行。"""
        return self._running
```

- [ ] **Step 4: 运行测试验证通过**

Run: `pytest tests/unit/screen/test_streamer.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add worker/screen/streamer.py tests/unit/screen/test_streamer.py
git commit -m "feat(screen): add WebSocketStreamer with async frame retrieval"
```

---

## Task 6: pinch 动作处理器

**Files:**
- Create: `worker/actions/gesture.py`
- Modify: `worker/platforms/base.py` (新增 pinch 抽象方法)
- Modify: `worker/platforms/android.py` (实现 pinch)
- Modify: `worker/platforms/ios.py` (实现 pinch)

- [ ] **Step 1: 写失败测试 - pinch 动作处理器**

```python
# tests/unit/actions/test_gesture.py
"""pinch 动作处理器测试。"""

import pytest
from unittest.mock import MagicMock

from worker.actions.gesture import PinchAction
from worker.task import Action, ActionStatus


class TestPinchAction:
    """测试 PinchAction。"""

    def test_name_is_pinch(self):
        """动作名称为 pinch。"""
        action = PinchAction()
        assert action.name == "pinch"

    def test_execute_calls_platform_pinch(self):
        """execute 调用平台 pinch 方法。"""
        handler = PinchAction()
        platform = MagicMock()
        platform.pinch = MagicMock()

        action = Action(
            action_type="pinch",
            value="in",
            params={"scale": 0.5, "duration": 500}
        )

        result = handler.execute(platform, action)

        platform.pinch.assert_called_once_with(
            direction="in",
            scale=0.5,
            duration=500,
            context=None
        )
        assert result.status == ActionStatus.SUCCESS

    def test_execute_with_default_params(self):
        """使用默认参数执行。"""
        handler = PinchAction()
        platform = MagicMock()
        platform.pinch = MagicMock()

        action = Action(action_type="pinch", value="out")

        result = handler.execute(platform, action)

        platform.pinch.assert_called_once_with(
            direction="out",
            scale=0.5,  # 默认值
            duration=500,  # 默认值
            context=None
        )

    def test_execute_returns_failed_on_exception(self):
        """异常时返回 FAILED。"""
        handler = PinchAction()
        platform = MagicMock()
        platform.pinch.side_effect = RuntimeError("pinch failed")

        action = Action(action_type="pinch", value="in")

        result = handler.execute(platform, action)

        assert result.status == ActionStatus.FAILED
        assert "pinch failed" in result.error
```

- [ ] **Step 2: 运行测试验证失败**

Run: `pytest tests/unit/actions/test_gesture.py -v`
Expected: FAIL with "No module named 'worker.actions.gesture'"

- [ ] **Step 3: 实现 pinch 动作处理器**

```python
# worker/actions/gesture.py
"""pinch 手势动作处理器。"""

import logging
from worker.actions.base import ActionExecutor
from worker.task import Action, ActionResult, ActionStatus

logger = logging.getLogger(__name__)


class PinchAction(ActionExecutor):
    """pinch 双指缩放动作。"""

    name = "pinch"

    def execute(self, platform, action: Action, context=None) -> ActionResult:
        """
        执行 pinch 手势。

        Args:
            platform: 平台管理器
            action: 动作参数
                - value: "in" 缩小 / "out" 放大
                - params.scale: 缩放比例（默认 0.5）
                - params.duration: 持续时间（毫秒，默认 500）
            context: 执行上下文
        """
        direction = action.value  # "in" 或 "out"
        scale = action.params.get("scale", 0.5) if action.params else 0.5
        duration = action.params.get("duration", 500) if action.params else 500

        try:
            platform.pinch(direction, scale, duration, context)
            return ActionResult(
                number=0,
                action_type=self.name,
                status=ActionStatus.SUCCESS,
            )
        except Exception as e:
            logger.error(f"pinch failed: {e}")
            return ActionResult(
                number=0,
                action_type=self.name,
                status=ActionStatus.FAILED,
                error=str(e),
            )
```

- [ ] **Step 4: 在 base.py 新增 pinch 抽象方法**

```python
# worker/platforms/base.py (在类中新增方法)
    # ========== 手势操作 ==========

    def pinch(self, direction: str, scale: float = 0.5,
              duration: int = 500, context: Any = None) -> None:
        """
        双指缩放手势。

        Args:
            direction: "in" 缩小 / "out" 放大
            scale: 缩放比例
            duration: 持续时间（毫秒）
            context: 执行上下文

        Note:
            仅 Android/iOS 平台支持，Web/Windows 不支持。
        """
        raise NotImplementedError("pinch is not supported on this platform")
```

- [ ] **Step 5: 运行测试验证通过**

Run: `pytest tests/unit/actions/test_gesture.py -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add worker/actions/gesture.py worker/platforms/base.py tests/unit/actions/test_gesture.py
git commit -m "feat(actions): add pinch gesture action handler and abstract method"
```

---

## Task 7: Android pinch 实现

**Files:**
- Modify: `worker/platforms/android.py`

- [ ] **Step 1: 写失败测试 - Android pinch**

```python
# tests/unit/platforms/test_android_pinch.py
"""Android pinch 实现测试。"""

import pytest
from unittest.mock import MagicMock, patch

from worker.platforms.android import AndroidPlatformManager


class TestAndroidPinch:
    """测试 Android pinch 实现。"""

    def test_pinch_in_calls_u2_multitouch(self):
        """pinch in 调用 uiautomator2 多点触控。"""
        with patch('uiautomator2.connect') as mock_connect:
            mock_device = MagicMock()
            mock_device.info = {"screenOn": True}
            mock_connect.return_value = mock_device

            from worker.config import PlatformConfig
            config = PlatformConfig()
            manager = AndroidPlatformManager(config)
            manager._device_clients["test_device"] = mock_device
            manager._current_device = "test_device"

            # 执行 pinch
            manager.pinch("in", scale=0.5, duration=500)

            # 验证调用了多点触控
            assert mock_device.pinch_in.called or mock_device.pinch.called

    def test_pinch_not_supported_raises_error(self):
        """不支持的平台调用 pinch 抛出异常。"""
        from worker.platforms.windows import WindowsPlatformManager
        from worker.config import PlatformConfig

        config = PlatformConfig()
        manager = WindowsPlatformManager(config)

        with pytest.raises(NotImplementedError):
            manager.pinch("in")
```

- [ ] **Step 2: 运行测试验证失败**

Run: `pytest tests/unit/platforms/test_android_pinch.py -v`
Expected: FAIL with "AttributeError: 'AndroidPlatformManager' has no attribute 'pinch'"

- [ ] **Step 3: 实现 Android pinch**

```python
# worker/platforms/android.py (在类中新增方法)
    def pinch(self, direction: str, scale: float = 0.5,
              duration: int = 500, context: Any = None) -> None:
        """
        双指缩放手势。

        使用 uiautomator2 的 pinch 方法实现。
        """
        device = context or self._device_clients.get(self._current_device)
        if not device:
            raise RuntimeError("No device context")

        duration_sec = duration / 1000.0

        if direction == "in":
            # 缩小：从外向内
            device.pinch_in(percent=scale, duration=duration_sec)
        else:
            # 放大：从内向外
            device.pinch_out(percent=scale, duration=duration_sec)

        logger.debug(f"pinch {direction} executed: scale={scale}, duration={duration}ms")
```

- [ ] **Step 4: 更新 Android SUPPORTED_ACTIONS**

```python
# worker/platforms/android.py (修改 SUPPORTED_ACTIONS)
    SUPPORTED_ACTIONS: set[str] = {"start_app", "stop_app", "unlock_screen", "pinch"}
```

- [ ] **Step 5: 运行测试验证通过**

Run: `pytest tests/unit/platforms/test_android_pinch.py -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add worker/platforms/android.py tests/unit/platforms/test_android_pinch.py
git commit -m "feat(android): implement pinch gesture with uiautomator2"
```

---

## Task 8: iOS pinch 实现

**Files:**
- Modify: `worker/platforms/ios.py`

- [ ] **Step 1: 写失败测试 - iOS pinch**

```python
# tests/unit/platforms/test_ios_pinch.py
"""iOS pinch 实现测试。"""

import pytest
from unittest.mock import MagicMock, patch

from worker.platforms.ios import iOSPlatformManager


class TestiOSPinch:
    """测试 iOS pinch 实现。"""

    def test_pinch_calls_wda_multitouch(self):
        """pinch 调用 WDA 多点触控。"""
        mock_client = MagicMock()
        mock_client.is_locked.return_value = False

        from worker.config import PlatformConfig
        config = PlatformConfig()
        manager = iOSPlatformManager(config)
        manager._device_clients["test_device"] = mock_client
        manager._current_device = "test_device"

        # 执行 pinch
        manager.pinch("in", scale=0.5, duration=500)

        # 验证 WDA 多点触控被调用
        assert mock_client.pinch.called or mock_client.touch_and_hold.called
```

- [ ] **Step 2: 运行测试验证失败**

Run: `pytest tests/unit/platforms/test_ios_pinch.py -v`
Expected: FAIL with "AttributeError"

- [ ] **Step 3: 实现 iOS pinch**

```python
# worker/platforms/ios.py (在类中新增方法)
    def pinch(self, direction: str, scale: float = 0.5,
              duration: int = 500, context: Any = None) -> None:
        """
        双指缩放手势。

        使用 WDA 的 pinch 方法实现。
        """
        client = context or self._device_clients.get(self._current_device)
        if not client:
            raise RuntimeError("No device context")

        duration_sec = duration / 1000.0

        if direction == "in":
            # 缩小
            client.pinch_with_scale(scale, duration=duration_sec)
        else:
            # 放大
            client.pinch_with_scale(1.0 / scale, duration=duration_sec)

        logger.debug(f"pinch {direction} executed: scale={scale}, duration={duration}ms")
```

- [ ] **Step 4: 更新 iOS SUPPORTED_ACTIONS**

```python
# worker/platforms/ios.py (修改 SUPPORTED_ACTIONS)
    SUPPORTED_ACTIONS: set[str] = {"start_app", "stop_app", "unlock_screen", "pinch"}
```

- [ ] **Step 5: 运行测试验证通过**

Run: `pytest tests/unit/platforms/test_ios_pinch.py -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add worker/platforms/ios.py tests/unit/platforms/test_ios_pinch.py
git commit -m "feat(ios): implement pinch gesture with WDA"
```

---

## Task 9: recording 动作处理器

**Files:**
- Create: `worker/actions/recording.py`
- Create: `tests/unit/actions/test_recording.py`

- [ ] **Step 1: 写失败测试 - recording 动作处理器**

```python
# tests/unit/actions/test_recording.py
"""录屏动作处理器测试。"""

import pytest
import tempfile
import os
from unittest.mock import MagicMock, patch

from worker.actions.recording import StartRecordingAction, StopRecordingAction
from worker.task import Action, ActionStatus


class TestStartRecordingAction:
    """测试 StartRecordingAction。"""

    def test_name_is_start_recording(self):
        """动作名称为 start_recording。"""
        action = StartRecordingAction()
        assert action.name == "start_recording"

    def test_execute_creates_screen_manager(self):
        """execute 创建 ScreenManager 并启动录屏。"""
        handler = StartRecordingAction()

        with patch('worker.actions.recording.get_screen_manager') as mock_get:
            mock_manager = MagicMock()
            mock_manager.start_recording.return_value = True
            mock_get.return_value = mock_manager

            platform = MagicMock()
            platform._current_device = "test_device"

            action = Action(
                action_type="start_recording",
                value="test.mp4",
                params={"fps": 10}
            )

            result = handler.execute(platform, action)

            mock_get.assert_called_once()
            mock_manager.start_recording.assert_called_once()
            assert result.status == ActionStatus.SUCCESS

    def test_execute_returns_failed_when_already_recording(self):
        """已有录屏时返回 FAILED。"""
        handler = StartRecordingAction()

        with patch('worker.actions.recording.get_screen_manager') as mock_get:
            mock_manager = MagicMock()
            mock_manager.start_recording.return_value = False
            mock_get.return_value = mock_manager

            platform = MagicMock()
            platform._current_device = "test_device"

            action = Action(action_type="start_recording")

            result = handler.execute(platform, action)

            assert result.status == ActionStatus.FAILED
            assert "already in progress" in result.error


class TestStopRecordingAction:
    """测试 StopRecordingAction。"""

    def test_name_is_stop_recording(self):
        """动作名称为 stop_recording。"""
        action = StopRecordingAction()
        assert action.name == "stop_recording"

    def test_execute_returns_file_path(self):
        """execute 返回录屏文件路径。"""
        handler = StopRecordingAction()

        with patch('worker.actions.recording.get_screen_manager') as mock_get:
            mock_manager = MagicMock()
            mock_manager.stop_recording.return_value = "/tmp/test.mp4"
            mock_get.return_value = mock_manager

            platform = MagicMock()
            platform._current_device = "test_device"

            action = Action(action_type="stop_recording")

            result = handler.execute(platform, action)

            assert result.status == ActionStatus.SUCCESS
            assert result.output == "/tmp/test.mp4"
```

- [ ] **Step 2: 运行测试验证失败**

Run: `pytest tests/unit/actions/test_recording.py -v`
Expected: FAIL with "No module named 'worker.actions.recording'"

- [ ] **Step 3: 实现 recording 动作处理器**

```python
# worker/actions/recording.py
"""录屏动作处理器。"""

import logging
import os
from datetime import datetime

from worker.actions.base import ActionExecutor
from worker.screen.manager import get_screen_manager
from worker.task import Action, ActionResult, ActionStatus

logger = logging.getLogger(__name__)


class StartRecordingAction(ActionExecutor):
    """启动录屏动作。"""

    name = "start_recording"
    requires_context = False

    def execute(self, platform, action: Action, context=None) -> ActionResult:
        """
        启动录屏。

        Args:
            platform: 平台管理器
            action: 动作参数
                - value: 输出文件名（可选，默认自动生成）
                - params.fps: 帧率（默认 10）
                - params.timeout: 超时（毫秒，默认 7200000）
            context: 执行上下文
        """
        from worker.config import Config

        # 获取输出目录
        output_dir = Config().get("recording.output_dir", "data/recordings")
        filename = action.value or f"recording_{datetime.now():%Y%m%d_%H%M%S}.mp4"

        # 处理路径
        if os.path.isabs(filename):
            output_path = filename
        else:
            output_path = os.path.join(output_dir, filename)

        # 确保目录存在
        os.makedirs(output_dir, exist_ok=True)

        fps = action.params.get("fps", 10) if action.params else 10
        timeout_ms = action.params.get("timeout", 7200000) if action.params else 7200000

        # 获取设备 ID
        device_id = getattr(platform, "_current_device", None) or "windows"

        try:
            # 创建 FrameSource
            from worker.screen.frame_source import WindowsFrameSource
            frame_source = WindowsFrameSource(fps=fps)

            screen_manager = get_screen_manager(device_id, frame_source)
            success = screen_manager.start_recording(output_path, fps, timeout_ms)

            if success:
                return ActionResult(
                    number=0,
                    action_type=self.name,
                    status=ActionStatus.SUCCESS,
                    output=output_path,
                )
            else:
                return ActionResult(
                    number=0,
                    action_type=self.name,
                    status=ActionStatus.FAILED,
                    error="Recording already in progress",
                )

        except Exception as e:
            logger.error(f"start_recording failed: {e}")
            return ActionResult(
                number=0,
                action_type=self.name,
                status=ActionStatus.FAILED,
                error=str(e),
            )


class StopRecordingAction(ActionExecutor):
    """停止录屏动作。"""

    name = "stop_recording"
    requires_context = False

    def execute(self, platform, action: Action, context=None) -> ActionResult:
        """
        停止录屏。

        Args:
            platform: 平台管理器
            action: 动作参数
            context: 执行上下文
        """
        from worker.screen.manager import get_screen_manager, close_screen_manager

        device_id = getattr(platform, "_current_device", None) or "windows"

        try:
            # 获取已存在的 ScreenManager
            from worker.screen.manager import _screen_managers

            if device_id not in _screen_managers:
                return ActionResult(
                    number=0,
                    action_type=self.name,
                    status=ActionStatus.FAILED,
                    error="No recording in progress",
                )

            screen_manager = _screen_managers[device_id]
            output_path = screen_manager.stop_recording()

            if output_path:
                return ActionResult(
                    number=0,
                    action_type=self.name,
                    status=ActionStatus.SUCCESS,
                    output=output_path,
                )
            else:
                return ActionResult(
                    number=0,
                    action_type=self.name,
                    status=ActionStatus.FAILED,
                    error="No recording in progress",
                )

        except Exception as e:
            logger.error(f"stop_recording failed: {e}")
            return ActionResult(
                number=0,
                action_type=self.name,
                status=ActionStatus.FAILED,
                error=str(e),
            )
```

- [ ] **Step 4: 运行测试验证通过**

Run: `pytest tests/unit/actions/test_recording.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add worker/actions/recording.py tests/unit/actions/test_recording.py
git commit -m "feat(actions): add start_recording and stop_recording action handlers"
```

---

## Task 10: WebSocket 路由

**Files:**
- Modify: `worker/server.py`

- [ ] **Step 1: 写失败测试 - WebSocket 路由**

```python
# tests/unit/test_server_websocket.py
"""WebSocket 路由测试。"""

import pytest
from fastapi.testclient import TestClient
from unittest.mock import MagicMock, patch

from worker.server import app


class TestWebSocketRoute:
    """测试 WebSocket 屏幕推流路由。"""

    def test_websocket_route_exists(self):
        """WebSocket 路由存在。"""
        # 检查路由是否注册
        routes = [route.path for route in app.routes]
        assert "/ws/screen/{device_id}" in routes

    @pytest.mark.asyncio
    async def test_websocket_rejects_when_max_connections(self):
        """超过最大连接数时拒绝连接。"""
        with patch('worker.server._ws_connections') as mock_conn:
            mock_conn.get.return_value = 5  # 已达上限

            client = TestClient(app)

            with pytest.raises(Exception):
                # WebSocket 连接应被拒绝
                client.websocket_connect("/ws/screen/test_device")
```

- [ ] **Step 2: 运行测试验证失败**

Run: `pytest tests/unit/test_server_websocket.py -v`
Expected: FAIL with route not found

- [ ] **Step 3: 实现 WebSocket 路由**

```python
# worker/server.py (追加 WebSocket 相关代码)
from fastapi import WebSocket, WebSocketDisconnect
import asyncio

# 连接计数器
_ws_connections: dict[str, int] = {}

# 最大连接数配置
MAX_WS_CONNECTIONS_PER_DEVICE = 3


@app.websocket("/ws/screen/{device_id}")
async def screen_stream(websocket: WebSocket, device_id: str):
    """实时屏幕推流（10fps）。"""

    # 检查连接数限制
    current_count = _ws_connections.get(device_id, 0)

    if current_count >= MAX_WS_CONNECTIONS_PER_DEVICE:
        # 超过限制，拒绝连接（WebSocket Policy Violation）
        await websocket.close(code=1008, reason="Max connections reached")
        return

    await websocket.accept()
    _ws_connections[device_id] = current_count + 1

    logger.info(f"WebSocket connected: device={device_id}, count={current_count + 1}")

    try:
        # 获取 ScreenManager
        from worker.screen.manager import get_screen_manager, _screen_managers
        from worker.screen.frame_source import WindowsFrameSource

        if device_id not in _screen_managers:
            frame_source = WindowsFrameSource(fps=10)
            screen_manager = get_screen_manager(device_id, frame_source)
        else:
            screen_manager = _screen_managers[device_id]

        streamer = screen_manager.start_streaming()

        while streamer.is_running():
            frame = await streamer.get_frame_async()
            # 发送 JPEG 原始数据
            await websocket.send_bytes(frame)
            await asyncio.sleep(0.1)  # 10fps

    except WebSocketDisconnect:
        logger.info(f"WebSocket disconnected: device={device_id}")
    except Exception as e:
        logger.error(f"WebSocket error: {e}")
    finally:
        # 确保减少连接计数
        _ws_connections[device_id] = _ws_connections.get(device_id, 1) - 1
        if _ws_connections[device_id] <= 0:
            del _ws_connections[device_id]
        logger.info(f"WebSocket connection closed: device={device_id}")
```

- [ ] **Step 4: 运行测试验证通过**

Run: `pytest tests/unit/test_server_websocket.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add worker/server.py tests/unit/test_server_websocket.py
git commit -m "feat(server): add WebSocket screen streaming route with connection limit"
```

---

## Task 11: 注册新动作处理器

**Files:**
- Modify: `worker/actions/__init__.py`
- Modify: `worker/platforms/base.py` (更新 BASE_SUPPORTED_ACTIONS)

- [ ] **Step 1: 注册动作处理器**

```python
# worker/actions/__init__.py (追加导入和注册)
from worker.actions.gesture import PinchAction
from worker.actions.recording import StartRecordingAction, StopRecordingAction

# 在 _register_all_actions 中追加
    # Gesture Actions
    ActionRegistry.register(PinchAction())

    # Recording Actions
    ActionRegistry.register(StartRecordingAction())
    ActionRegistry.register(StopRecordingAction())

# 在 __all__ 中追加
    # Gesture Actions
    "PinchAction",
    # Recording Actions
    "StartRecordingAction",
    "StopRecordingAction",
```

- [ ] **Step 2: 更新 BASE_SUPPORTED_ACTIONS**

```python
# worker/platforms/base.py (修改 BASE_SUPPORTED_ACTIONS)
    BASE_SUPPORTED_ACTIONS: Set[str] = {
        # ... 现有动作 ...
        "pinch",                 # 双指缩放（Android/iOS）
        "start_recording",       # 开始录屏
        "stop_recording",        # 停止录屏
    }
```

- [ ] **Step 3: 运行测试验证注册成功**

Run: `pytest tests/unit/ -v -k "gesture or recording"`
Expected: PASS

- [ ] **Step 4: Commit**

```bash
git add worker/actions/__init__.py worker/platforms/base.py
git commit -m "feat(actions): register pinch and recording action handlers"
```

---

## Task 12: 设备离线时关闭 ScreenManager

**Files:**
- Modify: `worker/device_monitor.py`

- [ ] **Step 1: 在设备离线检查中调用 close_screen_manager**

```python
# worker/device_monitor.py (修改 _check_online_devices 方法)
    def _check_online_devices(self) -> None:
        """检查在线设备状态。"""
        if self._android_manager:
            for device in self._android_devices[:]:
                udid = device["udid"]
                manager_devices = self._android_manager.get_online_devices()
                if udid not in manager_devices:
                    # 设备离线时关闭 ScreenManager
                    from worker.screen.manager import close_screen_manager
                    close_screen_manager(udid)

                    self._android_devices = [d for d in self._android_devices if d["udid"] != udid]
                    self._faulty_android_devices.append({"udid": udid})
                    logger.warning(f"Android device went offline: {udid}")

        if self._ios_manager:
            for device in self._ios_devices[:]:
                udid = device["udid"]
                manager_devices = self._ios_manager.get_online_devices()
                if udid not in manager_devices:
                    # 设备离线时关闭 ScreenManager
                    from worker.screen.manager import close_screen_manager
                    close_screen_manager(udid)

                    self._ios_devices = [d for d in self._ios_devices if d["udid"] != udid]
                    self._faulty_ios_devices.append({"udid": udid})
                    logger.warning(f"iOS device went offline: {udid}")
```

- [ ] **Step 2: Commit**

```bash
git add worker/device_monitor.py
git commit -m "feat(monitor): close ScreenManager when device goes offline"
```

---

## Task 13: 配置更新

**Files:**
- Modify: `worker/config.py`
- Create example config section in `config/worker.yaml`

- [ ] **Step 1: 新增录屏/推流配置项**

```python
# worker/config.py (在 WorkerConfig 类中追加属性)
    # 录屏配置
    recording_output_dir: str = "data/recordings"
    recording_default_fps: int = 10
    recording_max_timeout_ms: int = 7200000  # 2小时

    # WebSocket 推流配置
    websocket_max_connections_per_device: int = 3
```

```yaml
# config/worker.yaml (追加配置示例)
# 录屏配置
recording:
  output_dir: data/recordings    # 输出目录
  default_fps: 10                # 默认帧率
  max_timeout_ms: 7200000        # 最大超时（毫秒），2小时

# WebSocket 推流配置
websocket_streaming:
  max_connections_per_device: 3  # 单设备最大连接数
```

- [ ] **Step 2: Commit**

```bash
git add worker/config.py config/worker.yaml
git commit -m "feat(config): add recording and websocket streaming configuration"
```

---

## Task 14: Worker.stop() 关闭所有 ScreenManager

**Files:**
- Modify: `worker/worker.py`

- [ ] **Step 1: 在 Worker.stop() 中调用 close_all_screen_managers**

```python
# worker/worker.py (修改 stop 方法)
    def stop(self) -> None:
        """停止 Worker。"""
        if not self._started:
            return

        logger.info(f"Stopping Worker {self.worker_id}...")

        # 关闭所有 ScreenManager
        from worker.screen.manager import close_all_screen_managers
        close_all_screen_managers()

        # 停止设备监控
        if self.device_monitor:
            self.device_monitor.stop()

        # ... 其他清理代码 ...
```

- [ ] **Step 2: Commit**

```bash
git add worker/worker.py
git commit -m "feat(worker): close all ScreenManagers on worker stop"
```

---

## Task 15: 集成测试 - pinch

**Files:**
- Create: `tests/integration/test_pinch_integration.py`

- [ ] **Step 1: 写集成测试 - Android pinch**

```python
# tests/integration/test_pinch_integration.py
"""pinch 集成测试。"""

import pytest
from unittest.mock import MagicMock, patch

from worker.task import Task
from worker.worker import Worker


@pytest.mark.skip(reason="需要真实 Android 设备")
class TestAndroidPinchIntegration:
    """Android pinch 集成测试。"""

    def test_pinch_in_on_real_device(self):
        """真实设备上执行 pinch in。"""
        # 需要:
        # 1. 连接的 Android 设备
        # 2. 一个支持缩放的应用（如地图）
        pass

    def test_pinch_out_on_real_device(self):
        """真实设备上执行 pinch out。"""
        pass


@pytest.mark.skip(reason="需要真实 iOS 设备")
class TestiOSPinchIntegration:
    """iOS pinch 集成测试。"""

    def test_pinch_in_on_real_device(self):
        """真实设备上执行 pinch in。"""
        pass
```

- [ ] **Step 2: Commit**

```bash
git add tests/integration/test_pinch_integration.py
git commit -m "test(integration): add pinch integration test placeholders"
```

---

## Task 16: 集成测试 - 录屏

**Files:**
- Create: `tests/integration/test_recording_integration.py`

- [ ] **Step 1: 写集成测试 - Windows 录屏**

```python
# tests/integration/test_recording_integration.py
"""录屏集成测试。"""

import pytest
import os
import tempfile
import time

from worker.task import Task
from worker.worker import Worker


class TestWindowsRecordingIntegration:
    """Windows 录屏集成测试。"""

    def test_start_stop_recording_creates_mp4(self):
        """start/stop recording 创建 MP4 文件。"""
        with tempfile.TemporaryDirectory() as tmpdir:
            output_path = os.path.join(tmpdir, "test.mp4")

            # 模拟录屏流程
            # 1. start_recording
            # 2. 等待几秒（收集帧）
            # 3. stop_recording
            # 4. 验证文件存在

            # 注意：实际测试需要 FFmpeg
            # 此处为占位测试
            pass

    def test_recording_timeout_auto_stop(self):
        """录屏超时自动停止。"""
        # 设置短超时（如 2 秒）
        # 验证自动停止
        pass


@pytest.mark.skip(reason="需要真实 Android 设备")
class TestAndroidRecordingIntegration:
    """Android 录屏集成测试。"""

    def test_android_recording(self):
        """Android 设备录屏。"""
        pass


@pytest.mark.skip(reason="需要真实 iOS 设备")
class TestiOSRecordingIntegration:
    """iOS 录屏集成测试。"""

    def test_ios_recording(self):
        """iOS 设备录屏。"""
        pass
```

- [ ] **Step 2: Commit**

```bash
git add tests/integration/test_recording_integration.py
git commit -m "test(integration): add recording integration test placeholders"
```

---

## Task 17: 运行完整测试套件

- [ ] **Step 1: 运行所有单元测试**

Run: `pytest tests/unit/ -v`
Expected: All PASS

- [ ] **Step 2: 运行代码检查**

Run: `ruff check worker/screen/ worker/actions/gesture.py worker/actions/recording.py`
Expected: No errors

Run: `black --check worker/screen/ worker/actions/gesture.py worker/actions/recording.py`
Expected: All formatted

- [ ] **Step 3: 修复任何问题**

如果有检查失败，修复后重新运行。

- [ ] **Step 4: Final Commit**

```bash
git add -A
git commit -m "feat: complete Airtest features implementation - pinch, recording, websocket streaming"
```

---

## 实现完成检查清单

- [ ] 所有单元测试通过
- [ ] 代码检查无错误
- [ ] pinch 动作在 Android/iOS 可执行
- [ ] 录屏功能在 Windows 可执行（需要 FFmpeg）
- [ ] WebSocket 推流路由正常工作
- [ ] 配置项已添加
- [ ] 文档已更新（CLAUDE.md 如需要）