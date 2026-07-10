"""推流功能测试"""
import pytest
import asyncio
import struct
from unittest.mock import Mock, patch, AsyncMock


class TestPushFrameReader:
    """PushFrameReader 单元测试"""

    def test_init(self):
        """测试初始化"""
        from worker.screen.windows_sidecar import PushFrameReader, WindowsSidecarClient

        mock_client = Mock(spec=WindowsSidecarClient)
        mock_client.get_process.return_value = Mock(stderr=Mock(readline=Mock(return_value=b'')))

        reader = PushFrameReader(mock_client)
        assert reader._fps == 20
        assert reader._running == False

    def test_is_running(self):
        """测试运行状态检查"""
        from worker.screen.windows_sidecar import PushFrameReader, WindowsSidecarClient

        mock_client = Mock(spec=WindowsSidecarClient)
        mock_proc = Mock()
        mock_proc.stderr.readline = Mock(return_value=b'')
        mock_client.get_process.return_value = mock_proc
        mock_client.is_alive.return_value = True

        reader = PushFrameReader(mock_client)
        reader._running = True

        assert reader.is_running() == True

    def test_is_running_false_when_not_running(self):
        """测试运行状态检查 - 未运行"""
        from worker.screen.windows_sidecar import PushFrameReader, WindowsSidecarClient

        mock_client = Mock(spec=WindowsSidecarClient)
        mock_client.get_process.return_value = Mock()
        mock_client.is_alive.return_value = True

        reader = PushFrameReader(mock_client)
        reader._running = False

        assert reader.is_running() == False

    def test_handle_line_sps(self):
        """测试处理 SPS 帧"""
        from worker.screen.windows_sidecar import PushFrameReader, WindowsSidecarClient

        mock_client = Mock(spec=WindowsSidecarClient)
        mock_client.get_process.return_value = Mock(stderr=Mock())

        reader = PushFrameReader(mock_client)
        reader._frame_queue = asyncio.Queue()
        reader._running = True

        # 模拟接收 SPS 帧（前缀 0）
        test_b64 = "QUJDREVGR0hJSktMTU5PUFFSU1RVVldYWVowMTIzNDU="
        reader._handle_line(b'0' + test_b64.encode() + b'\n')

        # 验证队列中的数据
        frame_type, data = reader._frame_queue.get_nowait()
        assert frame_type == 'sps'

    def test_handle_line_pps(self):
        """测试处理 PPS 帧"""
        from worker.screen.windows_sidecar import PushFrameReader, WindowsSidecarClient

        mock_client = Mock(spec=WindowsSidecarClient)
        mock_client.get_process.return_value = Mock(stderr=Mock())

        reader = PushFrameReader(mock_client)
        reader._frame_queue = asyncio.Queue()
        reader._running = True

        # 模拟接收 PPS 帧（前缀 1）
        test_b64 = "QUJDREVGR0hJSktMTU5PUFFSU1RVVldYWVowMTIzNDU="
        reader._handle_line(b'1' + test_b64.encode() + b'\n')

        frame_type, data = reader._frame_queue.get_nowait()
        assert frame_type == 'pps'

    def test_handle_line_idr(self):
        """测试处理 IDR 帧"""
        from worker.screen.windows_sidecar import PushFrameReader, WindowsSidecarClient

        mock_client = Mock(spec=WindowsSidecarClient)
        mock_client.get_process.return_value = Mock(stderr=Mock())

        reader = PushFrameReader(mock_client)
        reader._frame_queue = asyncio.Queue()
        reader._running = True

        # 模拟接收 IDR 帧（前缀 2）
        test_b64 = "QUJDREVGR0hJSktMTU5PUFFSU1RVVldYWVowMTIzNDU="
        reader._handle_line(b'2' + test_b64.encode() + b'\n')

        frame_type, data = reader._frame_queue.get_nowait()
        assert frame_type == 'idr'

    def test_handle_line_p_frame(self):
        """测试处理 P 帧"""
        from worker.screen.windows_sidecar import PushFrameReader, WindowsSidecarClient

        mock_client = Mock(spec=WindowsSidecarClient)
        mock_client.get_process.return_value = Mock(stderr=Mock())

        reader = PushFrameReader(mock_client)
        reader._frame_queue = asyncio.Queue()
        reader._running = True

        # 模拟接收 P 帧（前缀 3）
        test_b64 = "QUJDREVGR0hJSktMTU5PUFFSU1RVVldYWVowMTIzNDU="
        reader._handle_line(b'3' + test_b64.encode() + b'\n')

        frame_type, data = reader._frame_queue.get_nowait()
        assert frame_type == 'p'


class TestMediaPacketReader:
    """RSM1 二进制媒体 packet reader 测试。"""

    @staticmethod
    def _packet(sequence: int, payload: bytes) -> bytes:
        return struct.pack(
            "<4sBBH Q q q I I I",
            b"RSM1",
            1,
            1,
            1 if sequence == 2 else 0,
            sequence,
            sequence * 333333,
            333333,
            1920,
            1080,
            len(payload),
        ) + payload

    def test_reader_handles_half_packet_and_concatenated_packets(self):
        from worker.screen.windows_sidecar import MediaPacketReader

        first = self._packet(1, b"first")
        second = self._packet(2, b"second")

        class FakeSocket:
            def __init__(self):
                self.chunks = [first[:9], first[9:] + second]

            def recv(self, _size):
                return self.chunks.pop(0) if self.chunks else b""

            def close(self):
                pass

        reader = MediaPacketReader("tcp://127.0.0.1:1234", sock=FakeSocket())
        assert reader.read_packet() == {
            "sequence": 1,
            "pts_100ns": 333333,
            "duration_100ns": 333333,
            "width": 1920,
            "height": 1080,
            "flags": 0,
            "message_type": 1,
            "payload": b"first",
        }
        assert reader.read_packet()["sequence"] == 2

    def test_reader_rejects_invalid_magic(self):
        from worker.screen.windows_sidecar import MediaPacketReader

        class FakeSocket:
            def recv(self, _size):
                return b"BAD!"

            def close(self):
                pass

        reader = MediaPacketReader("tcp://127.0.0.1:1234", sock=FakeSocket())
        with pytest.raises(ValueError, match="magic"):
            reader.read_packet()

    def test_reader_preserves_media_timing_metadata(self):
        from worker.screen.windows_sidecar import MediaPacketReader

        packet = self._packet(7, b"encoded")

        class FakeSocket:
            def recv(self, _size):
                nonlocal packet
                chunk, packet = packet, b""
                return chunk

            def close(self):
                pass

        reader = MediaPacketReader("tcp://127.0.0.1:1234", sock=FakeSocket())
        result = reader.read_packet()

        assert result["sequence"] == 7
        assert result["pts_100ns"] == 7 * 333333
        assert result["duration_100ns"] == 333333
        assert result["width"] == 1920
        assert result["height"] == 1080
        assert result["payload"] == b"encoded"

    @pytest.mark.parametrize(
        ("version", "payload_length"),
        [(2, 1), (1, 64 * 1024 * 1024 + 1)],
    )
    def test_reader_rejects_unsupported_version_or_oversized_payload(
        self, version, payload_length
    ):
        from worker.screen.windows_sidecar import MediaPacketReader

        packet = struct.pack(
            "<4sBBH Q q q I I I",
            b"RSM1",
            version,
            1,
            0,
            1,
            0,
            333333,
            1920,
            1080,
            payload_length,
        )

        class FakeSocket:
            def recv(self, _size):
                nonlocal packet
                chunk, packet = packet, b""
                return chunk

            def close(self):
                pass

        reader = MediaPacketReader("tcp://127.0.0.1:1234", sock=FakeSocket())
        with pytest.raises(ValueError):
            reader.read_packet()

    def test_media_packet_maps_to_websocket_frame_prefix(self):
        from worker.screen.windows_sidecar import media_packet_to_websocket_frame

        assert media_packet_to_websocket_frame({"flags": 0x02, "payload": b"config"}) == b"\x01config"
        assert media_packet_to_websocket_frame({"flags": 0x01, "payload": b"idr"}) == b"\x02idr"
        assert media_packet_to_websocket_frame({"flags": 0, "payload": b"p"}) == b"\x03p"

    def test_media_packet_with_keyframe_and_config_uses_keyframe_prefix(self):
        from worker.screen.windows_sidecar import media_packet_to_websocket_frame

        assert media_packet_to_websocket_frame({"flags": 0x03, "payload": b"idr"}) == b"\x02idr"

    def test_handle_command_fps(self):
        """测试处理 FPS 控制命令"""
        from worker.screen.windows_sidecar import PushFrameReader, WindowsSidecarClient

        mock_client = Mock(spec=WindowsSidecarClient)
        mock_client.get_process.return_value = Mock()

        with patch('worker.screen.windows_sidecar.logger') as mock_logger:
            reader = PushFrameReader(mock_client)
            reader._handle_command(b"FPS=30")

            # 验证日志记录
            mock_logger.info.assert_called_once()


class TestScreenStreamIntegration:
    """screen_stream 集成测试（需要实际环境）"""

    @pytest.mark.skip(reason="需要实际 Windows 环境和 sidecar 进程")
    async def test_h264_push_stream(self):
        """测试 H.264 推流模式"""
        # 此测试需要实际环境，运行方式：
        # pytest tests/screen/test_push_streaming.py -v -k "test_h264"
        pass


class TestWindowsSidecarStreamer:
    """Windows sidecar 二进制推流的启动和降级测试。"""

    def test_binary_media_reader_reconnects_after_disconnect(self):
        from worker.screen.windows_sidecar import WindowsSidecarStreamer

        client = Mock()
        reader = Mock()
        reader.read_packet.side_effect = [
            EOFError("二进制媒体通道已断开"),
            {"sequence": 2, "payload": b"recovered"},
        ]
        streamer = WindowsSidecarStreamer(
            client,
            session_id="windows/1/1",
            codec="h264",
            fps=10,
            binary=True,
        )
        streamer._running = True
        streamer._media_reader = reader

        first = asyncio.run(streamer.get_media_packet_async())
        second = asyncio.run(streamer.get_media_packet_async())

        assert first is None
        assert second == {"sequence": 2, "payload": b"recovered"}
        reader.reconnect.assert_called_once_with()

    def test_binary_stream_start_passes_binary_and_endpoint_to_reader(self):
        from worker.screen.windows_sidecar import WindowsSidecarStreamer

        client = Mock()
        client.request.return_value = {
            "binary_media_endpoint": "tcp://127.0.0.1:1234",
        }
        fake_reader = Mock()
        with patch("worker.screen.windows_sidecar.MediaPacketReader", return_value=fake_reader):
            streamer = WindowsSidecarStreamer(
                client,
                session_id="windows/1/1",
                codec="h264",
                fps=10,
                binary=True,
            )
            streamer.start(codec="h264")

        request_params = client.request.call_args.args[1]
        assert request_params["binary"] is True
        assert streamer.uses_binary_media is True
        assert streamer.is_running() is True

    def test_binary_stream_start_failure_falls_back_to_jpeg(self):
        from worker.screen.windows_sidecar import WindowsSidecarStreamer

        client = Mock()
        client.request.side_effect = [
            {},
            {},
        ]
        streamer = WindowsSidecarStreamer(
            client,
            session_id="windows/1/1",
            codec="h264",
            fps=10,
            binary=True,
        )

        streamer.start(codec="h264")

        assert streamer.codec == "jpeg"
        assert streamer.is_running() is True
        assert streamer.uses_binary_media is False
        assert [call.args[0] for call in client.request.call_args_list] == [
            "stream_start",
            "stream_stop",
        ]
