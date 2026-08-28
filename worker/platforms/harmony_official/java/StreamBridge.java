// 鸿蒙官方 HOScrcpy Java SDK 的独立 POC 桥接程序。
// stdout 只输出 HOS1 二进制协议，日志统一输出 stderr。

import com.huawei.hosscrcpy.api.HosRemoteConfig;
import com.huawei.hosscrcpy.api.HosRemoteDevice;
import com.huawei.hosscrcpy.api.ScreenCapCallback;

import java.io.BufferedReader;
import java.io.IOException;
import java.io.InputStreamReader;
import java.io.OutputStream;
import java.nio.ByteBuffer;
import java.nio.charset.StandardCharsets;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicLong;

public final class StreamBridge {
    private static final byte VERSION = 1;
    private static final byte READY = 1;
    private static final byte H264 = 2;
    private static final byte ERROR = 4;
    private static final byte STATS = 5;
    private static final byte EOF = 6;

    private static final String TYPE_MOBILE = "mobile";
    private static final String TYPE_PC = "pc";
    private static final long STATS_INTERVAL_MS = 60_000L;
    private static final long WRITE_BLOCK_THRESHOLD_MS = 100L;

    private final String serial;
    private final String deviceType;
    private final HosRemoteDevice device;
    private final ProtocolWriter writer;
    private final AtomicLong h264Messages = new AtomicLong();
    private final AtomicLong h264Bytes = new AtomicLong();
    private final AtomicLong h264WrittenMessages = new AtomicLong();
    private final AtomicLong h264WriteBlockCount = new AtomicLong();
    private final AtomicLong h264WriteBlockMs = new AtomicLong();
    private final AtomicLong h264MaxWriteMs = new AtomicLong();
    private final CountDownLatch ready = new CountDownLatch(1);
    private volatile boolean running = true;
    private volatile long startedAt = System.nanoTime();
    private volatile boolean h264WriteInProgress;
    private volatile long h264WriteStartedAtNanos;
    private volatile long h264LastWriteMs;
    private Thread backpressureMonitorThread;

    private StreamBridge(
            String serial,
            String deviceType,
            HosRemoteDevice device) {
        this.serial = serial;
        this.deviceType = deviceType;
        this.device = device;
        this.writer = new ProtocolWriter(System.out);
    }

    public static void main(String[] args) throws Exception {
        if (args.length < 1) {
            System.err.println("用法: StreamBridge <serial> [mobile|pc] [hdc] [ip] [port] [scale] [fps] [bitrate]");
            System.exit(2);
        }

        String serial = args[0];
        String deviceType = args.length > 1 ? args[1].toLowerCase() : TYPE_MOBILE;
        if (!TYPE_MOBILE.equals(deviceType) && !TYPE_PC.equals(deviceType)) {
            throw new IllegalArgumentException("设备类型必须是 mobile 或 pc: " + deviceType);
        }
        String hdcPath = args.length > 2 ? args[2] : "hdc";
        String ip = args.length > 3 ? args[3] : "127.0.0.1";
        int port = args.length > 4 ? Integer.parseInt(args[4]) : 8710;
        int scale = args.length > 5 ? Integer.parseInt(args[5]) : 720;
        int fps = args.length > 6 ? Integer.parseInt(args[6]) : 10;
        int bitrate = args.length > 7 ? Integer.parseInt(args[7]) : 4_000_000;

        HosRemoteConfig config = new HosRemoteConfig(serial);
        config.setIp(ip);
        config.setHdcPath(hdcPath);
        config.setPort(port);
        config.setImageScaleSize(scale);
        config.setFrameRate(fps);
        config.setBitRate(bitrate);

        StreamBridge bridge = new StreamBridge(
                serial,
                deviceType,
                new HosRemoteDevice(config));
        bridge.run();
    }

    private void run() throws Exception {
        startedAt = System.nanoTime();
        startInputThread();
        startBackpressureMonitor();

        System.err.printf(
                "STREAM_START serial=%s device_type=%s capture_mode=video%n",
                serial,
                deviceType);
        try {
            ScreenCapCallback callback = new ScreenCapCallback() {
                @Override
                public void onData(ByteBuffer buffer) {
                    byte[] payload = copyRemaining(buffer);
                    if (payload.length == 0) {
                        return;
                    }
                    writeH264(payload);
                }

                @Override
                public void onException(Throwable throwable) {
                    reportError("H264_EXCEPTION " + safeMessage(throwable));
                    ready.countDown();
                    running = false;
                }

                @Override
                public void onReady() {
                    long readyMs = elapsedMs();
                    System.err.printf("SDK_READY serial=%s elapsed_ms=%d%n", serial, readyMs);
                    sendText(READY, "serial=" + serial + " device_type=" + deviceType
                            + " capture_mode=video ready_ms=" + readyMs);
                    ready.countDown();
                }
            };
            device.startCaptureScreen(callback);

            if (!ready.await(30, TimeUnit.SECONDS)) {
                reportError("READY_TIMEOUT serial=" + serial);
                return;
            }

            long lastStatsAt = System.nanoTime();
            while (running) {
                Thread.sleep(200);
                if (elapsedMsSince(lastStatsAt) >= STATS_INTERVAL_MS) {
                    lastStatsAt = System.nanoTime();
                    sendStats();
                }
            }
        } catch (Throwable throwable) {
            reportError("CAPTURE_FAILED " + safeMessage(throwable));
        } finally {
            shutdown();
        }
    }

    private void startBackpressureMonitor() {
        backpressureMonitorThread = new Thread(() -> {
            while (running) {
                try {
                    Thread.sleep(STATS_INTERVAL_MS);
                } catch (InterruptedException interrupted) {
                    Thread.currentThread().interrupt();
                    return;
                }
                if (running) {
                    logBackpressure("periodic");
                }
            }
        }, "hoscrcpy-poc-backpressure-monitor");
        backpressureMonitorThread.setDaemon(true);
        backpressureMonitorThread.start();
    }

    private synchronized void writeH264(byte[] payload) {
        // SDK 回调可能来自不同线程；串行写出可保证每个 access unit 的协议帧
        // 不交错，同时保留完整性优先的阻塞语义，不在 Java 层主动丢帧。
        h264Messages.incrementAndGet();
        h264Bytes.addAndGet(payload.length);
        long writeStartedAt = System.nanoTime();
        h264WriteInProgress = true;
        h264WriteStartedAtNanos = writeStartedAt;
        try {
            writer.write(H264, payload);
            h264WrittenMessages.incrementAndGet();
        } catch (IOException ioException) {
            // stdout 已断开时无法再发送 ERROR，结束会话并交给上层重建。
            System.err.println("H264 输出失败: " + safeMessage(ioException));
            running = false;
        } finally {
            long writeMs = TimeUnit.NANOSECONDS.toMillis(
                    System.nanoTime() - writeStartedAt);
            h264LastWriteMs = writeMs;
            updateMax(h264MaxWriteMs, writeMs);
            if (writeMs >= WRITE_BLOCK_THRESHOLD_MS) {
                h264WriteBlockCount.incrementAndGet();
                h264WriteBlockMs.addAndGet(writeMs);
            }
            h264WriteInProgress = false;
        }
    }

    private void startInputThread() {
        Thread inputThread = new Thread(() -> {
            try (BufferedReader reader = new BufferedReader(
                    new InputStreamReader(System.in, StandardCharsets.UTF_8))) {
                String line;
                while (running && (line = reader.readLine()) != null) {
                    if (!handleCommand(line.trim())) {
                        reportError("INVALID_COMMAND " + line.trim());
                    }
                }
            } catch (IOException ioException) {
                reportError("STDIN_FAILED " + safeMessage(ioException));
            } finally {
                running = false;
            }
        }, "hoscrcpy-poc-input");
        inputThread.setDaemon(true);
        inputThread.start();
    }

    private boolean handleCommand(String line) {
        if (line.isEmpty()) {
            return true;
        }
        String[] parts = line.split("\\s+");
        try {
            switch (parts[0].toUpperCase()) {
                case "STOP":
                    running = false;
                    return parts.length == 1;
                case "REQUEST_IDR":
                    requestIdr("COMMAND");
                    return parts.length == 1;
                case "WAKE_STREAM":
                    wakeStream();
                    return parts.length == 1;
                case "TOUCH_DOWN":
                    requireMobile();
                    device.onTouchDown(parseInt(parts, 1), parseInt(parts, 2));
                    return parts.length == 3;
                case "TOUCH_MOVE":
                    requireMobile();
                    device.onTouchMove(parseInt(parts, 1), parseInt(parts, 2));
                    return parts.length == 3;
                case "TOUCH_UP":
                    requireMobile();
                    device.onTouchUp(parseInt(parts, 1), parseInt(parts, 2));
                    return parts.length == 3;
                case "MOUSE_DOWN":
                    requirePc();
                    device.onMouseDown(mouseType(parts, 1), parseInt(parts, 2), parseInt(parts, 3));
                    return parts.length == 4;
                case "MOUSE_MOVE":
                    requirePc();
                    device.onMouseMove(mouseType(parts, 1), parseInt(parts, 2), parseInt(parts, 3));
                    return parts.length == 4;
                case "MOUSE_UP":
                    requirePc();
                    device.onMouseUp(mouseType(parts, 1), parseInt(parts, 2), parseInt(parts, 3));
                    return parts.length == 4;
                case "WHEEL_UP":
                    requirePc();
                    device.onMouseWheelUp(parseInt(parts, 1), parseInt(parts, 2));
                    return parts.length == 3;
                case "WHEEL_DOWN":
                    requirePc();
                    device.onMouseWheelDown(parseInt(parts, 1), parseInt(parts, 2));
                    return parts.length == 3;
                case "WHEEL_STOP":
                    requirePc();
                    device.onMouseWheelStop(parseInt(parts, 1), parseInt(parts, 2));
                    return parts.length == 3;
                default:
                    return false;
            }
        } catch (Throwable throwable) {
            reportError("COMMAND_FAILED command=" + parts[0] + " error=" + safeMessage(throwable));
            return true;
        }
    }

    private void requireMobile() {
        if (!TYPE_MOBILE.equals(deviceType)) {
            throw new IllegalStateException("当前会话不是移动端");
        }
    }

    private void requirePc() {
        if (!TYPE_PC.equals(deviceType)) {
            throw new IllegalStateException("当前会话不是鸿蒙 PC");
        }
    }

    private static String mouseType(String[] parts, int index) {
        String value = parts[index].toUpperCase();
        if ("NONE".equals(value) || "NULL".equals(value)) {
            return null;
        }
        if ("LEFT".equals(value)) {
            return HosRemoteDevice.MOUSE_LEFT;
        }
        if ("MIDDLE".equals(value)) {
            return HosRemoteDevice.MOUSE_MIDDLE;
        }
        if ("RIGHT".equals(value)) {
            return HosRemoteDevice.MOUSE_RIGHT;
        }
        throw new IllegalArgumentException("未知鼠标类型: " + parts[index]);
    }

    private static int parseInt(String[] parts, int index) {
        if (index >= parts.length) {
            throw new IllegalArgumentException("缺少坐标参数");
        }
        return Integer.parseInt(parts[index]);
    }

    private static byte[] copyRemaining(ByteBuffer buffer) {
        ByteBuffer duplicate = buffer.duplicate();
        byte[] result = new byte[duplicate.remaining()];
        duplicate.get(result);
        return result;
    }

    private void sendStats() {
        String payload = "serial=" + serial
                + " capture_mode=video"
                + " output_mode=direct"
                + " capture_messages=" + h264Messages.get()
                + " capture_bytes=" + h264Bytes.get()
                + " written_messages=" + h264WrittenMessages.get()
                + " dropped_frames=0"
                + " queue_size=0"
                + " write_block_count=" + h264WriteBlockCount.get()
                + " write_block_ms=" + h264WriteBlockMs.get()
                + " last_write_ms=" + h264LastWriteMs
                + " max_write_ms=" + h264MaxWriteMs.get()
                + " write_in_progress=" + h264WriteInProgress
                + " active_write_ms=" + activeWriteMs()
                + " elapsed_ms=" + elapsedMs();
        sendText(STATS, payload);
    }

    private void logBackpressure(String reason) {
        System.err.println(
                "STREAM_BACKPRESSURE serial=" + serial
                        + " reason=" + reason
                        + " output_mode=direct"
                        + " capture_messages=" + h264Messages.get()
                        + " written_messages=" + h264WrittenMessages.get()
                        + " dropped_frames=0"
                        + " queue_size=0"
                        + " write_block_count=" + h264WriteBlockCount.get()
                        + " write_block_ms=" + h264WriteBlockMs.get()
                        + " last_write_ms=" + h264LastWriteMs
                        + " max_write_ms=" + h264MaxWriteMs.get()
                        + " write_in_progress=" + h264WriteInProgress
                        + " active_write_ms=" + activeWriteMs());
    }

    private long activeWriteMs() {
        if (!h264WriteInProgress) {
            return 0;
        }
        return TimeUnit.NANOSECONDS.toMillis(
                System.nanoTime() - h264WriteStartedAtNanos);
    }

    private static void updateMax(AtomicLong target, long value) {
        long current;
        do {
            current = target.get();
            if (value <= current) {
                return;
            }
        } while (!target.compareAndSet(current, value));
    }

    private void requestIdr(String source) {
        try {
            device.requestIDRFrame();
            System.err.println("REQUEST_IDR source=" + source);
        } catch (Throwable throwable) {
            reportError("REQUEST_IDR_FAILED source=" + source + " error=" + safeMessage(throwable));
        }
    }

    private void wakeStream() {
        try {
            // 只产生很小的移动轨迹，不执行点击，不改变应用当前页面，也不显示轨迹。
            device.executeShellCommand("uinput -M -m 100 100 200 200", 2);
            System.err.println("WAKE_STREAM completed");
        } catch (Throwable throwable) {
            // 唤醒只是静止画面的优化，失败不应中断已经 READY 的官方会话。
            System.err.println("WAKE_STREAM_FAILED error=" + safeMessage(throwable));
        }
    }

    private void reportError(String message) {
        System.err.println(message);
        sendText(ERROR, message);
    }

    private void sendText(byte type, String text) {
        try {
            writer.write(type, text.getBytes(StandardCharsets.UTF_8));
        } catch (IOException ioException) {
            System.err.println("状态输出失败: " + safeMessage(ioException));
            running = false;
        }
    }

    private void shutdown() {
        running = false;
        if (backpressureMonitorThread != null) {
            backpressureMonitorThread.interrupt();
            try {
                backpressureMonitorThread.join(1000);
            } catch (InterruptedException interrupted) {
                Thread.currentThread().interrupt();
            }
        }
        try {
            device.stopCaptureScreen();
        } catch (Throwable throwable) {
            System.err.println("停止采集失败: " + safeMessage(throwable));
        }
        sendText(EOF, "serial=" + serial + " reason=stopped");
        System.err.println("STREAM_STOP serial=" + serial);
    }

    private long elapsedMs() {
        return TimeUnit.NANOSECONDS.toMillis(System.nanoTime() - startedAt);
    }

    private static long elapsedMsSince(long startedAtNanos) {
        return TimeUnit.NANOSECONDS.toMillis(System.nanoTime() - startedAtNanos);
    }

    private static String safeMessage(Throwable throwable) {
        if (throwable == null) {
            return "unknown";
        }
        String message = throwable.getMessage();
        return message == null || message.isEmpty() ? throwable.getClass().getName() : message;
    }

    private static final class ProtocolWriter {
        private static final byte[] MAGIC = new byte[] {'H', 'O', 'S', '1'};
        private final OutputStream output;

        private ProtocolWriter(OutputStream output) {
            this.output = output;
        }

        private synchronized void write(byte type, byte[] payload) throws IOException {
            output.write(MAGIC);
            output.write(VERSION);
            output.write(type);
            output.write((payload.length >>> 24) & 0xff);
            output.write((payload.length >>> 16) & 0xff);
            output.write((payload.length >>> 8) & 0xff);
            output.write(payload.length & 0xff);
            output.write(payload);
            output.flush();
        }
    }
}
