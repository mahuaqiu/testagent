# WIN/iOS 推流实现设计

## 背景

当前 Worker 的 WebSocket 推流存在以下问题：

1. **Windows**: 使用 mss 截图 + JPEG 编码，带宽消耗大（8-20 Mbps）
2. **iOS**: 解析 WDA MJPEG 流提取单帧 JPEG，效率低

目标：支持 H.264 和 MJPEG 两种推流协议，适配到前端。

## 架构概述

```
┌──────────────────────────────────────────────────────────────────┐
│                        Worker (autotest)                         │
├────────────────────────┬─────────────────────────────────────────┤
│  Windows               │  iOS                                     │
│  - win-recorder 新增   │  - WDA 9100 HTTP MJPEG                   │
│    流式 H.264 编码     │  - HTTP→WS 代理透传                      │
│  - NAL 单元输出        │  - 直接透传二进制                        │
├────────────────────────┴─────────────────────────────────────────┤
│  帧类型协商: ?codec=h264|mjpeg|jpeg                               │
└──────────────────────────────────────────────────────────────────┘
                                    ↓ WebSocket
┌──────────────────────────────────────────────────────────────────┐
│                     zq-platform 前端                              │
├──────────────────────────────────────────────────────────────────┤
│  自动检测帧类型:                                                   │
│  - H.264 (0x01/0x02/0x03) → MSE 解码 / WASM fallback             │
│  - MJPEG (multipart)   → canvas 渲染                              │
│  - JPEG (单帧)         → Blob URL + img                          │
└──────────────────────────────────────────────────────────────────┘
```

## WebSocket 帧格式

### H.264 流格式

```
[1字节 帧类型] [4字节 时间戳] [N字节 数据]

帧类型:
  0x01 = SPS/PPS (关键帧配置，首帧发送)
  0x02 = IDR 帧 (I帧)
  0x03 = P 帧
  0x04 = JPEG 帧 (兼容模式)
```

### MJPEG 透传格式

直接透传 WDA 9100 端口的 HTTP MJPEG 原始二进制数据，无需额外封装。

### JPEG 格式（兼容）

直接发送 JPEG 原始字节，前端作为单帧处理。

## 模块设计

### 1. win-recorder 流式编码接口

#### 新增 Python API

```python
class StreamingRecorder:
    """流式编码器"""

    def __init__(self, fps: int = 10, bitrate: int = 2000000, monitor: int = 1):
        """初始化流式编码器

        Args:
            fps: 帧率
            bitrate: 码率 (默认 2Mbps)
            monitor: 显示器索引
        """

    def start(self) -> dict:
        """启动编码器

        Returns:
            dict: {
                "width": 1920,
                "height": 1080,
                "fps": 10,
                "sps": "<base64>",  # SPS 数据
                "pps": "<base64>"   # PPS 数据
            }
        """

    def encode_frame(self, bgra_data: bytearray) -> Optional[bytes]:
        """编码单帧

        Args:
            bgra_data: BGRA 原始帧数据

        Returns:
            Optional[bytes]: 编码后的 NAL 单元，包含帧类型前缀
                              格式: [1字节帧类型(0x02/0x03)] [N字节数据]
                              None 表示需要刷新编码器
        """

    def stop(self) -> None:
        """停止编码器，释放资源"""
```

#### Rust 内部改动

1. **新增流式编码器类** `StreamingEncoder`
   - 复用现有 `MFSinkWriter` 的 H.264 编码逻辑
   - 新增 `IMFByteStream` 内存输出替代文件输出
   - 输出格式：SPS/PPS/NAL 单元

2. **API 导出**
   - 使用 PyO3 导出 `StreamingRecorder` 类
   - 方法：`start()`, `encode_frame()`, `stop()`

### 2. autotest Worker 推流改造

#### WebSocket 端点改动

```python
# server.py
@app.websocket("/ws/screen/{platform}/{device_id}")
async def screen_stream(
    websocket: WebSocket,
    platform: str,
    device_id: str,
    monitor: int = 1,
    codec: str = "jpeg"  # 新增: h264/mjpeg/jpeg
):
```

#### 各平台实现

| 平台 | codec=h264 | codec=mjpeg | codec=jpeg |
|------|------------|-------------|------------|
| Windows | win-recorder 流编码 | 不支持 | mss+JPEG (当前) |
| iOS | 不支持 | HTTP→WS 代理透传 | 解析 MJPEG (当前) |

#### Windows H.264 推流

```python
# frame_source.py
class WindowsFrameSource:
    def start_streaming(self, codec: str = "jpeg"):
        if codec == "h264":
            self._streamer = H264Streamer(self)
        else:
            self._streamer = MJPEGStreamer(self)

    def get_frame_encoded(self) -> bytes:
        """获取编码帧（支持 H.264）"""
        return self._streamer.get_frame()
```

#### iOS MJPEG 透传

```python
# frame_source.py
class MJPEGFrameSource:
    def start_mjpeg_proxy(self):
        """启动 HTTP→WS 代理"""
        # 连接到 WDA 9100 端口，持续读取并转发到 WebSocket
        self._mjpeg_response = requests.get("http://localhost:9100", stream=True)
        self._iterator = self._mjpeg_response.iter_content(chunk_size=8192)

    def proxy_to_websocket(self, websocket):
        """透传到 WebSocket"""
        for chunk in self._iterator:
            await websocket.send_bytes(chunk)
```

### 3. zq-platform 前端推流改造

#### 帧类型检测

```typescript
// hooks/useScreenStream.ts
function detectFrameType(data: ArrayBuffer): FrameType {
  const view = new DataView(data);
  const firstByte = view.getUint8(0);

  // H.264 帧类型
  if (firstByte >= 0x01 && firstByte <= 0x03) {
    return 'h264';
  }

  // JPEG 魔数: FFD8
  if (view.getUint16(0) === 0xFFD8) {
    return 'jpeg';
  }

  // MJPEG 魔数: FFD8FF
  if (view.getUint16(0) === 0xFFD8 && view.getUint8(2) === 0xFF) {
    return 'mjpeg';
  }

  return 'unknown';
}
```

#### H.264 解码渲染

```typescript
// hooks/useH264Decoder.ts
class H264Decoder {
  private mediaSource: MediaSource | null = null;
  private videoBuffer: SourceBuffer | null = null;

  async init(width: number, height: number, sps: Uint8Array, pps: Uint8Array) {
    this.mediaSource = new MediaSource();
    // 设置 video 元素的 src 为 MediaSource URL

    await new Promise(resolve => {
      this.mediaSource.addEventListener('sourceopen', resolve);
    });

    const codecString = `avc1.${this.toHex(sps[1])}${this.toHex(sps[2])}${this.toHex(sps[3])}`;
    this.videoBuffer = this.mediaSource.addSourceBuffer(`video/mp4; codecs="${codecString}, mp4a.40.2"`);
  }

  appendFrame(nalUnit: Uint8Array, frameType: number) {
    // 处理 VCL NAL 单元，组装成 MP4 片段
    // 写入 SourceBuffer
  }
}
```

#### MJPEG 渲染

```typescript
// hooks/useMJPEGRenderer.ts
class MJPEGRenderer {
  private canvas: HTMLCanvasElement;
  private ctx: CanvasRenderingContext2D;

  async render(data: ArrayBuffer) {
    const blob = new Blob([data], { type: 'image/jpeg' });
    const url = URL.createObjectURL(blob);
    const img = new Image();
    img.onload = () => {
      this.ctx.drawImage(img, 0, 0);
      URL.revokeObjectURL(url);
    };
    img.src = url;
  }
}
```

## 带宽对比

| 方案 | 分辨率 | 帧率 | 带宽 |
|------|--------|------|------|
| 当前 JPEG | 1920x1080 | 10fps | 8-20 Mbps |
| H.264 | 1920x1080 | 10fps | 1-3 Mbps |
| MJPEG | 1920x1080 | 10fps | 5-15 Mbps |

## 实施计划

### 阶段 1: win-recorder 流式编码
1. Rust 侧新增 `StreamingEncoder` 类
2. 实现内存输出 `IMFByteStream`
3. PyO3 绑定 `StreamingRecorder`

### 阶段 2: Worker 推流改造
1. 添加 `?codec=` 参数解析
2. Windows 集成 H.264 编码
3. iOS 添加 MJPEG 透传模式

### 阶段 3: 前端改造
1. 帧类型自动检测
2. H.264 解码渲染 (MSE + WASM fallback)
3. MJPEG canvas 渲染
4. 兼容单帧 JPEG

## 兼容性

- 默认保持 `codec=jpeg` 兼容现有客户端
- 新客户端可通过 `?codec=h264` 或 `?codec=mjpeg` 启用新协议