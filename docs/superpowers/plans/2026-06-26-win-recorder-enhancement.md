# Win-Recorder 录屏功能增强实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 增强 win-recorder 录屏功能：默认开启水印、修复分辨率对齐、路径格式支持、停止录制幂等

**Architecture:** 修改现有 autotest 项目的录屏相关代码，参考 test_watermark.py 实现逻辑

**Tech Stack:** Python, win-recorder, mss

---

## 文件修改清单

| 文件 | 修改内容 |
|------|----------|
| `worker/actions/recording.py` | StartRecordingAction 添加 watermark 参数，处理路径逻辑 |
| `worker/screen/recorder.py` | ScreenRecorder 添加 watermark 参数，stop 方法幂等 |
| `worker/screen/manager.py` | start_recording 添加 watermark 参数 |
| `worker/screen/frame_source.py` | WindowsFrameSource.get_frame_bgra() 支持分辨率对��扩展 |

---

### Task 1: 修改 StartRecordingAction 添加 watermark 参数和路径处理

**Files:**
- Modify: `worker/actions/recording.py:21-88`
- Test: `worker/actions/recording.py` (手动测试)

- [ ] **Step 1: 添加 watermark 参数和路径处理逻辑**

在 `StartRecordingAction.execute()` 方法中：

1. 添加 `watermark` 参数读取（默认 True）：
   ```python
   watermark = action.params.get("watermark", True) if action.params else True
   ```

2. 修改路径处理逻辑，支持两种格式：
   ```python
   # 原有的 filename 处理逻辑需要修改
   # 新逻辑：
   # - 如果 action.value 是目录（不带 .mp4 后缀），自动生成文件名
   # - 如果 action.value 是完整文件路径（带 .mp4 后缀），直接使用
   # - 如果没有 action.value，使用默认目录 + 自动生成文件名
   ```

3. 传递 watermark 参数给 screen_manager.start_recording：
   ```python
   success = screen_manager.start_recording(output_path, fps, timeout_ms, audio, monitor, watermark)
   ```

- [ ] **Step 2: 验证修改**

运行现有测试确保没有破坏原有功能：
```bash
cd D:\code\autotest
python -c "from worker.actions.recording import StartRecordingAction; print('Import OK')"
```

---

### Task 2: 修改 ScreenRecorder 添加 watermark 参数

**Files:**
- Modify: `worker/screen/recorder.py:27-120`
- Test: `worker/screen/recorder.py` (手动测试)

- [ ] **Step 1: 添加 watermark 参数到构造函数**

修改 `ScreenRecorder.__init__()`：
```python
def __init__(
    self,
    screen_manager: "ScreenManager",
    output_path: str,
    fps: int = 10,
    timeout_sec: int = 7200,
    audio: bool = False,
    monitor: int = 1,
    watermark: bool = True,  # 新增参数，默认 True
):
```

保存到实例变量：
```python
self.watermark = watermark
```

- [ ] **Step 2: 传递 watermark 给 win_recorder.WinRecorder**

修改 `start()` 方法中的 WinRecorder 创建：
```python
self._win_recorder = win_recorder.WinRecorder(
    output_path=self.output_path,
    fps=self.fps,
    audio=self.audio,
    monitor=self.monitor,
    watermark=self.watermark,  # 新增
)
```

- [ ] **Step 3: 添加 stop 方法幂等处理**

修改 `stop()` 方法，在开始处添加检查：
```python
def stop(self) -> str:
    # 幂等处理：已经停止或从未开始，直接返回
    if self._win_recorder is None:
        logger.info("Recording already stopped or never started")
        return self.output_path

    # 标记停止事件（防止重复调用）
    if self._stop_event.is_set():
        logger.info("Recording stop already called")
        return self.output_path

    self._stop_event.set()
    # ... 其余代码保持不变
```

---

### Task 3: 修改 ScreenManager.start_recording 添加 watermark 参数

**Files:**
- Modify: `worker/screen/manager.py:277-307`
- Test: `worker/screen/manager.py` (手动测试)

- [ ] **Step 1: 添加 watermark 参数到 start_recording 方法**

修改方法签名：
```python
def start_recording(self, output_path: str, fps: int = 10,
                    timeout_ms: int = 7200000, audio: bool = False,
                    monitor: int = 1, watermark: bool = True) -> bool:
```

- [ ] **Step 2: 传递 watermark 给 ScreenRecorder**

修改方法内部：
```python
self._recorder = ScreenRecorder(self, output_path, fps, timeout_sec, audio, monitor, watermark)
```

---

### Task 4: 修改 WindowsFrameSource 支持分辨率对齐扩展

**Files:**
- Modify: `worker/screen/frame_source.py:277-307`
- Test: `worker/screen/frame_source.py` (手动测试)

**核心思路：** 参考 test_watermark.py，需要在���制开始后获取对齐后的分辨率，然后扩展帧数据

- [ ] **Step 1: 在 WindowsFrameSource 中添加对齐尺寸属性**

修改 `WindowsFrameSource.__init__()`：
```python
def __init__(self, fps: int = 10, monitor: int = 1):
    # ... 现有代码 ...
    self._aligned_width: Optional[int] = None
    self._aligned_height: Optional[int] = None
```

- [ ] **Step 2: 添加设置对齐尺寸的方法**

添加新方法：
```python
def set_aligned_size(self, width: int, height: int) -> None:
    """设置对齐后的分辨率（由 ScreenRecorder 调用）。"""
    self._aligned_width = width
    self._aligned_height = height
    logger.info(f"Aligned size set: {width}x{height}")
```

- [ ] **Step 3: 修改 get_frame_bgra() 支持帧扩展**

修改 `get_frame_bgra()` 方法：
```python
def get_frame_bgra(self) -> bytearray:
    # ... 现有截屏代码 ...

    width = screenshot.width
    height = screenshot.height
    rgb_array = numpy.frombuffer(screenshot.rgb, dtype=numpy.uint8).reshape(height, width, 3)

    # RGB -> BGRA
    bgra = numpy.empty((height, width, 4), dtype=numpy.uint8)
    bgra[:, :, 0] = rgb_array[:, :, 2]  # B
    bgra[:, :, 1] = rgb_array[:, :, 1]  # G
    bgra[:, :, 2] = rgb_array[:, :, 0]  # R
    bgra[:, :, 3] = 255  # A

    # 如果设置了对齐尺寸，且需要扩展
    if self._aligned_width and self._aligned_height:
        if width != self._aligned_width or height != self._aligned_height:
            # 创建对齐尺寸的空白 BGRA
            aligned_bgra = numpy.zeros((self._aligned_height, self._aligned_width, 4), dtype=numpy.uint8)
            # 填充原图数据
            aligned_bgra[:height, :width] = bgra
            return bytearray(aligned_bgra.tobytes())

    return bytearray(bgra.tobytes())
```

---

### Task 5: 修改 ScreenRecorder 同步对齐尺寸给 FrameSource

**Files:**
- Modify: `worker/screen/recorder.py:59-82`
- Test: 集成测试

- [ ] **Step 1: 在 start() 方法中同步对齐尺寸**

在 `win_recorder.start()` 调用后，获取对齐分辨率并设置给 frame_source：
```python
def start(self) -> None:
    # ... 现有代码 ...
    
    # 启动 win-recorder
    self._win_recorder = win_recorder.WinRecorder(...)
    self._win_recorder.start()

    # 获取对齐后的分辨率
    aligned_width = self._win_recorder.width
    aligned_height = self._win_recorder.height

    # 同步给 FrameSource
    self.screen_manager.set_frame_aligned_size(aligned_width, aligned_height)

    # ... 其余代码 ...
```

- [ ] **Step 2: 在 ScreenManager 中添加 set_frame_aligned_size 方法**

修改 `worker/screen/manager.py`：
```python
def set_frame_aligned_size(self, width: int, height: int) -> None:
    """设置帧对齐尺寸（由 ScreenRecorder 调用）。"""
    if self._frame_source:
        self._frame_source.set_aligned_size(width, height)
```

---

### Task 6: 整体集成测试

**Files:**
- Test: 手动测试 start_recording + stop_recording

- [ ] **Step 1: 测试水印默认开启**

发送 start_recording 请求（不带 watermark 参数），验证水印开启

- [ ] **Step 2: 测试 watermark 参数可关闭**

发送 start_recording 请求（watermark=False），验证水印关闭

- [ ] **Step 3: 测试路径格式**

- 测试 `d:\recorder`（目录）→ 应自动生成 `recording_YYYYMMDD_HHMMSS.mp4`
- 测试 `d:\recorder\test.mp4`（文件）→ 应直接使用该路径

- [ ] **Step 4: 测试停止录制幂等**

连续调用两次 stop_recording，第二次不应报错

- [ ] **Step 5: 测试录制视频可播放**

检查生成的 MP4 文件分辨率是否对齐 16 的倍数，验证视频可播放

---

## 预期产出

1. 录屏默认带时间水印
2. 可通过 watermark 参数关闭水印
3. 支持目录/文件两种路径格式
4. 停止录制可多次调用
5. 生成的 MP4 文件可正常播放