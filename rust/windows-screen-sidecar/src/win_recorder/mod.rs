//! 纯 Rust 屏幕录制和编码模块
//! 从 win_recorder 移植，去除了 PyO3 依赖

mod bgra_to_nv12;
mod d3d11;
mod error;
mod h264_encoder;
mod logical_time;
mod memory_byte_stream;
mod mf_writer;
mod recorder;
mod watermark;

pub use error::RecorderError;
pub use h264_encoder::{EncodedFrame, FrameType, H264Encoder};
pub use logical_time::{
    frame_duration_100ns, frame_index_to_pts_100ns, local_hms_ms_from_pts_100ns,
    next_sample_timing, now_as_pts_100ns, sample_timing_for_frame_index, SampleTimingState,
};
pub use recorder::WinRecorder;

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
    pub fn new(
        output_path: String,
        fps: u32,
        audio: bool,
        monitor: u32,
        watermark: bool,
    ) -> Result<Self, RecorderError> {
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

    pub fn write_frame(
        &mut self,
        bgra_data: &[u8],
        capture_pts_100ns: i64,
        duplicated: bool,
    ) -> Result<(), RecorderError> {
        if let Some(ref mut recorder) = self.recorder {
            recorder.write_frame(bgra_data, capture_pts_100ns, duplicated)
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

    pub fn width(&self) -> u32 {
        self.width
    }
    pub fn height(&self) -> u32 {
        self.height
    }
}

impl crate::media::FrameSink for RecordingContext {
    fn write_frame(&mut self, frame: crate::media::WriteFrame<'_>) -> Result<(), String> {
        RecordingContext::write_frame(
            self,
            frame.bgra,
            frame.capture_pts_100ns,
            frame.duplicated,
        )
        .map_err(|e| e.to_string())
    }

    fn stop(&mut self) -> Result<(), String> {
        RecordingContext::stop(self)
            .map(|_| ())
            .map_err(|e| e.to_string())
    }
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

        let mut encoder = H264Encoder::new_low_latency(fps, bitrate, monitor, profile)?;
        let info = encoder.start()?;

        Ok(Self {
            encoder: Some(encoder),
            width: info.width,
            height: info.height,
            fps,
        })
    }

    /// 使用真实屏幕帧预热编码器，返回首个真实关键帧。
    pub fn prime_with_frame_data(
        &mut self,
        bgra_data: &[u8],
    ) -> Result<Option<Vec<EncodedFrame>>, RecorderError> {
        if let Some(ref mut encoder) = self.encoder {
            encoder.prime_with_frame_data(bgra_data)
        } else {
            Err(RecorderError::NotEncoding)
        }
    }
    pub fn encode_frame(&mut self, bgra_data: &[u8]) -> Result<Option<Vec<u8>>, RecorderError> {
        if let Some(ref mut encoder) = self.encoder {
            encoder.encode_frame(bgra_data)
        } else {
            Err(RecorderError::NotEncoding)
        }
    }

    /// 逐 NAL 输出（供推流使用）：返回本帧编码出的所有 NAL 及其类型，不做拼包、不加自定义前缀。
    /// 推流路径据此分别推送 SPS/PPS/IDR/P 帧到 stderr，避免与拼包前缀字节混淆。
    pub fn encode_frames_detailed(
        &mut self,
        bgra_data: &[u8],
    ) -> Result<Option<Vec<EncodedFrame>>, RecorderError> {
        if let Some(ref mut encoder) = self.encoder {
            let frames = encoder.encode_frame_data(bgra_data)?;
            if frames.is_empty() {
                return Ok(None);
            }
            // 过滤掉 AUD (nal_type=9)，与拼包路径行为一致
            let filtered: Vec<EncodedFrame> = frames
                .into_iter()
                .filter(|f| !(f.data.len() > 4 && (f.data[4] & 0x1F) == 9))
                .collect();
            if filtered.is_empty() {
                Ok(None)
            } else {
                Ok(Some(filtered))
            }
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

    pub fn width(&self) -> u32 {
        self.width
    }
    pub fn height(&self) -> u32 {
        self.height
    }
    pub fn fps(&self) -> u32 {
        self.fps
    }
}
