//! Rust 媒体二进制通道的 packet 定义。
//!
//! 控制面仍然使用 JSON 行协议；媒体数据面使用本模块定义的定长头 + 原始 payload，
//! 避免 base64 带来的额外拷贝和 33% 体积膨胀。媒体 packet 当前通过独立 loopback
//! TCP 输出，控制命令仍通过 JSON 行协议传输。

pub const MAGIC: [u8; 4] = *b"RSM1";
pub const VERSION: u8 = 1;
pub const HEADER_LEN: usize = 44;
pub const MESSAGE_NAL: u8 = 1;
pub const FLAG_KEYFRAME: u16 = 1;
pub const FLAG_CONFIG: u16 = 1 << 1;

#[derive(Clone, Debug, PartialEq, Eq)]
pub struct MediaPacket {
    pub message_type: u8,
    pub flags: u16,
    pub sequence: u64,
    pub pts_100ns: i64,
    pub duration_100ns: i64,
    pub width: u32,
    pub height: u32,
    pub payload: Vec<u8>,
}

#[derive(Default)]
pub struct MediaPacketDecoder {
    buffer: Vec<u8>,
}

impl MediaPacketDecoder {
    pub fn new() -> Self {
        Self::default()
    }

    /// 推入任意长度的通道数据，返回其中已经完整到达的 packet。
    /// 半包会留在内部缓冲区，多个连续 packet 会在一次调用中全部取出。
    pub fn push(&mut self, bytes: &[u8]) -> Result<Vec<MediaPacket>, String> {
        self.buffer.extend_from_slice(bytes);
        let mut packets = Vec::new();

        loop {
            if self.buffer.len() < 4 {
                break;
            }
            if self.buffer[..4] != MAGIC {
                return Err("媒体 packet magic 不匹配".to_string());
            }
            if self.buffer.len() < HEADER_LEN {
                break;
            }

            let payload_len = u32::from_le_bytes(
                self.buffer[40..44]
                    .try_into()
                    .map_err(|_| "媒体 packet 长度字段损坏".to_string())?,
            ) as usize;
            let packet_len = HEADER_LEN
                .checked_add(payload_len)
                .ok_or_else(|| "媒体 packet 长度溢出".to_string())?;
            if self.buffer.len() < packet_len {
                break;
            }

            let packet_bytes: Vec<u8> = self.buffer.drain(..packet_len).collect();
            packets.push(MediaPacket::decode(&packet_bytes)?);
        }

        Ok(packets)
    }

    pub fn buffered_len(&self) -> usize {
        self.buffer.len()
    }
}

impl MediaPacket {
    pub fn new_nal(
        flags: u16,
        sequence: u64,
        pts_100ns: i64,
        duration_100ns: i64,
        width: u32,
        height: u32,
        payload: Vec<u8>,
    ) -> Self {
        Self {
            message_type: MESSAGE_NAL,
            flags,
            sequence,
            pts_100ns,
            duration_100ns,
            width,
            height,
            payload,
        }
    }

    pub fn encode(&self) -> Result<Vec<u8>, String> {
        let payload_len = u32::try_from(self.payload.len())
            .map_err(|_| "媒体 packet payload 超过 u32 上限".to_string())?;
        let mut bytes = Vec::with_capacity(HEADER_LEN + self.payload.len());
        bytes.extend_from_slice(&MAGIC);
        bytes.push(VERSION);
        bytes.push(self.message_type);
        bytes.extend_from_slice(&self.flags.to_le_bytes());
        bytes.extend_from_slice(&self.sequence.to_le_bytes());
        bytes.extend_from_slice(&self.pts_100ns.to_le_bytes());
        bytes.extend_from_slice(&self.duration_100ns.to_le_bytes());
        bytes.extend_from_slice(&self.width.to_le_bytes());
        bytes.extend_from_slice(&self.height.to_le_bytes());
        bytes.extend_from_slice(&payload_len.to_le_bytes());
        bytes.extend_from_slice(&self.payload);
        Ok(bytes)
    }

    /// 从完整 packet 解码，适用于单元测试和接收端协议验证。
    pub fn decode(bytes: &[u8]) -> Result<Self, String> {
        if bytes.len() < HEADER_LEN {
            return Err("媒体 packet 头部不完整".to_string());
        }
        if bytes[..4] != MAGIC {
            return Err("媒体 packet magic 不匹配".to_string());
        }
        if bytes[4] != VERSION {
            return Err(format!("不支持的媒体 packet 版本: {}", bytes[4]));
        }
        let payload_len = u32::from_le_bytes(bytes[40..44].try_into().unwrap()) as usize;
        if bytes.len() != HEADER_LEN + payload_len {
            return Err("媒体 packet 长度与 payload_length 不一致".to_string());
        }
        Ok(Self {
            message_type: bytes[5],
            flags: u16::from_le_bytes(bytes[6..8].try_into().unwrap()),
            sequence: u64::from_le_bytes(bytes[8..16].try_into().unwrap()),
            pts_100ns: i64::from_le_bytes(bytes[16..24].try_into().unwrap()),
            duration_100ns: i64::from_le_bytes(bytes[24..32].try_into().unwrap()),
            width: u32::from_le_bytes(bytes[32..36].try_into().unwrap()),
            height: u32::from_le_bytes(bytes[36..40].try_into().unwrap()),
            payload: bytes[44..].to_vec(),
        })
    }
}

/// 将同一编码 tick 产生的多个 Annex-B NAL 合并为一个媒体 packet。
///
/// packet 的 payload 保持 Annex-B 原样，接收端只需要按 packet 边界消费，
/// 不再依赖旧版单字节类型前缀和 base64 行边界。
pub fn packet_from_encoded_frames(
    frames: &[crate::win_recorder::EncodedFrame],
    sequence: u64,
    pts_100ns: i64,
    duration_100ns: i64,
    width: u32,
    height: u32,
) -> Option<MediaPacket> {
    if frames.is_empty() {
        return None;
    }

    let keyframe = frames
        .iter()
        .any(|frame| matches!(frame.frame_type, crate::win_recorder::FrameType::IDR));
    let config = frames.iter().any(|frame| {
        matches!(
            frame.frame_type,
            crate::win_recorder::FrameType::SPS | crate::win_recorder::FrameType::PPS
        )
    });
    let mut payload = Vec::new();
    for frame in frames {
        payload.extend_from_slice(&frame.data);
    }
    if payload.is_empty() {
        return None;
    }

    Some(MediaPacket::new_nal(
        (if keyframe { FLAG_KEYFRAME } else { 0 })
            | (if config { FLAG_CONFIG } else { 0 }),
        sequence,
        pts_100ns,
        duration_100ns,
        width,
        height,
        payload,
    ))
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::win_recorder::{EncodedFrame, FrameType};

    #[test]
    fn packet_round_trip_preserves_header_and_payload() {
        let packet = MediaPacket::new_nal(
            FLAG_KEYFRAME,
            17,
            333_333,
            333_334,
            1920,
            1080,
            vec![0, 0, 0, 1, 0x65, 1, 2, 3],
        );
        let encoded = packet.encode().expect("encode should succeed");
        assert_eq!(encoded.len(), HEADER_LEN + 8);
        assert_eq!(MediaPacket::decode(&encoded).unwrap(), packet);
    }

    #[test]
    fn packet_rejects_truncated_or_invalid_input() {
        assert!(MediaPacket::decode(&[0; HEADER_LEN - 1]).is_err());
        assert!(MediaPacket::decode(&[0; HEADER_LEN]).is_err());
    }

    #[test]
    fn encoded_frames_become_one_packet_with_logical_timing() {
        let frames = vec![
            EncodedFrame {
                frame_type: FrameType::SPS,
                data: vec![0, 0, 0, 1, 7],
            },
            EncodedFrame {
                frame_type: FrameType::IDR,
                data: vec![0, 0, 0, 1, 5, 1, 2],
            },
        ];
        let packet = packet_from_encoded_frames(&frames, 3, 666_666, 333_334, 1280, 720)
            .expect("packet should be assembled");

        assert_eq!(packet.sequence, 3);
        assert_eq!(packet.pts_100ns, 666_666);
        assert_eq!(packet.duration_100ns, 333_334);
        assert_eq!(packet.flags, FLAG_KEYFRAME | FLAG_CONFIG);
        assert_eq!(packet.payload.len(), 12);
    }

    #[test]
    fn configuration_only_frames_are_marked_as_config_packets() {
        let frames = vec![
            EncodedFrame {
                frame_type: FrameType::SPS,
                data: vec![0, 0, 0, 1, 7],
            },
            EncodedFrame {
                frame_type: FrameType::PPS,
                data: vec![0, 0, 0, 1, 8],
            },
        ];
        let packet = packet_from_encoded_frames(&frames, 0, 0, 333_333, 1280, 720)
            .expect("configuration frames should become a packet");

        assert_eq!(packet.flags, FLAG_CONFIG);
    }

    #[test]
    fn decoder_handles_half_packet_and_concatenated_packets() {
        let first = MediaPacket::new_nal(0, 1, 0, 333_333, 2, 2, vec![1, 2, 3]);
        let second = MediaPacket::new_nal(FLAG_KEYFRAME, 2, 333_333, 333_334, 2, 2, vec![4, 5]);
        let first_bytes = first.encode().unwrap();
        let second_bytes = second.encode().unwrap();
        let mut decoder = MediaPacketDecoder::new();

        assert!(decoder.push(&first_bytes[..10]).unwrap().is_empty());
        assert_eq!(decoder.buffered_len(), 10);
        assert_eq!(decoder.push(&first_bytes[10..]).unwrap(), vec![first]);

        let packets = decoder
            .push(&[second_bytes, second.encode().unwrap()].concat())
            .unwrap();
        assert_eq!(packets, vec![second.clone(), second]);
        assert_eq!(decoder.buffered_len(), 0);
    }

    #[test]
    fn decoder_rejects_invalid_magic_before_waiting_for_a_full_packet() {
        let mut decoder = MediaPacketDecoder::new();
        assert!(decoder.push(b"BAD!").is_err());
    }
}
