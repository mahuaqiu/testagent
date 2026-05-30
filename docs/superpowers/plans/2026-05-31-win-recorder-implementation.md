# win-recorder Rust 库实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 开发 Rust+Python 绑定的硬编录制库 win-recorder，实现 GPU 管线录屏（BGRA → D3D11 → Media Foundation → H.264 MP4）

**Architecture:** Python 端传入 BGRA bytearray → PyO3 零拷贝 slice → D3D11 Staging 纹理上传 → GPU 纹理 → MF SinkWriter 硬编 → MP4 文件。使用 Staging + Default 双纹理架构，MF 内置 Color Converter MFT 自动 BGRA→NV12 转换。

**Tech Stack:** Rust + PyO3 0.22 + windows-rs 0.58 (D3D11/MF/WASAPI)

**Spec:** `docs/superpowers/specs/2026-05-31-win-recorder-design.md`

**工程位置:** `D:\code\win-recorder`

---

## 文件结构

```
D:\code\win-recorder\
├── Cargo.toml                    # Rust 项目配置
├── pyproject.toml                # maturin wheel 构建配置
├── src/
│   ├── lib.rs                    # PyO3 模块入口（暴露 WinRecorder 类）
│   ├── recorder.rs               # WinRecorder 核心类（Python API）
│   ├── d3d11.rs                  # D3D11 设备 + 双纹理管理
│   ├── mf_writer.rs              # Media Foundation SinkWriter
│   ├── audio.rs                  # WASAPI 音频捕获（可选）
│   └── error.rs                  # RecorderError 错误类型
├── tests/
│   └── test_recorder.py          # Python 功能测试
└── README.md                     # 使用文档
```

---

## Task 1: 项目初始化

**Files:**
- Create: `D:\code\win-recorder\Cargo.toml`
- Create: `D:\code\win-recorder\pyproject.toml`
- Create: `D:\code\win-recorder\src\lib.rs`（骨架）

- [ ] **Step 1: 创建项目目录**

```powershell
mkdir D:\code\win-recorder
mkdir D:\code\win-recorder\src
mkdir D:\code\win-recorder\tests
```

- [ ] **Step 2: 创建 Cargo.toml**

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
]}
anyhow = "1.0"
thiserror = "1.0"
parking_lot = "0.12"

[build-dependencies]
pyo3-build-config = "0.22"
```

- [ ] **Step 3: 创建 pyproject.toml（maturin 配置）**

```toml
[build-system]
requires = ["maturin>=1.0"]
build-backend = "maturin"

[project]
name = "win-recorder"
version = "0.1.0"
requires-python = ">=3.10"

[tool.maturin]
features = ["pyo3/extension-module"]
```

- [ ] **Step 4: 创建 src/lib.rs 骨架**

```rust
use pyo3::prelude::*;

/// win-recorder: Windows 硬编录屏库
#[pymodule]
fn win_recorder(_py: Python<'_>, m: &Bound<'_, PyModule>) -> PyResult<()> {
    // TODO: 添加 WinRecorder 类
    Ok(())
}
```

- [ ] **Step 5: 验证 Rust 编译**

```powershell
cd D:\code\win-recorder
cargo check
```

Expected: 编译通过，无错误

- [ ] **Step 6: Commit**

```bash
git init
git add Cargo.toml pyproject.toml src/lib.rs
git commit -m "init: win-recorder Rust 项目骨架"
```

---

## Task 2: 错误类型定义

**Files:**
- Create: `D:\code\win-recorder\src\error.rs`

- [ ] **Step 1: 创建 error.rs**

```rust
use thiserror::Error;

/// win-recorder 错误类型
#[derive(Error, Debug)]
pub enum RecorderError {
    #[error("D3D11 device creation failed: {0}")]
    D3D11Error(String),

    #[error("D3D11 texture operation failed: {0}")]
    D3D11TextureError(String),

    #[error("Media Foundation error: {0}")]
    MFError(String),

    #[error("WASAPI audio error: {0}")]
    AudioError(String),

    #[error("Invalid parameter: {0}")]
    InvalidParam(String),

    #[error("Frame size mismatch: expected {expected} bytes, got {actual} bytes")]
    FrameSizeMismatch { expected: usize, actual: usize },

    #[error("Monitor not found: monitor={monitor}")]
    MonitorNotFound { monitor: u32 },

    #[error("Not recording")]
    NotRecording,

    #[error("Already recording")]
    AlreadyRecording,

    #[error("Recording failed: {0}")]
    RecordingFailed(String),
}

/// 转换为 Python 异常
impl From<RecorderError> for pyo3::PyErr {
    fn from(err: RecorderError) -> Self {
        use pyo3::exceptions::*;
        match err {
            RecorderError::InvalidParam(_) |
            RecorderError::FrameSizeMismatch { .. } => {
                PyValueError::new_err(err.to_string())
            }
            _ => PyRuntimeError::new_err(err.to_string()),
        }
    }
}
```

- [ ] **Step 2: 在 lib.rs 中引入 error 模块**

```rust
mod error;

use pyo3::prelude::*;
```

- [ ] **Step 3: 验证编译**

```powershell
cargo check
```

Expected: 编译通过

- [ ] **Step 4: Commit**

```bash
git add src/error.rs src/lib.rs
git commit -m "feat: 添加 RecorderError 错误类型"
```

---

## Task 3: D3D11 纹理管理模块

**Files:**
- Create: `D:\code\win-recorder\src\d3d11.rs`

- [ ] **Step 1: 创建 d3d11.rs 骨架**

```rust
use anyhow::Result;
use windows::{
    Win32::Graphics::Direct3D11::*,
    Win32::Graphics::Direct3D::*,
    Win32::Graphics::Dxgi::*,
};

use crate::error::RecorderError;

/// D3D11 GPU 纹理上下文（双纹理架构）
pub struct D3D11Context {
    device: ID3D11Device,
    context: ID3D11DeviceContext,
    staging_texture: ID3D11Texture2D,  // CPU 写入
    gpu_texture: ID3D11Texture2D,       // GPU 共享（MF）
    width: u32,
    height: u32,
}
```

- [ ] **Step 2: 实现 D3D11Context::new()**

```rust
impl D3D11Context {
    /// 创建 D3D11 设备和双纹理
    pub fn new(width: u32, height: u32) -> Result<Self, RecorderError> {
        // 创建 D3D11 设备
        let mut device = None;
        let mut context = None;
        let feature_levels = [D3D_FEATURE_LEVEL_11_0, D3D_FEATURE_LEVEL_10_1];

        unsafe {
            D3D11CreateDevice(
                None,
                D3D_DRIVER_TYPE_HARDWARE,
                None,
                D3D11_CREATE_DEVICE_VIDEO_SUPPORT,
                Some(&feature_levels),
                D3D11_SDK_VERSION,
                Some(&mut device),
                None,
                Some(&mut context),
            )
        }
        .map_err(|e| RecorderError::D3D11Error(format!("Device creation failed: {}", e)))?;

        let device = device.unwrap();
        let context = context.unwrap();

        // 创建 Staging 纹理（CPU 可写入）
        let staging_desc = D3D11_TEXTURE2D_DESC {
            Width: width,
            Height: height,
            MipLevels: 1,
            ArraySize: 1,
            Format: DXGI_FORMAT_B8G8R8A8_UNORM,
            SampleDesc: DXGI_SAMPLE_DESC { Count: 1, Quality: 0 },
            Usage: D3D11_USAGE_STAGING,
            BindFlags: 0,
            CPUAccessFlags: D3D11_CPU_ACCESS_WRITE,
            MiscFlags: 0,
        };

        let staging_texture = unsafe { device.CreateTexture2D(&staging_desc, None) }
            .map_err(|e| RecorderError::D3D11TextureError(format!("Staging texture failed: {}", e)))?;

        // 创建 GPU 纹理（可共享给 MF）
        let gpu_desc = D3D11_TEXTURE2D_DESC {
            Width: width,
            Height: height,
            MipLevels: 1,
            ArraySize: 1,
            Format: DXGI_FORMAT_B8G8R8A8_UNORM,
            SampleDesc: DXGI_SAMPLE_DESC { Count: 1, Quality: 0 },
            Usage: D3D11_USAGE_DEFAULT,
            BindFlags: D3D11_BIND_SHADER_RESOURCE | D3D11_BIND_RENDER_TARGET,
            CPUAccessFlags: 0,
            MiscFlags: D3D11_RESOURCE_MISC_SHARED,
        };

        let gpu_texture = unsafe { device.CreateTexture2D(&gpu_desc, None) }
            .map_err(|e| RecorderError::D3D11TextureError(format!("GPU texture failed: {}", e)))?;

        Ok(Self {
            device,
            context,
            staging_texture,
            gpu_texture,
            width,
            height,
        })
    }
}
```

- [ ] **Step 3: 实现 upload_bgra()**

```rust
impl D3D11Context {
    /// 上传 BGRA 数据到 GPU 纹理
    /// 流程：Map Staging → 写入 → Unmap → CopyResource 到 GPU
    pub fn upload_bgra(&self, bgra_data: &[u8]) -> Result<(), RecorderError> {
        // 校验大小
        let expected_size = self.width * self.height * 4;
        if bgra_data.len() != expected_size as usize {
            return Err(RecorderError::FrameSizeMismatch {
                expected: expected_size as usize,
                actual: bgra_data.len(),
            });
        }

        // Map Staging 纹理
        let mapped = unsafe {
            self.context.Map(
                &self.staging_texture,
                0,
                D3D11_MAP_WRITE,
                0,
            )
        }
        .map_err(|e| RecorderError::D3D11TextureError(format!("Map failed: {}", e)))?;

        // 写入 BGRA 数据（逐行拷贝，考虑 row pitch）
        unsafe {
            let dst_ptr = mapped.pData as *mut u8;
            let row_pitch = mapped.RowPitch as usize;
            let src_row_pitch = self.width as usize * 4;

            for row in 0..self.height as usize {
                let src_offset = row * src_row_pitch;
                let dst_offset = row * row_pitch;
                std::ptr::copy_nonoverlapping(
                    bgra_data.as_ptr().add(src_offset),
                    dst_ptr.add(dst_offset),
                    src_row_pitch,
                );
            }
        }

        // Unmap
        unsafe { self.context.Unmap(&self.staging_texture, 0) };

        // CopyResource: Staging → GPU
        unsafe { self.context.CopyResource(&self.gpu_texture, &self.staging_texture) };

        Ok(())
    }
}
```

- [ ] **Step 4: 实现 create_mf_sample()**

```rust
use windows::Win32::Media::MediaFoundation::*;

impl D3D11Context {
    /// 将 GPU 纹理包装成 MF Sample（供 SinkWriter 使用）
    pub fn create_mf_sample(&self) -> Result<IMFSample, RecorderError> {
        // 获取 DXGI Surface
        let dxgi_surface: IDXGISurface = unsafe { self.gpu_texture.cast() }
            .map_err(|e| RecorderError::D3D11TextureError(format!("Cast to surface failed: {}", e)))?;

        // 创建 MF Buffer（GPU-backed）
        let buffer = unsafe { MFCreateDXGISurfaceBuffer(&dxgi_surface, false) }
            .map_err(|e| RecorderError::MFError(format!("Create buffer failed: {}", e)))?;

        // 创建 MF Sample
        let sample = unsafe { MFCreateSample() }
            .map_err(|e| RecorderError::MFError(format!("Create sample failed: {}", e)))?;

        unsafe { sample.AddBuffer(&buffer) }
            .map_err(|e| RecorderError::MFError(format!("Add buffer failed: {}", e)))?;

        Ok(sample)
    }
}
```

- [ ] **Step 5: 实现显示器检测 detect_monitor()**

```rust
impl D3D11Context {
    /// 检测显示器配置
    /// monitor=1: 主屏幕（left=0）
    /// monitor=2: 副屏幕（另一个显示器）
    pub fn detect_monitor(monitor: u32) -> Result<(u32, u32), RecorderError> {
        unsafe {
            let dxgi_factory = CreateDXGIFactory()
                .map_err(|e| RecorderError::D3D11Error(format!("DXGI factory failed: {}", e)))?;

            // 收集所有显示器
            let mut outputs: Vec<IDXGIOutput> = Vec::new();
            for i in 0..10 {
                let adapter = dxgi_factory.EnumAdapters(i);
                if adapter.is_err() {
                    break;
                }
                let adapter = adapter.unwrap();
                for j in 0..10 {
                    let output = adapter.EnumOutputs(j);
                    if output.is_err() {
                        break;
                    }
                    outputs.push(output.unwrap());
                }
            }

            // 按 left 坐标排序
            let mut desc_list: Vec<DXGI_OUTPUT_DESC> = outputs
                .iter()
                .map(|o| unsafe { o.GetDesc() })
                .collect();
            desc_list.sort_by_key(|d| d.DesktopCoordinates.left);

            match monitor {
                1 => {
                    // 主屏幕：left=0
                    let primary = desc_list
                        .iter()
                        .find(|d| d.DesktopCoordinates.left == 0)
                        .ok_or(RecorderError::MonitorNotFound { monitor })?;
                    let rect = primary.DesktopCoordinates;
                    Ok((rect.right - rect.left, rect.bottom - rect.top))
                }
                2 => {
                    // 副屏幕：另一个显示器
                    let secondary = desc_list
                        .iter()
                        .find(|d| d.DesktopCoordinates.left != 0)
                        .ok_or(RecorderError::MonitorNotFound { monitor })?;
                    let rect = secondary.DesktopCoordinates;
                    Ok((rect.right - rect.left, rect.bottom - rect.top))
                }
                _ => Err(RecorderError::InvalidParam("monitor must be 1 or 2".into())),
            }
        }
    }
}
```

- [ ] **Step 6: 在 lib.rs 中引入 d3d11 模块**

```rust
mod error;
mod d3d11;
```

- [ ] **Step 7: 验证编译**

```powershell
cargo check
```

Expected: 编译通过（可能有 unused warnings）

- [ ] **Step 8: Commit**

```bash
git add src/d3d11.rs src/lib.rs
git commit -m "feat: D3D11 双纹理管理模块"
```

---

## Task 4: Media Foundation SinkWriter 模块

**Files:**
- Create: `D:\code\win-recorder\src\mf_writer.rs`

- [ ] **Step 1: 创建 mf_writer.rs 骨架**

```rust
use anyhow::Result;
use std::path::PathBuf;
use windows::{
    Win32::Media::MediaFoundation::*,
    Win32::System::Com::*,
};

use crate::error::RecorderError;

/// Media Foundation SinkWriter（硬编输出）
pub struct MFSinkWriter {
    writer: IMFSinkWriter,
    video_stream_index: u32,
    width: u32,
    height: u32,
    fps: u32,
    frame_duration: i64,  // 每帧时长（100ns 单位）
}
```

- [ ] **Step 2: 实现 MFStartup/MFShutdown 辅助函数**

```rust
/// 启动 Media Foundation
pub fn mf_startup() -> Result<(), RecorderError> {
    unsafe { MFStartup(MF_VERSION, MF_STARTUP_LITE) }
        .map_err(|e| RecorderError::MFError(format!("MFStartup failed: {}", e)))?;
    Ok(())
}

/// 关闭 Media Foundation
pub fn mf_shutdown() -> Result<(), RecorderError> {
    unsafe { MFShutdown() }
        .map_err(|e| RecorderError::MFError(format!("MFShutdown failed: {}", e)))?;
    Ok(())
}
```

- [ ] **Step 3: 实现 MFSinkWriter::new()**

```rust
impl MFSinkWriter {
    /// 创建 SinkWriter（仅视频）
    pub fn new(
        output_path: &PathBuf,
        width: u32,
        height: u32,
        fps: u32,
    ) -> Result<Self, RecorderError> {
        mf_startup()?;

        unsafe {
            // 创建 MP4 Media Sink
            let sink = MFCreateTranscodeSinkActivate()
                .map_err(|e| RecorderError::MFError(format!("Create sink failed: {}", e)))?;

            // 创建 SinkWriter
            let writer = MFCreateSinkWriterFromMediaSink(&sink, None)
                .map_err(|e| RecorderError::MFError(format!("Create writer failed: {}", e)))?;

            // 配置视频输出类型（H.264）
            let output_type = Self::create_h264_output_type(width, height, fps)?;

            // 配置视频输入类型（BGRA）
            let input_type = Self::create_bgra_input_type(width, height, fps)?;

            // 添加视频流
            let video_index = writer.AddStream(&output_type)
                .map_err(|e| RecorderError::MFError(format!("Add stream failed: {}", e)))?;

            // 设置输入类型（MF 自动插入 Color Converter MFT）
            writer.SetInputMediaType(video_index, &input_type, None)
                .map_err(|e| RecorderError::MFError(format!("Set input type failed: {}", e)))?;

            // 开始写入
            writer.BeginWriting()
                .map_err(|e| RecorderError::MFError(format!("BeginWriting failed: {}", e)))?;

            // 计算帧时长（100ns 单位）
            let frame_duration = (10_000_000_i64 / fps as i64);

            Ok(Self {
                writer,
                video_stream_index: video_index,
                width,
                height,
                fps,
                frame_duration,
            })
        }
    }
}
```

- [ ] **Step 4: 实现 create_h264_output_type()**

```rust
impl MFSinkWriter {
    /// 创建 H.264 输出媒体类型
    fn create_h264_output_type(width: u32, height: u32, fps: u32) -> Result<IMFMediaType, RecorderError> {
        unsafe {
            let media_type = MFCreateMediaType()
                .map_err(|e| RecorderError::MFError(format!("Create media type failed: {}", e)))?;

            media_type.SetGUID(MF_MT_MAJOR_TYPE, MFMediaType_Video)
                .map_err(|e| RecorderError::MFError(format!("Set major type failed: {}", e)))?;

            media_type.SetGUID(MF_MT_SUBTYPE, MFVideoFormat_H264)
                .map_err(|e| RecorderError::MFError(format!("Set subtype failed: {}", e)))?;

            media_type.SetUINT32(MF_MT_AVG_BITRATE, 5_000_000)
                .map_err(|e| RecorderError::MFError(format!("Set bitrate failed: {}", e)))?;

            media_type.SetUINT32(MF_MT_INTERLACE_MODE, MFVideoInterlace_Progressive)
                .map_err(|e| RecorderError::MFError(format!("Set interlace failed: {}", e)))?;

            MFSetAttributeSize(&media_type, MF_MT_FRAME_SIZE, width, height)
                .map_err(|e| RecorderError::MFError(format!("Set frame size failed: {}", e)))?;

            MFSetAttributeRatio(&media_type, MF_MT_FRAME_RATE, fps, 1)
                .map_err(|e| RecorderError::MFError(format!("Set frame rate failed: {}", e)))?;

            Ok(media_type)
        }
    }
}
```

- [ ] **Step 5: 实现 create_bgra_input_type()**

```rust
impl MFSinkWriter {
    /// 创建 BGRA 输入媒体类型
    /// MF 自动插入 Color Converter MFT：BGRA → NV12
    fn create_bgra_input_type(width: u32, height: u32, fps: u32) -> Result<IMFMediaType, RecorderError> {
        unsafe {
            let media_type = MFCreateMediaType()
                .map_err(|e| RecorderError::MFError(format!("Create media type failed: {}", e)))?;

            media_type.SetGUID(MF_MT_MAJOR_TYPE, MFMediaType_Video)
                .map_err(|e| RecorderError::MFError(format!("Set major type failed: {}", e)))?;

            // RGB32 = BGRA
            media_type.SetGUID(MF_MT_SUBTYPE, MFVideoFormat_RGB32)
                .map_err(|e| RecorderError::MFError(format!("Set subtype failed: {}", e)))?;

            media_type.SetUINT32(MF_MT_INTERLACE_MODE, MFVideoInterlace_Progressive)
                .map_err(|e| RecorderError::MFError(format!("Set interlace failed: {}", e)))?;

            MFSetAttributeSize(&media_type, MF_MT_FRAME_SIZE, width, height)
                .map_err(|e| RecorderError::MFError(format!("Set frame size failed: {}", e)))?;

            MFSetAttributeRatio(&media_type, MF_MT_FRAME_RATE, fps, 1)
                .map_err(|e| RecorderError::MFError(format!("Set frame rate failed: {}", e)))?;

            Ok(media_type)
        }
    }
}
```

- [ ] **Step 6: 实现 write_sample()**

```rust
impl MFSinkWriter {
    /// 写入视频帧
    pub fn write_sample(&self, sample: IMFSample, frame_count: u64) -> Result<(), RecorderError> {
        unsafe {
            // 设置时间戳
            let timestamp = frame_count as i64 * self.frame_duration;
            sample.SetSampleTime(timestamp)
                .map_err(|e| RecorderError::MFError(format!("Set sample time failed: {}", e)))?;

            sample.SetSampleDuration(self.frame_duration)
                .map_err(|e| RecorderError::MFError(format!("Set sample duration failed: {}", e)))?;

            // 写入
            self.writer.WriteSample(self.video_stream_index, &sample)
                .map_err(|e| RecorderError::MFError(format!("Write sample failed: {}", e)))?;
        }
        Ok(())
    }
}
```

- [ ] **Step 7: 实现 finalize()**

```rust
impl MFSinkWriter {
    /// Finalize 编码，输出 MP4
    pub fn finalize(&self) -> Result<(), RecorderError> {
        unsafe {
            self.writer.Flush(self.video_stream_index)
                .map_err(|e| RecorderError::MFError(format!("Flush failed: {}", e)))?;

            self.writer.Finalize()
                .map_err(|e| RecorderError::MFError(format!("Finalize failed: {}", e)))?;
        }

        mf_shutdown()?;
        Ok(())
    }
}
```

- [ ] **Step 8: 在 lib.rs 中引入 mf_writer 模块**

```rust
mod error;
mod d3d11;
mod mf_writer;
```

- [ ] **Step 9: 验证编译**

```powershell
cargo check
```

Expected: 编译通过

- [ ] **Step 10: Commit**

```bash
git add src/mf_writer.rs src/lib.rs
git commit -m "feat: Media Foundation SinkWriter 模块"
```

---

## Task 5: WinRecorder 核心类（Python API）

**Files:**
- Create: `D:\code\win-recorder\src\recorder.rs`
- Modify: `D:\code\win-recorder\src\lib.rs`

- [ ] **Step 1: 创建 recorder.rs**

```rust
use pyo3::prelude::*;
use pyo3::types::PyByteArray;
use std::path::PathBuf;

use crate::d3d11::D3D11Context;
use crate::mf_writer::MFSinkWriter;
use crate::error::RecorderError;

/// Windows 硬编录制器
#[pyclass]
pub struct WinRecorder {
    output_path: PathBuf,
    fps: u32,
    audio: bool,
    monitor: u32,

    // 内部状态（启动后初始化）
    width: u32,
    height: u32,
    d3d11_ctx: Option<D3D11Context>,
    mf_writer: Option<MFSinkWriter>,
    frame_count: u64,
    is_recording: bool,
}
```

- [ ] **Step 2: 实现 #[new] 构造函数**

```rust
#[pymethods]
impl WinRecorder {
    #[new]
    #[pyo3(signature = (output_path, fps=30, audio=false, monitor=1))]
    fn new(
        output_path: String,
        fps: u32,
        audio: bool,
        monitor: u32,
    ) -> PyResult<Self> {
        // 参数校验
        if monitor != 1 && monitor != 2 {
            return Err(RecorderError::InvalidParam("monitor must be 1 or 2".into()).into());
        }
        if fps == 0 || fps > 120 {
            return Err(RecorderError::InvalidParam("fps must be 1-120".into()).into());
        }

        Ok(Self {
            output_path: PathBuf::from(output_path),
            fps,
            audio,
            monitor,
            width: 0,
            height: 0,
            d3d11_ctx: None,
            mf_writer: None,
            frame_count: 0,
            is_recording: false,
        })
    }
}
```

- [ ] **Step 3: 实现静态方法 get_monitor_size()**

```rust
#[pymethods]
impl WinRecorder {
    /// 获取显示器尺寸（供 Python 端预分配 buffer）
    #[staticmethod]
    #[pyo3(signature = (monitor=1))]
    fn get_monitor_size(monitor: u32) -> PyResult<(u32, u32)> {
        D3D11Context::detect_monitor(monitor)
            .map_err(|e| e.into())
    }
}
```

- [ ] **Step 4: 实现 start()**

```rust
#[pymethods]
impl WinRecorder {
    /// 启动录制
    fn start(&mut self) -> PyResult<()> {
        if self.is_recording {
            return Err(RecorderError::AlreadyRecording.into());
        }

        // 检测显示器尺寸
        let (width, height) = D3D11Context::detect_monitor(self.monitor)?;
        self.width = width;
        self.height = height;

        // 初始化 D3D11
        self.d3d11_ctx = Some(D3D11Context::new(width, height)?);

        // 初始化 MF SinkWriter
        self.mf_writer = Some(MFSinkWriter::new(
            &self.output_path,
            width,
            height,
            self.fps,
        )?);

        self.frame_count = 0;
        self.is_recording = true;
        Ok(())
    }
}
```

- [ ] **Step 5: 实现 add_frame()（关键：PyByteArray 零拷贝）**

```rust
#[pymethods]
impl WinRecorder {
    /// 添加一帧（零拷贝：PyByteArray → &[u8] slice）
    fn add_frame(&mut self, frame: &PyByteArray) -> PyResult<()> {
        if !self.is_recording {
            return Err(RecorderError::NotRecording.into());
        }

        let bgra_data: &[u8] = frame.as_bytes();  // 零拷贝 slice

        // 上传到 GPU
        self.d3d11_ctx
            .as_ref()
            .unwrap()
            .upload_bgra(bgra_data)?;

        // 创建 MF Sample
        let sample = self.d3d11_ctx
            .as_ref()
            .unwrap()
            .create_mf_sample()?;

        // 写入
        self.mf_writer
            .as_ref()
            .unwrap()
            .write_sample(sample, self.frame_count)?;

        self.frame_count += 1;
        Ok(())
    }
}
```

- [ ] **Step 6: 实现 stop()**

```rust
#[pymethods]
impl WinRecorder {
    /// 停止录制，返回输出文件路径
    fn stop(&mut self) -> PyResult<String> {
        if !self.is_recording {
            return Err(RecorderError::NotRecording.into());
        }

        // Finalize MF
        self.mf_writer
            .as_ref()
            .unwrap()
            .finalize()?;

        // 清理资源
        self.d3d11_ctx = None;
        self.mf_writer = None;
        self.is_recording = false;

        Ok(self.output_path.to_string_lossy().to_string())
    }
}
```

- [ ] **Step 7: 实现 get_info()**

```rust
#[pymethods]
impl WinRecorder {
    /// 获取录制信息
    fn get_info(&self) -> PyResult<PyObject> {
        Python::with_gil(|py| {
            let dict = pyo3::types::PyDict::new(py);
            dict.set_item("width", self.width)?;
            dict.set_item("height", self.height)?;
            dict.set_item("fps", self.fps)?;
            dict.set_item("monitor", self.monitor)?;
            dict.set_item("audio", self.audio)?;
            dict.set_item("frame_count", self.frame_count)?;
            dict.set_item("is_recording", self.is_recording)?;
            Ok(dict.into())
        })
    }
}
```

- [ ] **Step 8: 在 lib.rs 中暴露 WinRecorder**

```rust
mod error;
mod d3d11;
mod mf_writer;
mod recorder;

use pyo3::prelude::*;
use recorder::WinRecorder;

/// win-recorder: Windows 硬编录屏库
#[pymodule]
fn win_recorder(_py: Python<'_>, m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_class::<WinRecorder>()?;
    Ok(())
}
```

- [ ] **Step 9: 验证编译**

```powershell
cargo check
```

Expected: 编译通过

- [ ] **Step 10: Commit**

```bash
git add src/recorder.rs src/lib.rs
git commit -m "feat: WinRecorder Python API（PyByteArray 零拷贝）"
```

---

## Task 6: 构建 Python Wheel

**Files:**
- Modify: `D:\code\win-recorder\Cargo.toml`
- Create: `D:\code\win-recorder\tests\test_recorder.py`

- [ ] **Step 1: 安装 maturin**

```powershell
pip install maturin
```

- [ ] **Step 2: 构建 wheel**

```powershell
cd D:\code\win-recorder
maturin develop
```

Expected: 编译成功，生成 `.whl` 文件

- [ ] **Step 3: 创建 Python 测试文件**

```python
# tests/test_recorder.py
import os
import tempfile
import win_recorder

def test_get_monitor_size():
    """测试显示器尺寸获取"""
    width, height = win_recorder.WinRecorder.get_monitor_size(monitor=1)
    assert width > 0
    assert height > 0
    print(f"Primary monitor: {width}x{height}")

def test_basic_recording():
    """测试基本录制功能（使用模拟帧）"""
    width, height = win_recorder.WinRecorder.get_monitor_size(monitor=1)
    
    with tempfile.NamedTemporaryFile(suffix=".mp4", delete=False) as f:
        output_path = f.name

    try:
        recorder = win_recorder.WinRecorder(
            output_path=output_path,
            fps=10,
            audio=False,
            monitor=1,
        )

        recorder.start()

        # 创建模拟 BGRA 帧（纯绿色）
        frame = bytearray(width * height * 4)
        for i in range(len(frame) // 4):
            frame[i * 4] = 0      # B
            frame[i * 4 + 1] = 255  # G
            frame[i * 4 + 2] = 0    # R
            frame[i * 4 + 3] = 255  # A

        # 添加 30 帧
        for _ in range(30):
            recorder.add_frame(frame)

        result = recorder.stop()
        assert result == output_path
        assert os.path.exists(output_path)
        assert os.path.getsize(output_path) > 0
        print(f"Output file: {output_path}, size: {os.path.getsize(output_path)} bytes")

    finally:
        if os.path.exists(output_path):
            os.unlink(output_path)

if __name__ == "__main__":
    test_get_monitor_size()
    test_basic_recording()
    print("All tests passed!")
```

- [ ] **Step 4: 运行测试**

```powershell
python tests/test_recorder.py
```

Expected: 测试通过，生成有效 MP4 文件

- [ ] **Step 5: Commit**

```bash
git add tests/test_recorder.py
git commit -m "test: Python 功能测试"
```

---

## Task 7: 创建 README.md

**Files:**
- Create: `D:\code\win-recorder\README.md`

- [ ] **Step 1: 创建 README**

```markdown
# win-recorder

Windows 硬编录屏库（Rust + Python 绑定）

## 特性

- GPU 管线硬编（D3D11 + Media Foundation）
- 零拷贝 Python→Rust（PyByteArray slice）
- 支持 30fps + 4K
- 可选音频录制（WASAPI LOOPBACK）
- DLL < 1MB，无 FFmpeg 依赖

## 安装

```bash
pip install win-recorder
```

## 使用

```python
import win_recorder

# 获取显示器尺寸
width, height = win_recorder.WinRecorder.get_monitor_size(monitor=1)

# 预分配 BGRA buffer
buffer = bytearray(width * height * 4)

# 创建录制器
recorder = win_recorder.WinRecorder(
    output_path="output.mp4",
    fps=30,
    audio=False,
    monitor=1,  # 主屏幕
)

recorder.start()

# 添加帧（配合 mss 截屏）
import mss
sct = mss.mss()
shot = sct.grab(mss.mss().monitors[1])
buffer[:] = shot.raw  # BGRA 数据拷贝
recorder.add_frame(buffer)

# 停止录制
recorder.stop()
```

## Monitor 参数

| monitor | 说明 |
|---------|------|
| 1 | 主屏幕（left=0） |
| 2 | 副屏幕 |

## 构建

```bash
maturin develop
```
```

- [ ] **Step 2: Commit**

```bash
git add README.md
git commit -m "docs: README 使用文档"
```

---

## Task 8: 打包发布 Wheel

**Files:**
- Build: `D:\code\win-recorder\target\wheels\win_recorder-0.1.0-*.whl`

- [ ] **Step 1: 构建 release wheel**

```powershell
cd D:\code\win-recorder
maturin build --release
```

Expected: 生成 wheel 文件到 `target/wheels/`

- [ ] **Step 2: 检查 wheel 大小**

```powershell
dir target\wheels\*.whl
```

Expected: wheel 大小 < 1MB

- [ ] **Step 3: 测试安装 wheel**

```powershell
pip install target\wheels\win_recorder-0.1.0-*.whl --force-reinstall
python tests\test_recorder.py
```

Expected: 测试通过

- [ ] **Step 4: Commit release**

```bash
git add .
git commit -m "release: win-recorder v0.1.0"
```

---

## 完成检查点

| 检查项 | 验证命令 |
|--------|----------|
| **Rust 编译** | `cargo check` |
| **Wheel 构建** | `maturin build --release` |
| **Python 测试** | `python tests/test_recorder.py` |
| **DLL 大小** | wheel < 1MB |

---

## 后续计划

完成 win-recorder Rust 库后，需要进行 **autotest Python 集成计划**：

1. 修改 `worker/screen/frame_source.py` 新增 `get_frame_raw()`
2. 重写 `worker/screen/recorder.py` 替换 FFmpeg
3. 修改 `worker/screen/manager.py` 支持 bytearray 模式
4. 修改 `worker/actions/recording.py` 新增 monitor/audio 参数
5. 修改 `config/worker.yaml` 新增 audio_enabled 配置
6. 修改 `pyproject.toml` 新增 win-recorder 依赖
7. 修改 `scripts/build_windows.ps1` 安装 win-recorder wheel

---

**计划完成。**