# Worker 推流延迟优化实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将 Windows 平台 H.264 推流延迟从 2-3 秒优化到 200ms 以内，通过推模式（Push）替代拉模式（Pull）

**Architecture:** 
- 帧数据通过 stderr 推送（与 stdout JSON-RPC 分离）
- Rust 采集帧后立即输出到 stderr
- Python 后台线程监听 stderr，通过 asyncio 队列转发到 WebSocket
- 推流和录制共用采集线程，按最高帧率运行

**Tech Stack:** Python (asyncio, threading), Rust (std::io::stderr), WebSocket

---

## 文件结构

### Rust 端修改
- Modify: `rust/windows-screen-sidecar/src/main.rs` - 新增 `stream_push_start/stop` 命令
- Modify: `rust/windows-screen-sidecar/src/session.rs` - 新增推送循环和帧分发逻辑

### Python 端修改
- Modify: `worker/screen/windows_sidecar.py` - 新增 `get_process()`, `is_alive()`, `write_command()` 方法，新增 `PushFrameReader` 类
- Modify: `worker/server.py` - 修改 `screen_stream` 函数支持推模式

### 测试文件
- Create: `tests/screen/test_push_streaming.py` - 推流功能测试

---

## 实现计划

### 阶段 1: Rust 端实现

#### Task 1: 添加 stderr 推送函数

**Files:**
- Modify: `rust/windows-screen-sidecar/src/session.rs`

- [ ] **Step 1: 查看当前 session.rs 的 imports 和结构**

Run: 读取 `rust/windows-screen-sidecar/src/session.rs` 前 50 行

- [ ] **Step 2: 添加帧推送函数**

在 session.rs 文件末尾添加：

```rust
/// 通过 stderr 推送帧数据
/// frame_type: 0=SPS, 1=PPS, 2=IDR, 3=P
pub fn push_frame_to_stderr(frame_type: u8, data: &[u8]) {
    use std::io::Write;
    let encoded = base64::encode(data);
    // 使用 eprintln! 输出到 stderr（自动换行）
    eprintln!("{}{}", frame_type as char, encoded);
}
```

- [ ] **Step 3: Commit**

```bash
cd D:/code/autotest/rust/windows-screen-sidecar
git add src/session.rs
git commit -m "feat: add push_frame_to_stderr function"
```

---

#### Task 2: 实现推送循环

**Files:**
- Modify: `rust/windows-screen-sidecar/src/session.rs:291-400`

- [ ] **Step 1: 查看当前 capture_loop 实现**

Run: 读取 `rust/windows-screen-sidecar/src/session.rs` 第 291-400 行

- [ ] **Step 2: 修改 capture_loop 支持推送模式**

找到现有的 capture_loop 函数，添加帧推送逻辑：

```rust
// 在 capture_loop 函数内部，采集并编码帧后添加：
// 获取帧类型并推送到 stderr
let frame_type = determine_frame_type(&encoded_frame); // 0=SPS, 1=PPS, 2=IDR, 3=P
push_frame_to_stderr(frame_type, &encoded_frame);
```

- [ ] **Step 3: 添加帧类型判断函数**

```rust
/// 判断 H.264 帧类型
/// 0 = SPS, 1 = PPS, 2 = IDR, 3 = P
fn determine_frame_type(nal: &[u8]) -> u8 {
    if nal.is_empty() {
        return 3;
    }
    let nal_type = nal[0] & 0x1F;
    match nal_type {
        7 => 0,  // SPS
        8 => 1,  // PPS
        5 => 2,  // IDR
        _ => 3,  // P frame
    }
}
```

- [ ] **Step 4: Commit**

```bash
cd D:/code/autotest/rust/windows-screen-sidecar
git add src/session.rs
git commit -m "feat: add push loop to capture_loop"
```

---

#### Task 3: 新增 stream_push_start/stop 命令

**Files:**
- Modify: `rust/windows-screen-sidecar/src/main.rs:174-221`

- [ ] **Step 1: 在 main.rs 添加 stream_push_start 命令处理**

在 `"stream_start"` 处理后添加：

```rust
"stream_push_start" => {
    let session_id = parse_string(&params, "session_id", "windows/1");
    let fps = parse_u32(&params, "fps", 20);
    
    // 启动推送模式（在后台线程中运行）
    let state = Arc::clone(&state);
    let sid = session_id.clone();
    
    std::thread::spawn(move || {
        // 帧率控制
        let interval = std::time::Duration::from_secs_f64(1.0 / fps as f64);
        
        loop {
            // 采集一帧（复用现有 capture 逻辑）
            // ... 采集和编码逻辑 ...
            
            // 推送到 stderr
            push_frame_to_stderr(frame_type, &encoded);
            
            std::thread::sleep(interval);
        }
    });
    
    Response::ok(request.id, serde_json::json!({"status": "push_started"}))
}
"stream_push_stop" => {
    // 设置停止标志
    Response::ok(request.id, serde_json::json!({"status": "push_stopped"}))
}
```

- [ ] **Step 2: 处理 @PUSH_STOP 控制命令**

在 main.rs 的命令解析循环中，添加对 stdin ���始命令的处理：

```rust
// 在读取 stdin 行后、解析 JSON 之前
if line.starts_with('@') {
    // 处理控制命令
    let cmd = line.trim();
    if cmd.starts_with("@PUSH_STOP") {
        // 设置全局停止标志
        // ...
    } else if cmd.starts_with("@FPS=") {
        // 解析并更新帧率
        // ...
    }
    continue; // 不作为 JSON-RPC 处理
}
```

- [ ] **Step 3: Commit**

```bash
cd D:/code/autotest/rust/windows-screen-sidecar
git add src/main.rs
git commit -m "feat: add stream_push_start/stop commands"
```

---

### 阶段 2: Python 端实现

#### Task 4: 扩展 WindowsSidecarClient

**Files:**
- Modify: `worker/screen/windows_sidecar.py:45-260`

- [ ] **Step 1: 添加 get_process() 方法**

在 `WindowsSidecarClient` 类中添加：

```python
def get_process(self) -> subprocess.Popen:
    """暴露底层 subprocess 引用，供 PushFrameReader 使用"""
    with self._lock:
        return self._proc
```

- [ ] **Step 2: 添加 is_alive() 方法**

```python
def is_alive(self) -> bool:
    """检查 sidecar 进程是否存活"""
    with self._lock:
        return self._proc is not None and self._proc.poll() is None
```

- [ ] **Step 3: 添加 write_command() 方法**

```python
def write_command(self, cmd: str) -> None:
    """发送控制命令到 stdin（不等待响应）"""
    with self._lock:
        if not self._proc or not self._proc.stdin:
            raise RuntimeError("sidecar 进程未启动")
        self._proc.stdin.write(cmd + "\n")
        self._proc.stdin.flush()
```

- [ ] **Step 4: Commit**

```bash
cd D:/code/autotest
git add worker/screen/windows_sidecar.py
git commit -m "feat: add get_process, is_alive, write_command methods to WindowsSidecarClient"
```

---

#### Task 5: 实现 PushFrameReader 类

**Files:**
- Modify: `worker/screen/windows_sidecar.py` (在文件末尾添加)

- [ ] **Step 1: 添加 PushFrameReader 类**

在 windows_sidecar.py 文件末尾添加：

```python
class PushFrameReader:
    """推模式帧读取器 - 从 stderr 读取 Rust 推送的帧数据"""

    def __init__(self, client: WindowsSidecarClient):
        self._client = client
        self._running = False
        self._fps = 20
        self._frame_queue: asyncio.Queue = None
        self._proc = client.get_process()

    def set_fps(self, fps: int):
        """动态配置帧��"""
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
        # write_command 会自动添加 \n
        self._client.write_command("@PUSH_STOP")

    def _start_listener_thread(self):
        """后台线程监听 Rust 推送（通过 stderr）"""
        def listener():
            while self._running:
                try:
                    # 从 stderr 读取推送数据
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

    def _handle_command(self, cmd: bytes):
        """处理控制命令"""
        try:
            cmd_str = cmd.decode('utf-8')
            if cmd_str.startswith("FPS="):
                # 帧率确认
                logger.info(f"帧率已设置为: {cmd_str}")
        except Exception as e:
            logger.warning(f"解析控制命令失败: {e}")

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

- [ ] **Step 2: 添加 asyncio 导入**

确认文件顶部有 `import asyncio`

- [ ] **Step 3: Commit**

```bash
cd D:/code/autotest
git add worker/screen/windows_sidecar.py
git commit -m "feat: add PushFrameReader class for push mode streaming"
```

---

#### Task 6: 修改 screen_stream 支持推模式

**Files:**
- Modify: `worker/server.py:1010-1040`

- [ ] **Step 1: 查看当前 screen_stream 的主循环**

Run: 读取 `worker/server.py` 第 1010-1040 行

- [ ] **Step 2: 修改为推模式**

找到 H.264 推流的主循环，修改为：

```python
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
```

- [ ] **Step 3: 处理异常退出**

在 finally 块中添加：

```python
# 停止推流模式
if 'reader' in locals():
    reader.stop_push()
```

- [ ] **Step 4: Commit**

```bash
cd D:/code/autotest
git add worker/server.py
git commit -m "feat: modify screen_stream to use push mode for H.264"
```

---

### 阶段 3: 测试验证

#### Task 7: 集成测试

**Files:**
- Create: `tests/screen/test_push_streaming.py`

- [ ] **Step 1: 创建测试文件**

```python
"""推流功能测试"""
import pytest
import asyncio
from unittest.mock import Mock, patch, AsyncMock


class TestPushFrameReader:
    """PushFrameReader 单元测试"""

    def test_init(self):
        """测试初始化"""
        from worker.screen.windows_sidecar import PushFrameReader, WindowsSidecarClient
        
        mock_client = Mock(spec=WindowsSidecarClient)
        mock_client.get_process.return_value = Mock(stderr=Mock(readline=Mock(return_value=b'')))
        
        reader = PushFrameReader(mock_client)
        assert reader._fps == 20
        assert reader._running == False

    def test_is_running(self):
        """测试运行状态检查"""
        from worker.screen.windows_sidecar import PushFrameReader, WindowsSidecarClient
        
        mock_client = Mock(spec=WindowsSidecarClient)
        mock_proc = Mock()
        mock_proc.stderr.readline = Mock(return_value=b'')
        mock_client.get_process.return_value = mock_proc
        mock_client.is_alive.return_value = True
        
        reader = PushFrameReader(mock_client)
        reader._running = True
        
        assert reader.is_running() == True


class TestScreenStreamIntegration:
    """screen_stream 集成测试（需要实际环境）"""
    
    @pytest.mark.skip(reason="需要实际 Windows 环境和 sidecar 进程")
    async def test_h264_push_stream(self):
        """测试 H.264 推流模式"""
        # 此测试需要实际环境，运行方式：
        # pytest tests/screen/test_push_streaming.py -v -k "test_h264"
        pass
```

- [ ] **Step 2: 运行测试验证基础功能**

Run: `pytest tests/screen/test_push_streaming.py -v`

Expected: 基础测试通过

- [ ] **Step 3: Commit**

```bash
cd D:/code/autotest
git add tests/screen/test_push_streaming.py
git commit -m "test: add push streaming tests"
```

---

#### Task 8: 手动验证

- [ ] **Step 1: 编译 Rust sidecar**

Run: `cd rust/windows-screen-sidecar && cargo build --release`

- [ ] **Step 2: 启动 Worker**

Run: `python -m worker.main`

- [ ] **Step 3: 测试推流延迟**

使用 WebSocket 客户端连接到 `/worker/stream/windows/test` 并测量延迟：
- 预期：< 200ms

- [ ] **Step 4: 验证兼容性**

- 录制功能正常
- 截图功能正常

---

## 执行方式

**计划已保存到:** `docs/superpowers/plans/2026-07-02-streaming-latency-optimization-plan.md`

**两个执行选项：**

1. **Subagent-Driven (推荐)** - 每个任务派遣一个子代理，任务间审查，快速迭代

2. **Inline Execution** - 在当前会话中使用 executing-plans 技能，批量执行并在审查点暂停

**你选择哪种执行方式？**