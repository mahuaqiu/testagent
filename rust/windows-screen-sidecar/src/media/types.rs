#[derive(Clone, Debug, PartialEq, Eq)]
pub struct CapturedFrame {
    pub seq: u64,
    pub capture_pts_100ns: i64,
    pub width: u32,
    pub height: u32,
    pub bgra: Vec<u8>,
}

#[derive(Clone, Debug, Default, PartialEq, Eq)]
pub struct FrameHubStats {
    pub published_frames: u64,
    pub duplicate_reads: u64,
    pub last_frame_age_ms: u128,
}

#[derive(Clone, Debug, Default, PartialEq, Eq)]
pub struct RecorderStats {
    pub written_frames: u64,
    pub duplicated_frames: u64,
    pub dropped_late_ticks: u64,
    pub last_write_ms: u128,
}

#[derive(Clone, Debug, Default, PartialEq, Eq)]
pub struct StreamStats {
    pub encoded_frames: u64,
    pub dropped_frames: u64,
    pub sent_packets: u64,
    pub last_encode_ms: u128,
    pub last_send_ms: u128,
}

#[derive(Clone, Debug, Default, PartialEq, Eq)]
pub struct MediaSessionStats {
    pub published_frames: u64,
    pub recording_running: bool,
    pub streaming_running: bool,
}
