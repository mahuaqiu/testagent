use std::collections::VecDeque;
use std::io::{stderr, Write};
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::{Arc, Mutex};
use std::thread::{self, JoinHandle};
use std::time::{Duration, Instant};

use base64::{engine::general_purpose::STANDARD, Engine as _};

use crate::win_recorder::{EncodedFrame, EncodingContext, FrameType};

use super::{packet_from_encoded_frames, BinaryMediaOutputSender, CapturedFrame, FrameHub};

/// 编码器启动阶段不再注入黑帧，首个 IDR 即可作为真实画面起点。


#[derive(Debug, Default)]
struct PushWarmupGate {
    real_idr_seen: bool,
}

impl PushWarmupGate {
    fn should_push(&mut self, frame_type: &FrameType, _data_len: usize) -> bool {
        if self.real_idr_seen {
            return true;
        }
        if matches!(frame_type, FrameType::IDR) {
            self.real_idr_seen = true;
            return true;
        }
        false
    }
}

/// 推流 worker 的运行统计。
#[derive(Clone, Debug, Default, PartialEq, Eq)]
pub struct StreamWorkerStats {
    pub encoded_frames: u64,
    pub queued_packets: u64,
    pub pushed_nals: u64,
    pub duplicated_ticks: u64,
    pub dropped_late_ticks: u64,
    pub last_encode_ms: u128,
}

pub struct StreamWorkerHandle {
    stop: Arc<AtomicBool>,
    thread: Option<JoinHandle<Result<StreamWorkerStats, String>>>,
}

impl StreamWorkerHandle {
    pub fn start(
        frame_hub: Arc<FrameHub>,
        fps: u32,
        mut encoder: EncodingContext,
        stream_queue: Option<Arc<Mutex<VecDeque<Vec<u8>>>>>,
        push_enabled: Arc<AtomicBool>,
        binary_sender: Option<BinaryMediaOutputSender>,
        initial_frames: Option<Vec<EncodedFrame>>,
    ) -> Result<Self, String> {
        let stop = Arc::new(AtomicBool::new(false));
        let stop_thread = stop.clone();
        let thread = thread::Builder::new()
            .name("stream-worker".to_string())
            .spawn(move || {
                let mut stats = StreamWorkerStats::default();
                let interval = Duration::from_secs_f64(1.0 / fps.max(1) as f64);
                let mut next_tick = Instant::now();
                let mut last_seq = 0_u64;
                let mut tick_sequence = 0_u64;
                let mut packet_sequence = 0_u64;
                let mut last_frame: Option<Arc<CapturedFrame>> = None;
                let mut push_warmup_gate = PushWarmupGate::default();
                let stream_started = Instant::now();
                let mut diagnostic_started = Instant::now();
                let mut diagnostic_packets = 0_u64;
                let mut diagnostic_bytes = 0_usize;

                // 真实屏幕帧已在启动阶段预热到 MFT，优先作为首个媒体包发送。
                // 这会消化黑帧暖机残留，避免客户端等待下一个 GOP。
                if let Some(frames) = initial_frames {
                    if let Some(packet) = build_binary_packet(
                        &frames,
                        0,
                        fps,
                        encoder.width(),
                        encoder.height(),
                        &mut packet_sequence,
                    ) {
                        let encoded_packet = packet.encode()?;
                        if let Some(sender) = &binary_sender {
                            sender.send(encoded_packet);
                        } else if let Some(stream_queue) = &stream_queue {
                            if let Ok(mut queue) = stream_queue.lock() {
                                if queue.len() >= 16 {
                                    queue.pop_front();
                                }
                                queue.push_back(assemble_packet(&frames));
                            }
                        }
                        if push_enabled.load(Ordering::Relaxed) {
                            let mut gate = PushWarmupGate::default();
                            for encoded in &frames {
                                let is_config = matches!(encoded.frame_type, FrameType::SPS | FrameType::PPS);
                                if is_config || gate.should_push(&encoded.frame_type, encoded.data.len()) {
                                    push_frame_to_stderr(
                                        frame_type_to_u8(&encoded.frame_type),
                                        &encoded.data,
                                    );
                                    stats.pushed_nals = stats.pushed_nals.saturating_add(1);
                                }
                            }
                        }
                        stats.encoded_frames = stats.encoded_frames.saturating_add(1);
                    }
                }
                tick_sequence = 1;
                next_tick = Instant::now();

                while !stop_thread.load(Ordering::Relaxed) {

                    let now = Instant::now();
                    if now < next_tick {
                        thread::sleep(next_tick.duration_since(now));
                    }
                    let now = Instant::now();
                    let late_ticks = now
                        .saturating_duration_since(next_tick)
                        .as_nanos()
                        .checked_div(interval.as_nanos().max(1))
                        .unwrap_or(0) as u64;
                    stats.dropped_late_ticks = stats.dropped_late_ticks.saturating_add(late_ticks);
                    next_tick += interval.saturating_mul(late_ticks.saturating_add(1) as u32);
                    let tick_index = tick_sequence;
                    // 先等到本次编码 tick，再读取最新帧，避免提前拿到旧帧后继续等待。
                    let frame = match frame_hub.latest() {
                        Some(frame) => {
                            if frame.seq == last_seq {
                                stats.duplicated_ticks = stats.duplicated_ticks.saturating_add(1);
                            } else {
                                last_seq = frame.seq;
                            }
                            last_frame = Some(frame.clone());
                            frame
                        }
                        None => match &last_frame {
                            Some(frame) => frame.clone(),
                            None => {
                                thread::sleep(Duration::from_millis(2));
                                continue;
                            }
                        },
                    };

                    tick_sequence = tick_sequence.saturating_add(1);

                    let started = Instant::now();
                    let frames = encoder
                        .encode_frames_detailed(&frame.bgra)
                        .map_err(|e| format!("推流编码失败: {e}"))?;
                    stats.last_encode_ms = started.elapsed().as_millis();
                    let Some(frames) = frames else { continue };
                    stats.encoded_frames = stats.encoded_frames.saturating_add(1);

                    let encoded_bytes = frames.iter().map(|item| item.data.len()).sum::<usize>();
                    let has_idr = frames.iter().any(|item| matches!(item.frame_type, FrameType::IDR));
                    let has_config = frames.iter().any(|item| matches!(item.frame_type, FrameType::SPS | FrameType::PPS));
                    diagnostic_packets = diagnostic_packets.saturating_add(1);
                    diagnostic_bytes = diagnostic_bytes.saturating_add(encoded_bytes);
                    if stats.encoded_frames <= 15 || has_idr {
                        let capture_age_ms = crate::capture::current_timestamp_ms().saturating_sub((frame.capture_pts_100ns.max(0) / 10_000) as u128);
                        eprintln!("[stream-diag] encoder packet frame_seq={} tick={} packet_seq={} elapsed_ms={} encode_ms={} capture_age_ms={} bytes={} idr={} config={} duplicated_ticks={} dropped_late_ticks={}", frame.seq, tick_index, packet_sequence, stream_started.elapsed().as_millis(), stats.last_encode_ms, capture_age_ms, encoded_bytes, has_idr, has_config, stats.duplicated_ticks, stats.dropped_late_ticks);
                    }
                    if diagnostic_started.elapsed() >= Duration::from_secs(1) {
                        eprintln!("[stream-diag] encoder summary elapsed_ms={} packets={} bytes={} last_frame_seq={} next_packet_seq={} encode_ms={} duplicated_ticks={} dropped_late_ticks={}", stream_started.elapsed().as_millis(), diagnostic_packets, diagnostic_bytes, frame.seq, packet_sequence, stats.last_encode_ms, stats.duplicated_ticks, stats.dropped_late_ticks);
                        diagnostic_started = Instant::now();
                        diagnostic_packets = 0;
                        diagnostic_bytes = 0;
                    }
                    if let Some(stream_queue) = &stream_queue {
                        let combined = assemble_packet(&frames);
                        if let Ok(mut queue) = stream_queue.lock() {
                            if queue.len() >= 16 {
                                queue.pop_front();
                            }
                            queue.push_back(combined);
                            stats.queued_packets = stats.queued_packets.saturating_add(1);
                        }
                    }

                    if let Some(sender) = &binary_sender {
                        if let Some(packet) = build_binary_packet(
                            &frames,
                            tick_index,
                            fps,
                            encoder.width(),
                            encoder.height(),
                            &mut packet_sequence,
                        ) {
                            let encoded_packet = packet.encode()?;
                            sender.send(encoded_packet);
                        }
                    }
                    if push_enabled.load(Ordering::Relaxed) {
                        for encoded in &frames {
                            if !push_warmup_gate
                                .should_push(&encoded.frame_type, encoded.data.len())
                            {
                                continue;
                            }
                            push_frame_to_stderr(
                                frame_type_to_u8(&encoded.frame_type),
                                &encoded.data,
                            );
                            stats.pushed_nals = stats.pushed_nals.saturating_add(1);
                        }
                    }
                }

                encoder
                    .stop()
                    .map_err(|e| format!("停止推流编码器失败: {e}"))?;
                Ok(stats)
            })
            .map_err(|e| format!("启动推流 worker 失败: {e}"))?;

        Ok(Self {
            stop,
            thread: Some(thread),
        })
    }

    pub fn push_sps_pps_once(&self, sps: &[u8], pps: &[u8]) {
        if !sps.is_empty() {
            push_frame_to_stderr(0, sps);
        }
        if !pps.is_empty() {
            push_frame_to_stderr(1, pps);
        }
    }

    pub fn stop(mut self) -> Result<StreamWorkerStats, String> {
        self.stop.store(true, Ordering::Relaxed);
        let thread = self
            .thread
            .take()
            .ok_or_else(|| "推流 worker 已停止".to_string())?;
        thread
            .join()
            .map_err(|_| "推流 worker 线程异常退出".to_string())?
    }
}

fn build_binary_packet(
    frames: &[EncodedFrame],
    tick_index: u64,
    fps: u32,
    width: u32,
    height: u32,
    next_sequence: &mut u64,
) -> Option<super::MediaPacket> {
    let (pts_100ns, duration_100ns) =
        crate::win_recorder::sample_timing_for_frame_index(tick_index, fps);
    let mut packet = packet_from_encoded_frames(
        frames,
        *next_sequence,
        pts_100ns,
        duration_100ns,
        width,
        height,
    )?;
    packet.sequence = *next_sequence;
    *next_sequence = (*next_sequence).saturating_add(1);
    Some(packet)
}

impl Drop for StreamWorkerHandle {
    fn drop(&mut self) {
        if let Some(thread) = self.thread.take() {
            self.stop.store(true, Ordering::Relaxed);
            let _ = thread.join();
        }
    }
}

pub fn push_frame_to_stderr(frame_type: u8, data: &[u8]) {
    let encoded = STANDARD.encode(data);
    let mut output = stderr();
    let _ = writeln!(output, "{}{}", (b'0' + frame_type) as char, encoded);
    let _ = output.flush();
}

fn frame_type_to_u8(frame_type: &FrameType) -> u8 {
    match frame_type {
        FrameType::SPS => 0,
        FrameType::PPS => 1,
        FrameType::IDR => 2,
        FrameType::PFrame | FrameType::Unknown => 3,
    }
}

fn assemble_packet(frames: &[EncodedFrame]) -> Vec<u8> {
    let prefix = if frames
        .iter()
        .any(|f| matches!(f.frame_type, FrameType::IDR))
    {
        0x02
    } else if frames
        .iter()
        .any(|f| matches!(f.frame_type, FrameType::SPS | FrameType::PPS))
    {
        0x01
    } else {
        0x03
    };
    let mut packet = vec![prefix];
    for frame in frames {
        packet.extend_from_slice(&frame.data);
    }
    packet
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::win_recorder::sample_timing_for_frame_index;

    #[test]
    fn warmup_gate_discards_black_idr_and_pre_idr_frames() {
        let mut gate = PushWarmupGate::default();

        assert!(gate.should_push(&FrameType::IDR, 6_176));
        assert!(gate.should_push(&FrameType::PFrame, 4_000));
        assert!(gate.should_push(&FrameType::SPS, 32));
        assert!(gate.should_push(&FrameType::PPS, 16));
    }

    #[test]
    fn warmup_gate_opens_on_real_idr_and_keeps_following_nals() {
        let mut gate = PushWarmupGate::default();

        assert!(gate.should_push(&FrameType::IDR, 6_176));
        assert!(gate.should_push(&FrameType::PFrame, 100));
        assert!(gate.should_push(&FrameType::SPS, 32));
    }

    #[test]
    fn binary_packet_uses_logical_tick_timing_and_encoder_dimensions() {
        let frames = vec![EncodedFrame {
            frame_type: FrameType::IDR,
            data: vec![0, 0, 0, 1, 0x65, 1, 2],
        }];
        let (pts_100ns, duration_100ns) = sample_timing_for_frame_index(2, 30);
        let packet = packet_from_encoded_frames(&frames, 2, pts_100ns, duration_100ns, 1920, 1080)
            .expect("encoded frames should become a media packet");

        assert_eq!(packet.sequence, 2);
        assert_eq!(packet.pts_100ns, 666_666);
        assert_eq!(packet.duration_100ns, 333_334);
        assert_eq!((packet.width, packet.height), (1920, 1080));
        assert_eq!(packet.flags, super::super::FLAG_KEYFRAME);
    }

    #[test]
    fn binary_packet_sequence_counts_media_packets_not_empty_encoder_ticks() {
        let frames = vec![EncodedFrame {
            frame_type: FrameType::PFrame,
            data: vec![0, 0, 0, 1, 0x41, 1, 2],
        }];
        let mut next_sequence = 0_u64;

        assert!(build_binary_packet(&[], 0, 30, 1920, 1080, &mut next_sequence).is_none());
        assert_eq!(next_sequence, 0);

        let first = build_binary_packet(&frames, 1, 30, 1920, 1080, &mut next_sequence)
            .expect("first encoded tick should produce a packet");
        assert_eq!(first.sequence, 0);
        assert_eq!(first.pts_100ns, 333_333);
        assert_eq!(next_sequence, 1);

        let second = build_binary_packet(&frames, 4, 30, 1920, 1080, &mut next_sequence)
            .expect("later encoded tick should produce a packet");
        assert_eq!(second.sequence, 1);
        assert_eq!(second.pts_100ns, 1_333_333);
        assert_eq!(next_sequence, 2);
    }
}
