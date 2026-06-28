//! Session 管理 - 纯 Rust 实现，不依赖 Python
use crate::capture::{bgra_to_jpeg, capture_monitor, current_timestamp_ms, CapturedFrame};
use crate::win_recorder::{init_media_foundation, EncodingContext, RecordingContext};
use base64::{engine::general_purpose::STANDARD, Engine as _};
use serde_json::{json, Value};
use std::collections::VecDeque;
use std::sync::{Arc, Mutex};
use std::thread::{self, JoinHandle};
use std::time::{Duration, Instant};

#[derive(Clone)]
pub struct SessionHandle {
    inner: Arc<Mutex<SessionState>>,
}

pub struct SessionState {
    pub monitor: u32,
    pub idle_fps: u32,
    pub active_fps: u32,
    pub latest_frame: Option<CapturedFrame>,
    // 纯 Rust 编码器
    pub recorder: Option<RecordingContext>,
    pub encoder: Option<EncodingContext>,
    pub encoder_info: Option<Value>,
    pub stream_queue: VecDeque<Vec<u8>>,
    pub recording_output_path: Option<String>,
    pub running: bool,
    pub capture_stop: Arc<std::sync::atomic::AtomicBool>,
    pub capture_thread: Option<JoinHandle<()>>,
}

impl SessionHandle {
    pub fn new(_session_id: String, monitor: u32, idle_fps: u32, active_fps: u32) -> Result<Self, String> {
        // 初始化 Media Foundation
        if let Err(e) = init_media_foundation() {
            eprintln!("[session] Warning: init_media_foundation failed: {}", e);
        }

        let capture_stop = Arc::new(std::sync::atomic::AtomicBool::new(false));
        let inner = Arc::new(Mutex::new(SessionState {
            monitor,
            idle_fps,
            active_fps,
            latest_frame: None,
            recorder: None,
            encoder: None,
            encoder_info: None,
            stream_queue: VecDeque::with_capacity(16),
            recording_output_path: None,
            running: true,
            capture_stop: capture_stop.clone(),
            capture_thread: None,
        }));

        let thread_inner = inner.clone();
        let thread_capture_stop = capture_stop.clone();
        let handle = thread::spawn(move || capture_loop(thread_inner, thread_capture_stop));
        {
            let mut state = inner.lock().map_err(|_| "session mutex poisoned".to_string())?;
            state.capture_thread = Some(handle);
        }

        Ok(Self { inner })
    }

    pub fn monitor(&self) -> Result<u32, String> {
        self.inner
            .lock()
            .map(|s| s.monitor)
            .map_err(|_| "session mutex poisoned".to_string())
    }

    pub fn snapshot(&self, format: &str, quality: u8, max_age_ms: u128) -> Result<Value, String> {
        let frame = {
            let state = self.inner.lock().map_err(|_| "session mutex poisoned".to_string())?;
            match &state.latest_frame {
                Some(latest) if current_timestamp_ms().saturating_sub(latest.captured_at_ms) <= max_age_ms => {
                    latest.clone()
                }
                _ => capture_monitor(state.monitor)?,
            }
        };

        {
            let mut state = self.inner.lock().map_err(|_| "session mutex poisoned".to_string())?;
            state.latest_frame = Some(frame.clone());
        }

        match format {
            "raw" => Ok(json!({
                "width": frame.width,
                "height": frame.height,
                "captured_at_ms": frame.captured_at_ms,
                "bgra_b64": STANDARD.encode(&frame.bgra),
            })),
            _ => {
                let jpeg = bgra_to_jpeg(&frame.bgra, frame.width, frame.height, quality)?;
                Ok(json!({
                    "width": frame.width,
                    "height": frame.height,
                    "captured_at_ms": frame.captured_at_ms,
                    "image_b64": STANDARD.encode(jpeg),
                }))
            }
        }
    }

    /// 开始录制 - 纯 Rust 实现
    pub fn start_recording(
        &self,
        output_path: String,
        fps: u32,
        audio: bool,
        watermark: bool,
    ) -> Result<Value, String> {
        let monitor = self.monitor()?;

        // 检查是否已经在录制
        {
            let state = self.inner.lock().map_err(|_| "session mutex poisoned".to_string())?;
            if state.recorder.is_some() {
                return Err("recording already running".to_string());
            }
        }

        // 创建并启动录制器
        let mut recorder = RecordingContext::new(output_path.clone(), fps, audio, monitor, watermark)
            .map_err(|e| e.to_string())?;
        recorder.start().map_err(|e| e.to_string())?;

        // 在 move 到 state 之前获取对齐后的尺寸
        let aligned_width = recorder.width();
        let aligned_height = recorder.height();

        // 保存到状态
        let mut state = self.inner.lock().map_err(|_| "session mutex poisoned".to_string())?;
        state.recording_output_path = Some(output_path.clone());
        state.recorder = Some(recorder);
        state.active_fps = state.active_fps.max(fps);

        Ok(json!({
            "output_path": output_path,
            "fps": fps,
            "monitor": monitor,
            "watermark": watermark,
            "aligned_width": aligned_width,
            "aligned_height": aligned_height,
        }))
    }

    /// 停止录制 - 纯 Rust 实现
    pub fn stop_recording(&self) -> Result<Value, String> {
        let (mut recorder, output_path) = {
            let mut state = self.inner.lock().map_err(|_| "session mutex poisoned".to_string())?;
            let recorder = state.recorder.take().ok_or_else(|| "recording not running".to_string())?;
            let output_path = state.recording_output_path.take().unwrap_or_default();
            (recorder, output_path)
        };

        // 停止录制
        recorder.stop().map_err(|e| e.to_string())?;

        Ok(json!({
            "output_path": output_path,
            "finalized": true,
        }))
    }

    /// 开始推流 - 纯 Rust 实现
    pub fn start_streaming(&self, fps: u32, bitrate: u32, profile: u32) -> Result<Value, String> {
        let monitor = self.monitor()?;

        // 检查是否已经在推流
        {
            let state = self.inner.lock().map_err(|_| "session mutex poisoned".to_string())?;
            if state.encoder.is_some() {
                return Err("stream already running".to_string());
            }
        }

        // 创建并启动编码器
        let mut encoder = EncodingContext::new(fps, bitrate, monitor, profile)
            .map_err(|e| e.to_string())?;

        // 获取 SPS/PPS
        let (sps, pps) = encoder.get_sps_pps().unwrap_or((Vec::new(), Vec::new()));

        let info = json!({
            "width": encoder.width(),
            "height": encoder.height(),
            "fps": encoder.fps(),
            "sps_b64": STANDARD.encode(&sps),
            "pps_b64": STANDARD.encode(&pps),
        });

        // 保存到状态
        let mut state = self.inner.lock().map_err(|_| "session mutex poisoned".to_string())?;
        state.encoder = Some(encoder);
        state.encoder_info = Some(info.clone());
        state.active_fps = state.active_fps.max(fps);

        Ok(info)
    }

    /// 获取下一帧 - 纯 Rust 实现
    pub fn next_stream_frame(&self) -> Result<Value, String> {
        let mut state = self.inner.lock().map_err(|_| "session mutex poisoned".to_string())?;
        if let Some(frame) = state.stream_queue.pop_front() {
            Ok(json!({
                "frame_b64": STANDARD.encode(frame),
            }))
        } else {
            Ok(json!({"frame_b64": Value::Null}))
        }
    }

    /// 停止推流 - 纯 Rust 实现
    pub fn stop_streaming(&self) -> Result<Value, String> {
        let mut encoder = {
            let mut state = self.inner.lock().map_err(|_| "session mutex poisoned".to_string())?;
            state.encoder.take().ok_or_else(|| "stream not running".to_string())?
        };

        encoder.stop().map_err(|e| e.to_string())?;

        let mut state = self.inner.lock().map_err(|_| "session mutex poisoned".to_string())?;
        state.encoder_info = None;
        state.stream_queue.clear();
        Ok(json!({"stopped": true}))
    }

    pub fn close(&self) -> Result<Value, String> {
        let _ = self.stop_streaming();
        let _ = self.stop_recording();

        let (handle, stop_flag) = {
            let mut state = self.inner.lock().map_err(|_| "session mutex poisoned".to_string())?;
            state.running = false;
            let handle = state.capture_thread.take();
            let stop_flag = state.capture_stop.clone();
            (handle, stop_flag)
        };

        stop_flag.store(true, std::sync::atomic::Ordering::SeqCst);
        if let Some(handle) = handle {
            let _ = handle.join();
        }

        Ok(json!({"closed": true}))
    }
}

/// 捕获循环 - 纯 Rust 实现
fn capture_loop(state: Arc<Mutex<SessionState>>, stop_flag: Arc<std::sync::atomic::AtomicBool>) {
    let mut last_tick = Instant::now();
    loop {
        if stop_flag.load(std::sync::atomic::Ordering::SeqCst) {
            break;
        }

        // 获取当前状态 - 分开获取避免多次 mutable borrow
        let (monitor, target_fps) = {
            let guard = match state.lock() {
                Ok(g) => g,
                Err(_) => break,
            };
            (guard.monitor, guard.active_fps.max(guard.idle_fps).max(1))
        };

        // 检查是否有活动的 recorder/encoder
        let (has_recorder, has_encoder) = {
            let guard = match state.lock() {
                Ok(g) => g,
                Err(_) => break,
            };
            (guard.recorder.is_some(), guard.encoder.is_some())
        };

        // 控制帧率
        let interval = Duration::from_secs_f64(1.0 / target_fps as f64);
        let elapsed = last_tick.elapsed();
        if elapsed < interval {
            thread::sleep(interval - elapsed);
        }

        // 捕获屏幕
        let frame = match capture_monitor(monitor) {
            Ok(frame) => frame,
            Err(err) => {
                eprintln!("[windows-screen-sidecar] capture failed: {err}");
                thread::sleep(Duration::from_millis(250));
                continue;
            }
        };
        last_tick = Instant::now();

        // 更新最新帧
        {
            let mut guard = match state.lock() {
                Ok(guard) => guard,
                Err(_) => break,
            };
            guard.latest_frame = Some(frame.clone());
        }

        // 写入录制器
        if has_recorder {
            let mut guard = match state.lock() {
                Ok(g) => g,
                Err(_) => break,
            };
            if let Some(recorder) = guard.recorder.as_mut() {
                if let Err(err) = recorder.write_frame(&frame.bgra) {
                    eprintln!("[windows-screen-sidecar] write_frame failed: {}", err);
                }
            }
        }

        // 编码推流帧
        if has_encoder {
            let mut guard = match state.lock() {
                Ok(g) => g,
                Err(_) => break,
            };
            if let Some(encoder) = guard.encoder.as_mut() {
                match encoder.encode_frame(&frame.bgra) {
                    Ok(Some(encoded_frame)) => {
                        drop(guard); // 释放锁后再操作 stream_queue
                        let mut guard = match state.lock() {
                            Ok(guard) => guard,
                            Err(_) => break,
                        };
                        if guard.stream_queue.len() >= 16 {
                            guard.stream_queue.pop_front();
                        }
                        guard.stream_queue.push_back(encoded_frame);
                    }
                    Ok(None) => {} // 没有输出帧（流水线延迟）
                    Err(err) => {
                        eprintln!("[windows-screen-sidecar] encode_frame failed: {}", err);
                    }
                }
            }
        }
    }
}