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

/// 录制 sample 时间轴状态：相对首帧 capture 锚点。
#[derive(Clone, Copy, Debug, Default, PartialEq, Eq)]
pub struct SampleTimingState {
    pub anchor_pts_100ns: Option<i64>,
    pub last_pts_100ns: i64,
}

/// 单帧容器时长（100ns），固定 1/fps。
pub fn frame_duration_100ns(fps: u32) -> i64 {
    let fps = fps.max(1) as i64;
    10_000_000 / fps
}

/// 根据抓帧 pts 与 duplicate 标志推进容器 PTS。
///
/// - 首帧：pts=0，anchor=capture
/// - 真实新帧：pts = capture - anchor（非单调则 last+1）
/// - duplicate：pts = last + 1/fps（水印侧应冻结 capture）
pub fn next_sample_timing(
    state: &mut SampleTimingState,
    capture_pts_100ns: i64,
    duplicated: bool,
    fps: u32,
) -> (i64, i64) {
    let duration = frame_duration_100ns(fps);
    let pts = if state.anchor_pts_100ns.is_none() {
        state.anchor_pts_100ns = Some(capture_pts_100ns);
        0
    } else if duplicated {
        state.last_pts_100ns.saturating_add(duration)
    } else {
        let anchor = state.anchor_pts_100ns.unwrap_or(capture_pts_100ns);
        let raw = capture_pts_100ns.saturating_sub(anchor);
        if raw <= state.last_pts_100ns {
            state.last_pts_100ns.saturating_add(1)
        } else {
            raw
        }
    };
    state.last_pts_100ns = pts;
    (pts, duration)
}

/// 当前墙钟 → Unix epoch 100ns（与 capture 路径 `ms * 10_000` 一致）。
pub fn now_as_pts_100ns() -> i64 {
    std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map(|d| (d.as_millis() as i64).saturating_mul(10_000))
        .unwrap_or(0)
}

/// Unix epoch 100ns → 本地 `HH:MM:SS.mmm`。
pub fn local_hms_ms_from_pts_100ns(pts_100ns: i64) -> String {
    #[cfg(windows)]
    {
        use windows::Win32::Foundation::{FILETIME, SYSTEMTIME};
        use windows::Win32::System::Time::{FileTimeToSystemTime, SystemTimeToTzSpecificLocalTime};

        // Unix epoch 100ns → FILETIME(1601) → UTC SYSTEMTIME → 本地 SYSTEMTIME
        const UNIX_EPOCH_AS_FILETIME: i64 = 116_444_736_000_000_000;
        let ft_val = pts_100ns.saturating_add(UNIX_EPOCH_AS_FILETIME);
        let utc_ft = FILETIME {
            dwLowDateTime: ft_val as u32,
            dwHighDateTime: (ft_val >> 32) as u32,
        };

        unsafe {
            let mut utc_st = SYSTEMTIME::default();
            if FileTimeToSystemTime(&utc_ft, &mut utc_st).is_err() {
                return "00:00:00.000".to_string();
            }
            let mut local_st = SYSTEMTIME::default();
            if SystemTimeToTzSpecificLocalTime(None, &utc_st, &mut local_st).is_err() {
                // 回退：按 UTC 显示，避免崩溃
                local_st = utc_st;
            }
            format!(
                "{:02}:{:02}:{:02}.{:03}",
                local_st.wHour, local_st.wMinute, local_st.wSecond, local_st.wMilliseconds
            )
        }
    }

    #[cfg(not(windows))]
    {
        let _ = pts_100ns;
        "00:00:00.000".to_string()
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
        advance_clock_time, frame_duration_100ns, frame_index_to_pts_100ns,
        local_hms_ms_from_pts_100ns, logical_time_for_frame_index, logical_time_string,
        next_sample_timing, now_as_pts_100ns, sample_timing_for_frame_index, ClockTime,
        SampleTimingState,
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

    #[test]
    fn frame_duration_matches_fps() {
        assert_eq!(frame_duration_100ns(10), 1_000_000);
        assert_eq!(frame_duration_100ns(30), 333_333);
        assert_eq!(frame_duration_100ns(0), 10_000_000);
    }

    #[test]
    fn next_sample_timing_first_frame_is_zero() {
        let mut state = SampleTimingState::default();
        let (pts, dur) = next_sample_timing(&mut state, 1_000_000_000, false, 30);
        assert_eq!(pts, 0);
        assert_eq!(dur, frame_duration_100ns(30));
        assert_eq!(state.anchor_pts_100ns, Some(1_000_000_000));
        assert_eq!(state.last_pts_100ns, 0);
    }

    #[test]
    fn next_sample_timing_new_frame_uses_capture_delta() {
        let mut state = SampleTimingState::default();
        let _ = next_sample_timing(&mut state, 1_000_000_000, false, 10);
        let (pts, dur) = next_sample_timing(&mut state, 1_000_000_000 + 2_500_000, false, 10);
        assert_eq!(pts, 2_500_000);
        assert_eq!(dur, frame_duration_100ns(10));
        assert_eq!(state.last_pts_100ns, 2_500_000);
    }

    #[test]
    fn next_sample_timing_duplicate_advances_by_frame_duration() {
        let mut state = SampleTimingState::default();
        let _ = next_sample_timing(&mut state, 5_000, false, 10);
        let (pts, _) = next_sample_timing(&mut state, 5_000, true, 10);
        assert_eq!(pts, frame_duration_100ns(10));
        let (pts2, _) = next_sample_timing(&mut state, 5_000, true, 10);
        assert_eq!(pts2, 2 * frame_duration_100ns(10));
    }

    #[test]
    fn next_sample_timing_clamps_non_monotonic_capture() {
        let mut state = SampleTimingState::default();
        let _ = next_sample_timing(&mut state, 1_000_000_000, false, 10);
        let _ = next_sample_timing(&mut state, 1_000_000_000 + 5_000_000, false, 10);
        let (pts, _) = next_sample_timing(&mut state, 1_000_000_000 + 1_000_000, false, 10);
        assert_eq!(pts, 5_000_000 + 1);
    }

    #[test]
    fn local_hms_ms_roundtrip_shape() {
        let pts = now_as_pts_100ns();
        let s = local_hms_ms_from_pts_100ns(pts);
        assert_eq!(s.len(), 12, "got {s}");
        assert_eq!(&s[2..3], ":");
        assert_eq!(&s[5..6], ":");
        assert_eq!(&s[8..9], ".");
    }
}
