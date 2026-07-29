//! Session 管理 - 纯 Rust 实现，不依赖 Python
use crate::capture::{bgra_to_jpeg, capture_monitor, current_timestamp_ms, CapturedFrame};
use crate::capture_dxgi::DxgiCaptureSource;
use crate::media::{
    push_frame_to_stderr, BinaryMediaOutput, CaptureProducer, FrameHub, RecordingWorkerHandle,
    StreamWorkerHandle,
};
use crate::win_recorder::{init_media_foundation, EncodingContext, RecordingContext};
use base64::{engine::general_purpose::STANDARD, Engine as _};
use serde_json::{json, Value};
use std::collections::VecDeque;
use std::io::{stderr, Write};
use std::sync::atomic::{AtomicU32, Ordering};
use std::sync::{Arc, Mutex};
use std::time::Duration;

/// 全局推流模式标志：当为 true 时，禁用 stderr 调试日志，避免污染帧流
static PUSH_MODE_ACTIVE: std::sync::atomic::AtomicBool = std::sync::atomic::AtomicBool::new(false);

/// 打印调试日志到 stderr 并刷新
/// 推流模式下禁用调试日志（避免与帧推送冲突）
macro_rules! debug_eprintln {
    ($($arg:tt)*) => {{
        if !PUSH_MODE_ACTIVE.load(std::sync::atomic::Ordering::Relaxed) {
            eprintln!($($arg)*);
            let _ = stderr().flush();
        }
    };
}}

#[derive(Clone)]
pub struct SessionHandle {
    inner: Arc<Mutex<SessionState>>,
}

pub struct SessionState {
    pub monitor: u32,
    pub idle_fps: u32,
    pub active_fps: u32,
    pub latest_frame: Option<CapturedFrame>,
    // Rust 媒体数据面：录制 worker 从 FrameHub 独立消费帧
    pub frame_hub: Arc<FrameHub>,
    pub recording_worker: Option<RecordingWorkerHandle>,
    pub recording_starting: bool,
    pub capture_producer: Option<CaptureProducer>,
    pub capture_target_fps: Arc<AtomicU32>,
    pub stream_worker: Option<StreamWorkerHandle>,
    pub encoder_info: Option<Value>,
    pub stream_queue: Arc<Mutex<VecDeque<Vec<u8>>>>,
    pub stream_sps: Vec<u8>,
    pub stream_pps: Vec<u8>,
    pub recording_output_path: Option<String>,
    pub running: bool,
    // 推流模式标志
    pub push_enabled: Arc<std::sync::atomic::AtomicBool>,
    pub push_fps: Arc<std::sync::atomic::AtomicU32>,
    pub binary_output: Option<BinaryMediaOutput>,
}

fn capture_is_needed(recording_running: bool, streaming_running: bool) -> bool {
    recording_running || streaming_running
}

/// 录制期间的抓帧目标帧率：取 ≥ base 且为录制 fps 整数倍的最小值。
/// 若抓帧率不是录制率的整数倍（如抓 15fps、录 10fps），录制 tick 取到的
/// 最新帧抓取时刻会周期性摆动，水印步进呈 67/133ms 交替而非均匀 100ms。
fn aligned_capture_fps(base: u32, recording_fps: u32) -> u32 {
    let base = base.max(1);
    let recording_fps = recording_fps.max(1);
    let rem = base % recording_fps;
    if rem == 0 {
        base
    } else {
        base + (recording_fps - rem)
    }
}

fn clear_stream_state(state: &mut SessionState) -> Result<Option<BinaryMediaOutput>, String> {
    state.encoder_info = None;
    state.stream_sps.clear();
    state.stream_pps.clear();
    state
        .stream_queue
        .lock()
        .map_err(|_| "stream queue mutex poisoned".to_string())?
        .clear();
    Ok(state.binary_output.take())
}

impl SessionHandle {
    pub fn new(
        _session_id: String,
        monitor: u32,
        idle_fps: u32,
        active_fps: u32,
    ) -> Result<Self, String> {
        // 初始化 Media Foundation
        if let Err(e) = init_media_foundation() {
            debug_eprintln!("[session] Warning: init_media_foundation failed: {}", e);
        }

        let capture_target_fps = Arc::new(AtomicU32::new(idle_fps.max(active_fps).max(1)));
        let inner = Arc::new(Mutex::new(SessionState {
            monitor,
            idle_fps,
            active_fps,
            latest_frame: None,
            frame_hub: Arc::new(FrameHub::new()),
            recording_worker: None,
            recording_starting: false,
            capture_producer: None,
            capture_target_fps: capture_target_fps.clone(),
            stream_worker: None,
            encoder_info: None,
            stream_queue: Arc::new(Mutex::new(VecDeque::with_capacity(16))),
            stream_sps: Vec::new(),
            stream_pps: Vec::new(),
            recording_output_path: None,
            running: true,
            push_enabled: Arc::new(std::sync::atomic::AtomicBool::new(false)),
            push_fps: Arc::new(std::sync::atomic::AtomicU32::new(20)),
            binary_output: None,
        }));

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
            let state = self
                .inner
                .lock()
                .map_err(|_| "session mutex poisoned".to_string())?;
            match &state.latest_frame {
                Some(latest)
                    if current_timestamp_ms().saturating_sub(latest.captured_at_ms)
                        <= max_age_ms =>
                {
                    latest.clone()
                }
                _ => capture_monitor(state.monitor)?,
            }
        };

        {
            let mut state = self
                .inner
                .lock()
                .map_err(|_| "session mutex poisoned".to_string())?;
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

        // 预留录制启动权，避免并发请求在 worker 写入 state 之前重复创建录制器。
        {
            let mut state = self
                .inner
                .lock()
                .map_err(|_| "session mutex poisoned".to_string())?;
            if state.recording_worker.is_some() || state.recording_starting {
                return Err("recording already running".to_string());
            }
            state.recording_starting = true;
        }

        // 创建并启动录制器。
        let mut recorder =
            match RecordingContext::new(output_path.clone(), fps, audio, monitor, watermark) {
                Ok(recorder) => recorder,
                Err(error) => {
                    self.release_recording_start();
                    return Err(error.to_string());
                }
            };
        if let Err(error) = recorder.start() {
            self.release_recording_start();
            return Err(error.to_string());
        }

        // 在 move 到 worker 之前获取对齐后的尺寸。
        let aligned_width = recorder.width();
        let aligned_height = recorder.height();

        // 建立新的录制消费边界：旧快照不能作为本轮录制的首帧，帧序号也绝不回退。
        let frame_hub = {
            let mut state = match self.inner.lock() {
                Ok(state) => state,
                Err(_) => {
                    let _ = recorder.stop();
                    self.release_recording_start();
                    return Err("session mutex poisoned".to_string());
                }
            };
            if !state.running {
                state.recording_starting = false;
                drop(state);
                let _ = recorder.stop();
                return Err("session 已关闭".to_string());
            }
            state.frame_hub.clear_latest();
            state.active_fps = state.active_fps.max(fps);
            state.capture_target_fps.store(
                aligned_capture_fps(state.active_fps.max(state.idle_fps), fps),
                Ordering::Relaxed,
            );
            state.frame_hub.clone()
        };

        // 先确保 producer 已运行，再启动录制消费者；worker 会等待本轮的新帧，不会消费已清理的旧快照。
        if let Err(error) = self.ensure_capture_thread_running() {
            let _ = recorder.stop();
            self.release_recording_start();
            self.stop_capture_if_unused();
            return Err(error);
        }

        let recording_worker =
            match RecordingWorkerHandle::start(frame_hub, fps, Box::new(recorder)) {
                Ok(worker) => worker,
                Err(error) => {
                    self.release_recording_start();
                    self.stop_capture_if_unused();
                    return Err(error);
                }
            };

        let mut state = match self.inner.lock() {
            Ok(state) => state,
            Err(_) => {
                let _ = recording_worker.stop();
                self.release_recording_start();
                self.stop_capture_if_unused();
                return Err("session mutex poisoned".to_string());
            }
        };
        if !state.running {
            state.recording_starting = false;
            drop(state);
            let _ = recording_worker.stop();
            self.stop_capture_if_unused();
            return Err("session 已关闭".to_string());
        }
        state.recording_output_path = Some(output_path.clone());
        state.recording_worker = Some(recording_worker);
        state.recording_starting = false;

        Ok(json!({
            "output_path": output_path,
            "fps": fps,
            "monitor": monitor,
            "watermark": watermark,
            "aligned_width": aligned_width,
            "aligned_height": aligned_height,
        }))
    }

    /// 释放录制启动预留；该方法只负责状态回滚，不会停止已登记的录制 worker。
    fn release_recording_start(&self) {
        if let Ok(mut state) = self.inner.lock() {
            state.recording_starting = false;
        }
    }

    /// 停止录制 - 纯 Rust 实现
    pub fn stop_recording(&self) -> Result<Value, String> {
        let (recording_worker, output_path) = {
            let mut state = self
                .inner
                .lock()
                .map_err(|_| "session mutex poisoned".to_string())?;
            let worker = state
                .recording_worker
                .take()
                .ok_or_else(|| "recording not running".to_string())?;
            let output_path = state.recording_output_path.take().unwrap_or_default();
            (worker, output_path)
        };

        // worker 负责停止底层 RecordingContext，并等待线程退出。
        let stats = recording_worker.stop()?;
        self.stop_capture_if_unused();

        Ok(json!({
            "output_path": output_path,
            "finalized": true,
            "written_frames": stats.written_frames,
            "duplicated_frames": stats.duplicated_frames,
            "dropped_late_ticks": stats.dropped_late_ticks,
            "last_write_ms": stats.last_write_ms,
        }))
    }
    /// 开始推流 - 纯 Rust 实现
    pub fn start_streaming(
        &self,
        fps: u32,
        bitrate: u32,
        profile: u32,
        binary: bool,
    ) -> Result<Value, String> {
        let monitor = self.monitor()?;
        debug_eprintln!(
            "[windows-sidecar] === start_streaming ENTRY === fps={}, monitor={}",
            fps,
            monitor
        );

        // 检查是否已经在推流
        {
            let state = self
                .inner
                .lock()
                .map_err(|_| "session mutex poisoned".to_string())?;
            if state.stream_worker.is_some() {
                debug_eprintln!("[windows-sidecar] start_streaming: stream already running");
                return Err("stream already running".to_string());
            }
        }

        // 先启动本轮采集并等待一张新鲜屏幕帧，再创建编码器。
        // 这样真实帧可以在启动阶段预热 MFT，避免黑帧残留顶到客户端。
        let (frame_hub, first_frame_seq) = {
            let mut state = self
                .inner
                .lock()
                .map_err(|_| "session mutex poisoned".to_string())?;
            let baseline = state
                .frame_hub
                .latest()
                .map(|frame| frame.seq)
                .unwrap_or(0);
            state.frame_hub.clear_latest();
            state.active_fps = state.active_fps.max(fps);
            state.capture_target_fps.store(
                state.active_fps.max(state.idle_fps).max(1),
                Ordering::Relaxed,
            );
            (state.frame_hub.clone(), baseline)
        };
        self.ensure_capture_thread_running()?;
        let first_frame = match frame_hub.wait_newer_than(first_frame_seq, Duration::from_secs(2)) {
            Some(frame) => frame,
            None => {
                self.stop_capture_if_unused();
                return Err("等待首帧屏幕采集超时".to_string());
            }
        };

        debug_eprintln!("[windows-sidecar] === start_streaming CALLING EncodingContext::new ===");

        // 创建编码器
        let mut encoder = match EncodingContext::new(fps, bitrate, monitor, profile) {
            Ok(e) => e,
            Err(e) => {
                self.stop_capture_if_unused();
                debug_eprintln!("[windows-sidecar] EncodingContext::new error: {}", e);
                return Err(e.to_string());
            }
        };

        debug_eprintln!("[windows-sidecar] === start_streaming EncodingContext::new SUCCESS ===");

        // 用真实屏幕帧消化黑帧暖机残留，返回真实 IDR 作为 binary 首包。
        let initial_frames = match encoder.prime_with_frame_data(&first_frame.bgra) {
            Ok(frames) => frames,
            Err(error) => {
                self.stop_capture_if_unused();
                return Err(format!("真实帧预热编码器失败: {error}"));
            }
        };
        debug_eprintln!(
            "[windows-sidecar] real-frame warmup completed: has_initial_keyframe={}",
            initial_frames.is_some()
        );

        // 获取 SPS/PPS
        let (sps, pps) = encoder.get_sps_pps().unwrap_or((Vec::new(), Vec::new()));

        debug_eprintln!(
            "[windows-sidecar] start_streaming: SPS={} bytes, PPS={} bytes",
            sps.len(),
            pps.len()
        );

        let mut info = json!({
            "width": encoder.width(),
            "height": encoder.height(),
            "fps": encoder.fps(),
            "sps_b64": STANDARD.encode(&sps),
            "pps_b64": STANDARD.encode(&pps),
        });

        let binary_output = if binary {
            Some(BinaryMediaOutput::start()?)
        } else {
            None
        };
        let binary_sender = binary_output.as_ref().map(|output| output.sender());
        if let Some(output) = &binary_output {
            info["binary_media_endpoint"] = json!(output.endpoint());
            info["binary_media_protocol"] = json!("RSM1");
            info["binary_media_version"] = json!(1);
        }

        debug_eprintln!("[windows-sidecar] start_streaming: storing encoder in state");

        // 把编码器交给独立 worker，避免编码和推流阻塞抓帧生产者。
        let (frame_hub, stream_queue, push_enabled) = {
            let state = self
                .inner
                .lock()
                .map_err(|_| "session mutex poisoned".to_string())?;
            (
                state.frame_hub.clone(),
                state.stream_queue.clone(),
                state.push_enabled.clone(),
            )
        };
        let stream_worker = StreamWorkerHandle::start(
            frame_hub,
            fps,
            encoder,
            if binary { None } else { Some(stream_queue) },
            push_enabled,
            binary_sender,
            initial_frames,
        )?;

        let mut state = self.inner.lock().map_err(|e| {
            debug_eprintln!("[windows-sidecar] start_streaming: lock error: {}", e);
            "session mutex poisoned".to_string()
        })?;
        state.stream_worker = Some(stream_worker);
        state.encoder_info = Some(info.clone());
        state.stream_sps = sps;
        state.stream_pps = pps;
        state.binary_output = binary_output;
        state.active_fps = state.active_fps.max(fps);
        state.capture_target_fps.store(
            state.active_fps.max(state.idle_fps).max(1),
            Ordering::Relaxed,
        );

        debug_eprintln!("[windows-sidecar] start_streaming: drop state lock");

        // 确保捕获线程正在运行（在 encoder 设置之后再启动）
        drop(state); // 释放锁
        debug_eprintln!("[windows-sidecar] start_streaming: calling ensure_capture_thread_running");
        self.ensure_capture_thread_running()?;
        debug_eprintln!(
            "[windows-sidecar] start_streaming: ensure_capture_thread_running 返回成功"
        );

        debug_eprintln!("[windows-sidecar] === start_streaming RETURNING ===");
        debug_eprintln!("[windows-sidecar] start_streaming: done");

        Ok(info)
    }

    /// 获取下一帧 - 纯 Rust 实现
    pub fn next_stream_frame(&self) -> Result<Value, String> {
        let frame = {
            let state = self
                .inner
                .lock()
                .map_err(|_| "session mutex poisoned".to_string())?;
            let frame = state
                .stream_queue
                .lock()
                .map_err(|_| "stream queue mutex poisoned".to_string())?
                .pop_front();
            frame
        };
        if let Some(frame) = frame {
            Ok(json!({
                "frame_b64": STANDARD.encode(frame),
            }))
        } else {
            Ok(json!({"frame_b64": Value::Null}))
        }
    }

    /// 停止推流 - 纯 Rust 实现
    pub fn stop_streaming(&self) -> Result<Value, String> {
        let stream_worker = {
            let mut state = self
                .inner
                .lock()
                .map_err(|_| "session mutex poisoned".to_string())?;
            state
                .stream_worker
                .take()
                .ok_or_else(|| "stream not running".to_string())?
        };

        stream_worker.stop()?;

        let mut state = self
            .inner
            .lock()
            .map_err(|_| "session mutex poisoned".to_string())?;
        let binary_output = clear_stream_state(&mut state)?;
        drop(state);
        drop(binary_output);
        self.stop_capture_if_unused();
        Ok(json!({"stopped": true}))
    }

    /// 确保捕获线程正在运行（如果已退出则重新启动）
    pub fn ensure_capture_thread_running(&self) -> Result<(), String> {
        // 用 debug_eprintln!：推流态下屏蔽，避免与 push_frame_to_stderr 抢同一 stderr
        // 管道（裸 eprintln 在 stderr 缓冲满时会阻塞 capture_loop，放大首帧延迟）。
        // 非推流态 PUSH_MODE_ACTIVE=false，日志仍正常可见。
        let (monitor, frame_hub, target_fps, old_producer) = {
            let mut state = self
                .inner
                .lock()
                .map_err(|_| "session mutex poisoned".to_string())?;
            if !state.running {
                return Err("session 已关闭".to_string());
            }
            if state
                .capture_producer
                .as_ref()
                .is_some_and(|producer| producer.is_running())
            {
                return Ok(());
            }
            (
                state.monitor,
                state.frame_hub.clone(),
                state.capture_target_fps.clone(),
                state.capture_producer.take(),
            )
        };

        // 不能在持有 session 锁时析构旧 producer：其线程可能正在回调更新
        // latest_frame，析构 join 会因此与 session 锁形成死锁。
        drop(old_producer);

        let producer_inner = Arc::downgrade(&self.inner);
        // 录制/推流热路径用 DXGI Desktop Duplication 抓屏（单帧稳定几 ms），
        // GDI BitBlt 仅作为 RDP/旋转屏等不支持场景的自动降级路径。
        let capture_source = DxgiCaptureSource::new(monitor);
        let producer = CaptureProducer::start_with_capture_fn_and_callback(
            frame_hub,
            target_fps,
            move || capture_source.capture(),
            move |frame| {
                if let Some(inner) = producer_inner.upgrade() {
                    if let Ok(mut state) = inner.lock() {
                        state.latest_frame = Some(frame.clone());
                    }
                }
            },
        )?;

        let mut state = self
            .inner
            .lock()
            .map_err(|_| "session mutex poisoned".to_string())?;
        if !state.running {
            drop(state);
            drop(producer);
            return Err("session 已关闭".to_string());
        }
        if state.capture_producer.is_none() {
            state.capture_producer = Some(producer);
        }
        Ok(())
    }

    /// 没有录制或推流消费者时释放抓帧线程，避免空闲 session 持续占用捕获资源。
    fn stop_capture_if_unused(&self) {
        let producer = self.inner.lock().ok().and_then(|mut state| {
            if !capture_is_needed(
                state.recording_worker.is_some() || state.recording_starting,
                state.stream_worker.is_some(),
            ) {
                state.capture_producer.take()
            } else {
                None
            }
        });
        drop(producer);
    }

    pub fn close(&self) -> Result<Value, String> {
        let _ = self.stop_streaming();
        let _ = self.stop_recording();

        let producer = {
            let mut state = self
                .inner
                .lock()
                .map_err(|_| "session mutex poisoned".to_string())?;
            state.running = false;
            state.capture_producer.take()
        };
        // 在释放 session 锁后 join，避免 producer 回调与关闭流程互相等待。
        drop(producer);
        Ok(json!({"closed": true}))
    }

    /// 设置推流模式启用状态。
    pub fn set_push_enabled(&self, enabled: bool) -> Result<(), String> {
        let state = self
            .inner
            .lock()
            .map_err(|_| "session mutex poisoned".to_string())?;
        state.push_enabled.store(enabled, Ordering::Relaxed);
        PUSH_MODE_ACTIVE.store(enabled, Ordering::Relaxed);
        if enabled {
            let _ = stderr().flush();
        }
        Ok(())
    }

    /// 设置推流帧率。
    pub fn set_push_fps(&self, fps: u32) -> Result<(), String> {
        let state = self
            .inner
            .lock()
            .map_err(|_| "session mutex poisoned".to_string())?;
        state.push_fps.store(fps.max(1), Ordering::Relaxed);
        Ok(())
    }

    /// 推流启动时主动推送一次 SPS/PPS，兼容旧 Python 行协议。
    pub fn push_sps_pps_once(&self) -> Result<(), String> {
        let (sps, pps) = {
            let state = self
                .inner
                .lock()
                .map_err(|_| "session mutex poisoned".to_string())?;
            (state.stream_sps.clone(), state.stream_pps.clone())
        };
        if !sps.is_empty() {
            push_frame_to_stderr(0, &sps);
        }
        if !pps.is_empty() {
            push_frame_to_stderr(1, &pps);
        }
        Ok(())
    }

    pub fn is_capture_thread_stopped(&self) -> bool {
        self.inner
            .lock()
            .map(|state| {
                state
                    .capture_producer
                    .as_ref()
                    .is_none_or(|producer| !producer.is_running())
            })
            .unwrap_or(true)
    }

    pub fn restart_capture_thread(&self) -> Result<(), String> {
        self.ensure_capture_thread_running()
    }
}

#[cfg(test)]
mod tests {
    use super::{aligned_capture_fps, capture_is_needed, clear_stream_state, SessionHandle};
    use serde_json::json;

    #[test]
    fn capture_is_needed_only_when_recording_or_streaming() {
        assert!(!capture_is_needed(false, false));
        assert!(capture_is_needed(true, false));
        assert!(capture_is_needed(false, true));
        assert!(capture_is_needed(true, true));
    }

    #[test]
    fn aligned_capture_fps_rounds_up_to_multiple_of_recording_fps() {
        // 抓帧基准 15、录 10 → 对齐到 20，保证水印步进均匀
        assert_eq!(aligned_capture_fps(15, 10), 20);
        // 已是整数倍时不变
        assert_eq!(aligned_capture_fps(20, 20), 20);
        assert_eq!(aligned_capture_fps(30, 30), 30);
        assert_eq!(aligned_capture_fps(30, 10), 30);
        // 录制 fps 高于基准时直接对齐到录制 fps
        assert_eq!(aligned_capture_fps(15, 20), 20);
        // 非法输入保底
        assert_eq!(aligned_capture_fps(0, 0), 1);
    }

    #[test]
    fn stream_state_cleanup_removes_encoder_metadata_and_queued_frames() {
        let session =
            SessionHandle::new("test".to_string(), 1, 1, 10).expect("session should be created");
        let mut state = session.inner.lock().expect("session lock should succeed");
        state.encoder_info = Some(json!({"width": 1920}));
        state.stream_sps = vec![1, 2, 3];
        state.stream_pps = vec![4, 5, 6];
        state
            .stream_queue
            .lock()
            .expect("stream queue lock should succeed")
            .push_back(vec![7, 8, 9]);

        let binary_output = clear_stream_state(&mut state).expect("stream state should clear");

        assert!(binary_output.is_none());
        assert!(state.encoder_info.is_none());
        assert!(state.stream_sps.is_empty());
        assert!(state.stream_pps.is_empty());
        assert!(state
            .stream_queue
            .lock()
            .expect("stream queue lock should succeed")
            .is_empty());
    }
}
