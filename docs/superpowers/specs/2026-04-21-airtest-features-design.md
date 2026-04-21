# Airtest 借鉴功能设计文档

**日期**: 2026-04-21
**状态**: 设计阶段

---

## 一、需求概述

从 Airtest 项目借鉴以下功能，增强 autotest 的截图、录屏、手势操作能力：

| 功能 | 平台支持 | 优先级 |
|------|---------|--------|
| 双指缩放手势 (pinch) | Android, iOS | 高 |
| 录屏功能 | Android, iOS, Windows | 高 |
| WebSocket 屏幕推流 | Android, iOS, Windows, Web | 高 |

**平台支持说明**：
- **Mac 平台**：不支持录屏/推流（Worker 仅在 Windows 宿主机运行，Mac 宿主机不连接移动设备）
- **Web 平台录屏**：不支持，通过 Windows 录屏覆盖（调用 Windows 录屏时指定 system level）

**跳过功能**：多尺度图像匹配（暂不实现）

---

## 二、架构设计

### 2.1 新增模块结构

```
worker/
├── screen/                   # 新增：截图/录屏/推流统一模块
│   ├── __init__.py
│   ├── manager.py            # ScreenManager：统一管理帧源、录屏、推流
│   ├── frame_source.py       # FrameSource：帧获取抽象层
│   ├── recorder.py           # ScreenRecorder：FFmpeg 录屏器
│   └── streamer.py           # WebSocketStreamer：WebSocket 推流器
│
├── platforms/
│   ├── base.py               # 增加 pinch() 抽象方法
│   ├── android.py            # 实现 pinch + 录屏帧源
│   ├── ios.py                # 实现 pinch + 录屏帧源
│   └── windows.py            # 实现 Windows 录屏（支持帧率配置）
│   └── web.py                # 仅支持推流帧源
│
├── actions/
│   └── gesture.py            # 新增：pinch 动作处理
│   └── recording.py          # 新增：start/stop_recording 动作处理
│
└── server.py                 # 增加 WebSocket 路由
```

### 2.2 核心设计原则

1. **帧源共享**：录屏和推流共享同一帧获取逻辑，避免重复截图
2. **异步设计**：WebSocket 推流使用 asyncio，不阻塞主线程
3. **超时保护**：录屏自动超时停止，防止忘记 stop_recording
4. **平台隔离**：pinch 和录屏的具体实现由各平台独立处理
5. **参数单位统一**：外部接口统一使用毫秒，内部按需转换

---

## 三、截图/录屏/推流统一模块

### 3.0 ScreenManager 生命周期管理

**创建时机**：
- 首次调用 `start_recording` 或首次 WebSocket 连接时创建
- 按 `device_id` 缓存，同一设备复用同一 ScreenManager

**缓存策略**：
```python
# screen/manager.py
_screen_managers: dict[str, ScreenManager] = {}  # 全局缓存

def get_screen_manager(device_id: str, platform: PlatformManager) -> ScreenManager:
    """获取或创建 ScreenManager（按设备 ID 缓存）"""
    if device_id not in _screen_managers:
        frame_source = create_frame_source(platform, device_id)
        _screen_managers[device_id] = ScreenManager(frame_source)
        _screen_managers[device_id].start_capture()
    return _screen_managers[device_id]
```

**销毁时机与触发机制**：

| 销毁触发点 | 调用路径 |
|-----------|---------|
| 设备离线 | DeviceMonitor → `close_screen_manager(device_id)` → ScreenManager.stop() |
| Worker 停止 | `worker.stop()` → `close_all_screen_managers()` → 循环调用 stop() |
| 显式调用 | 动作处理器或外部直接调用 `close_screen_manager(device_id)` |

```python
# screen/manager.py
def close_screen_manager(device_id: str) -> None:
    """关闭指定设备的 ScreenManager"""
    if device_id in _screen_managers:
        manager = _screen_managers[device_id]
        manager.stop()
        del _screen_managers[device_id]

def close_all_screen_managers() -> None:
    """关闭所有 ScreenManager（Worker 停止时调用）"""
    for device_id in list(_screen_managers.keys()):
        close_screen_manager(device_id)
```

```python
# worker/device_monitor.py
def _check_device_status(self):
    for udid in self._tracked_devices:
        status, _ = platform.ensure_device_service(udid)
        if status == "faulty" or device_offline:
            # 设备离线时关闭 ScreenManager
            close_screen_manager(udid)
            self.mark_device_faulty(udid)
```

**Web 平台特殊处理**：
- WebFrameSource 不需要 device_id，使用固定 key `"web_context"`
- 创建时机：首次 WebSocket 连接或 `start_recording`（通过 Windows 覆盖）

### 3.1 FrameSource 帧获取抽象层

```python
class FrameSource(ABC):
    """帧获取抽象基类"""

    MAX_RECONNECT_ATTEMPTS = 3
    RECONNECT_INTERVAL = 1  # 秒

    @abstractmethod
    def get_frame(self) -> bytes:
        """获取单帧（JPEG 格式）"""
        pass

    @abstractmethod
    def get_screen_size(self) -> tuple[int, int]:
        """获取屏幕尺寸"""
        pass

    @abstractmethod
    def start(self) -> None:
        """启动帧源（如建立 socket 连接）"""
        pass

    @abstractmethod
    def stop(self) -> None:
        """停止帧源"""
        pass

    @abstractmethod
    def get_blank_frame(self) -> bytes:
        """获取空白帧（连接失败时返回）"""
        pass

    def get_frame_with_reconnect(self) -> bytes:
        """获取帧（带自动重连）"""
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


class MinicapFrameSource(FrameSource):
    """Android: minicap socket 流"""

    def get_frame(self) -> bytes:
        # 从 minicap socket 读取 JPEG 帧
        pass


class MJPEGFrameSource(FrameSource):
    """iOS: WDA 9100 MJPEG 流"""

    def get_frame(self) -> bytes:
        # 从 WDA MJPEG HTTP 流读取帧
        pass


class WindowsFrameSource(FrameSource):
    """Windows: mss 截屏"""

    def __init__(self, fps: int = 10):
        self.fps = fps  # 支持配置帧率（最高 30）

    def get_frame(self) -> bytes:
        # 使用 mss 截屏，转为 JPEG
        pass


class WebFrameSource(FrameSource):
    """Web: Playwright screenshot（仅用于推流，不支持录屏）"""

    def __init__(self, page):
        self.page = page  # Playwright Page 对象

    def get_frame(self) -> bytes:
        # Playwright page.screenshot(type='jpeg', quality=80)
        screenshot = self.page.screenshot(type="jpeg", quality=80)
        return screenshot
```

### 3.2 ScreenManager 统一管理器

```python
class ScreenManager:
    """统一管理截图/录屏/推流"""

    _frame_source: FrameSource
    _recorder: ScreenRecorder | None
    _streamer: WebSocketStreamer | None
    _capture_thread: Thread | None
    _frame_queue: Queue
    _is_recording: bool = False  # 并发录屏保护
    _recording_lock: Lock        # 录屏互斥锁

    def __init__(self, frame_source: FrameSource):
        self._frame_source = frame_source
        self._frame_queue = Queue(maxsize=30)
        self._recorder = None
        self._streamer = None
        self._is_recording = False
        self._recording_lock = Lock()

    def get_frame(self) -> bytes:
        """获取单帧（供录屏和推流共享）"""
        try:
            return self._frame_queue.get(timeout=1)
        except Empty:
            # 队列空时返回空白帧，避免阻塞
            return self._frame_source.get_blank_frame()

    def start_capture(self) -> None:
        """启动后台截图线程"""
        self._capture_thread = Thread(target=self._capture_loop)
        self._capture_thread.start()

    def _capture_loop(self) -> None:
        """后台截图循环（队列满时丢弃旧帧）"""
        while self._running:
            frame = self._frame_source.get_frame_with_reconnect()
            if self._frame_queue.full():
                # 队列满时丢弃最旧的帧
                try:
                    self._frame_queue.get_nowait()
                except Empty:
                    pass
            self._frame_queue.put(frame, timeout=1)

    def start_recording(self, output_path: str, fps: int = 10,
                        timeout_ms: int = 7200000) -> bool:
        """
        启动录屏

        Args:
            output_path: 输出文件路径
            fps: 帧率
            timeout_ms: 超时时间（毫秒），默认 2 小时

        Returns:
            bool: 是否成功启动（False 表示已有录屏进行中）
        """
        with self._recording_lock:
            if self._is_recording:
                logger.warning("Recording already in progress")
                return False

            timeout_sec = timeout_ms // 1000  # 内部转换为秒
            self._recorder = ScreenRecorder(self, output_path, fps, timeout_sec)
            self._recorder.start()
            self._is_recording = True
            return True

    def stop_recording(self) -> str:
        """停止录屏，返回文件路径"""
        with self._recording_lock:
            if not self._is_recording or not self._recorder:
                return ""

            output_path = self._recorder.stop()
            self._recorder = None
            self._is_recording = False
            return output_path

    def start_streaming(self) -> WebSocketStreamer:
        """启动 WebSocket 推流"""
        self._streamer = WebSocketStreamer(self, fps=10)
        self._streamer.start()
        return self._streamer
```

---

## 四、录屏功能设计

### 4.1 动作参数

**start_recording**:
```json
{
  "action_type": "start_recording",
  "value": "output.mp4",      // 输出文件名（可选，默认自动生成）
  "fps": 10,                  // 帧率（可选，默认 10）
  "timeout": 7200000          // 超时（毫秒，可选，默认 7200000 = 2小时）
}
```

**stop_recording**:
```json
{
  "action_type": "stop_recording"
}
```

### 4.2 ScreenRecorder 录屏器

```python
class ScreenRecorder:
    """FFmpeg 录屏器（队列缓冲 + 双线程）"""

    def __init__(self, screen_manager: ScreenManager,
                 output_path: str, fps: int = 10, timeout_sec: int = 7200):
        """
        Args:
            screen_manager: ScreenManager 实例
            output_path: 输出文件路径
            fps: 帧率
            timeout_sec: 超时时间（秒），由 ScreenManager.start_recording 转换
        """
        self.screen_manager = screen_manager
        self.output_path = output_path
        self.fps = fps
        self.timeout_sec = timeout_sec  # 秒（已从毫秒转换）
        self._stop_event = threading.Event()
        self._timeout_timer: Timer | None = None
        self._ffmpeg_process: subprocess.Popen | None = None

    def start(self) -> None:
        """启动录屏"""
        # 启动超时定时器（忘记 stop 时自动停止）
        self._timeout_timer = Timer(self.timeout_sec, self.stop)
        self._timeout_timer.start()

        # 启动 FFmpeg 写入线程
        self._write_thread = Thread(target=self._write_loop)
        self._write_thread.start()

    def _write_loop(self) -> None:
        """FFmpeg 编码写入线程"""
        width, height = self.screen_manager._frame_source.get_screen_size()

        # FFmpeg 命令：使用 image2pipe 输入 JPEG 序列，避免解码开销
        ffmpeg_cmd = [
            'ffmpeg', '-y',
            '-f', 'image2pipe',           # 输入格式：JPEG 序列
            '-vcodec', 'mjpeg',           # 输入编码：JPEG
            '-r', str(self.fps),          # 输入帧率
            '-i', '-',                    # 从 stdin 读取
            '-c:v', 'libx264',            # 输出编码：H.264
            '-preset', 'ultrafast',
            '-pix_fmt', 'yuv420p',
            '-s', f'{width}x{height}',    # 输出尺寸
            self.output_path
        ]
        self._ffmpeg_process = subprocess.Popen(
            ffmpeg_cmd, stdin=subprocess.PIPE
        )

        while not self._stop_event.is_set():
            try:
                frame = self.screen_manager.get_frame()
                if frame and self._ffmpeg_process:
                    # 直接写入 JPEG 数据，无需解码
                    self._ffmpeg_process.stdin.write(frame)
            except Empty:
                continue

    def stop(self) -> str:
        """停止录屏，返回文件路径"""
        self._stop_event.set()

        if self._timeout_timer:
            self._timeout_timer.cancel()

        if self._ffmpeg_process:
            self._ffmpeg_process.stdin.close()
            self._ffmpeg_process.wait(timeout=5)

        self._write_thread.join(timeout=5)

        return self.output_path
```

### 4.3 平台支持矩阵

| 平台 | 录屏支持 | 默认帧率 | 最高帧率 | 截图来源 |
|------|---------|---------|---------|---------|
| Android | ✓ | 10 fps | 10 fps | minicap socket |
| iOS | ✓ | 10 fps | 10 fps | WDA MJPEG 流 |
| Windows | ✓ | 10 fps | 30 fps | mss 截屏 |
| Web | ✗ | - | - | 通过 Windows 录屏覆盖 |

### 4.4 配置项（worker.yaml）

```yaml
# 录屏配置
recording:
  default_fps: 10           # 默认帧率
  max_timeout_ms: 7200000   # 最大超时（毫秒），2小时
  output_dir: data/recordings  # 输出目录

# WebSocket 推流配置
websocket_streaming:
  default_fps: 10           # 默认推流帧率
  max_connections_per_device: 3  # 单设备最大 WebSocket 连接数
```

---

## 五、WebSocket 屏幕推流

### 5.1 WebSocket 路由

```python
# server.py
from fastapi import WebSocket, WebSocketDisconnect
from worker.config import Config

# 连接计数器
_ws_connections: dict[str, int] = {}  # device_id -> count

@app.websocket("/ws/screen/{device_id}")
async def screen_stream(websocket: WebSocket, device_id: str):
    """实时屏幕推流（10fps）"""

    # 检查连接数限制
    max_conn = Config().get("websocket_streaming.max_connections_per_device", 3)
    current_count = _ws_connections.get(device_id, 0)

    if current_count >= max_conn:
        # 超过限制，拒绝连接（返回 429 Too Many Requests）
        await websocket.close(code=429, reason="Max connections reached")
        return

    await websocket.accept()
    _ws_connections[device_id] = current_count + 1

    screen_manager = get_screen_manager(device_id)
    streamer = screen_manager.start_streaming()

    try:
        while True:
            frame = await streamer.get_frame_async()
            # 发送 JPEG 原始数据
            await websocket.send_bytes(frame)
            await asyncio.sleep(0.1)  # 10fps
    except WebSocketDisconnect:
        pass
    finally:
        # 确保 streamer 停止并减少连接计数
        streamer.stop()
        _ws_connections[device_id] = _ws_connections.get(device_id, 1) - 1
        if _ws_connections[device_id] <= 0:
            del _ws_connections[device_id]
```

### 5.2 WebSocketStreamer 推流器

```python
class WebSocketStreamer:
    """WebSocket 屏幕推流器"""

    def __init__(self, screen_manager: ScreenManager, fps: int = 10):
        self.screen_manager = screen_manager
        self.fps = fps
        self._running = False

    async def get_frame_async(self) -> bytes:
        """异步获取帧（避免阻塞 WebSocket）"""
        return await asyncio.to_thread(self.screen_manager.get_frame)

    def start(self) -> None:
        self._running = True

    def stop(self) -> None:
        self._running = False
```

### 5.3 前端调用示例

```javascript
// 连接 WebSocket
const ws = new WebSocket(`ws://localhost:8088/ws/screen/${deviceId}`);

ws.binaryType = 'arraybuffer';

ws.onmessage = (event) => {
    // 接收 JPEG 原始数据
    const blob = new Blob([event.data], { type: 'image/jpeg' });
    const url = URL.createObjectURL(blob);
    document.getElementById('screen').src = url;
};

ws.onerror = (error) => {
    console.error('WebSocket error:', error);
};
```

### 5.4 多设备并发

- 每个设备独立 WebSocket 连接
- ScreenManager 按设备 ID 管理 FrameSource
- 推流在独立线程，不影响任务执行

---

## 六、pinch 双指缩放手势

### 6.1 动作参数

```json
{
  "action_type": "pinch",
  "value": "in",        // "in" 缩小 / "out" 放大
  "scale": 0.5,         // 缩放比例（可选，默认 0.5）
  "duration": 500       // 持续时间（毫秒，可选，默认 500）
}
```

### 6.2 PlatformManager 抽象接口

```python
# base.py 新增抽象方法
class PlatformManager(ABC):

    @abstractmethod
    def pinch(self, direction: str, scale: float = 0.5,
              duration: int = 500, context: Any = None) -> None:
        """
        双指缩放手势

        Args:
            direction: "in" 缩小 / "out" 放大
            scale: 缩放比例
            duration: 持续时间（毫秒）
            context: 执行上下文
        """
        pass
```

### 6.3 Android 实现

```python
# android.py
class AndroidPlatformManager(PlatformManager):

    def pinch(self, direction: str, scale: float = 0.5,
              duration: int = 500, context: Any = None) -> None:
        device = context or self._device_clients.get(self._current_device)

        # 获取屏幕尺寸
        width, height = self.get_screen_size()
        center_x, center_y = width // 2, height // 2

        # 计算双指位置
        base_distance = 100
        if direction == "in":
            start_distance = base_distance
            end_distance = base_distance * scale
        else:
            start_distance = base_distance * scale
            end_distance = base_distance

        # 使用 uiautomator2 多点触控
        # 或构造 MotionEvent 序列实现双指动画
        self._perform_pinch_gesture(
            device, center_x, center_y,
            start_distance, end_distance, duration
        )
```

### 6.4 iOS 实现

```python
# ios.py
class iOSPlatformManager(PlatformManager):

    def pinch(self, direction: str, scale: float = 0.5,
              duration: int = 500, context: Any = None) -> None:
        client = context or self._device_clients.get(self._current_device)

        width, height = self.get_screen_size()
        center_x, center_y = width // 2, height // 2

        base_distance = 100
        if direction == "in":
            start_distance = base_distance
            end_distance = base_distance * scale
        else:
            start_distance = base_distance * scale
            end_distance = base_distance

        # WDA 多指触控 JSON
        self._perform_wda_pinch(
            client, center_x, center_y,
            start_distance, end_distance, duration
        )
```

### 6.5 Web/Windows 平台

- **Web**: 不支持 pinch（无双指触摸场景）
- **Windows**: 不支持 pinch（无双指触摸场景）

---

## 七、动作注册

### 7.1 新增动作类型

```python
# actions/__init__.py
BASE_SUPPORTED_ACTIONS = {
    # ... 现有动作 ...

    # 新增
    "pinch",                 # 双指缩放（Android/iOS）
    "start_recording",       # 开始录屏
    "stop_recording",        # 停止录屏
}
```

### 7.2 动作处理器注册

```python
# actions/gesture.py
@ActionRegistry.register("pinch")
def handle_pinch(platform: PlatformManager, action: Action) -> ActionResult:
    direction = action.value  # "in" 或 "out"
    scale = action.params.get("scale", 0.5)
    duration = action.params.get("duration", 500)

    try:
        platform.pinch(direction, scale, duration)
        return ActionResult(status=ActionStatus.SUCCESS)
    except Exception as e:
        return ActionResult(status=ActionStatus.FAILED, error=str(e))


# actions/recording.py
@ActionRegistry.register("start_recording")
def handle_start_recording(platform: PlatformManager, action: Action) -> ActionResult:
    # 文件名处理：相对路径与 output_dir 配置结合
    output_dir = Config().get("recording.output_dir", "data/recordings")
    filename = action.value or f"recording_{datetime.now():%Y%m%d_%H%M%S}.mp4"

    # 如果是绝对路径，直接使用；否则拼接 output_dir
    if os.path.isabs(filename):
        output_path = filename
    else:
        output_path = os.path.join(output_dir, filename)

    # 确保输出目录存在
    os.makedirs(output_dir, exist_ok=True)

    fps = action.params.get("fps", 10)
    timeout_ms = action.params.get("timeout", 7200000)  # 外部接口：毫秒

    try:
        screen_manager = get_screen_manager(platform._current_device)
        success = screen_manager.start_recording(output_path, fps, timeout_ms)
        if success:
            return ActionResult(status=ActionStatus.SUCCESS, result={"output": output_path})
        else:
            return ActionResult(status=ActionStatus.FAILED, error="Recording already in progress")
    except Exception as e:
        return ActionResult(status=ActionStatus.FAILED, error=str(e))


@ActionRegistry.register("stop_recording")
def handle_stop_recording(platform: PlatformManager, action: Action) -> ActionResult:
    try:
        screen_manager = get_screen_manager(platform._current_device)
        output_path = screen_manager.stop_recording()
        if output_path:
            return ActionResult(status=ActionStatus.SUCCESS, result={"file": output_path})
        else:
            return ActionResult(status=ActionStatus.FAILED, error="No recording in progress")
    except Exception as e:
        return ActionResult(status=ActionStatus.FAILED, error=str(e))
```

---

## 八、依赖项

### 8.1 新增 Python 包

```toml
# pyproject.toml
dependencies = [
    # ... 现有依赖 ...

    "mss>=9.0.0",           # Windows 截屏（高性能）
    "websockets>=12.0",     # WebSocket 支持（FastAPI 已内置）
]
```

### 8.2 外部依赖

- **FFmpeg**: 录屏编码，需要系统安装或打包带入

---

## 九、测试计划

### 9.1 单元测试

| 测试项 | 测试内容 |
|-------|---------|
| FrameSource | 各平台帧获取正确性 |
| ScreenRecorder | FFmpeg 启动/停止、超时保护 |
| WebSocketStreamer | WebSocket 连接/断开、帧推送 |
| pinch | Android/iOS 双指手势执行 |

### 9.2 集成测试

| 测试场景 | 验证点 |
|---------|-------|
| Android 录屏 | start → 执行动作 → stop → 检查 MP4 文件 |
| iOS 录屏 | 同上 |
| Windows 录屏（30fps） | 高帧率录屏正确性 |
| WebSocket 推流 | 前端连接 → 检查帧显示 → 断开 |
| pinch 缩放 | 地图/图片缩放功能验证 |

---

## 十、实现工作量估算

| 功能模块 | 预估工时 | 复杂度 |
|---------|---------|-------|
| screen/ 模块（FrameSource + Manager） | 2 天 | 中 |
| ScreenRecorder 录屏器 | 1 天 | 低 |
| WebSocketStreamer + 路由 | 1 天 | 低 |
| pinch Android 实现 | 1 天 | 中 |
| pinch iOS 实现 | 1 天 | 中 |
| 动作处理器注册 | 0.5 天 | 低 |
| 测试编写 | 1 天 | 低 |
| **总计** | **7.5 天** | - |

---

## 十一、风险与对策

| 风险 | 影响 | 对策 |
|------|------|------|
| FFmpeg 未安装 | 录屏失败 | 打包时带入 ffmpeg.exe + 启动时检查 PATH |
| minicap 连接中断 | Android 帧源失效 | 自动重连（最多 3 次，间隔 1 秒），失败后 fallback 到 u2 截图 |
| WDA MJPEG 流断开 | iOS 帧源失效 | 自动重连（最多 3 次，间隔 1 秒），失败后返回黑屏帧 |
| WebSocket 连接过多 | 性能下降 | 限制单设备最大连接数（配置项控制，默认 3） |
| FFmpeg 进程僵死 | 录屏文件损坏 | stop 时强制 kill 进程（`terminate()` + `kill()`），设置 `wait(timeout=5)` |
| 并发录屏 | 文件损坏/资源泄漏 | ScreenManager 使用 `_is_recording` 标志 + `_recording_lock` 互斥锁保护 |
| 帧队列满 | 截图阻塞 | `_capture_loop` 队列满时丢弃旧帧（`get_nowait()` + `put`） |
| WebSocket 无认证 | 安全风险 | 后续版本增加 Token 认证或 IP 白名单（当前版本标记为已知风险） |
| 内存泄漏（长时间运行） | 队列积压 | 帧队列限制 maxsize=30，丢弃策略防止无限增长 |

---

**设计完成**