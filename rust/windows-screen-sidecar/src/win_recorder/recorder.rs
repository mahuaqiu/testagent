//! 纯 Rust 屏幕录制器 - 从 win_recorder 移植，移除 PyO3 依赖
use crate::win_recorder::d3d11::D3D11TextureManager;
use crate::win_recorder::error::RecorderError;
use crate::win_recorder::logical_time::{
    local_hms_ms_from_pts_100ns, next_sample_timing, SampleTimingState,
};
use crate::win_recorder::mf_writer::{log_process_memory, MFSinkWriter};
use crate::win_recorder::watermark::WatermarkRenderer;
use parking_lot::Mutex;
use std::sync::Arc;
use windows::Win32::Media::MediaFoundation::*;

/// Windows 录屏器
///
/// 使用 D3D11 纹理和 Media Foundation SinkWriter 进行硬件编码。
/// 水印与 sample PTS 均由抓帧 `capture_pts_100ns` 派生。
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
    /// 相对首帧 capture 的容器时间轴状态
    timing: SampleTimingState,
}

impl WinRecorder {
    /// 创建录屏器
    pub fn new(
        output_path: String,
        fps: u32,
        audio: bool,
        monitor: u32,
        watermark: bool,
    ) -> Result<Self, RecorderError> {
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
            timing: SampleTimingState::default(),
        })
    }

    /// 开始录制
    pub fn start(&mut self) -> Result<(), RecorderError> {
        if self.recording {
            return Err(RecorderError::AlreadyRecording);
        }

        // 先创建 SinkWriter（内部会对齐分辨率）
        let mut sink_writer = MFSinkWriter::new(
            &self.output_path,
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

        // 锚点在首帧写入时设定，不在 start 时拍墙钟
        self.timing = SampleTimingState::default();

        // 更新内部尺寸为对齐后的尺寸
        self.width = aligned_width;
        self.height = aligned_height;

        self.texture_manager = Some(Arc::new(texture_manager));
        self.sink_writer = Some(Arc::new(Mutex::new(sink_writer)));
        self.recording = true;

        Ok(())
    }

    /// 写入一帧
    ///
    /// - `capture_pts_100ns`: 该帧抓取墙钟（Unix epoch 100ns）
    /// - `duplicated`: 是否为 tick 重复上一帧（水印冻结，容器 PTS 仍前进）
    pub fn write_frame(
        &mut self,
        frame_data: &[u8],
        capture_pts_100ns: i64,
        duplicated: bool,
    ) -> Result<(), RecorderError> {
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

        // 水印：始终使用该帧 capture 本地墙钟（duplicate 时调用方传入原 pts → 冻结）
        if self.watermark {
            if let Some(renderer) = &mut self.watermark_renderer {
                let time_str = local_hms_ms_from_pts_100ns(capture_pts_100ns);
                if let Err(e) = renderer.render(
                    texture_manager.context(),
                    texture_manager.staging_texture(),
                    self.width,
                    self.height,
                    &time_str,
                ) {
                    eprintln!("Warning: watermark render failed: {}", e);
                }
            }
        }

        // 直接从 staging 纹理创建 MF Sample（跳过 GPU 纹理，避免冗余拷贝）
        let sample = texture_manager.create_sample_from_staging()?;

        let (timestamp, duration) =
            next_sample_timing(&mut self.timing, capture_pts_100ns, duplicated, self.fps);

        // 写入 SinkWriter
        let mut writer = sink_writer.lock();
        writer.write_sample(&sample, timestamp, duration)?;

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
        self.timing = SampleTimingState::default();
        self.recording = false;

        // 关闭 Media Foundation
        unsafe {
            let _ = MFShutdown();
            log_process_memory("after_recorder_resources_dropped");
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
