# win-recorder 设计文档

## 元数据

| 属性 | 值 |
|------|-----|
| **创建日期** | 2026-05-31 |
| **作者** | Claude |
| **状态** | Draft |
| **目标版本** | win-recorder v0.1.0 |

---

## 1. 概述

### 1.1 背景

当前自动化测试执行基建（autotest）使用 FFmpeg subprocess 方式实现录屏功能，存在以下问题：

| 问题 | 影响 |
|------|------|
| **打包体积大** | ffmpeg.exe ~90MB，总打包体积 854MB |
| **CPU 占用高** | JPEG 解码 + 软编码，CPU 占用 ~15-20% |
| **内存抖动** | 每帧新建 bytes 对象，30fps 下内存分配频繁 |
| **外部依赖** | FFmpeg 需单独安装或打包，增加维护成本 |

### 1.2 目标

开发 Rust+Python 绑定的硬编录制库 `win-recorder`，实现：

| 目标 | 量化指标 |
|------|----------|
| **极低 CPU 占用** | <5%（GPU 硬编） |
| **零拷贝内存** | Python bytearray 固定地址，无内存分配 |
| **体积最小** | DLL <1MB，无 FFmpeg 依赖 |
| **高性能录制** | 支持 30fps + 4K |
| **可选音频** | 参数控制，默认不录制 |

### 1.3 技术栈

| 层级 | 技术 |
|------|------|
| **绑定层** | PyO3（Python ↔ Rust） |
| **GPU 层** | DirectX 11（纹理管理） |
| **编码层** | Media Foundation（硬编管线） |
| **音频层** | WASAPI（可选音频捕获） |

---

## 2. 系统架构

### 2.1 整体管线

```
┌──────────────────────────────────────────────────────────────────┐
│                          Python 端                                │
│  ┌─────────────────────────────────────────────────────────────┐ │
│  │ WindowsFrameSource                                           │ │
│  │   pre_allocated_buffer = bytearray(width * height * 4)       │ │
│  │   get_frame_raw() → memoryview (BGRA, 固定地址)              │ │
│  └─────────────────────────────────────────────────────────────┘ │
└───────────────────────────────┬──────────────────────────────────┘
                                │
                                ▼ PyO3 (PyByteArray::as_bytes())
┌──────────────────────────────────────────────────────────────────┐
│                          Rust 端                                 │
│  ┌─────────────────────────────────────────────────────────────┐ │
│  │ win_recorder.WinRecorder                                     │ │
│  │   add_frame(bytes_slice: &[u8])                              │ │
│  │       → ID3D11Texture2D::Map/Unmap (GPU upload)              │ │
│  │       → MFCreateDXGISurfaceBuffer                            │ │
│  │       → IMFSinkWriter::WriteSample                           │ │
│  └─────────────────────────────────────────────────────────────┘ │
│  ┌─────────────────────────────────────────────────────────────┐ │
│  │ Media Foundation 管线                                        │ │
│  │   IMFSinkWriter                                              │ │
│  │     Input: MFVideoFormat_RGB32 (BGRA)                        │ │
│  │     [Color Converter MFT] ← Windows 自动 GPU BGRA→NV12      │ │
│  │     [H.264 Encoder MFT] ← NVENC/QSV 硬编                     │ │
│  │     Output: MP4                                              │ │
│  └─────────────────────────────────────────────────────────────┘ │
└──────────────────────────────────────────────────────────────────┘
```

### 2.2 核心优化点

#### 优化点 1：最小化内存拷贝

**注意**：mss 10.x 的 `grab()` 方法不支持 `output` 参数，无法直接写入预分配 buffer。

实际内存流动：
```
mss 内部:
  DXGI Desktop Duplication → GPU 纹理 → 新建 bytearray (shot.raw)
      │
      │ 每帧创建新 bytearray (不可避免，mss API 限制)
      │
      ▼ Python 端内存拷贝
  pre_allocated_buffer[:] = shot.raw  # 一次内存拷贝

Python → Rust:
  pre_allocated_buffer (固定地址 bytearray)
      │
      ▼ PyByteArray::as_bytes() (零拷贝 slice 引用)
  &[u8] (物理地址固定，无 Python→Rust 拷贝)
      │
      ▼ Staging Texture Upload
  GPU 纹理
```

**效果**：
- Python→Rust 零拷贝（PyByteArray slice 直接引用）
- Python 端一次内存拷贝（不可避免，mss API 限制）
- Rust→GPU 使用 Staging 纹理高效上传

#### 优化点 2：MF 内置颜色转换

```
BGRA bytes
    │
    ▼ ID3D11Texture2D (GPU 纹理)
    │
    ▼ IMFMediaBuffer (GPU-backed)
    │
    ▼ IMFSinkWriter
    │   ├── Input:  MFVideoFormat_RGB32 (BGRA)
    │   ├── [Color Converter MFT] ← Windows 内置，GPU 加速
    │   └ Output: MFVideoFormat_NV12
    │   └── [H.264 Encoder MFT] ← 硬编 (NVENC/QSV)
    │
    ▼ MP4 File (H.264)
```

**效果**：
- 无需手动编写 Compute Shader
- Windows 自动 GPU 加速 BGRA→NV12 转换
- 代码量减少约 500 行

---

## 3. 项目结构

### 3.1 Rust 工程 (`D:\code\win-recorder`)

```
D:\code\win-recorder\
├── Cargo.toml
├── pyproject.toml           # Python wheel 构建配置
├── src/
│   ├── lib.rs               # PyO3 模块入口
│   ├── recorder.rs          # WinRecorder 核心类
│   ├── d3d11.rs             # DirectX 11 纹理管理
│   ├── mf_writer.rs         # Media Foundation SinkWriter
│   ├── audio.rs             # WASAPI 音频捕获（可选）
│   └── error.rs             # 自定义错误类型
├── tests/
│   └── test_recorder.py     # Python 测试
└── README.md
```

### 3.2 Python 项目改动 (`D:\code\autotest`)

```
D:\code\autotest\
├── worker/
│   ├── screen/
│   │   ├── frame_source.py  # 改动：新增 get_frame_raw()
│   │   ├── recorder.py      # 重写：替换 FFmpeg → win-recorder
│   │   └── manager.py       # 改动：帧队列支持 bytearray 模式
│   └── actions/
│   │   └ recording.py     # 改动：新增 monitor/audio 参数
│   └── config.py            # 新增：recording 配置项
├── config/
│   └── worker.yaml          # 新增：audio_enabled 配置
└── pyproject.toml           # 新增依赖：win-recorder
```

---

## 4. 核心依赖

### 4.1 Cargo.toml

```toml
[package]
name = "win-recorder"
version = "0.1.0"
edition = "2021"

[lib]
name = "win_recorder"
crate-type = ["cdylib"]

[dependencies]
pyo3 = { version = "0.22", features = ["extension-module"] }
windows = { version = "0.58", features = [
    "Win32_Foundation",
    "Win32_Media_MediaFoundation",
    "Win32_Media_Audio",
    "Win32_Graphics_Direct3D11",
    "Win32_Graphics_Direct3D",
    "Win32_Graphics_Dxgi",
    "Win32_System_Com",
    "Win32_System_SystemServices",
]}
anyhow = "1.0"
thiserror = "1.0"
parking_lot = "0.12"
```

---

## 5. API 设计

### 5.1 Python 端 API

```python
import win_recorder

# 创建录制器
recorder = win_recorder.WinRecorder(
    output_path="output.mp4",
    fps=30,
    audio=False,           # 默认不录制音频
    monitor=1,             # 显示器选择：0=全部, 1=主屏幕(默认), 2=副屏幕
)

# 获取显示器尺寸（预分配 buffer）
width, height = win_recorder.WinRecorder.get_monitor_size(monitor=1)
buffer = bytearray(width * height * 4)

# 启动录制
recorder.start()

# 添加帧（零拷贝）
recorder.add_frame(buffer)

# 停止录制
output_path = recorder.stop()
```

### 5.2 Monitor 参数说明

| monitor 值 | 说明 | 与截图一致性 |
|------------|------|-------------|
| `1` | 主屏幕（left=0） | ✅ 与 WindowsFrameSource 一致（默认） |
| `2` | 副屏幕（另一个显示器） | ✅ 与 WindowsFrameSource 一致 |

**注意**：`monitor=0`（全部屏幕）暂不支持，需要额外的虚拟屏幕合成逻辑，可在后续版本迭代。

### 5.3 Rust 端核心类

```rust
#[pyclass]
pub struct WinRecorder {
    output_path: PathBuf,
    fps: u32,
    audio: bool,
    monitor: u32,
    
    // 内部状态（启动后自动检测）
    width: u32,
    height: u32,
    monitor_offset: (i32, i32),
    
    d3d11_ctx: Option<D3D11Context>,
    mf_writer: Option<MFSinkWriter>,
    audio_cap: Option<AudioCapture>,
    frame_count: u64,
    is_recording: bool,
}

#[pymethods]
impl WinRecorder {
    #[new]
    #[pyo3(signature = (output_path, fps=30, audio=false, monitor=1))]
    fn new(output_path: String, fps: u32, audio: bool, monitor: u32) -> PyResult<Self>;
    
    #[staticmethod]
    #[pyo3(signature = (monitor=1))]
    fn get_monitor_size(monitor: u32) -> PyResult<(u32, u32)>;
    
    fn start(&mut self) -> PyResult<()>;
    
    /// 添加帧（使用 PyByteArray 支持可变 bytearray）
    fn add_frame(&mut self, frame: &PyByteArray) -> PyResult<()> {
        let bgra_data: &[u8] = frame.as_bytes();  // 零拷贝 slice
        // ...
    }
    
    fn stop(&mut self) -> PyResult<String>;
    fn get_info(&self) -> PyResult<PyObject>;
}
```

**关键修正**：使用 `&PyByteArray` 替代 `PyBytes<'_>`，以支持 Python 的 `bytearray` 类型。

---

## 6. 核心模块设计

### 6.1 D3D11 纹理管理 (`d3d11.rs`)

**双纹理架构**（关键修正）：

```
┌─────────────────────────────────────────────────────────────┐
│  D3D11Context                                               │
│  ┌─────────────────────┐  ┌─────────────────────┐          │
│  │ Staging Texture     │  │ GPU Texture (Shared)│          │
│  │ D3D11_USAGE_STAGING │  │ D3D11_USAGE_DEFAULT │          │
│  │ CPU_WRITE 可访问     │  │ GPU 默认池          │          │
│  └─────────────────────┘  └─────────────────────┘          │
│           │ Map/Unmap              │ CopyResource          │
│           │ 写入 BGRA              │ Staging → GPU         │
│           ▼                        ▼                       │
│     &[u8] 数据               MFCreateDXGISurfaceBuffer      │
└─────────────────────────────────────────────────────────────┘
```

| 方法 | 功能 |
|------|------|
| `D3D11Context::new()` | 创建 D3D11 设备、Staging 纹理 + GPU 纹理 |
| `upload_bgra(&[u8])` | Map Staging → 写入 → Unmap → CopyResource 到 GPU |
| `create_mf_sample()` | 将 GPU 纹理包装成 IMFMediaBuffer |
| `detect_monitor(u32)` | 检测显示器配置（与 Python 端一致） |

**纹理参数**：
| 纹理类型 | Format | Usage | CPUAccess | MiscFlags |
|---------|--------|-------|-----------|-----------|
| **Staging** | `DXGI_FORMAT_B8G8R8A8_UNORM` | `D3D11_USAGE_STAGING` | `D3D11_CPU_ACCESS_WRITE` | 0 |
| **GPU** | `DXGI_FORMAT_B8G8R8A8_UNORM` | `D3D11_USAGE_DEFAULT` | 0 | `D3D11_RESOURCE_MISC_SHARED` |

**注意**：`D3D11_USAGE_DEFAULT` 纹理不能直接 Map/Unmap，必须通过 Staging 纹理间接写入。

### 6.2 Media Foundation SinkWriter (`mf_writer.rs`)

| 方法 | 功能 |
|------|------|
| `MFSinkWriter::new()` | 创建 SinkWriter，配置输入/输出类型 |
| `create_h264_output_type()` | 配置 H.264 输出（5Mbps） |
| `create_bgra_input_type()` | 配置 BGRA 输入（MF 自动转换） |
| `write_sample()` | 写入视频帧（带时间戳） |
| `finalize()` | Finalize 编码，输出 MP4 |

**关键配置**：
- 输入类型：`MFVideoFormat_RGB32`（BGRA）
- 输出类型：`MFVideoFormat_H264`
- MF 自动插入 Color Converter MFT（BGRA→NV12）

### 6.3 WASAPI 音频捕获 (`audio.rs`)

| 方法 | 功能 |
|------|------|
| `AudioCapture::new()` | 启动 WASAPI 音频捕获线程 |
| `get_packet()` | 获取音频数据包（带时间戳） |
| `stop()` | 停止音频捕获 |

**关键配置**：
- 模式：`AUDCLNT_STREAMFLAGS_LOOPBACK`（捕获系统音频）
- 格式：44.1kHz, 16bit, stereo

### 6.4 音视频同步机制

**双流写入**：
```
┌─────────────────────────────────────────────────────────────┐
│  IMFSinkWriter                                               │
│  ┌─────────────────────┐  ┌─────────────────────┐          │
│  │ Stream 0: Video     │  │ Stream 1: Audio     │          │
│  │ MFVideoFormat_H264  │  │ MFAudioFormat_AAC   │          │
│  └─────────────────────┘  └─────────────────────┘          │
│           │                        │                        │
│           │ SetSampleTime(t)       │ SetSampleTime(t)      │
│           │ 按帧时间戳             │ 按音频采样时间戳       │
│           ▼                        ▼                        │
│     WriteSample(0)           WriteSample(1)                 │
└─────────────────────────────────────────────────────────────┘
```

**时间戳计算**：
- 视频：`timestamp = frame_count * frame_duration`（100ns 单位）
- 音频：`timestamp = sample_count * (1_000_000_000 / sample_rate) / 100`

**同步策略**：SinkWriter 内部会根据时间戳交错写入视频和音频帧，无需手动同步。

---

## 7. 错误处理

### 7.1 Rust 错误类型

```rust
#[derive(Error, Debug)]
pub enum RecorderError {
    #[error("D3D11 device creation failed: {0}")]
    D3D11Error(String),
    
    #[error("Media Foundation error: {0}")]
    MFError(String),
    
    #[error("Invalid parameter: {0}")]
    InvalidParam(String),
    
    #[error("Frame size mismatch: expected {expected}, got {actual}")]
    FrameSizeMismatch { expected: usize, actual: usize },
    
    #[error("Monitor not found: {0}")]
    MonitorNotFound(u32),
}
```

### 7.2 Python 异常映射

| Rust 错误类型 | Python 异常 | 场景 |
|--------------|-------------|------|
| `InvalidParam` | `ValueError` | 参数校验失败 |
| `FrameSizeMismatch` | `ValueError` | 帧大小不匹配 |
| `MonitorNotFound` | `RuntimeError` | 显示器不存在 |
| `D3D11Error` | `RuntimeError` | GPU 设备失败 |
| `MFError` | `RuntimeError` | 编码器错误 |

---

## 8. Python 端集成改动

### 8.1 改动文件清单

| 文件 | 改动内容 |
|------|----------|
| `worker/screen/frame_source.py` | 新增 `get_frame_raw()` 方法 |
| `worker/screen/recorder.py` | 重写：替换 FFmpeg → win-recorder |
| `worker/screen/manager.py` | 帧队列支持 bytearray 模式 |
| `worker/actions/recording.py` | 新增 `monitor`/`audio` 参数 |
| `config/worker.yaml` | 新增 `audio_enabled` 配置 |
| `pyproject.toml` | 新增 `win-recorder` 依赖 |
| `scripts/build_windows.ps1` | 安装 win-recorder wheel |

### 8.2 frame_source.py 改动

```python
class WindowsFrameSource(FrameSource):
    def __init__(self, fps: int = 10, monitor: int = 1):
        self.monitor = monitor
        self._sct: Optional[mss.mss] = None  # 复用 mss 实例
        self._bgra_buffer: Optional[bytearray] = None
        self._bgra_memview: Optional[memoryview] = None
    
    def _ensure_sct(self) -> mss.mss:
        """确保 mss 实例可用（复用，避免重复初始化）"""
        if self._sct is None:
            self._sct = mss.mss()
        return self._sct
    
    def get_frame_raw(self) -> memoryview:
        """获取 BGRA 原始帧（录制用）
        
        内存流动：
        1. mss.grab() → 新建 bytearray (shot.raw，不可避免)
        2. buffer[:] = shot.raw → 一次内存拷贝到预分配 buffer
        3. Rust 端 PyByteArray::as_bytes() → 零拷贝 slice 引用
        """
        if self._bgra_buffer is None:
            width, height = self.get_screen_size()
            self._bgra_buffer = bytearray(width * height * 4)
            self._bgra_memview = memoryview(self._bgra_buffer)
        
        sct = self._ensure_sct()
        from worker.screen.monitor_utils import get_mapped_monitor_index
        _, target_monitor = get_mapped_monitor_index(self.monitor)
        
        # mss grab 返回新对象，需要拷贝到预分配 buffer
        shot = sct.grab(target_monitor)
        self._bgra_buffer[:] = shot.raw  # 内存拷贝
        
        return self._bgra_memview
    
    def stop(self) -> None:
        """清理 mss 实例"""
        if self._sct:
            self._sct.close()
            self._sct = None
```

**关键修正**：
- mss 实例复用（避免重复初始化 DXGI Desktop Duplication）
- 使用 `buffer[:] = shot.raw` 进行内存拷贝（mss 不支持 output 参数）

### 8.3 recorder.py 重写

```python
class ScreenRecorder:
    def start(self) -> None:
        self._recorder = win_recorder.WinRecorder(
            output_path=self.output_path,
            fps=self.fps,
            audio=self.audio,
            monitor=self.monitor,
        )
        self._recorder.start()
        self._write_thread = threading.Thread(target=self._write_loop, daemon=True)
        self._write_thread.start()
    
    def _write_loop(self) -> None:
        while not self._stop_event.is_set():
            frame = self.screen_manager.get_frame_raw()
            if frame and self._recorder:
                self._recorder.add_frame(frame)
```

---

## 9. HTTP API 使用示例

### 9.1 启动录制（主屏幕，无音频）

```json
{
  "action_type": "start_recording",
  "value": "test.mp4",
  "params": {
    "fps": 30,
    "monitor": 1
  }
}
```

### 9.2 启动录制（副屏幕，带音频）

```json
{
  "action_type": "start_recording",
  "params": {
    "fps": 30,
    "monitor": 2,
    "audio": true
  }
}
```

---

## 10. 配置项

### 10.1 config/worker.yaml

```yaml
recording:
  output_dir: data/recordings
  default_fps: 10
  max_timeout_ms: 7200000
  audio_enabled: false      # 默认不录制音频
```

---

## 11. 性能预期

| 指标 | 原方案（FFmpeg） | 新方案（win-recorder） |
|------|-----------------|----------------------|
| **打包体积** | ~90MB (ffmpeg.exe) | **<1MB (DLL)** |
| **CPU 占用** | ~15-20%（JPEG 解码 + 软编） | **<5%（GPU 硬编）** |
| **Python 端内存** | 每帧新建 JPEG bytes | **一次拷贝（mss → buffer）** |
| **Python→Rust** | 每帧新 bytes 对象 | **零拷贝（PyByteArray slice）** |
| **录制质量** | 10fps, 1080p | **30fps, 4K** |

**说明**：mss API 限制导致 Python 端无法完全避免内存分配，但通过预分配 buffer + PyByteArray slice，Python→Rust 实现零拷贝。

---

## 12. 约束与限制

| 约束 | 说明 |
|------|------|
| **仅 Windows** | Media Foundation 是 Windows 专用 API |
| **硬件要求** | 需支持 D3D11 的显卡（NVENC/QSV 硬编） |
| **Python 版本** | 3.10+ |

---

## 13. 后续迭代

| 版本 | 功能 |
|------|------|
| v0.2.0 | monitor=0 全部屏幕录制（虚拟屏幕合成） |
| v0.3.0 | H.265 HEVC 编码支持 |
| v0.4.0 | 实时推流（WebSocket） |
| v0.5.0 | 多显示器合并录制 |