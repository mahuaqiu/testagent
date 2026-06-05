# WIN/iOS 推流实现实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 为 Worker 添加 H.264 和 MJPEG 推流支持，前端自动检测帧类型并渲染

**Architecture:** 分三阶段实现：
1. win-recorder 新增流式 H.264 编码接口
2. autotest Worker 推流改造（codec 参数、Windows H.264、iOS MJPEG 透传）
3. zq-platform 前端改造（帧类型检测、H.264/MJPEG 渲染）

**Tech Stack:**
- win-recorder: Rust + PyO3 + Media Foundation
- autotest: Python + FastAPI + mss + WebSocket
- zq-platform: TypeScript + Vue 3 + MediaSource Extensions + WASM

---

## 文件结构映射

### 阶段 1: win-recorder (Rust)

```
/Users/ma/Documents/win-recorder/
├── src/
│   ├── lib.rs              # [修改] 新增 StreamingRecorder 导出
│   ├── recorder.rs         # [修改] 新增 StreamingEncoder 类
│   ├── streaming_encoder.rs # [新建] 流式编码器核心实现
│   └── mf_writer.rs        # [修改] 新增内存输出支持
├── tests/
│   └── test_streaming.py   # [新建] 流式编码测试
└── Cargo.toml              # [修改] 可能需要新依赖
```

### 阶段 2: autotest (Python)

```
/Users/ma/Documents/autotest/
├── worker/
│   ├── server.py           # [修改] codec 参数 + 帧类型前缀
│   ├── screen/
│   │   ├── frame_source.py # [修改] H.264 编码器集成
│   │   ├── streamer.py     # [修改] 支持 H.264/MJPEG 透传
│   │   └── manager.py      # [修改] 流式编码器管理
│   └── config.py           # [修改] 新增流式编码配置
├── config/
│   └── worker.yaml         # [修改] 流式编码配置项
└── tests/
    └── test_streaming.py   # [新建] 推流功能测试
```

### 阶段 3: zq-platform (前端)

```
/Users/ma/Documents/zq-platform/
├── web/apps/web-ele/src/views/device-debug/
│   ├── hooks/
│   │   ├── useWebSocket.ts    # [修改] 帧类型检测
│   │   ├── useH264Decoder.ts  # [新建] H.264 解码渲染
│   │   └── useMJPEGRenderer.ts # [新建] MJPEG 渲染
│   ├── components/
│   │   └── ScreenDisplay.vue  # [修改] 支持视频流渲染
│   └── utils/
│       └── stream.ts          # [新建] 流处理工具
└── backend-fastapi/
    └── (无改动)
```

---

## 阶段 1: win-recorder 流式编码

### Task 1: 创建 StreamingEncoder Rust 核心实现

**Files:**
- Create: `/Users/ma/Documents/win-recorder/src/streaming_encoder.rs`
- Modify: `/Users/ma/Documents/win-recorder/src/lib.rs`
- Test: `/Users/ma/Documents/win-recorder/tests/test_streaming.py`

- [ ] **Step 1: 创建流式编码器模块**

新建文件 `streaming_encoder.rs`，实现：

```rust
use windows::core::PCWSTR;
use windows::Media::Foundation::{IMFByteStream, IMFMediaBuffer, IMFSample};
use std::io::Cursor;

/// 流式编码器输出回调
pub trait StreamCallback: Send {
    fn on_encoded_frame(&mut self, frame_type: u8, data: &[u8], timestamp: u64);
}

/// 流式 H.264 编码器
pub struct StreamingEncoder {
    // 内部状态字段
}

impl StreamingEncoder {
    /// 创建新编码器
    pub fn new(width: u32, height: u32, fps: u32, bitrate: u32) -> Result<Self, RecorderError>;

    /// 启动编码器，返回 SPS/PPS
    pub fn start(&mut self) -> Result<(Vec<u8>, Vec<u8>), RecorderError>;

    /// 编码单帧 BGRA
    pub fn encode_frame(&mut self, bgra_data: &[u8]) -> Result<Option<EncodedFrame>, RecorderError>;

    /// 停止编码器
    pub fn stop(&mut self) -> Result<(), RecorderError>;
}

/// 编码后的帧数据
pub struct EncodedFrame {
    pub frame_type: FrameType,
    pub data: Vec<u8>,
    pub timestamp: u64,
}

#[derive(Clone, Copy)]
pub enum FrameType {
    SpsPps = 0x01,
    Idr = 0x02,
    P = 0x03,
}
```

- [ ] **Step 2: 修改 lib.rs 导出 StreamingEncoder**

```rust
// src/lib.rs 新增
use crate::streaming_encoder::{StreamingEncoder, EncodedFrame, FrameType};

#[pyclass]
pub struct PyStreamingEncoder {
    encoder: StreamingEncoder,
}

#[pymethods]
impl PyStreamingEncoder {
    #[new]
    fn new(fps: u32, bitrate: u32, monitor: u32) -> Result<Self, RecorderError> {
        let (width, height) = D3D11TextureManager::detect_monitor(monitor)?;
        let encoder = StreamingEncoder::new(width, height, fps, bitrate)?;
        Ok(Self { encoder })
    }

    fn start(&mut self) -> Result<Py<PyDict>, RecorderError> {
        let (sps, pps) = self.encoder.start()?;
        // 返回 Python dict
    }

    fn encode_frame(&mut self, frame_data: &[u8]) -> Result<Option<Vec<u8>>, RecorderError> {
        // 调用 encoder.encode_frame，返回带帧类型前缀的数据
    }

    fn stop(&mut self) -> Result<(), RecorderError> {
        self.encoder.stop()
    }
}

// 模块导出更新
pub fn register_module(py: Python, m: &PyModule) -> PyResult<()> {
    m.add_class::<PyStreamingEncoder>()?;
    // ... 其他导出
}
```

- [ ] **Step 3: 运行测试验证编译**

```bash
cd /Users/ma/Documents/win-recorder
cargo build
```

Expected: BUILD SUCCESS

- [ ] **Step 4: 创建 Python 集成测试**

```python
# tests/test_streaming.py
import win_recorder

def test_streaming_encoder_init():
    encoder = win_recorder.StreamingEncoder(fps=10, bitrate=2000000, monitor=1)
    assert encoder is not None

def test_streaming_encoder_start():
    encoder = win_recorder.StreamingEncoder(fps=10, bitrate=2000000, monitor=1)
    info = encoder.start()
    assert "width" in info
    assert "height" in info
    assert "sps" in info
    assert "pps" in info

def test_streaming_encode_frame():
    encoder = win_recorder.StreamingEncoder(fps=10, bitrate=2000000, monitor=1)
    encoder.start()
    # 模拟 BGRA 数据
    bgra_data = bytearray(1920 * 1080 * 4)
    frame = encoder.encode_frame(bgra_data)
    assert frame is not None
    assert len(frame) > 0

def test_streaming_encoder_stop():
    encoder = win_recorder.StreamingEncoder(fps=10, bitrate=2000000, monitor=1)
    encoder.start()
    encoder.stop()
```

Run: `python -m pytest tests/test_streaming.py -v`

- [ ] **Step 5: 提交**

```bash
git add src/streaming_encoder.rs src/lib.rs tests/test_streaming.py
git commit -m "feat(win-recorder): add StreamingEncoder for H.264 streaming"
```

---

### Task 2: 实现内存输出 IMFByteStream

**Files:**
- Modify: `/Users/ma/Documents/win-recorder/src/streaming_encoder.rs`
- Modify: `/Users/ma/Documents/win-recorder/src/mf_writer.rs`

- [ ] **Step 1: 新增内存缓冲区管理**

在 `streaming_encoder.rs` 中添加：

```rust
/// 内存输出流
pub struct MemoryByteStream {
    buffer: Vec<u8>,
    position: usize,
}

impl MemoryByteStream {
    pub fn new() -> Self;
    pub fn write(&mut self, data: &[u8]);
    pub fn read(&mut self, size: usize) -> Option<Vec<u8>>;
    pub fn clear(&mut self);
}
```

- [ ] **Step 2: 实现 IMFByteStream 接口**

使用 Windows API 实现自定义内存流：

```rust
use windows::Media::Foundation::{IMFByteStream, IMFMediaBuffer};

// 实现 COM 接口
unsafe impl IMFByteStream for MemoryByteStream {
    // 实现所需方法
}
```

注意：完整的 IMFByteStream 实现较复杂，可以考虑简化方案：
- 使用 `IMFStreamSink` 配合自定义缓冲区
- 或者使用内存映射文件

简化方案（推荐）：不实现完整的 IMFByteStream，而是：
1. 复用现有 SinkWriter 的编码逻辑
2. 在编码后手动提取 NAL 单元
3. 通过回调返回编码数据

- [ ] **Step 3: 提交**

```bash
git commit -m "feat(win-recorder): add memory output support for streaming"
```

---

## 阶段 2: autotest Worker 推流改造

### Task 3: 添加 codec 参数支持

**Files:**
- Modify: `/Users/ma/Documents/autotest/worker/server.py:872-986`
- Test: `tests/test_streaming.py`

- [ ] **Step 1: 读取当前 server.py WebSocket 实现**

确认 screen_stream 函数的参数处理。

- [ ] **Step 2: 修改 WebSocket 端点添加 codec 参数**

```python
# worker/server.py

@app.websocket("/ws/screen/{platform}/{device_id}")
async def screen_stream(
    websocket: WebSocket,
    platform: str,
    device_id: str,
    monitor: int = 1,
    codec: str = "jpeg"  # 新增参数
):
    """实时屏幕推流

    Args:
        platform: 设备平台类型 (ios, android, windows, mac, web)
        device_id: 设备标识符
        monitor: 屏幕索引（mss索引）
        codec: 推流编码格式 (jpeg/h264/mjpeg)
    """
    # 验证 codec 参数
    valid_codecs = ["jpeg", "h264", "mjpeg"]
    if codec not in valid_codecs:
        await websocket.close(code=1008, reason=f"Invalid codec: {codec}")
        return

    # 根据 codec 选择帧源
    # ... 后续实现
```

- [ ] **Step 3: 添加 ERROR 日志的降级处理**

```python
# 在 screen_stream 函数中添加
import logging
logger = logging.getLogger(__name__)

# 编码器初始化失败时的降级处理
try:
    frame_source = _create_frame_source(platform, device_id, monitor, codec)
except Exception as e:
    logger.error(f"Failed to create frame source with codec={codec}: {e}, falling back to jpeg")
    codec = "jpeg"  # 降级到 JPEG
    frame_source = _create_frame_source(platform, device_id, monitor, codec)
```

- [ ] **Step 4: 测试**

启动 Worker 并测试：
```bash
# 测试 codec 参数
wscat -c "ws://localhost:8080/ws/screen/windows/test?codec=h264"
wscat -c "ws://localhost:8080/ws/screen/windows/test?codec=mjpeg"
wscat -c "ws://localhost:8080/ws/screen/windows/test?codec=jpeg"
```

- [ ] **Step 5: 提交**

```bash
git commit -m "feat(worker): add codec parameter to WebSocket streaming endpoint"
```

---

### Task 4: Windows H.264 推流集成

**Files:**
- Modify: `/Users/ma/Documents/autotest/worker/screen/frame_source.py:214-330`
- Create: `/Users/ma/Documents/autotest/worker/screen/h264_streamer.py`

- [ ] **Step 1: 创建 H264Streamer 类**

新建文件 `worker/screen/h264_streamer.py`：

```python
import logging
from typing import Optional

logger = logging.getLogger(__name__)


class H264Streamer:
    """H.264 流式编码器"""

    def __init__(self, frame_source, fps: int = 10, bitrate: int = 2000000):
        self.frame_source = frame_source
        self.fps = fps
        self.bitrate = bitrate
        self._encoder = None
        self._sps_pps_sent = False

    def start(self):
        """启动 H.264 编码器"""
        try:
            import win_recorder
            self._encoder = win_recorder.StreamingEncoder(
                fps=self.fps,
                bitrate=self.bitrate,
                monitor=self.frame_source.monitor
            )
            info = self._encoder.start()
            logger.info(f"H264 encoder started: {info}")
            self._sps_pps_sent = False
            return info
        except Exception as e:
            logger.error(f"Failed to start H264 encoder: {e}")
            raise

    def get_frame(self) -> Optional[bytes]:
        """获取编码帧

        Returns:
            bytes: 格式 [1字节帧类型][N字节数据]
                   0x01=SPS/PPS, 0x02=IDR, 0x03=P帧
            None: 编码器未就绪
        """
        if not self._encoder:
            return None

        # 获取 BGRA 帧
        bgra_frame = self.frame_source.get_frame_bgra()
        if not bgra_frame:
            return None

        # 编码
        try:
            encoded = self._encoder.encode_frame(bytes(bgra_frame))
            if not encoded:
                return None

            # 解析帧类型并添加前缀
            # encoded 格式: [4字节长度][NAL单元数据]...
            return self._parse_encoded_data(encoded)

        except Exception as e:
            logger.error(f"Failed to encode frame: {e}")
            return None

    def _parse_encoded_data(self, encoded: bytes) -> bytes:
        """解析编码数据，添加帧类型前缀"""
        # 解析编码输出，提取 NAL 单元并添加类型前缀
        # 返回格式: [0x02][NAL数据] 或 [0x03][NAL数据]
        pass

    def stop(self):
        """停止编码器"""
        if self._encoder:
            self._encoder.stop()
            self._encoder = None
            logger.info("H264 encoder stopped")
```

- [ ] **Step 2: 修改 WindowsFrameSource 支持 H.264**

```python
# worker/screen/frame_source.py

class WindowsFrameSource(FrameSource):
    # ... 现有代码 ...

    def get_frame_encoded(self, codec: str = "jpeg") -> bytes:
        """获取编码帧（支持 H.264）"""
        if codec == "h264":
            if not hasattr(self, '_h264_streamer'):
                from worker.screen.h264_streamer import H264Streamer
                self._h264_streamer = H264Streamer(self, fps=self.fps)
                self._h264_streamer.start()

            frame = self._h264_streamer.get_frame()
            if frame:
                return frame

            # H.264 失败，降级到 JPEG
            logger.error("H264 encoding failed, falling back to JPEG")
            return self.get_frame()

        elif codec == "mjpeg":
            # Windows 不支持 MJPEG
            raise ValueError("Windows does not support MJPEG codec")

        else:
            return self.get_frame()

    def stop(self) -> None:
        """释放资源"""
        if hasattr(self, '_h264_streamer'):
            self._h264_streamer.stop()
            self._h264_streamer = None
        # ... 现有代码 ...
```

- [ ] **Step 3: 修改 server.py 使用 get_frame_encoded**

```python
# worker/server.py - screen_stream 函数中
# 获取帧
if codec == "h264":
    frame = frame_source.get_frame_encoded("h264")
else:
    frame = frame_source.get_frame()

# 发送帧
await websocket.send_bytes(frame)
```

- [ ] **Step 4: 测试 H.264 推流**

```bash
# 启动 Worker
python -m worker.main

# 测试连接
wscat -c "ws://localhost:8080/ws/screen/windows/desktop?codec=h264"
# 应该收到 H.264 流数据
```

- [ ] **Step 5: 提交**

```bash
git add worker/screen/h264_streamer.py worker/screen/frame_source.py worker/server.py
git commit -m "feat(worker): add H.264 streaming support for Windows"
```

---

### Task 5: iOS MJPEG 透传

**Files:**
- Modify: `/Users/ma/Documents/autotest/worker/screen/frame_source.py:119-211`
- Test: `tests/test_mjpeg_proxy.py`

- [ ] **Step 1: 创建 MJPEG 透传类**

新建文件 `worker/screen/mjpeg_proxy.py`：

```python
import asyncio
import logging
import threading
from typing import Optional

import requests

logger = logging.getLogger(__name__)


class MJPEGProxy:
    """MJPEG HTTP→WebSocket 代理"""

    def __init__(self, host: str, port: int = 9100):
        self.host = host
        self.port = port
        self._response = None
        self._iterator = None
        self._running = False
        self._thread = None

    def start(self):
        """启动 MJPEG 流连接"""
        mjpeg_url = f"http://{self.host}:{self.port}"
        try:
            self._response = requests.get(mjpeg_url, stream=True, timeout=30)
            self._iterator = self._response.iter_content(chunk_size=8192)
            self._running = True
            logger.info(f"MJPEG proxy started: {mjpeg_url}")
        except Exception as e:
            logger.error(f"Failed to start MJPEG proxy: {e}")
            raise

    async def proxy_to_websocket(self, websocket):
        """透传到 WebSocket"""
        try:
            while self._running:
                try:
                    chunk = next(self._iterator)
                    await websocket.send_bytes(chunk)
                except StopIteration:
                    # 流结束，重新连接
                    logger.warning("MJPEG stream ended, reconnecting...")
                    self.start()
                except Exception as e:
                    logger.error(f"MJPEG proxy error: {e}")
                    break
        finally:
            self.stop()

    def stop(self):
        """停止透传"""
        self._running = False
        if self._response:
            self._response.close()
            self._response = None
        logger.info("MJPEG proxy stopped")
```

- [ ] **Step 2: 修改 MJPEGFrameSource 支持透传**

```python
# worker/screen/frame_source.py

class MJPEGFrameSource(FrameSource):
    # ... 现有代码 ...

    def start_mjpeg_proxy(self) -> "MJPEGProxy":
        """启动 MJPEG 透传"""
        from worker.screen.mjpeg_proxy import MJPEGProxy

        # 从 wda_client 获取主机地址
        host_with_port = self.wda_client.base_url.split('/')[2]
        host = host_with_port.split(':')[0]

        proxy = MJPEGProxy(host=host, port=9100)
        proxy.start()
        return proxy
```

- [ ] **Step 3: 修改 server.py 使用 MJPEG 透传**

```python
# worker/server.py - screen_stream 函数中

if platform == "ios" and codec == "mjpeg":
    # 使用 MJPEG 透传
    mjpeg_proxy = frame_source.start_mjpeg_proxy()
    await mjpeg_proxy.proxy_to_websocket(websocket)
else:
    # 使用现有逻辑
    while streamer.is_running():
        frame = await streamer.get_frame_async()
        await websocket.send_bytes(frame)
        await asyncio.sleep(1.0 / streaming_fps)
```

- [ ] **Step 4: 测试 iOS MJPEG 透传**

```bash
# 确保 iOS 设备已连接
# 测试连接
wscat -c "ws://localhost:8080/ws/screen/ios/{udid}?codec=mjpeg"
# 应该收到透传的 MJPEG 数据
```

- [ ] **Step 5: 提交**

```bash
git add worker/screen/mjpeg_proxy.py worker/screen/frame_source.py worker/server.py
git commit -m "feat(worker): add MJPEG proxy for iOS streaming"
```

---

### Task 6: 配置项添加

**Files:**
- Modify: `/Users/ma/Documents/autotest/worker/config.py`
- Modify: `/Users/ma/Documents/autotest/config/worker.yaml`

- [ ] **Step 1: 添加流式编码配置**

```python
# worker/config.py - WorkerConfig

@dataclass
class WorkerConfig:
    # ... 现有字段 ...

    # 流式编码配置
    streaming_codec: str = "jpeg"  # 默认编码格式
    streaming_bitrate: int = 2000000  # H.264 码率
    streaming_fps: int = 10  # ��率（已有）

    @classmethod
    def from_yaml(cls, path: str) -> "WorkerConfig":
        # ... 现有加载逻辑 ...
        streaming_cfg = data.get("streaming", {})

        return cls(
            # ... 现有参数 ...
            streaming_codec=streaming_cfg.get("codec", "jpeg"),
            streaming_bitrate=streaming_cfg.get("bitrate", 2000000),
            streaming_fps=streaming_cfg.get("fps", 10),
        )
```

- [ ] **Step 2: 更新 worker.yaml**

```yaml
# config/worker.yaml

# Streaming Settings
streaming:
  codec: jpeg           # 默认: jpeg (可选: h264, mjpeg)
  fps: 10               # 帧率
  bitrate: 2000000      # H.264 码率 (2Mbps)
```

- [ ] **Step 3: 提交**

```bash
git commit -m "feat(config): add streaming codec configuration"
```

---

## 阶段 3: zq-platform 前端改造

### Task 7: 帧类型检测

**Files:**
- Create: `/Users/ma/Documents/zq-platform/web/apps/web-ele/src/views/device-debug/utils/stream.ts`
- Modify: `/Users/ma/Documents/zq-platform/web/apps/web-ele/src/views/device-debug/hooks/useWebSocket.ts`

- [ ] **Step 1: 创建帧类型检测工具**

新建文件 `utils/stream.ts`：

```typescript
// 帧类型枚举
export enum FrameType {
  Unknown = 'unknown',
  JPEG = 'jpeg',
  MJPEG = 'mjpeg',
  H264 = 'h264',
}

/**
 * 检测帧数据类型
 * @param data WebSocket 接收的二进制数据
 */
export function detectFrameType(data: ArrayBuffer): FrameType {
  if (!data || data.byteLength < 4) {
    return FrameType.Unknown;
  }

  const view = new DataView(data);

  // 优先检测 H.264 (带帧类型前缀: 0x01=SPS/PPS, 0x02=IDR, 0x03=P)
  const firstByte = view.getUint8(0);
  if (firstByte >= 0x01 && firstByte <= 0x03) {
    return FrameType.H264;
  }

  // 检测 JPEG/MJPEG 魔数: FFD8
  const magic = view.getUint16(0);
  if (magic === 0xFFD8) {
    // 检测是否为 MJPEG (多个 FFD8 连在一起)
    if (data.byteLength >= 6 && view.getUint8(2) === 0xFF) {
      return FrameType.MJPEG;
    }
    return FrameType.JPEG;
  }

  // H.264 SPS (NAL unit type = 7)
  if (magic === 0x0001 && view.getUint8(4) === 0x07) {
    return FrameType.H264;
  }

  return FrameType.Unknown;
}

/**
 * 从 H.264 数据中提取 NAL 单元
 */
export function extractNalUnit(data: ArrayBuffer): { type: number; data: Uint8Array } | null {
  const view = new DataView(data);
  if (data.byteLength < 5) return null;

  const frameType = view.getUint8(0);
  const nalData = new Uint8Array(data, 1);

  return { type: frameType, data: nalData };
}
```

- [ ] **Step 2: 修改 useWebSocket.ts 集成帧检测**

```typescript
// hooks/useWebSocket.ts

import { detectFrameType, FrameType } from '../utils/stream';

// 在 WebSocket 消息处理中添加
ws.onmessage = (event) => {
  const arrayBuffer = event.data as ArrayBuffer;
  const frameType = detectFrameType(arrayBuffer);

  switch (frameType) {
    case FrameType.H264:
      // 发送到 H.264 解码器
      h264Decoder.appendFrame(arrayBuffer);
      break;
    case FrameType.MJPEG:
      // 发送到 MJPEG 渲染器
      mjpegRenderer.render(arrayBuffer);
      break;
    case FrameType.JPEG:
    default:
      // 使用现有的 Blob URL 方式
      const blob = new Blob([arrayBuffer], { type: 'image/jpeg' });
      const url = URL.createObjectURL(blob);
      screenshotBase64.value = url;
      break;
  }
};
```

- [ ] **Step 3: 测试帧类型检测**

```bash
# 启动前端开发服务器
cd /Users/ma/Documents/zq-platform/web
pnpm dev

# 连接不同 codec 的推流，验证帧类型检测正确
```

- [ ] **Step 4: 提交**

```bash
# 在 zq-platform 项目中
git add web/apps/web-ele/src/views/device-debug/utils/stream.ts
git add web/apps/web-ele/src/views/device-debug/hooks/useWebSocket.ts
git commit -m "feat(web): add frame type detection for streaming"
```

---

### Task 8: H.264 解码渲染

**Files:**
- Create: `/Users/ma/Documents/zq-platform/web/apps/web-ele/src/views/device-debug/hooks/useH264Decoder.ts`
- Modify: `/Users/ma/Documents/zq-platform/web/apps/web-ele/src/views/device-debug/components/ScreenDisplay.vue`

- [ ] **Step 1: 创建 H.264 解码器 Hook**

新建文件 `hooks/useH264Decoder.ts`：

```typescript
import { ref, onUnmounted } from 'vue';

export interface H264DecoderOptions {
  width: number;
  height: number;
  onReady?: () => void;
  onError?: (error: Error) => void;
}

export function useH264Decoder(options: H264DecoderOptions) {
  const videoRef = ref<HTMLVideoElement | null>(null);
  const mediaSource = ref<MediaSource | null>(null);
  const sourceBuffer = ref<SourceBuffer | null>(null);
  const isReady = ref(false);

  // WASM 解码器 fallback
  const wasmDecoder = ref<any>(null);

  const initMSE = (sps: Uint8Array, pps: Uint8Array) => {
    if (!MediaSource.isTypeSupported(`video/mp4; codecs="avc1.42001e, mp4a.40.2"`)) {
      // MSE 不支持，fallback 到 WASM
      initWASM();
      return;
    }

    mediaSource.value = new MediaSource();
    videoRef.value!.src = URL.createObjectURL(mediaSource.value);

    mediaSource.value.addEventListener('sourceopen', () => {
      try {
        sourceBuffer.value = mediaSource.value!.addSourceBuffer(
          `video/mp4; codecs="avc1.42001e, mp4a.40.2"`
        );
        isReady.value = true;
        options.onReady?.();
      } catch (e) {
        console.error('MSE init error:', e);
        initWASM();
      }
    });
  };

  const initWASM = async () => {
    try {
      // 动态加载 WASM 解码器 (jsmpeg 或 h264wasm)
      const module = await import('h264wasm-decoder');
      wasmDecoder.value = new module.H264Decoder(options.width, options.height);
      await wasmDecoder.value.init();
      isReady.value = true;
      options.onReady?.();
    } catch (e) {
      console.error('WASM decoder init error:', e);
      options.onError?.(e as Error);
    }
  };

  const appendFrame = (data: ArrayBuffer) => {
    if (wasmDecoder.value) {
      // WASM 解码
      const frame = wasmDecoder.value.decode(data);
      if (frame) {
        // 渲染到 canvas
      }
    } else if (sourceBuffer.value) {
      // MSE 解码
      // 需要将 H.264 数据转换为 MP4 片段
      // 简化处理：直接将数据写入 SourceBuffer
      if (!sourceBuffer.value.updating) {
        sourceBuffer.value.appendBuffer(data);
      }
    }
  };

  const dispose = () => {
    mediaSource.value?.endOfStream();
    mediaSource.value = null;
    sourceBuffer.value = null;
    wasmDecoder.value?.delete();
    isReady.value = false;
  };

  onUnmounted(dispose);

  return {
    videoRef,
    isReady,
    initMSE,
    initWASM,
    appendFrame,
    dispose,
  };
}
```

- [ ] **Step 2: 修改 ScreenDisplay.vue 支持视频渲染**

```vue
<script setup lang="ts">
import { ref, onMounted } from 'vue';
import { useH264Decoder } from '../hooks/useH264Decoder';
import { useMJPEGRenderer } from '../hooks/useMJPEGRenderer';

const videoRef = ref<HTMLVideoElement | null>(null);
const canvasRef = ref<HTMLCanvasElement | null>(null);

// 初始化解码器
const { initMSE, initWASM, appendFrame: appendH264Frame } = useH264Decoder({
  width: 1920,
  height: 1080,
  onReady: () => console.log('H264 decoder ready'),
  onError: (e) => console.error('H264 error:', e),
});

const { render: renderMJPEG } = useMJPEGRenderer(canvasRef);

// 根据帧类型选择渲染方式
const renderFrame = (data: ArrayBuffer, frameType: string) => {
  switch (frameType) {
    case 'h264':
      appendH264Frame(data);
      break;
    case 'mjpeg':
      renderMJPEG(data);
      break;
    case 'jpeg':
    default:
      // 使用 img 渲染
      const url = URL.createObjectURL(new Blob([data], { type: 'image/jpeg' }));
      screenshotUrl.value = url;
      break;
  }
};

defineExpose({ renderFrame });
</script>

<template>
  <div class="screen-container">
    <!-- 视频元素用于 H.264 MSE 渲染 -->
    <video ref="videoRef" style="display: none;"></video>
    <!-- Canvas 用于 MJPEG/WASM 渲染 -->
    <canvas ref="canvasRef"></canvas>
    <!-- img 用于 JPEG -->
    <img v-if="screenshotUrl" :src="screenshotUrl" />
  </div>
</template>
```

- [ ] **Step 3: 测试 H.264 渲染**

```bash
# 启动前端
cd /Users/ma/Documents/zq-platform/web
pnpm dev

# 连接 h264 推流，验证视频播放
```

- [ ] **Step 4: 提交**

```bash
git add web/apps/web-ele/src/views/device-debug/hooks/useH264Decoder.ts
git add web/apps/web-ele/src/views/device-debug/components/ScreenDisplay.vue
git commit -m "feat(web): add H.264 decoder with MSE and WASM fallback"
```

---

### Task 9: MJPEG 渲染

**Files:**
- Create: `/Users/ma/Documents/zq-platform/web/apps/web-ele/src/views/device-debug/hooks/useMJPEGRenderer.ts`
- Test: 本地测试

- [ ] **Step 1: 创建 MJPEG 渲染器 Hook**

新建文件 `hooks/useMJPEGRenderer.ts`：

```typescript
import { ref, onUnmounted } from 'vue';

export function useMJPEGRenderer(canvasRef: any) {
  const ctx = ref<CanvasRenderingContext2D | null>(null);
  const imageBitmap = ref<ImageBitmap | null>(null);

  const init = () => {
    if (canvasRef.value) {
      ctx.value = canvasRef.value.getContext('2d');
    }
  };

  const render = async (data: ArrayBuffer) => {
    if (!ctx.value) return;

    try {
      // 创建 Blob URL
      const blob = new Blob([data], { type: 'image/jpeg' });
      const url = URL.createObjectURL(blob);

      // 加载图片
      const img = new Image();
      img.onload = () => {
        // 绘制到 canvas
        ctx.value!.drawImage(img, 0, 0);
        // 释放资源
        URL.revokeObjectURL(url);
        img.remove();
      };
      img.onerror = () => {
        URL.revokeObjectURL(url);
      };
      img.src = url;
    } catch (e) {
      console.error('MJPEG render error:', e);
    }
  };

  onUnmounted(() => {
    imageBitmap.value?.close();
  });

  return {
    init,
    render,
  };
}
```

- [ ] **Step 2: 集成到 ScreenDisplay**

在 Step 8 中已包含 MJPEG 渲染支持。

- [ ] **Step 3: 测试**

```bash
# 连接 mjpeg 推流，验证 canvas 渲染
```

- [ ] **Step 4: 提交**

```bash
git add web/apps/web-ele/src/views/device-debug/hooks/useMJPEGRenderer.ts
git commit -m "feat(web): add MJPEG renderer for iOS streaming"
```

---

## 实施检查点

### 阶段 1 检查点
- [ ] win-recorder StreamingEncoder 编译通过
- [ ] Python 测试用例全部通过

### 阶段 2 检查点
- [ ] Worker WebSocket 支持 ?codec= 参数
- [ ] Windows H.264 推流正常工作
- [ ] iOS MJPEG 透传正常工作
- [ ] 降级到 JPEG 时打印 ERROR 日志

### 阶段 3 检查点
- [ ] 帧类型检测正确识别 H.264/MJPEG/JPEG
- [ ] H.264 在 Chrome/Firefox 正常播放
- [ ] H.264 在 Safari 降级到 WASM 播放
- [ ] iOS MJPEG 正常渲染

---

## 总结

实施计划包含 **9 个 Task**，分 3 个阶段：

| 阶段 | Task 数 | 核心工作 |
|------|---------|----------|
| 1. win-recorder | 2 | 流式 H.264 编码器 |
| 2. autotest | 4 | Worker 推流改造 |
| 3. zq-platform | 3 | 前端渲染改造 |

每个 Task 都包含具体的文件路径、代码示例和测试命令。