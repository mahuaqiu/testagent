use std::sync::{Arc, Condvar, Mutex};
use std::time::{Duration, Instant};

use super::types::{CapturedFrame, FrameHubStats};

#[derive(Default, Debug)]
struct State {
    latest: Option<Arc<CapturedFrame>>,
    next_seq: u64,
    published_frames: u64,
    duplicate_reads: u64,
    last_frame_age_ms: u128,
}

#[derive(Clone, Debug)]
pub struct FrameHub {
    state: Arc<(Mutex<State>, Condvar)>,
}

impl FrameHub {
    pub fn new() -> Self {
        Self {
            state: Arc::new((Mutex::new(State::default()), Condvar::new())),
        }
    }

    /// 发布原始抓屏数据，并由 FrameHub 分配 session 级单调递增帧序号。
    pub fn publish_raw(
        &self,
        capture_pts_100ns: i64,
        width: u32,
        height: u32,
        bgra: Vec<u8>,
    ) -> u64 {
        self.publish(CapturedFrame {
            seq: 0,
            capture_pts_100ns,
            width,
            height,
            bgra,
        })
    }

    /// 发布帧。调用方提供的 seq 不参与排序，统一由 FrameHub 分配连续序号。
    pub fn publish(&self, mut frame: CapturedFrame) -> u64 {
        let (lock, cvar) = &*self.state;
        if let Ok(mut state) = lock.lock() {
            let next_seq = state
                .next_seq
                .checked_add(1)
                .expect("FrameHub 帧序号已耗尽");
            state.next_seq = next_seq;
            frame.seq = next_seq;
            state.latest = Some(Arc::new(frame));
            state.published_frames = state.published_frames.saturating_add(1);
            cvar.notify_all();
            return next_seq;
        }

        0
    }

    /// 清除最新帧快照，但保留单调帧序号，避免新消费者误用上一轮的旧帧。
    pub fn clear_latest(&self) {
        let (lock, cvar) = &*self.state;
        if let Ok(mut state) = lock.lock() {
            state.latest = None;
            cvar.notify_all();
        }
    }

    pub fn latest(&self) -> Option<Arc<CapturedFrame>> {
        let (lock, _) = &*self.state;
        lock.lock().ok().and_then(|state| state.latest.clone())
    }

    pub fn wait_newer_than(&self, last_seq: u64, timeout: Duration) -> Option<Arc<CapturedFrame>> {
        let (lock, cvar) = &*self.state;
        let mut state = lock.lock().ok()?;
        let deadline = Instant::now() + timeout;
        loop {
            if let Some(frame) = &state.latest {
                if frame.seq > last_seq {
                    return Some(frame.clone());
                }
            }

            let now = Instant::now();
            if now >= deadline {
                return None;
            }
            let remaining = deadline.saturating_duration_since(now);
            let (guard, wait_result) = cvar.wait_timeout(state, remaining).ok()?;
            state = guard;
            if wait_result.timed_out() {
                return state
                    .latest
                    .as_ref()
                    .filter(|frame| frame.seq > last_seq)
                    .cloned();
            }
        }
    }

    pub fn stats(&self) -> FrameHubStats {
        let (lock, _) = &*self.state;
        lock.lock()
            .map(|state| FrameHubStats {
                published_frames: state.published_frames,
                duplicate_reads: state.duplicate_reads,
                last_frame_age_ms: state.last_frame_age_ms,
            })
            .unwrap_or_default()
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::thread;

    fn make_frame(seq: u64) -> CapturedFrame {
        CapturedFrame {
            seq,
            capture_pts_100ns: seq as i64 * 10_000,
            width: 2,
            height: 2,
            bgra: vec![seq as u8; 16],
        }
    }

    #[test]
    fn latest_is_none_when_empty() {
        let hub = FrameHub::new();
        assert!(hub.latest().is_none());
    }

    #[test]
    fn publish_updates_latest_frame() {
        let hub = FrameHub::new();
        assert_eq!(hub.publish(make_frame(1)), 1);
        let latest = hub.latest().expect("expected latest frame");
        assert_eq!(latest.seq, 1);
        assert_eq!(latest.capture_pts_100ns, 10_000);
    }

    #[test]
    fn clear_latest_keeps_frame_sequence_monotonic() {
        let hub = FrameHub::new();
        assert_eq!(hub.publish(make_frame(300)), 1);
        hub.clear_latest();
        assert!(hub.latest().is_none());
        assert_eq!(hub.publish(make_frame(1)), 2);
        assert_eq!(hub.latest().expect("expected latest frame").seq, 2);
    }

    #[test]
    fn wait_newer_than_returns_new_frame_after_publish() {
        let hub = FrameHub::new();
        let wait_hub = hub.clone();
        let handle = thread::spawn(move || wait_hub.wait_newer_than(0, Duration::from_millis(500)));
        thread::sleep(Duration::from_millis(50));
        hub.publish(make_frame(1));
        let result = handle.join().expect("thread join failed");
        assert!(result.is_some());
        assert_eq!(result.unwrap().seq, 1);
    }
}
