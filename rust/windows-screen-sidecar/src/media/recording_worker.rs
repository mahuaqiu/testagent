use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::Arc;
use std::thread::{self, JoinHandle};
use std::time::{Duration, Instant};

use super::{CapturedFrame, FrameHub};

/// 单次写入：像素借用 + 抓帧时间 + 是否重复 tick。
/// 禁止为传时间戳而 clone bgra。
pub struct WriteFrame<'a> {
    pub bgra: &'a [u8],
    pub capture_pts_100ns: i64,
    pub duplicated: bool,
}

/// 录制 worker 使用的帧写入抽象，生产环境由 RecordingContext 实现。
pub trait FrameSink: Send + 'static {
    fn write_frame(&mut self, frame: WriteFrame<'_>) -> Result<(), String>;
    fn stop(&mut self) -> Result<(), String>;
}

/// 录制 worker 的运行统计。
#[derive(Clone, Debug, Default, PartialEq, Eq)]
pub struct RecordingWorkerStats {
    pub written_frames: u64,
    pub duplicated_frames: u64,
    pub dropped_late_ticks: u64,
    pub last_write_ms: u128,
}

pub struct RecordingWorkerHandle {
    stop: Arc<AtomicBool>,
    thread: Option<JoinHandle<Result<RecordingWorkerStats, String>>>,
}

impl RecordingWorkerHandle {
    pub fn start(
        frame_hub: Arc<FrameHub>,
        fps: u32,
        mut sink: Box<dyn FrameSink>,
    ) -> Result<Self, String> {
        let fps = fps.max(1);
        let interval = Duration::from_secs_f64(1.0 / fps as f64);
        let stop = Arc::new(AtomicBool::new(false));
        let stop_thread = stop.clone();

        let thread = thread::Builder::new()
            .name("recording-worker".to_string())
            .spawn(move || {
                let mut stats = RecordingWorkerStats::default();
                let mut next_tick = Instant::now();
                let mut last_seq = 0_u64;
                let mut last_frame: Option<Arc<CapturedFrame>> = None;

                while !stop_thread.load(Ordering::Relaxed) {
                    if last_frame.is_none() {
                        if let Some(frame) = frame_hub.latest() {
                            last_seq = frame.seq;
                            last_frame = Some(frame);
                            next_tick = Instant::now();
                        } else {
                            thread::sleep(Duration::from_millis(2));
                            continue;
                        }
                    }

                    let now = Instant::now();
                    if now < next_tick {
                        thread::sleep(next_tick.duration_since(now));
                        continue;
                    }

                    let late_ticks = now
                        .saturating_duration_since(next_tick)
                        .as_nanos()
                        .checked_div(interval.as_nanos().max(1))
                        .unwrap_or(0) as u64;
                    if late_ticks > 0 {
                        stats.dropped_late_ticks =
                            stats.dropped_late_ticks.saturating_add(late_ticks);
                    }
                    next_tick += interval.saturating_mul(late_ticks.saturating_add(1) as u32);

                    let (frame, duplicated) = match frame_hub.latest() {
                        Some(frame) if frame.seq > last_seq => {
                            last_seq = frame.seq;
                            last_frame = Some(frame.clone());
                            (frame, false)
                        }
                        // tick 时尚无新帧：在半拍预算内短暂等待 producer 发布，
                        // 避免“tick 恰好赶在新帧发布前几毫秒采样→误判复用旧帧，
                        // 下一拍水印跳变”的相位问题；等待不影响容器 PTS（由 capture 时间推导）。
                        _ => match frame_hub.wait_newer_than(last_seq, interval / 2) {
                            Some(frame) => {
                                last_seq = frame.seq;
                                last_frame = Some(frame.clone());
                                (frame, false)
                            }
                            None => (
                                last_frame.as_ref().expect("last frame must exist").clone(),
                                true,
                            ),
                        },
                    };

                    let write_started = Instant::now();
                    sink.write_frame(WriteFrame {
                        bgra: &frame.bgra,
                        capture_pts_100ns: frame.capture_pts_100ns,
                        duplicated,
                    })?;
                    stats.last_write_ms = write_started.elapsed().as_millis();
                    stats.written_frames = stats.written_frames.saturating_add(1);
                    if duplicated {
                        stats.duplicated_frames = stats.duplicated_frames.saturating_add(1);
                    }
                }

                sink.stop()?;
                Ok(stats)
            })
            .map_err(|e| format!("启动录制 worker 失败: {e}"))?;

        Ok(Self {
            stop,
            thread: Some(thread),
        })
    }

    pub fn stop(mut self) -> Result<RecordingWorkerStats, String> {
        self.stop_and_join()
    }

    fn stop_and_join(&mut self) -> Result<RecordingWorkerStats, String> {
        self.stop.store(true, Ordering::Relaxed);
        let thread = self
            .thread
            .take()
            .ok_or_else(|| "录制 worker 已停止".to_string())?;
        thread
            .join()
            .map_err(|_| "录制 worker 线程异常退出".to_string())?
    }
}

impl Drop for RecordingWorkerHandle {
    fn drop(&mut self) {
        if self.thread.is_some() {
            let _ = self.stop_and_join();
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::sync::{Arc, Mutex};
    use std::time::{Duration, Instant};

    struct TestSink {
        writes: Arc<Mutex<Vec<(Vec<u8>, i64, bool)>>>,
    }

    impl FrameSink for TestSink {
        fn write_frame(&mut self, frame: WriteFrame<'_>) -> Result<(), String> {
            // 仅测试收集允许 to_vec
            self.writes.lock().unwrap().push((
                frame.bgra.to_vec(),
                frame.capture_pts_100ns,
                frame.duplicated,
            ));
            Ok(())
        }

        fn stop(&mut self) -> Result<(), String> {
            Ok(())
        }
    }

    fn publish_frame(hub: &FrameHub, seq: u64, value: u8) -> u64 {
        hub.publish(CapturedFrame {
            seq,
            capture_pts_100ns: seq as i64 * 10_000,
            width: 1,
            height: 1,
            bgra: vec![value; 4],
        })
    }

    #[test]
    fn worker_writes_frames_at_fixed_ticks_and_reuses_latest_frame() {
        let hub = Arc::new(FrameHub::new());
        let writes = Arc::new(Mutex::new(Vec::new()));
        let sink = TestSink {
            writes: writes.clone(),
        };
        let worker = RecordingWorkerHandle::start(hub.clone(), 100, Box::new(sink))
            .expect("worker should start");

        publish_frame(&hub, 1, 7);
        std::thread::sleep(Duration::from_millis(35));
        let stats = worker.stop().expect("worker should stop");

        let written = writes.lock().unwrap().clone();
        assert!(
            written.len() >= 2,
            "expected fixed ticks to write multiple frames"
        );
        assert!(written.iter().all(|(frame, _, _)| frame == &vec![7; 4]));
        assert_eq!(stats.written_frames as usize, written.len());
        assert!(stats.duplicated_frames >= 1, "expected a duplicated tick");
    }

    #[test]
    fn worker_accepts_new_frame_after_capture_producer_restarts() {
        let hub = Arc::new(FrameHub::new());
        let writes = Arc::new(Mutex::new(Vec::new()));
        let sink = TestSink {
            writes: writes.clone(),
        };
        let worker = RecordingWorkerHandle::start(hub.clone(), 100, Box::new(sink))
            .expect("worker should start");

        // 模拟上一轮 producer 的局部序号已经较大；FrameHub 仍从本 session 的第 1 帧编号。
        let previous_seq = publish_frame(&hub, 300, 7);
        assert_eq!(previous_seq, 1);
        let deadline = Instant::now() + Duration::from_millis(200);
        while writes.lock().unwrap().is_empty() && Instant::now() < deadline {
            std::thread::sleep(Duration::from_millis(1));
        }
        assert!(
            !writes.lock().unwrap().is_empty(),
            "worker should consume the previous frame first"
        );

        // 模拟重启后的 producer 局部序号从 1 开始；FrameHub 序号必须继续递增。
        let restarted_seq = publish_frame(&hub, 1, 9);
        assert_eq!(restarted_seq, previous_seq + 1);
        std::thread::sleep(Duration::from_millis(35));
        worker.stop().expect("worker should stop");

        assert!(
            writes
                .lock()
                .unwrap()
                .iter()
                .any(|(frame, _, _)| frame == &vec![9; 4]),
            "worker should consume the restarted producer frame instead of repeating the stale frame"
        );
    }

    #[test]
    fn worker_forwards_capture_pts_and_duplicated_flag() {
        let hub = Arc::new(FrameHub::new());
        let writes = Arc::new(Mutex::new(Vec::new()));
        let sink = TestSink {
            writes: writes.clone(),
        };
        let worker = RecordingWorkerHandle::start(hub.clone(), 50, Box::new(sink))
            .expect("worker should start");

        hub.publish(CapturedFrame {
            seq: 0,
            capture_pts_100ns: 420_000,
            width: 1,
            height: 1,
            bgra: vec![1; 4],
        });
        std::thread::sleep(Duration::from_millis(60));
        let _ = worker.stop().expect("worker should stop");

        let w = writes.lock().unwrap();
        assert!(!w.is_empty());
        assert!(
            w.iter().any(|(_, pts, _)| *pts == 420_000),
            "expected capture_pts forwarded, got {:?}",
            w.iter().map(|(_, p, _)| *p).collect::<Vec<_>>()
        );
        assert!(
            w.iter().any(|(_, _, dup)| *dup),
            "expected at least one duplicated tick at high fps"
        );
    }
}
