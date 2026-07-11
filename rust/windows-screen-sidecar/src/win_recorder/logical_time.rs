use std::fmt;

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct ClockTime {
    pub hour: u8,
    pub minute: u8,
    pub second: u8,
    pub millisecond: u16,
}

impl ClockTime {
    pub fn new(hour: u8, minute: u8, second: u8, millisecond: u16) -> Self {
        Self {
            hour,
            minute,
            second,
            millisecond,
        }
    }
}

impl fmt::Display for ClockTime {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        write!(
            f,
            "{:02}:{:02}:{:02}.{:03}",
            self.hour, self.minute, self.second, self.millisecond
        )
    }
}

pub fn advance_clock_time(base: ClockTime, elapsed_ms: u128) -> ClockTime {
    let total_ms = (base.hour as u128 * 3_600_000)
        + (base.minute as u128 * 60_000)
        + (base.second as u128 * 1_000)
        + base.millisecond as u128
        + elapsed_ms;
    let day_ms = 24_u128 * 3_600_000;
    let total_ms = total_ms % day_ms;
    let hour = (total_ms / 3_600_000) as u8;
    let minute = ((total_ms % 3_600_000) / 60_000) as u8;
    let second = ((total_ms % 60_000) / 1_000) as u8;
    let millisecond = (total_ms % 1_000) as u16;
    ClockTime {
        hour,
        minute,
        second,
        millisecond,
    }
}

pub fn logical_time_string(base: ClockTime, elapsed_100ns: i64) -> String {
    let elapsed_ms = if elapsed_100ns <= 0 {
        0
    } else {
        (elapsed_100ns as u128) / 10_000
    };
    advance_clock_time(base, elapsed_ms).to_string()
}

pub fn logical_time_for_frame_index(base: ClockTime, frame_index: u64, fps: u32) -> String {
    logical_time_string(base, frame_index_to_pts_100ns(frame_index, fps))
}

pub fn frame_index_to_pts_100ns(frame_index: u64, fps: u32) -> i64 {
    let fps = fps.max(1) as i64;
    let frame_index = frame_index as i64;
    frame_index.saturating_mul(10_000_000) / fps
}

pub fn sample_timing_for_frame_index(frame_index: u64, fps: u32) -> (i64, i64) {
    let pts = frame_index_to_pts_100ns(frame_index, fps);
    let next_pts = frame_index_to_pts_100ns(frame_index.saturating_add(1), fps);
    let duration = (next_pts - pts).max(1);
    (pts, duration)
}

#[cfg(test)]
mod tests {
    use super::{
        advance_clock_time, frame_index_to_pts_100ns, logical_time_for_frame_index,
        logical_time_string, sample_timing_for_frame_index, ClockTime,
    };

    #[test]
    fn logical_pts_increases_by_frame_duration() {
        assert_eq!(frame_index_to_pts_100ns(0, 10), 0);
        assert_eq!(frame_index_to_pts_100ns(1, 10), 1_000_000);
        assert_eq!(frame_index_to_pts_100ns(2, 10), 2_000_000);
    }

    #[test]
    fn logical_pts_handles_25fps() {
        assert_eq!(frame_index_to_pts_100ns(0, 25), 0);
        assert_eq!(frame_index_to_pts_100ns(1, 25), 400_000);
        assert_eq!(frame_index_to_pts_100ns(2, 25), 800_000);
    }

    #[test]
    fn logical_clock_time_rolls_over_midnight() {
        let base = ClockTime::new(23, 59, 59, 900);
        let advanced = advance_clock_time(base, 250);
        assert_eq!(advanced, ClockTime::new(0, 0, 0, 150));
    }

    #[test]
    fn logical_clock_time_string_uses_base_time_and_elapsed() {
        let base = ClockTime::new(12, 34, 56, 789);
        assert_eq!(logical_time_string(base, 0), "12:34:56.789");
        assert_eq!(logical_time_string(base, 5_000_000), "12:34:57.289");
    }

    #[test]
    fn logical_time_for_frame_index_uses_frame_index_and_fps() {
        let base = ClockTime::new(12, 34, 56, 789);
        assert_eq!(logical_time_for_frame_index(base, 15, 30), "12:34:57.289");
    }

    #[test]
    fn sample_timing_is_exactly_spaced_by_frame_index() {
        assert_eq!(sample_timing_for_frame_index(0, 30), (0, 333_333));
        assert_eq!(sample_timing_for_frame_index(1, 30), (333_333, 333_333));
        assert_eq!(sample_timing_for_frame_index(2, 30), (666_666, 333_334));
    }
}
