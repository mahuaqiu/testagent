# Worker 推流延迟优化设计文档

**日期**: 2026-07-02  
**状态**: 初稿  
**目标**: 将 Windows 平台 H.264 推流延迟从 2-3 秒优化到 200ms 以内

## 1. 背景与问题

### 1.1 当前架构（拉模式）

当前推流采用 Python 主动拉取（Pull）模式：

```
Python: sleep(100ms) → request("stream_next") → wait 30ms → send → sleep(100ms) → ...
    ↓
Rust:  capture → encode → queue → wait RPC → return frame
```

**延迟来源**：
- 每帧一次 JSON-RPC 同步调用（往返约 30-50ms）
- Python 层 sleep 控制帧率（默认 10fps = 100ms 间隔）
- 端到端延迟约 2-3 秒

### 1.2 优化目标

| 指标 | 当前值 | 目标值 |
|-----|-------|-------|
| 端到端延迟 | 2-3 秒 | < 200ms |
| RPC 调用频率 | 10 次/秒 | 0 次/秒（纯推送）|
| 帧采集到发送 | ~130ms | ~10ms |

## 2. 优化方案（推模式）

### 2.1 核心思路

彻底取消 Python 的定时 RPC 请求，改为 Rust 采集到帧后立即推送（Push）：

```
当前（拉模式）：
Python: sleep(100ms) → RPC请求帧 → 等待响应 → 发送 → sleep(100ms) → ...

优化后（推模式）：
Rust:   capture → encode → 立即输出到 stdout → capture → encode → ...
Python: while True: frame = readline() → 发送 → while True: ...
```

### 2.2 架构对比

```
┌─────────────────────────────────────────────────────────────┐
│                    当前架构（拉模式）                         │
├─────────────────────────────────────────────────────────────┤
│  Python: sleep(100ms) → request("stream_next") → wait 30ms │
│    ↓                                                        │
│  Rust:   capture_loop → queue → wait RPC → return frame    │
└─────────────────────────────────────────────────────────────┘

┌────────��────────────────────────────────────────────────────┐
│                    优化后架构（推模式）                        │
├─────────────────────────────────────────────────────────────┤
│  Rust:   capture_loop → encode → 立即输出 "@TYPE=data\n"   │
│    ↓                                                        │
│  Python: thread/blocking_readline() → send via WebSocket   │
└─────────────────────────────────────────────────────────────┘
```

## 3. 消息协议设计

### 3.1 通道分离（关键设计）

**stdout 用于 JSON-RPC，stderr 用于帧推送**，避免冲突：

```
┌─────────────────────────────────────────────────────────────┐
│                    Rust Sidecar                             │
├─────────────────────────────────────────────────────────────┤
│  stdin  ← JSON-RPC 请求（Python→Rust）                      │
│  stdout → JSON-RPC 响应（Rust→Python）                      │
│  stderr → 帧推送数据（Rust→Python，推模式专用）              │
└────────────��────────────────────────────────────────────────┘
```

### 3.2 帧格式

帧数据通过 **stderr** 推送（与 stdout JSON-RPC 分离）：

```
[TYPE_PREFIX][BASE64_DATA]\n
```

**示例**：
```
1QUJDREVGR0hJSktMTU5PUFFSU1RVVldYWVowMTIzNDU=\n
2QUJDREVGR0hJSktMTU5PUFFSU1RVVldYWVowMTIzNDU=\n
@FPS=20\n
```

### 3.3 stderr 推送模式

### 3.2 消息类型

| 前缀 | 类型 | 说明 |
|-----|------|------|
| `@` | 控制命令 | 如 `@FPS=20`，Python 解析后配置 |
| `0` | SPS | H.264 序列参数集（单独发送） |
| `1` | PPS | H.264 图像参数集（单独发送） |
| `2` | IDR 帧 | 关键帧（I 帧） |
| `3` | P 帧 | 预测帧 |
| `J` | JPEG 帧 | JPEG 格式帧 |
| `H` | 心跳 | Rust 发送的心跳，保持连接活跃 |
| `E` | 错误 | 推送过程中的错误信息 |

### 3.3 stderr 推送模式

**关键设计**：帧数据通过 **stderr** 推送，保持 stdout 用于 JSON-RPC：

```
stdin  → JSON-RPC 请求（原有）
stdout → JSON-RPC 响应（原有）
stderr → 帧推送数据（新增推模式专用）
```

**优势**：
- 不影响现有 `snapshot`、`recording_start` 等 JSON-RPC 调用
- stdout 保持同步响应模式，兼容性最佳
- stderr 可配置为行缓冲或无缓冲

**Rust 端实现**：

```rust
use std::io::Write;

// 帧推送使用 stderr
fn push_frame(frame_type: u8, data: &[u8]) {
    let encoded = base64::encode(data);
    let mut stderr = std::io::stderr();
    writeln!(stderr, "{}{}", frame_type as char, encoded).unwrap();
}
```

```
@FPS=20\n          - 设置帧率（推流和录制共用，按最高帧率运行）
@PUSH_START\n     - 启动推流模式
@PUSH_STOP\n      - 停止推流模式
@REC_START=30\n   - 启动录制，指定 FPS
@REC_STOP\n       - 停止录制
@HEARTBEAT=5\n    - 设置心跳间隔（秒）
@QUIT\n           - 退出推流模式
```

## 4. 帧率控制与复用

### 4.1 按最高帧率运行

推流和录制共用一个采集线程，按需求的最高帧率运行：

```
target_fps = max(push_fps, recording_fps, idle_fps)
```

### 4.2 帧分发器

采集的帧同时推送给推流和录制，丢弃多余的：

```
┌─────────────────────────────────────────────────────────────┐
│                    Rust capture_loop                        │
│                      按 max(推流, 录制) fps 采集             │
│                     （复用现有 stream_queue）               │
└─────────────────────────┬───────────────────────────────────┘
                          │
                    ┌─────┴─────┐
                    ▼           ▼
            ┌───────────┐  ┌───────────┐
            │  push     │  │  rec      │
            │ 消费流    │  │ 消费流    │
            │ (推模式)  │  │ (录制文件)│
            └───────────┘  └───────────┘
```

**实现说明**：
- **复用现有 `stream_queue`**：原 `stream_queue` (容量16) 改为仅用于录制消费
- **新增推送消费者**：Python 推模式直接从 Rust stdout 读取，不再使用 `stream_queue`
- **录制保持兼容**：录制功能仍使用原有 `stream_next` RPC 方式从 `stream_queue` 消费

**帧分发逻辑**：
```
每采集一帧:
  1. push 到 stream_queue（供录制消费）
  2. 同时推送到 stdout（供推流消费）
```

## 5. Rust 端实现

### 5.1 新增命令

```rust
// 新增命令处理
"stream_push_start" => {
    // 启动推流模式，进入推送循环
    // 参数：fps, session_id
}
"stream_push_stop" => {
    // 停止推流模式
}
"stream_set_fps" => {
    // 动态调整帧率
}
```

### 5.2 推送循环实现

```rust
fn push_frames_loop(&mut self, session_id: &str, target_fps: u32) {
    let interval = Duration::from_secs_f64(1.0 / target_fps as f64);

    loop {
        let frame = self.capture_and_encode();
        if let Some(frame) = frame {
            // 编码帧，区分类型
            let frame_type = determine_frame_type(&frame); // 0=SPS, 1=PPS, 2=IDR, 3=P
            let encoded = base64::encode(&frame);

            // 输出到 stdout（带前缀，无分隔符）
            println!("{}{}", frame_type as char, encoded);
        }

        // 控制帧率
        thread::sleep(interval);
    }
}
```

### 5.3 帧分发器实现

```rust
struct FrameDispatcher {
    push_queue: VecDeque<Vec<u8>>,  // 容量 1
    rec_queue: VecDeque<Vec<u8>>,   // 容量 1
}

impl FrameDispatcher {
    fn push_frame(&mut self, frame: Vec<u8>) {
        // 推流队列
        if self.push_queue.len() >= 1 {
            self.push_queue.pop_front();
        }
        self.push_queue.push_back(frame.clone());

        // 录制队列
        if self.rec_queue.len() >= 1 {
            self.rec_queue.pop_front();
        }
        self.rec_queue.push_back(frame);
    }

    fn get_push_frame(&mut self) -> Option<Vec<u8>> {
        self.push_queue.pop_front()
    }

    fn get_rec_frame(&mut self) -> Option<Vec<u8>> {
        self.rec_queue.pop_front()
    }
}
```

## 6. Python 端实现

### 6.1 PushFrameReader 类

```python
class PushFrameReader:
    """推模式帧读取器"""

    def __init__(self, client: WindowsSidecarClient):
        self._client = client
        self._running = False
        self._fps = 20
        self._frame_queue: asyncio.Queue = None
        self._proc = client.get_process()  # 获取底层 subprocess 引用

    def set_fps(self, fps: int):
        """动态配置帧率"""
        self._fps = fps
        self._client.write_command(f"@FPS={fps}")

    def is_running(self) -> bool:
        """检查推流是否仍在运行"""
        return self._running and self._client.is_alive()

    def start_push(self, fps: int = 20):
        """启动推流模式"""
        self._fps = fps
        self._frame_queue = asyncio.Queue(maxsize=2)
        self._running = True
        # 通知 Rust 启动推送
        self._client.request("stream_push_start", {"fps": fps})
        # 启动后台监听线程
        self._start_listener_thread()

    def stop_push(self):
        """停止推流模式"""
        self._running = False
        # write_command 会自动添加 \n，所以这里不传
        self._client.write_command("@PUSH_STOP")

    def _start_listener_thread(self):
        """后台线程监听 Rust 推送（通过 stderr）"""
        def listener():
            while self._running:
                try:
                    # 从 stderr 读取推送数据（不是 stdout）
                    line = self._proc.stderr.readline()
                    if not line:
                        break
                    self._handle_line(line)
                except Exception as e:
                    logger.error(f"帧监听异常: {e}")
                    break

        thread = threading.Thread(target=listener, daemon=True)
        thread.start()

    def _handle_line(self, line: bytes):
        """处理接收到的行"""
        if not line:
            return

        prefix = line[0:1]
        content = line[1:].strip()

        if prefix == b'@':
            # 控制命令
            self._handle_command(content)
        elif prefix == b'0':
            # SPS - 序列参数集
            data = base64.b64decode(content)
            self._frame_queue.put_nowait(('sps', data))
        elif prefix == b'1':
            # PPS - 图像参数集
            data = base64.b64decode(content)
            self._frame_queue.put_nowait(('pps', data))
        elif prefix == b'2':
            # IDR 帧
            data = base64.b64decode(content)
            self._frame_queue.put_nowait(('idr', data))
        elif prefix == b'3':
            # P 帧
            data = base64.b64decode(content)
            self._frame_queue.put_nowait(('p', data))
        elif prefix == b'H':
            # 心跳，忽略
            pass
        elif prefix == b'E':
            # 错误日志
            logger.error(f"[Rust] {content.decode('utf-8', errors='ignore')}")
        # elif prefix == b'J':
        #     # JPEG 帧 - 本次优化范围之外，预留
        #     pass

    async def get_frame(self) -> tuple[str, Optional[bytes]]:
        """获取一帧（异步）

        Returns:
            tuple: (frame_type, data) - 帧类型和二进制数据
                   frame_type: 'sps' | 'pps' | 'idr' | 'p'
        """
        try:
            frame_type, data = await asyncio.wait_for(
                self._frame_queue.get(),
                timeout=0.5
            )
            return (frame_type, data)
        except asyncio.TimeoutError:
            return ('', None)
```

### 6.2 修改 screen_stream

```python
async def screen_stream(websocket: WebSocket, ...):
    # ... 现有代码 ...

    # 获取 streamer
    streamer = screen_manager.start_streaming(codec=codec)

    if codec == "h264":
        # H.264 推流：使用推模式
        from worker.screen.windows_sidecar import PushFrameReader
        reader = PushFrameReader(client)
        reader.start_push(fps=streaming_fps)

        # 先发送 SPS+PPS（它们会先到达，需要等待两者都收到）
        sps_data = None
        pps_data = None

        # 等待 SPS 和 PPS 都收到
        while sps_data is None or pps_data is None:
            frame_type, frame_data = await reader.get_frame()
            if frame_data is None:
                break
            if frame_type == 'sps':
                sps_data = frame_data
            elif frame_type == 'pps':
                pps_data = frame_data

        # 合并发送 SPS+PPS（格式：[1字节前缀][SPS][1字节前缀][PPS]）
        if sps_data and pps_data:
            combined = bytes([0x01]) + sps_data + bytes([0x01]) + pps_data
            await websocket.send_bytes(combined)

        # 主循环：从推模式读取器获取帧并发送
        while reader.is_running():
            frame_type, frame_data = await reader.get_frame()
            if frame_data and frame_type in ('idr', 'p'):
                await websocket.send_bytes(frame_data)
    else:
        # JPEG：继续使用原有模式（或也改造成推模式）
        while streamer.is_running():
            await asyncio.sleep(1.0 / streaming_fps)
            frame = await streamer.get_frame_async()
            if frame:
                await websocket.send_bytes(frame)
```

## 7. 兼容性设计

### 7.1 不影响现有功能

| 功能 | 保持方式 |
|-----|---------|
| 录制 | 仍使用 `recording_start/stop` 同步 JSON-RPC 命令 |
| 截图 | 仍使用 `snapshot` 同步请求 |
| 非推流场景 | 原有 JSON-RPC 模式保持不变 |

### 7.2 新增接口

| 命令 | 说明 |
|-----|------|
| `stream_push_start` | 启动推流模式 |
| `stream_push_stop` | 停止推流模式 |
| `stream_set_fps` | 动态调整帧率 |

### 7.3 WindowsSidecarClient 扩展

需要新增以下方法：

```python
class WindowsSidecarClient:
    def get_process(self) -> subprocess.Popen:
        """暴露底层 subprocess 引用，供 PushFrameReader 使用"""
        return self._proc

    def is_alive(self) -> bool:
        """检查 sidecar 进程是否存活"""
        return self._proc is not None and self._proc.poll() is None

    def write_command(self, cmd: str) -> None:
        """发送控制命令到 stdin（不等待响应）"""
        with self._lock:
            if not self._proc or not self._proc.stdin:
                raise RuntimeError("sidecar 进程未启动")
            self._proc.stdin.write(cmd + "\n")
            self._proc.stdin.flush()
```

### 7.4 退出流程

推模式下的退出流程：

```
1. WebSocket 断开
2. Python: 设置 _running = False，停止监听循环
3. Python: 发送 @PUSH_STOP\n 到 Rust stdin
4. Rust: 收到 @PUSH_STOP，退出推送循环
5. Rust: 继续录制（如果有）或降为 idle fps
6. Python: 清理资源，关闭 session
```

## 8. 实现步骤

### 8.1 Rust 端

1. **新增帧分发器** (`FrameDispatcher`)
   - 实现 push_queue 和 rec_queue
   - 实现 `get_push_frame()` 和 `get_rec_frame()`

2. **新增推送命令**
   - `stream_push_start`: 启动推送循环
   - `stream_push_stop`: 停止推送
   - `stream_set_fps`: 动态调整帧率

3. **修改 capture_loop**
   - 实现按需帧率（max of push/rec/idle）
   - 采集后分发给各个队列

### 8.2 Python 端

1. **新增 PushFrameReader 类**
   - 后台线程监听 stdout
   - 异步队列转发帧

2. **修改 server.py**
   - screen_stream 支持推模式
   - 动态帧率配置

### 8.3 测试验证

1. 验证帧率控制生效（10fps / 20fps / 30fps）
2. 验证 WebSocket 客户端正常接收帧
3. 验证推流 + 录制同时进行
4. 验证录制/截图功能不受影响
5. 测量端到端延迟

## 9. 预期效果

| 指标 | 当前 | 优化后 |
|-----|------|-------|
| RPC 调用频率 | 10 次/秒 | 0 次/秒 |
| 帧采集到发送延迟 | ~130ms | ~10ms |
| 端到端延迟 | 2-3 秒 | **< 200ms** |

## 10. 风险与注意事项

1. **stdout 缓冲问题**：需要确保 stdout 无缓冲输出（Rust 端使用 `println!` 或设置 `BufWriter`）
2. **异常处理**：Rust 推送端退出时，Python 端需要正确感知并清理
3. **内存泄漏**：长时间运行后，队列需要正确释放
4. **多客户端并发**：
   - 本次优化**仅支持单推流客户端**
   - 多客户端场景需要后续扩展（如拒绝第二客户端或队列广播）
5. **并发模型**：
   - Rust 端：推送循环在独立线程中运行，不阻塞主线程的命令处理
   - Python 端：监听线程通过 `asyncio.Queue` 转发到主事件循环
   - 线程安全：stdout 写入需要加锁（`Mutex` 保护）

### 10.1 JPEG 推流

本次优化**仅针对 H.264 推流**。JPEG 推流保持原有拉模式，或在后续迭代中改造。