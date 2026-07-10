use std::sync::atomic::{AtomicBool, AtomicU32, AtomicU64, Ordering};
use std::sync::Arc;
use std::thread::{self, JoinHandle};
use std::time::{Duration, Instant};

use crate::capture::{capture_monitor, CapturedFrame as RawCapturedFrame};

use super::{CapturedFrame, FrameHub};

/// 抓帧生产者：只负责按目标帧率抓屏并发布到 FrameHub，不参与编码或推流。
pub struct CaptureProducer {
    running: Arc<AtomicBool>,
    thread: Option<JoinHandle<()>>,
}

impl CaptureProducer {
    pub fn start(
        monitor: u32,
        frame_hub: Arc<FrameHub>,
        target_fps: Arc<AtomicU32>,
    ) -> Result<Self, String> {
        Self::start_with_capture_fn(frame_hub, target_fps, move || capture_monitor(monitor))
    }

    pub fn start_with_capture_fn<F>(
        frame_hub: Arc<FrameHub>,
        target_fps: Arc<AtomicU32>,
        capture_fn: F,
    ) -> Result<Self, String>
    where
        F: Fn() -> Result<RawCapturedFrame, String> + Send + Sync + 'static,
    {
        Self::start_with_capture_fn_and_callback(frame_hub, target_fps, capture_fn, |_| {})
    }

    /// 启动抓帧生产者，并在发布前通知控制面更新快照缓存。
    pub fn start_with_capture_fn_and_callback<F, C>(
        frame_hub: Arc<FrameHub>,
        target_fps: Arc<AtomicU32>,
        capture_fn: F,
        on_frame: C,
    ) -> Result<Self, String>
    where
        F: Fn() -> Result<RawCapturedFrame, String> + Send + Sync + 'static,
        C: Fn(&RawCapturedFrame) + Send + Sync + 'static,
    {
        let running = Arc::new(AtomicBool::new(true));
        let running_thread = running.clone();
        let seq = Arc::new(AtomicU64::new(0));
        let seq_thread = seq.clone();
        let frame_hub_thread = frame_hub.clone();
        let capture_fn = Arc::new(capture_fn);
        let capture_fn_thread = capture_fn.clone();
        let on_frame = Arc::new(on_frame);
        let on_frame_thread = on_frame.clone();

        let thread = thread::Builder::new()
            .name("capture-producer".to_string())
            .spawn(move || {
                let mut last_tick = Instant::now();
                while running_thread.load(Ordering::Relaxed) {
                    let fps = target_fps.load(Ordering::Relaxed).max(1);
                    let interval = Duration::from_secs_f64(1.0 / fps as f64);
                    let elapsed = last_tick.elapsed();
                    if elapsed < interval {
                        thread::sleep(interval - elapsed);
                    }
                    last_tick = Instant::now();

                    let raw_frame = match capture_fn_thread() {
                        Ok(frame) => frame,
                        Err(_) => continue,
                    };
                    on_frame_thread(&raw_frame);
                    let next_seq = seq_thread.fetch_add(1, Ordering::SeqCst) + 1;
                    let frame = CapturedFrame {
                        seq: next_seq,
                        capture_pts_100ns: raw_frame.captured_at_ms.saturating_mul(10_000) as i64,
                        width: raw_frame.width,
                        height: raw_frame.height,
                        bgra: raw_frame.bgra,
                    };
                    frame_hub_thread.publish(frame);
                }
            })
            .map_err(|e| format!("启动抓帧 producer 失败: {e}"))?;

        Ok(Self {
            running,
            thread: Some(thread),
        })
    }

    pub fn stop(&mut self) {
        self.running.store(false, Ordering::Relaxed);
        if let Some(thread) = self.thread.take() {
            let _ = thread.join();
        }
    }

    pub fn is_running(&self) -> bool {
        self.running.load(Ordering::Relaxed)
    }
}

impl Drop for CaptureProducer {
    fn drop(&mut self) {
        self.stop();
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::capture::CapturedFrame as RawCapturedFrame;
    use std::sync::atomic::AtomicU64;
    use std::sync::Mutex;

    #[test]
    fn capture_producer_publishes_frames_and_stops() {
        let hub = Arc::new(FrameHub::new());
        let target_fps = Arc::new(AtomicU32::new(60));
        let seq = Arc::new(AtomicU64::new(0));
        let seq_for_capture = seq.clone();
        let latest = Arc::new(Mutex::new(None));
        let latest_for_callback = latest.clone();

        let mut producer = CaptureProducer::start_with_capture_fn_and_callback(
            hub.clone(),
            target_fps,
            move || {
                let next = seq_for_capture.fetch_add(1, Ordering::SeqCst) + 1;
                Ok(RawCapturedFrame {
                    width: 2,
                    height: 2,
                    bgra: vec![next as u8; 16],
                    captured_at_ms: next as u128,
                })
            },
            move |frame| {
                *latest_for_callback.lock().unwrap() = Some(frame.clone());
            },
        )
        .expect("producer start failed");

        let latest_frame = hub
            .wait_newer_than(0, Duration::from_millis(500))
            .expect("expected frame");
        assert_eq!(latest_frame.seq, 1);
        assert_eq!(latest_frame.capture_pts_100ns, 10_000);
        assert_eq!(latest.lock().unwrap().as_ref().unwrap().width, 2);

        producer.stop();
        assert!(!producer.is_running());
    }
}
