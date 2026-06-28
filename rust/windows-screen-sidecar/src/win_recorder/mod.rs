//! 纯 Rust 屏幕录制和编码模块
//! 从 win_recorder 移植，去除了 PyO3 依赖

mod error;
mod memory_byte_stream;
mod bgra_to_nv12;
mod watermark;
mod d3d11;
mod mf_writer;
mod h264_encoder;
mod recorder;

pub use error::RecorderError;
pub use recorder::WinRecorder;
pub use h264_encoder::H264Encoder;

use parking_lot::Mutex;
use std::sync::Arc;

/// 全局 Media Foundation 初始化
pub fn init_media_foundation() -> Result<(), RecorderError> {
    unsafe {
        use windows::Win32::System::Com::{CoInitializeEx, COINIT_APARTMENTTHREADED};
        let _ = CoInitializeEx(None, COINIT_APARTMENTTHREADED);
    }
    Ok(())
}

/// 录制器管理器 - 封装 WinRecorder 用于 sidecar
pub struct RecordingContext {
    recorder: Option<WinRecorder>,
    output_path: String,
    width: u32,
    height: u32,
}

impl RecordingContext {
    pub fn new(output_path: String, fps: u32, audio: bool, monitor: u32, watermark: bool) -> Result<Self, RecorderError> {
        init_media_foundation()?;

        let recorder = WinRecorder::new(output_path.clone(), fps, audio, monitor, watermark)?;
        let width = recorder.width();
        let height = recorder.height();

        Ok(Self {
            recorder: Some(recorder),
            output_path,
            width,
            height,
        })
    }

    pub fn start(&mut self) -> Result<(), RecorderError> {
        if let Some(ref mut recorder) = self.recorder {
            recorder.start()?;
            // 更新对齐后的尺寸
            self.width = recorder.width();
            self.height = recorder.height();
            Ok(())
        } else {
            Err(RecorderError::NotRecording)
        }
    }

    pub fn write_frame(&mut self, bgra_data: &[u8]) -> Result<(), RecorderError> {
        if let Some(ref mut recorder) = self.recorder {
            recorder.write_frame(bgra_data)
        } else {
            Err(RecorderError::NotRecording)
        }
    }

    pub fn stop(&mut self) -> Result<String, RecorderError> {
        if let Some(ref mut recorder) = self.recorder {
            recorder.stop()?;
            Ok(self.output_path.clone())
        } else {
            Err(RecorderError::NotRecording)
        }
    }

    pub fn width(&self) -> u32 { self.width }
    pub fn height(&self) -> u32 { self.height }
}

/// H.264 编码器上下文
pub struct EncodingContext {
    encoder: Option<H264Encoder>,
    width: u32,
    height: u32,
    fps: u32,
}

impl EncodingContext {
    pub fn new(fps: u32, bitrate: u32, monitor: u32, profile: u32) -> Result<Self, RecorderError> {
        init_media_foundation()?;

        let mut encoder = H264Encoder::new(fps, bitrate, monitor, profile)?;
        let info = encoder.start()?;

        Ok(Self {
            encoder: Some(encoder),
            width: info.width,
            height: info.height,
            fps,
        })
    }

    pub fn encode_frame(&mut self, bgra_data: &[u8]) -> Result<Option<Vec<u8>>, RecorderError> {
        if let Some(ref mut encoder) = self.encoder {
            encoder.encode_frame(bgra_data)
        } else {
            Err(RecorderError::NotEncoding)
        }
    }

    pub fn stop(&mut self) -> Result<(), RecorderError> {
        if let Some(ref mut encoder) = self.encoder {
            encoder.stop()
        } else {
            Ok(())
        }
    }

    pub fn get_sps_pps(&self) -> Option<(Vec<u8>, Vec<u8>)> {
        self.encoder.as_ref().map(|e| e.get_sps_pps())
    }

    pub fn width(&self) -> u32 { self.width }
    pub fn height(&self) -> u32 { self.height }
    pub fn fps(&self) -> u32 { self.fps }
}