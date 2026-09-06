"""Spike：直接往 StreamBridge stdin 灌 MOUSE_DOWN/MOVE/UP 序列，验证鸿蒙 PC 拖图标。

手动真机诊断工具（pytest 不会收集）：在连接鸿蒙 PC 的机器上、worker 根目录下运行，
用于排查官方输入链路（长按起拖/滚轮 stop 等实时节奏问题）。

在连接鸿蒙 PC 的机器上运行（worker 根目录下）：
    python tests/spike_drag.py                          # 默认拖 (150,939)->(1250,939)
    python tests/spike_drag.py --x 150 --y 939 --end-x 600 --end-y 400 --duration 1500
"""
import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from worker.platforms.harmony_official.protocol import (  # noqa: E402
    command_mouse_down, command_mouse_move, command_mouse_up,
)
from worker.platforms.harmony_official.session import HarmonyOfficialSession  # noqa: E402

SETTINGS = {
    "java_path": "java",
    "jar_path": "tools/harmony/hosScrcpy-1.0.15-beta.jar",
    "bridge_class_path": "tools/harmony/bridge",
    "temp_dir": "temp/hos_bridge",
    "image_scale_size": 720, "frame_rate": 10, "bit_rate": 4_000_000,
    "startup_timeout_seconds": 35, "frame_queue_capacity": 2,
    "input_ready_delay_seconds": 1.5, "backpressure_log_interval_seconds": 600,
}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--serial", default="3QC0124A10000066")
    ap.add_argument("--hdc", default="tools/hdc/hdc.exe")
    ap.add_argument("--x", type=int, default=150); ap.add_argument("--y", type=int, default=939)
    ap.add_argument("--end-x", type=int, default=1250); ap.add_argument("--end-y", type=int, default=939)
    ap.add_argument("--duration", type=int, default=2000, help="按下后移动总时长 ms")
    ap.add_argument("--hold", type=int, default=300, help="down 后停留 ms（验证长按起拖）")
    ap.add_argument("--steps", type=int, default=30)
    args = ap.parse_args()

    session = HarmonyOfficialSession(
        serial=args.serial, device_type="pc", hdc_path=args.hdc, settings=dict(SETTINGS))
    t0 = time.perf_counter()
    session.start()
    print(f"[spike] 会话就绪 {time.perf_counter() - t0:.2f}s，输入延迟窗口后开始拖拽")

    def send(label: str, cmd: bytes) -> None:
        tick = time.perf_counter()
        session._send(cmd)
        print(f"[spike] +{time.perf_counter() - t0:7.3f}s {label:24s} send={(time.perf_counter() - tick) * 1000:.2f}ms")

    try:
        send("MOUSE_DOWN LEFT", command_mouse_down("LEFT", args.x, args.y))
        if args.hold:
            time.sleep(args.hold / 1000)
        for i in range(1, args.steps + 1):
            r = i / args.steps
            x = round(args.x + (args.end_x - args.x) * r)
            y = round(args.y + (args.end_y - args.y) * r)
            send(f"MOUSE_MOVE LEFT ({x},{y})", command_mouse_move("LEFT", x, y))
            time.sleep(args.duration / args.steps / 1000)
        time.sleep(0.05)
        send("MOUSE_UP LEFT", command_mouse_up("LEFT", args.end_x, args.end_y))
        print("[spike] 序列完成，观察桌面图标是否被拖动；5s 后退出")
        time.sleep(5)
    finally:
        session.stop()


if __name__ == "__main__":
    main()
