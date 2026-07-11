use std::collections::VecDeque;
use std::io::{ErrorKind, Write};
use std::net::{TcpListener, TcpStream};
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::{Arc, Mutex};
use std::thread::{self, JoinHandle};
use std::time::Duration;

use super::packet::{MediaPacket, FLAG_CONFIG, FLAG_KEYFRAME};

const QUEUE_CAPACITY: usize = 8;
#[cfg(test)]
const REAL_IDR_MIN_BYTES: usize = 30_000;

struct BinaryQueue {
    packets: VecDeque<Vec<u8>>,
    needs_keyframe: bool,
}

#[derive(Clone)]
pub struct BinaryMediaOutputSender {
    queue: Arc<Mutex<BinaryQueue>>,
}

impl BinaryMediaOutputSender {
    pub fn send(&self, packet: Vec<u8>) {
        if let Ok(mut queue) = self.queue.lock() {
            if queue.packets.len() >= QUEUE_CAPACITY {
                queue.packets.clear();
                queue.needs_keyframe = true;
            }
            queue.packets.push_back(packet);
        }
    }
}

pub struct BinaryMediaOutput {
    endpoint: String,
    stop: Arc<AtomicBool>,
    thread: Option<JoinHandle<()>>,
    sender: BinaryMediaOutputSender,
}

impl BinaryMediaOutput {
    pub fn start() -> Result<Self, String> {
        let listener = TcpListener::bind(("127.0.0.1", 0))
            .map_err(|e| format!("创建二进制媒体端点失败: {e}"))?;
        listener
            .set_nonblocking(true)
            .map_err(|e| format!("设置二进制媒体端点非阻塞失败: {e}"))?;
        let endpoint = listener
            .local_addr()
            .map(|address| format!("tcp://{address}"))
            .map_err(|e| format!("读取二进制媒体端点失败: {e}"))?;

        let queue = Arc::new(Mutex::new(BinaryQueue {
            packets: VecDeque::with_capacity(QUEUE_CAPACITY),
            needs_keyframe: false,
        }));
        let sender = BinaryMediaOutputSender {
            queue: queue.clone(),
        };
        let stop = Arc::new(AtomicBool::new(false));
        let stop_thread = stop.clone();
        let thread = thread::Builder::new()
            .name("binary-media-output".to_string())
            .spawn(move || run_output_loop(listener, queue, stop_thread))
            .map_err(|e| format!("启动二进制媒体输出线程失败: {e}"))?;

        Ok(Self {
            endpoint,
            stop,
            thread: Some(thread),
            sender,
        })
    }

    pub fn endpoint(&self) -> &str {
        &self.endpoint
    }

    pub fn sender(&self) -> BinaryMediaOutputSender {
        self.sender.clone()
    }
}

impl Drop for BinaryMediaOutput {
    fn drop(&mut self) {
        self.stop.store(true, Ordering::Relaxed);
        if let Some(thread) = self.thread.take() {
            let _ = thread.join();
        }
    }
}

fn run_output_loop(
    listener: TcpListener,
    queue: Arc<Mutex<BinaryQueue>>,
    stop: Arc<AtomicBool>,
) {
    let mut client: Option<TcpStream> = None;
    let mut keyframe_gate = InitialKeyframeGate::default();
    while !stop.load(Ordering::Relaxed) {
        if client.is_none() {
            match listener.accept() {
                Ok((stream, _)) => {
                    let _ = stream.set_nodelay(true);
                    client = Some(stream);
                    keyframe_gate = InitialKeyframeGate::default();
                }
                Err(error) if error.kind() == ErrorKind::WouldBlock => {
                    thread::sleep(Duration::from_millis(5));
                    continue;
                }
                Err(_) => break,
            }
        }

        let (packet, reset_gate) = queue
            .lock()
            .ok()
            .and_then(|mut pending| {
                if client.is_none() {
                    return None;
                }
                let reset_gate = pending.needs_keyframe;
                pending.needs_keyframe = false;
                Some((pending.packets.pop_front(), reset_gate))
            })
            .unwrap_or((None, false));
        if reset_gate {
            keyframe_gate = InitialKeyframeGate::default();
        }
        let Some(packet) = packet else {
            thread::sleep(Duration::from_millis(2));
            continue;
        };

        for packet in keyframe_gate.accept(&packet) {
            if let Some(stream) = client.as_mut() {
                if stream.write_all(&packet).is_err() {
                    client = None;
                    keyframe_gate = InitialKeyframeGate::default();
                    break;
                }
            } else {
                break;
            }
        }
    }
}

/// 新客户端连接后的首包门控。
///
/// 连接建立时，队列里可能已经存在无法独立解码的 P 帧。门控会丢弃这些
/// 旧 P 帧，暂存最近的配置包，并在收到下一个关键帧时一次性释放配置和关键帧。
/// 如果关键帧本身已经携带配置，则不会重复发送旧配置。
#[derive(Default)]
struct InitialKeyframeGate {
    ready: bool,
    pending_config: Option<Vec<u8>>,
}

impl InitialKeyframeGate {
    fn accept(&mut self, packet: &[u8]) -> Vec<Vec<u8>> {
        if self.ready {
            return vec![packet.to_vec()];
        }

        let Ok(decoded) = MediaPacket::decode(packet) else {
            return Vec::new();
        };
        let is_keyframe = decoded.flags & FLAG_KEYFRAME != 0;
        let has_config = decoded.flags & FLAG_CONFIG != 0;

        if !is_keyframe {
            if has_config {
                self.pending_config = config_packet(&decoded);
            }
            return Vec::new();
        }

        self.ready = true;
        if has_config {
            return vec![packet.to_vec()];
        }

        let mut packets = Vec::with_capacity(2);
        if let Some(config) = self.pending_config.take() {
            packets.push(config);
        }
        packets.push(packet.to_vec());
        packets
    }
}

fn config_packet(packet: &MediaPacket) -> Option<Vec<u8>> {
    let payload = extract_config_nals(&packet.payload)?;
    let mut config = packet.clone();
    config.flags = FLAG_CONFIG;
    config.payload = payload;
    config.encode().ok()
}

fn extract_config_nals(payload: &[u8]) -> Option<Vec<u8>> {
    let mut config = Vec::new();
    let mut offset = 0;
    while let Some((start, nal_start)) = next_annex_b_nal(payload, offset) {
        let nal_end = next_annex_b_start_code(payload, nal_start).unwrap_or(payload.len());
        if nal_start < nal_end {
            let nal_type = payload[nal_start] & 0x1f;
            if nal_type == 7 || nal_type == 8 {
                config.extend_from_slice(&payload[start..nal_end]);
            }
        }
        offset = nal_end;
    }
    (!config.is_empty()).then_some(config)
}

fn next_annex_b_nal(payload: &[u8], offset: usize) -> Option<(usize, usize)> {
    let (start, start_code_len) = next_annex_b_start_code_with_len(payload, offset)?;
    Some((start, start + start_code_len))
}

fn next_annex_b_start_code(payload: &[u8], offset: usize) -> Option<usize> {
    next_annex_b_start_code_with_len(payload, offset).map(|(start, _)| start)
}

fn next_annex_b_start_code_with_len(payload: &[u8], offset: usize) -> Option<(usize, usize)> {
    let mut index = offset;
    while index + 3 < payload.len() {
        if payload[index..index + 4] == [0, 0, 0, 1] {
            return Some((index, 4));
        }
        if payload[index..index + 3] == [0, 0, 1] {
            return Some((index, 3));
        }
        index += 1;
    }
    None
}
#[cfg(test)]
mod tests {
    use super::*;
    use std::io::Read;
    use std::net::TcpStream;
    use std::time::Duration;

    #[test]
    fn binary_output_exposes_loopback_endpoint_and_sender() {
        let output = BinaryMediaOutput::start().expect("binary output should start");
        assert!(output.endpoint().starts_with("tcp://127.0.0.1:"));
        output.sender().send(vec![1, 2, 3]);
    }

    #[test]
    fn binary_output_keeps_packets_until_client_connects() {
        let output = BinaryMediaOutput::start().expect("binary output should start");
        output.sender().send(test_packet(FLAG_KEYFRAME, b'i'));

        let address = output
            .endpoint()
            .strip_prefix("tcp://")
            .expect("endpoint should use tcp scheme");
        let mut client = TcpStream::connect(address).expect("client should connect");
        client
            .set_read_timeout(Some(Duration::from_secs(1)))
            .expect("read timeout should be set");

        let mut received = [0_u8; 45];
        client
            .read_exact(&mut received)
            .expect("queued packet should be delivered after connect");
        assert_eq!(&received[..4], b"RSM1");
    }

    fn test_packet(flags: u16, payload: u8) -> Vec<u8> {
        let payload = if flags & FLAG_KEYFRAME != 0 {
            let mut payload_data = Vec::new();
            if flags & FLAG_CONFIG != 0 {
                payload_data.extend_from_slice(&[0, 0, 0, 1, 7, b'c']);
                payload_data.extend_from_slice(&[0, 0, 0, 1, 8, b'c']);
            }
            payload_data.extend_from_slice(&[0, 0, 0, 1, 5]);
            payload_data.extend(std::iter::repeat(payload).take(REAL_IDR_MIN_BYTES));
            payload_data
        } else if flags & FLAG_CONFIG != 0 {
            vec![0, 0, 0, 1, 7, payload, 0, 0, 0, 1, 8, payload]
        } else {
            vec![payload]
        };
        MediaPacket::new_nal(flags, 1, 0, 333_333, 1920, 1080, payload)
            .encode()
            .expect("test packet should encode")
    }

    #[test]
    fn new_client_discards_stale_p_frames_until_keyframe() {
        let mut gate = InitialKeyframeGate::default();

        assert!(gate.accept(&test_packet(0, b'p')).is_empty());
        assert!(gate.accept(&test_packet(FLAG_CONFIG, b'c')).is_empty());

        let recovered = gate.accept(&test_packet(FLAG_KEYFRAME, b'i'));
        assert_eq!(recovered.len(), 2);
        let config = MediaPacket::decode(&recovered[0]).expect("config packet should decode");
        let keyframe = MediaPacket::decode(&recovered[1]).expect("keyframe packet should decode");
        assert_eq!(config.flags, FLAG_CONFIG);
        assert_eq!(config.payload, vec![0, 0, 0, 1, 7, b'c', 0, 0, 0, 1, 8, b'c']);
        assert!(keyframe.flags & FLAG_KEYFRAME != 0);
        assert_eq!(keyframe.payload[4], 5);
        assert_eq!(gate.accept(&test_packet(0, b'p')).len(), 1);
    }

    #[test]
    fn small_keyframe_is_accepted_without_size_heuristic() {
        let mut gate = InitialKeyframeGate::default();
        let packet = MediaPacket::new_nal(
            FLAG_KEYFRAME,
            1,
            0,
            333_333,
            1920,
            1080,
            vec![0, 0, 0, 1, 5, b'k'],
        )
.encode()
.expect("small keyframe should encode");

        let recovered = gate.accept(&packet);
        assert_eq!(recovered.len(), 1);
        let decoded = MediaPacket::decode(&recovered[0]).expect("keyframe should decode");
        assert!(decoded.flags & FLAG_KEYFRAME != 0);
    }

    #[test]
    fn keyframe_with_config_does_not_duplicate_old_configuration() {
        let mut gate = InitialKeyframeGate::default();

        assert!(gate.accept(&test_packet(FLAG_CONFIG, b'c')).is_empty());
        let recovered = gate.accept(&test_packet(FLAG_CONFIG | FLAG_KEYFRAME, b'i'));

        assert_eq!(recovered.len(), 1);
        let keyframe = MediaPacket::decode(&recovered[0]).expect("keyframe packet should decode");
        assert!(keyframe.flags & FLAG_KEYFRAME != 0);
        assert!(keyframe.flags & FLAG_CONFIG != 0);
    }
}
