pub mod binary_output;
pub mod capture_producer;
pub mod frame_hub;
pub mod packet;
pub mod recording_worker;
pub mod stream_worker;
pub mod types;

pub use binary_output::{BinaryMediaOutput, BinaryMediaOutputSender};
pub use capture_producer::CaptureProducer;
pub use frame_hub::FrameHub;
pub use packet::{
    packet_from_encoded_frames, MediaPacket, MediaPacketDecoder, FLAG_CONFIG, FLAG_KEYFRAME,
    HEADER_LEN, MAGIC, MESSAGE_NAL, VERSION,
};
pub use recording_worker::{FrameSink, RecordingWorkerHandle, RecordingWorkerStats, WriteFrame};
pub use stream_worker::{push_frame_to_stderr, StreamWorkerHandle, StreamWorkerStats};
pub use types::{CapturedFrame, FrameHubStats, MediaSessionStats, RecorderStats, StreamStats};
