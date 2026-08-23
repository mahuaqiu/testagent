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
import java.util.concurrent.ArrayBlockingQueue;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicLong;

public final class StreamBridge {
    private static final byte VERSION = 1;
    private static final byte READY = 1;
    private static final byte H264 = 2;
    private static final byte SIZE = 3;
    private static final byte ERROR = 4;
    private static final byte STATS = 5;
    private static final byte EOF = 6;
    private static final byte IMAGE = 7;

    private static final String TYPE_MOBILE = "mobile";
    private static final String TYPE_PC = "pc";
    private static final String CAPTURE_VIDEO = "video";
    private static final String CAPTURE_IMAGE = "image";
    private static final int FRAME_QUEUE_CAPACITY = 8;

    private final String serial;
    private final String deviceType;
    private final String captureMode;
    private final byte frameMessageType;
    private final HosRemoteDevice device;
    private final ProtocolWriter writer;
    private final ArrayBlockingQueue<byte[]> frameQueue =
            new ArrayBlockingQueue<>(FRAME_QUEUE_CAPACITY);
    private final AtomicLong h264Messages = new AtomicLong();
    private final AtomicLong h264Bytes = new AtomicLong();
    private final AtomicLong droppedFrames = new AtomicLong();
    private final CountDownLatch ready = new CountDownLatch(1);
    private volatile boolean running = true;
    private volatile long startedAt = System.nanoTime();
    private Thread outputThread;

    private StreamBridge(
            String serial,
            String deviceType,
            String captureMode,
            HosRemoteDevice device) {
        this.serial = serial;
        this.deviceType = deviceType;
        this.captureMode = captureMode;
        this.frameMessageType = CAPTURE_VIDEO.equals(captureMode) ? H264 : IMAGE;
        this.device = device;
        this.writer = new ProtocolWriter(System.out);
    }

    public static void main(String[] args) throws Exception {
        if (args.length < 1) {
            System.err.println("用法: StreamBridge <serial> [mobile|pc] [hdc] [ip] [port] [scale] [fps] [bitrate] [video|image]");
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
        int fps = args.length > 6 ? Integer.parseInt(args[6]) : 30;
        int bitrate = args.length > 7 ? Integer.parseInt(args[7]) : 4_000_000;
        String captureMode = args.length > 8 ? args[8].toLowerCase() : CAPTURE_VIDEO;
        if (!CAPTURE_VIDEO.equals(captureMode) && !CAPTURE_IMAGE.equals(captureMode)) {
            throw new IllegalArgumentException("采集模式必须是 video 或 image: " + captureMode);
        }

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
                captureMode,
                new HosRemoteDevice(config));
        bridge.run();
    }

    private void run() throws Exception {
        startedAt = System.nanoTime();
        startOutputThread();
        startInputThread();

        System.err.printf(
                "STREAM_START serial=%s device_type=%s capture_mode=%s%n",
                serial,
                deviceType,
                captureMode);
        try {
            ScreenCapCallback callback = new ScreenCapCallback() {
                @Override
                public void onData(ByteBuffer buffer) {
                    byte[] payload = copyRemaining(buffer);
                    if (payload.length == 0) {
                        return;
                    }
                    h264Messages.incrementAndGet();
                    h264Bytes.addAndGet(payload.length);
                    if (!frameQueue.offer(payload)) {
                        frameQueue.poll();
                        droppedFrames.incrementAndGet();
                        frameQueue.offer(payload);
                    }
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
                    if (CAPTURE_VIDEO.equals(captureMode)) {
                        requestIdr("SDK_READY");
                    }
                    sendText(READY, "serial=" + serial + " device_type=" + deviceType
                            + " capture_mode=" + captureMode + " ready_ms=" + readyMs);
                    ready.countDown();
                }
            };
            if (CAPTURE_VIDEO.equals(captureMode)) {
                device.startCaptureScreen(callback);
            } else {
                device.startImageScreenCapture(callback);
            }

            if (!ready.await(30, TimeUnit.SECONDS)) {
                reportError("READY_TIMEOUT serial=" + serial);
                return;
            }

            long lastStatsAt = System.nanoTime();
            while (running) {
                Thread.sleep(200);
                if (elapsedMsSince(lastStatsAt) >= 5000) {
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

    private void startOutputThread() {
        outputThread = new Thread(() -> {
            try {
                while (running || !frameQueue.isEmpty()) {
                    byte[] frame = frameQueue.poll(200, TimeUnit.MILLISECONDS);
                    if (frame != null) {
                        writer.write(frameMessageType, frame);
                    }
                }
            } catch (InterruptedException interrupted) {
                Thread.currentThread().interrupt();
            } catch (IOException ioException) {
                System.err.println("协议输出失败: " + safeMessage(ioException));
                running = false;
            }
        }, "hoscrcpy-poc-output");
        outputThread.setDaemon(true);
        outputThread.start();
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
                + " capture_mode=" + captureMode
                + " capture_messages=" + h264Messages.get()
                + " capture_bytes=" + h264Bytes.get()
                + " dropped_frames=" + droppedFrames.get()
                + " queue_size=" + frameQueue.size()
                + " elapsed_ms=" + elapsedMs();
        sendText(STATS, payload);
        System.err.println("STATS " + payload);
    }

    private void requestIdr(String source) {
        if (!CAPTURE_VIDEO.equals(captureMode)) {
            return;
        }
        try {
            device.requestIDRFrame();
            System.err.println("REQUEST_IDR source=" + source);
        } catch (Throwable throwable) {
            reportError("REQUEST_IDR_FAILED source=" + source + " error=" + safeMessage(throwable));
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
        try {
            if (CAPTURE_VIDEO.equals(captureMode)) {
                device.stopCaptureScreen();
            } else {
                device.stopImageScreenCapture();
            }
        } catch (Throwable throwable) {
            System.err.println("停止采集失败: " + safeMessage(throwable));
        }
        if (outputThread != null) {
            try {
                outputThread.join(2000);
            } catch (InterruptedException interrupted) {
                Thread.currentThread().interrupt();
            }
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
