//! Session 管理 - 纯 Rust 实现，不依赖 Python
use crate::capture::{bgra_to_jpeg, capture_monitor, current_timestamp_ms, CapturedFrame};
use crate::win_recorder::{init_media_foundation, EncodingContext, EncodedFrame, FrameType, RecordingContext};
use base64::{engine::general_purpose::STANDARD, Engine as _};
use serde_json::{json, Value};
use std::collections::VecDeque;
use std::io::{self, Write, stderr};
use std::sync::{Arc, Mutex};
use std::thread::{self, JoinHandle};
use std::time::{Duration, Instant};

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
    // 纯 Rust 编码器
    pub recorder: Option<RecordingContext>,
    pub encoder: Option<EncodingContext>,
    pub encoder_info: Option<Value>,
    pub stream_queue: VecDeque<Vec<u8>>,
    pub recording_output_path: Option<String>,
    pub running: bool,
    pub capture_stop: Arc<std::sync::atomic::AtomicBool>,
    pub capture_thread: Option<JoinHandle<()>>,
    // 推流模式标志
    pub push_enabled: std::sync::atomic::AtomicBool,
    pub push_fps: std::sync::atomic::AtomicU32,
}

impl SessionHandle {
    pub fn new(_session_id: String, monitor: u32, idle_fps: u32, active_fps: u32) -> Result<Self, String> {
        // 初始化 Media Foundation
        if let Err(e) = init_media_foundation() {
            debug_eprintln!("[session] Warning: init_media_foundation failed: {}", e);
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
            push_enabled: std::sync::atomic::AtomicBool::new(false),
            push_fps: std::sync::atomic::AtomicU32::new(20),
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

        // 保存到状态（先设置 recorder，这样捕获线程启动时才会正常运行）
        let mut state = self.inner.lock().map_err(|_| "session mutex poisoned".to_string())?;
        state.recording_output_path = Some(output_path.clone());
        state.recorder = Some(recorder);
        state.active_fps = state.active_fps.max(fps);

        // 确保捕获线程正在运行（在 recorder 设置之后再启动）
        drop(state); // 释放锁
        self.ensure_capture_thread_running()?;

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
        debug_eprintln!("[windows-sidecar] === start_streaming ENTRY === fps={}, monitor={}", fps, monitor);

        // 检查是否已经在推流
        {
            let state = self.inner.lock().map_err(|_| "session mutex poisoned".to_string())?;
            if state.encoder.is_some() {
                debug_eprintln!("[windows-sidecar] start_streaming: stream already running");
                return Err("stream already running".to_string());
            }
        }

        debug_eprintln!("[windows-sidecar] === start_streaming CALLING EncodingContext::new ===");

        // 创建编码器
        let encoder = match EncodingContext::new(fps, bitrate, monitor, profile) {
            Ok(e) => e,
            Err(e) => {
                debug_eprintln!("[windows-sidecar] EncodingContext::new error: {}", e);
                return Err(e.to_string());
            }
        };

        debug_eprintln!("[windows-sidecar] === start_streaming EncodingContext::new SUCCESS ===");

        // 获取 SPS/PPS
        let (sps, pps) = encoder.get_sps_pps().unwrap_or((Vec::new(), Vec::new()));

        debug_eprintln!("[windows-sidecar] start_streaming: SPS={} bytes, PPS={} bytes", sps.len(), pps.len());

        let info = json!({
            "width": encoder.width(),
            "height": encoder.height(),
            "fps": encoder.fps(),
            "sps_b64": STANDARD.encode(&sps),
            "pps_b64": STANDARD.encode(&pps),
        });

        debug_eprintln!("[windows-sidecar] start_streaming: storing encoder in state");

        // 保存到状态（先设置 encoder，这样捕获线程启动时才会正常运行）
        let mut state = self.inner.lock().map_err(|e| {
            debug_eprintln!("[windows-sidecar] start_streaming: lock error: {}", e);
            "session mutex poisoned".to_string()
        })?;
        state.encoder = Some(encoder);
        state.encoder_info = Some(info.clone());
        state.active_fps = state.active_fps.max(fps);

        debug_eprintln!("[windows-sidecar] start_streaming: drop state lock");

        // 确保捕获线程正在运行（在 encoder 设置之后再启动）
        drop(state); // 释放锁
        debug_eprintln!("[windows-sidecar] start_streaming: calling ensure_capture_thread_running");
        self.ensure_capture_thread_running()?;
        debug_eprintln!("[windows-sidecar] start_streaming: ensure_capture_thread_running 返回成功");

        debug_eprintln!("[windows-sidecar] === start_streaming RETURNING ===");
        debug_eprintln!("[windows-sidecar] start_streaming: done");

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

    /// 确保捕获线程正在运行（如果已退出则重新启动）
    pub fn ensure_capture_thread_running(&self) -> Result<(), String> {
        // 用 debug_eprintln!：推流态下屏蔽，避免与 push_frame_to_stderr 抢同一 stderr
        // 管道（裸 eprintln 在 stderr 缓冲满时会阻塞 capture_loop，放大首帧延迟）。
        // 非推流态 PUSH_MODE_ACTIVE=false，日志仍正常可见。
        debug_eprintln!("[windows-sidecar] ensure_capture_thread_running: 开始检查");
        let needs_spawn = {
            let state = self.inner.lock().map_err(|_| "session mutex poisoned".to_string())?;
            let is_none = state.capture_thread.is_none();
            debug_eprintln!("[windows-sidecar] ensure_capture_thread_running: is_none={}", is_none);
            is_none
        };

        if needs_spawn {
            debug_eprintln!("[windows-sidecar] ensure_capture_thread_running: 需要启动新线程");
            // 重置停止标志
            self.inner
                .lock()
                .map_err(|_| "session mutex poisoned".to_string())?
                .capture_stop
                .store(false, std::sync::atomic::Ordering::SeqCst);

            let thread_inner = self.inner.clone();
            let thread_capture_stop = self.inner.lock()
                .map_err(|_| "session mutex poisoned".to_string())?
                .capture_stop.clone();
            let handle = thread::spawn(move || capture_loop(thread_inner, thread_capture_stop));

            let mut state = self.inner.lock().map_err(|_| "session mutex poisoned".to_string())?;
            state.capture_thread = Some(handle);
            debug_eprintln!("[windows-sidecar] ensure_capture_thread_running: 新线程已启动");
        } else {
            debug_eprintln!("[windows-sidecar] ensure_capture_thread_running: 线程已在运行");
        }

        Ok(())
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

    /// 设置推流模式启用状态
    pub fn set_push_enabled(&self, enabled: bool) -> Result<(), String> {
        let state = self.inner.lock().map_err(|_| "session mutex poisoned".to_string())?;
        state.push_enabled.store(enabled, std::sync::atomic::Ordering::Relaxed);
        // 设置全局推流标志，禁用 stderr 调试日志
        PUSH_MODE_ACTIVE.store(enabled, std::sync::atomic::Ordering::Relaxed);
        // 推流模式启用时，强制刷新一下 stderr
        if enabled {
            let _ = stderr().flush();
        }
        Ok(())
    }

    /// 设置推流帧率
    pub fn set_push_fps(&self, fps: u32) -> Result<(), String> {
        let state = self.inner.lock().map_err(|_| "session mutex poisoned".to_string())?;
        state.push_fps.store(fps, std::sync::atomic::Ordering::Relaxed);
        Ok(())
    }

    /// 推流启动时主动推送一次 SPS/PPS 到 stderr，
    /// 避免 Python 等待 SPS+PPS 时依赖编码器后续是否输出独立 SPS/PPS NAL。
    /// 必须在 set_push_enabled(true) 之后调用，确保 PUSH_MODE_ACTIVE 已生效（屏蔽调试日志）。
    pub fn push_sps_pps_once(&self) -> Result<(), String> {
        let (sps, pps) = {
            let state = self.inner.lock().map_err(|_| "session mutex poisoned".to_string())?;
            if let Some(encoder) = state.encoder.as_ref() {
                encoder.get_sps_pps().unwrap_or((Vec::new(), Vec::new()))
            } else {
                (Vec::new(), Vec::new())
            }
        };
        // 用 debug_eprintln!：推流态屏蔽，避免与紧随其后的 push_frame_to_stderr(SPS/PPS 帧)
        // 在同一瞬抢 stderr——首帧路径上少一次管道竞争。SPS/PPS 帧数据本身由下方
        // push_frame_tostderr 输出，功能不受影响。非推流态仍可见。
        debug_eprintln!("[windows-sidecar] push_sps_pps_once: sps={} bytes, pps={} bytes", sps.len(), pps.len());
        if !sps.is_empty() {
            push_frame_to_stderr(0, &sps);
        }
        if !pps.is_empty() {
            push_frame_to_stderr(1, &pps);
        }
        Ok(())
    }

    /// 检查捕获线程是否已停止
    pub fn is_capture_thread_stopped(&self) -> bool {
        self.inner
            .lock()
            .map(|s| s.capture_thread.is_none())
            .unwrap_or(true)
    }

    /// 重新启动捕获线程（用于推流模式）
    pub fn restart_capture_thread(&self) -> Result<(), String> {
        // 重置停止标志
        self.inner
            .lock()
            .map_err(|_| "session mutex poisoned".to_string())?
            .capture_stop
            .store(false, std::sync::atomic::Ordering::SeqCst);

        let thread_inner = self.inner.clone();
        let thread_capture_stop = self.inner.lock()
            .map_err(|_| "session mutex poisoned".to_string())?
            .capture_stop.clone();
        let handle = thread::spawn(move || capture_loop(thread_inner, thread_capture_stop));

        let mut state = self.inner.lock().map_err(|_| "session mutex poisoned".to_string())?;
        state.capture_thread = Some(handle);
        debug_eprintln!("[windows-sidecar] restart_capture_thread: 新线程已启动");
        Ok(())
    }
}

/// 捕获循环 - 纯 Rust 实现
/// 当没有活动的 recorder 和 encoder 时，循环退出以节省资源
fn capture_loop(state: Arc<Mutex<SessionState>>, stop_flag: Arc<std::sync::atomic::AtomicBool>) {
    // 用 debug_eprintln!：推流态下 capture_loop 每轮都跑，裸 eprintln 会与
    // push_frame_to_stderr 抢同一 stderr 管道。stderr 缓冲满时 eprintln 阻塞
    // capture_loop 线程，是首帧延迟放大的主因（实测裸 eprintln 每帧打 1-2 行）。
    // 非推流态 PUSH_MODE_ACTIVE=false，日志仍正常可见。
    debug_eprintln!("[windows-sidecar] capture_loop: 线程启动");
    let mut last_tick = Instant::now();
    // 诊断：编码器预热测量——记录 push 启动时刻到首帧产出的耗时（测点1）
    let mut prev_push_enabled = false;
    let mut push_just_on: Option<Instant> = None;
    let mut none_count_after_push_on: u64 = 0;
    let mut first_frame_logged = false;
    // 暖管冒帧丢弃标志：prime 暖管后 MFT 管道里挂着 1 帧黑帧（实测是黑帧 IDR ~6KB），
    // capture_loop 第一帧真屏会把它顶出。这批暖管冒出的黑帧序列不能推给 jmuxer
    // （黑帧 IDR 会被锁成黑屏基线，直到真屏 IDR 才覆盖，反而比旧版 1.5s 全黑暖管更糟）。
    // push 启动跳变时置 true，丢帧直到第一个真屏 IDR（size > REAL_IDR_MIN_BYTES）
    // 才开始推送给客户端。
    let mut push_skip_until_real_idr = false;
    // 真屏 IDR 最小字节阈值：黑帧 IDR 实测 ~6KB，真屏 IDR ~240KB，30KB 区分度极大。
    // 阈值取 30KB：远大于黑帧 IDR，远小于真屏 IDR，避免黑屏/极简画面被误判为真屏。
    const REAL_IDR_MIN_BYTES: usize = 30_000;
    loop {
        if stop_flag.load(std::sync::atomic::Ordering::SeqCst) {
            debug_eprintln!("[windows-sidecar] capture_loop: stop_flag 触发，退出");
            break;
        }

        // 获取当前状态 - 分开获取避免多次 mutable borrow
        // 推流模式下使用 push_fps，否则使用 active_fps/idle_fps
        let (monitor, target_fps) = {
            let guard = match state.lock() {
                Ok(g) => g,
                Err(_) => break,
            };
            // 如果启用推模式，使用 push_fps（优先），否则使用 active_fps/idle_fps
            let base_fps = guard.active_fps.max(guard.idle_fps).max(1);
            let effective_fps = if guard.push_enabled.load(std::sync::atomic::Ordering::Relaxed) {
                guard.push_fps.load(std::sync::atomic::Ordering::Relaxed).max(base_fps)
            } else {
                base_fps
            };
            (guard.monitor, effective_fps)
        };

        // 检查是否有活动的 recorder/encoder/推流
        let (has_recorder, has_encoder, push_enabled) = {
            let guard = match state.lock() {
                Ok(g) => g,
                Err(_) => break,
            };
            (
                guard.recorder.is_some(),
                guard.encoder.is_some(),
                guard.push_enabled.load(std::sync::atomic::Ordering::Relaxed),
            )
        };

        // 每帧都跑的诊断行：推流态屏蔽（最大污染源——实测每帧打此行 + 多次锁竞争）。
        // 非推流态仍可见。
        debug_eprintln!("[windows-sidecar] capture_loop: has_recorder={}, has_encoder={}, push_enabled={}",
            has_recorder, has_encoder, push_enabled);

        // 诊断测点1：检测 push_enabled 从 false→true 跳变，记录预热起点
        if push_enabled && !prev_push_enabled {
            push_just_on = Some(Instant::now());
            none_count_after_push_on = 0;
            first_frame_logged = false;
            // 暖管冒帧丢弃：push 每次启动都重新进入"丢弃直到真屏 IDR"模式。
            // 前一次 push 留下的丢帧状态（若中途 push 关了又开）会被这里复位。
            push_skip_until_real_idr = true;
        }
        prev_push_enabled = push_enabled;

        // 关键优化：当既没有 recorder 也没有 encoder 也没有推流时，退出循环
        // 但推流模式下需要等待 encoder 被创建
        if !has_recorder && !has_encoder {
            let mut guard = match state.lock() {
                Ok(g) => g,
                Err(_) => break,
            };
            // 如果有推流标志但没有 encoder，说明正在初始化，等待
            if guard.push_enabled.load(std::sync::atomic::Ordering::Relaxed) {
                debug_eprintln!("[windows-sidecar] capture_loop: push enabled but no encoder, waiting...");
                drop(guard);
                thread::sleep(Duration::from_millis(100));
                continue;
            }
            guard.capture_thread = None;
            debug_eprintln!("[windows-sidecar] capture_loop: exiting (no work)");
            break;
        }

        // 如果有推流但没有 encoder，需要创建临时 encoder
        if push_enabled && !has_encoder {
            debug_eprintln!("[windows-sidecar] capture_loop: 需要创建临时 encoder");
            // 这里需要创建 encoder，但目前简化处理暂时无法实现
            // 因为 EncodingContext 创建涉及更多逻辑
            // 暂时让循环休眠一段时间后继续
            thread::sleep(Duration::from_millis(500));
            continue;
        }

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
                debug_eprintln!("[windows-screen-sidecar] capture failed: {err}");
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
                    debug_eprintln!("[windows-screen-sidecar] write_frame failed: {}", err);
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
                match encoder.encode_frames_detailed(&frame.bgra) {
                    Ok(Some(frames)) => {
                        let push_enabled =
                            guard.push_enabled.load(std::sync::atomic::Ordering::Relaxed);
                        drop(guard); // 释放锁后再操作 stream_queue / stderr

                        // 录制仍使用拼包结果：把本组 NAL 拼成 [自定义前缀][NAL...]
                        // 兼容现有 stream_queue 消费格式（与原 encode_frame 行为一致）
                        // 注意：丢帧只影响 stderr 推流路径，stream_queue（本地录制）照常
                        // 塞入暖管冒帧也无妨——录制里多几帧黑帧无意义但不影响正确性。
                        {
                            let mut guard = match state.lock() {
                                Ok(guard) => guard,
                                Err(_) => break,
                            };
                            if guard.stream_queue.len() >= 16 {
                                guard.stream_queue.pop_front();
                            }
                            let combined = assemble_packet(&frames);
                            guard.stream_queue.push_back(combined);
                        }

                        // 推流：逐 NAL 分别推送到 stderr，前缀用 ASCII 数字字符
                        if push_enabled {
                            // 暖管冒帧丢弃：prime 暖管后 MFT 管道里挂的 1 帧黑帧（实测黑帧 IDR
                            // ~6KB）会被 capture_loop 第一帧真屏顶出。在找到第一个真屏 IDR 之前，
                            // 暖管冒出的黑帧序列（黑帧 IDR + 跟随的黑屏 P）不推给 jmuxer，
                            // 避免黑帧 IDR 被锁成黑屏基线（实测画面真正出现要 2s，比旧版 1.8s
                            // 全黑暖管更糟）。
                            //
                            // 判定真屏 IDR：本批 frames 里含 IDR 且其 data.len 超过阈值。
                            // 黑帧 IDR ~6KB，真屏 IDR ~240KB，30KB 阈值区分度极大。
                            // 本批不含真屏 IDR（要么纯黑屏 P，要么黑帧 IDR）→ 整批 stderr 不推。
                            // 一旦命中真屏 IDR → 清掉丢帧标志，本批（含其自带 SPS/PPS+IDR）
                            // 正常推送给 jmuxer 建真屏基线。
                            if push_skip_until_real_idr {
                                let found_real_idr = frames.iter().any(|ef| {
                                    matches!(ef.frame_type, FrameType::IDR)
                                        && ef.data.len() >= REAL_IDR_MIN_BYTES
                                });
                                if !found_real_idr {
                                    debug_eprintln!(
                                        "[windows-sidecar] 暖管冒帧丢弃: 本批无真屏 IDR，跳过推送 (NAL 类型 {:?})",
                                        frames.iter().map(|f| &f.frame_type).collect::<Vec<_>>()
                                    );
                                } else {
                                    push_skip_until_real_idr = false;
                                    debug_eprintln!(
                                        "[windows-sidecar] 暖管冒帧结束: 命中真屏 IDR，开始推送客户端首帧"
                                    );
                                }
                            }

                            if !push_skip_until_real_idr {
                                for ef in &frames {
                                    let frame_type = frame_type_to_u8(&ef.frame_type);
                                    push_frame_to_stderr(frame_type, &ef.data);
                                }
                            }

                            // 诊断测点1：客户端首帧推送耗时（push 启动后首个真正推送的真屏帧）
                            // 注意：原实现一收到 Some 就标记 first_frame_logged，会把暖管冒出的
                            // 黑帧当首帧统计。现改为只在真正推送给客户端后才标记，测得的是端到端
                            // 首帧可见延迟（含暖管冒帧丢弃等待）。
                            if !first_frame_logged && !push_skip_until_real_idr {
                                if let Some(t0) = push_just_on {
                                    debug_eprintln!("[windows-sidecar] 预热测点1: push→客户端首帧耗时={}ms (期间 None 次数={})",
                                        t0.elapsed().as_millis(), none_count_after_push_on);
                                }
                                first_frame_logged = true;
                            }
                        }
                    }
                    Ok(None) => {
                        // 诊断测点1：push 启动后编码器返回 None 计数（流水线预热/冷启动）
                        if push_enabled && !first_frame_logged {
                            none_count_after_push_on += 1;
                        }
                    } // 没有输出帧（流水线延迟）
                    Err(err) => {
                        debug_eprintln!("[windows-screen-sidecar] encode_frame failed: {}", err);
                    }
                }
            }
        }
    }
}

/// 通过 stderr 推送帧数据
/// frame_type: 0=SPS, 1=PPS, 2=IDR, 3=P
/// 注意：这个函数不受 PUSH_MODE_ACTIVE 影响，始终输出帧数据
/// 前缀使用 ASCII 数字字符 ('0'..'3')，与 Python _handle_line 的 b'0'..b'3' 匹配。
fn push_frame_to_stderr(frame_type: u8, data: &[u8]) {
    let encoded = STANDARD.encode(data);
    // 直接输出到 stderr，不经过 debug_eprintln!（因为推流时需要禁用调试日志但要发送帧）
    // frame_type 转为 ASCII 数字字符，避免控制字符前缀
    eprintln!("{}{}", (b'0' + frame_type) as char, encoded);
    let _ = stderr().flush();
}

/// 把 FrameType 枚举映射为推送用的 u8 类型码
/// SPS=0, PPS=1, IDR=2, PFrame/Unknown=3
fn frame_type_to_u8(ft: &FrameType) -> u8 {
    match ft {
        FrameType::SPS => 0,
        FrameType::PPS => 1,
        FrameType::IDR => 2,
        FrameType::PFrame | FrameType::Unknown => 3,
    }
}

/// 把一组 NAL 拼装为兼容原 encode_frame 的拼包结果：[1字节自定义前缀][NAL...]
/// 前缀语义：含 IDR=0x02，含 SPS/PPS=0x01，否则=0x03。用于 stream_queue(录制消费)兼容。
fn assemble_packet(frames: &[EncodedFrame]) -> Vec<u8> {
    let has_idr = frames.iter().any(|f| matches!(f.frame_type, FrameType::IDR));
    let final_prefix = if has_idr {
        0x02
    } else {
        let has_sps_pps = frames
            .iter()
            .any(|f| matches!(f.frame_type, FrameType::SPS | FrameType::PPS));
        if has_sps_pps {
            0x01
        } else {
            0x03
        }
    };
    let mut result = vec![final_prefix];
    for f in frames {
        result.extend_from_slice(&f.data);
    }
    result
}