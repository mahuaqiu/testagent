use std::sync::{Arc, Condvar, Mutex};
use std::time::{Duration, Instant};

use super::types::{CapturedFrame, FrameHubStats};

#[derive(Default, Debug)]
struct State {
    latest: Option<Arc<CapturedFrame>>,
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

    pub fn publish(&self, frame: CapturedFrame) {
        let (lock, cvar) = &*self.state;
        if let Ok(mut state) = lock.lock() {
            state.latest = Some(Arc::new(frame));
            state.published_frames += 1;
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
        hub.publish(make_frame(1));
        let latest = hub.latest().expect("expected latest frame");
        assert_eq!(latest.seq, 1);
        assert_eq!(latest.capture_pts_100ns, 10_000);
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
