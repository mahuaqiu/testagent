//! 纯 Rust 屏幕录制器 - 从 win_recorder 移植，移除 PyO3 依赖
use crate::win_recorder::d3d11::D3D11TextureManager;
use crate::win_recorder::error::RecorderError;
use crate::win_recorder::mf_writer::MFSinkWriter;
use crate::win_recorder::watermark::WatermarkRenderer;
use parking_lot::Mutex;
use std::sync::Arc;
use windows::Win32::Media::MediaFoundation::*;

/// Windows 录屏器
///
/// 使用 D3D11 纹理和 Media Foundation SinkWriter 进行硬件编码
pub struct WinRecorder {
    texture_manager: Option<Arc<D3D11TextureManager>>,
    sink_writer: Option<Arc<Mutex<MFSinkWriter>>>,
    output_path: String,
    fps: u32,
    audio: bool,
    monitor: u32,
    width: u32,
    height: u32,
    recording: bool,
    watermark: bool,
    watermark_renderer: Option<WatermarkRenderer>,
}

impl WinRecorder {
    /// 创建录屏器
    pub fn new(output_path: String, fps: u32, audio: bool, monitor: u32, watermark: bool) -> Result<Self, RecorderError> {
        // 检测显示器尺寸
        let (width, height) = D3D11TextureManager::detect_monitor(monitor)?;

        let watermark_renderer = if watermark {
            Some(WatermarkRenderer::new())
        } else {
            None
        };

        Ok(Self {
            texture_manager: None,
            sink_writer: None,
            output_path,
            fps,
            audio,
            monitor,
            width,
            height,
            recording: false,
            watermark,
            watermark_renderer,
        })
    }

    /// 开始录制
    pub fn start(&mut self) -> Result<(), RecorderError> {
        if self.recording {
            return Err(RecorderError::AlreadyRecording);
        }

        // 先创建 SinkWriter（内部会对齐分辨率）
        let temp_texture_manager = D3D11TextureManager::new(self.width, self.height)?;
        let device = temp_texture_manager.device().clone();

        let mut sink_writer = MFSinkWriter::new(
            &self.output_path,
            &device,
            self.width,
            self.height,
            self.fps,
            self.audio,
        )?;

        // 获取对齐后的分辨率
        let aligned_width = sink_writer.width();
        let aligned_height = sink_writer.height();

        // 使用对齐后的分辨率创建纹理管理器
        let texture_manager = D3D11TextureManager::new(aligned_width, aligned_height)?;

        sink_writer.begin_writing()?;

        // 更新内部尺寸为对齐后的尺寸
        self.width = aligned_width;
        self.height = aligned_height;

        self.texture_manager = Some(Arc::new(texture_manager));
        self.sink_writer = Some(Arc::new(Mutex::new(sink_writer)));
        self.recording = true;

        Ok(())
    }

    /// 写入一帧 (纯 Rust 版本，接收 &[u8])
    pub fn write_frame(&mut self, frame_data: &[u8]) -> Result<(), RecorderError> {
        if !self.recording {
            return Err(RecorderError::NotRecording);
        }

        let texture_manager = self
            .texture_manager
            .as_ref()
            .ok_or(RecorderError::NotRecording)?;

        let sink_writer = self
            .sink_writer
            .as_ref()
            .ok_or(RecorderError::NotRecording)?;

        // 上传到 staging（不拷贝到 GPU）
        texture_manager.upload_bgra_to_staging(frame_data)?;

        // 如果开启水印，绘制水印到 staging 纹理
        if self.watermark {
            if let Some(renderer) = &mut self.watermark_renderer {
                if let Err(e) = renderer.render(
                    texture_manager.context(),
                    texture_manager.staging_texture(),
                    self.width,
                    self.height,
                ) {
                    eprintln!("Warning: watermark render failed: {}", e);
                }
            }
        }

        // 拷贝 staging 到 GPU
        texture_manager.copy_staging_to_gpu();

        // 创建 MF Sample
        let sample = texture_manager.create_mf_sample()?;

        // 写入 SinkWriter
        let mut writer = sink_writer.lock();
        writer.write_sample(&sample)?;

        Ok(())
    }

    /// 结束录制
    pub fn stop(&mut self) -> Result<(), RecorderError> {
        if !self.recording {
            return Err(RecorderError::NotRecording);
        }

        // 结束编码
        if let Some(sink_writer) = &self.sink_writer {
            let mut writer = sink_writer.lock();
            writer.finalize()?;
        }

        // 清理资源
        self.sink_writer = None;
        self.texture_manager = None;
        self.recording = false;

        // 关闭 Media Foundation
        unsafe {
            let _ = MFShutdown();
        }

        Ok(())
    }

    /// 获取视频宽度
    pub fn width(&self) -> u32 {
        self.width
    }

    /// 获取视频高度
    pub fn height(&self) -> u32 {
        self.height
    }

    /// 获取帧率
    pub fn fps(&self) -> u32 {
        self.fps
    }

    /// 获取是否正在录制
    pub fn is_recording(&self) -> bool {
        self.recording
    }

    /// 获取已编码帧数
    pub fn frame_count(&self) -> Result<u64, RecorderError> {
        let sink_writer = self
            .sink_writer
            .as_ref()
            .ok_or(RecorderError::NotRecording)?;

        let writer = sink_writer.lock();
        Ok(writer.frame_count())
    }

    /// 获取显示器尺寸（静态方法）
    pub fn get_monitor_size(monitor: u32) -> Result<(u32, u32), RecorderError> {
        D3D11TextureManager::detect_monitor(monitor)
    }
}