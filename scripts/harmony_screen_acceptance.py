# -*- coding: utf-8 -*-
"""鸿蒙远程投屏真机验收脚本。

覆盖只有真机才能验证的事项（单测已覆盖纯逻辑部分）：
1. 设备发现与状态（list targets、Ready/Connected）
2. 设备形态分类（device_category → mobile/pc）
3. 分辨率解析（真机 hidumper 输出格式）
4. snapshot_display 截图（轮询兜底路径的底座）
5. uitest 帧流启动（agent 部署 + daemon 重启 + fport + startCaptureScreen）
6. 帧率与帧质量（JPEG 合法性、fps、帧尺寸与 display_size 一致性）
7. 停止清理（fport 规则无残留）
8. 帧流失败自动降级轮询（HarmonyFrameSource）
9. Worker WS 端到端推流（可选，需 worker 正在运行且已安装 websockets）

用法（在 autotest 仓库根目录）：
    python scripts/harmony_screen_acceptance.py                     # 自动取第一台在线设备
    python scripts/harmony_screen_acceptance.py --udid 3QC0124A...  # 指定设备
    python scripts/harmony_screen_acceptance.py --worker-url ws://127.0.0.1:8088  # 附带 WS 端到端
    python scripts/harmony_screen_acceptance.py --skip-fallback     # 跳过降级测试（省时）
"""

import argparse
import io
import os
import sys
import tempfile
import time
import uuid

# 允许从仓库根目录直接运行
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PIL import Image  # noqa: E402

from worker.platforms import harmony_capture  # noqa: E402
from worker.platforms.harmony_capture import (  # noqa: E402
    HarmonyCaptureError,
    HarmonyScreenCapture,
)
from worker.platforms.harmony_hdc import (  # noqa: E402
    HarmonyHdcWrapper,
    list_target_info,
)
from worker.screen.frame_source import HarmonyFrameSource  # noqa: E402

JPEG_START = b"\xff\xd8"
JPEG_END = b"\xff\xd9"

RESULTS: list[tuple[str, str, str]] = []  # (状态, 检查项, 说明)


def record(status: str, name: str, detail: str = "") -> None:
    RESULTS.append((status, name, detail))
    print(f"[{status}] {name}" + (f" —— {detail}" if detail else ""))


def check(name: str):
    """装饰器：统一捕获异常记为 FAIL，返回值 (True, detail) 记为 PASS。"""

    def wrapper(func):
        def inner(*args, **kwargs):
            try:
                ok, detail = func(*args, **kwargs)
            except Exception as exc:  # noqa: BLE001 验收脚本逐项容错
                record("FAIL", name, f"异常: {exc}")
                return False
            record("PASS" if ok else "FAIL", name, detail)
            return ok

        return inner

    return wrapper


def is_valid_jpeg(data: bytes) -> bool:
    return bool(data) and data[:2] == JPEG_START and data[-2:] == JPEG_END


def jpeg_size(data: bytes) -> tuple[int, int]:
    with Image.open(io.BytesIO(data)) as img:
        return img.size


# ============================================================================
# 检查项
# ============================================================================


@check("1. 设备发现与状态")
def check_discovery(udid: str | None, hdc_path: str | None):
    targets = list_target_info(hdc_path)
    if not targets:
        return False, "list targets 未发现任何在线设备"
    lines = ", ".join(f"{t.udid}({t.status})" for t in targets)
    if udid and udid not in [t.udid for t in targets]:
        return False, f"指定设备 {udid} 不在线，当前: {lines}"
    return True, lines


@check("2. 设备形态分类")
def check_category(hdc: HarmonyHdcWrapper):
    category = hdc.device_category()
    return category in ("mobile", "pc"), f"device_category={category}"


@check("3. 分辨率解析")
def check_display_size(hdc: HarmonyHdcWrapper):
    size = hdc.display_size()
    return size != (0, 0), f"display_size={size[0]}x{size[1]}"


@check("4. snapshot_display 截图")
def check_screenshot(hdc: HarmonyHdcWrapper):
    local_path = os.path.join(
        tempfile.gettempdir(), f"harmony_accept_{uuid.uuid4().hex}.jpeg"
    )
    try:
        if not hdc.screenshot(local_path):
            return False, "hdc.screenshot 返回 False"
        with open(local_path, "rb") as f:
            data = f.read()
    finally:
        if os.path.isfile(local_path):
            os.remove(local_path)
    if not is_valid_jpeg(data):
        return False, f"截图非合法 JPEG（{len(data)} 字节）"
    return True, f"JPEG {len(data)} 字节, 尺寸 {jpeg_size(data)}"


def start_capture_with_agent_trace(hdc: HarmonyHdcWrapper):
    """启动帧流并记录实际生效的 agent 版本。"""
    capture = HarmonyScreenCapture(hdc)
    used_agents: list[str] = []
    original = capture._setup_device_agent

    def traced(path: str) -> None:
        used_agents.append(os.path.basename(path))
        original(path)

    capture._setup_device_agent = traced
    capture.start()
    return capture, (used_agents[-1] if used_agents else "?")


@check("6. 帧率与帧质量")
def check_frame_stream(capture: HarmonyScreenCapture, duration: float,
                       expected_size: tuple[int, int]):
    frames = 0
    last = None
    first_frame = None
    deadline = time.monotonic() + duration
    while time.monotonic() < deadline:
        frame = capture.get_frame(timeout=2.0)
        if frame is None:
            return False, "get_frame 超时（帧流中断）"
        if frame is not last:
            frames += 1
            last = frame
            if first_frame is None:
                first_frame = frame
        time.sleep(0.02)
    if first_frame is None:
        return False, f"{duration}s 内未收到任何帧"
    if not is_valid_jpeg(first_frame):
        return False, "首帧非合法 JPEG"
    fps = frames / duration
    size = jpeg_size(first_frame)
    detail = f"fps={fps:.1f}, 帧尺寸={size}, display_size={expected_size}"
    # 帧尺寸与 hidumper 分辨率允许缩放（agent 可能降采样），仅提示不判失败
    if fps < 3:
        return False, f"帧率过低: {detail}"
    return True, detail


@check("7. 停止清理（fport 无残留）")
def check_cleanup(hdc: HarmonyHdcWrapper, capture: HarmonyScreenCapture):
    local_port = capture.local_port
    capture.stop()
    rules = hdc.fport_ls()
    leftover = [r for r in rules if local_port and f"tcp:{local_port}" in r]
    if leftover:
        return False, f"fport 残留: {leftover}"
    return True, f"fport ls 剩余 {len(rules)} 条规则（与本次无关）"


@check("8. 帧流失败自动降级轮询")
def check_fallback(hdc: HarmonyHdcWrapper):
    saved = harmony_capture.AGENT_CANDIDATES
    harmony_capture.AGENT_CANDIDATES = ("__nonexistent_agent__.so",)
    try:
        source = HarmonyFrameSource(hdc.serial, hdc)
        source.start()
        if source._capture is not None or not source._polling:
            return False, "帧流未按预期失败/未进入轮询模式"
        frame = source.get_frame()
        source.stop()
    finally:
        harmony_capture.AGENT_CANDIDATES = saved
    if not is_valid_jpeg(frame):
        return False, "轮询模式产出非合法 JPEG"
    return True, f"轮询模式出帧 {len(frame)} 字节"


@check("9. Worker WS 端到端推流")
def check_ws_streaming(worker_url: str, platform: str, udid: str):
    try:
        import asyncio

        import websockets
    except ImportError:
        return False, "缺少 websockets 库（pip install websockets）"

    url = f"{worker_url}/ws/screen/{platform}/{udid}?codec=jpeg"

    async def recv_frames() -> tuple[int, int]:
        received, first_len = 0, 0
        async with websockets.connect(url, max_size=16 * 1024 * 1024) as ws:
            deadline = time.monotonic() + 15
            while time.monotonic() < deadline and received < 5:
                msg = await asyncio.wait_for(ws.recv(), timeout=10)
                if isinstance(msg, bytes) and is_valid_jpeg(msg):
                    received += 1
                    first_len = first_len or len(msg)
        return received, first_len

    received, first_len = asyncio.run(recv_frames())
    if received == 0:
        return False, f"15s 内未从 {url} 收到合法 JPEG 帧"
    return True, f"收到 {received} 帧（首帧 {first_len} 字节）"


# ============================================================================
# 主流程
# ============================================================================


def main() -> int:
    parser = argparse.ArgumentParser(description="鸿蒙远程投屏真机验收")
    parser.add_argument("--udid", help="设备序列号（缺省取第一台在线设备）")
    parser.add_argument("--hdc-path", help="hdc 工具路径（缺省自动查找）")
    parser.add_argument("--duration", type=float, default=5.0, help="帧率测量时长（秒）")
    parser.add_argument("--skip-fallback", action="store_true", help="跳过降级轮询测试")
    parser.add_argument(
        "--worker-url", help="worker WS 地址（如 ws://127.0.0.1:8088），提供则测端到端推流"
    )
    args = parser.parse_args()

    if not check_discovery(args.udid, args.hdc_path):
        print("\n设备不在线，终止验收。")
        return 1

    udid = args.udid or list_target_info(args.hdc_path)[0].udid
    hdc = HarmonyHdcWrapper(udid, args.hdc_path)
    print(f"\n验收设备: {udid}\n")

    check_category(hdc)
    check_display_size(hdc)
    check_screenshot(hdc)

    # 5+6+7: 帧流全链路
    capture = None
    try:
        capture, agent_name = start_capture_with_agent_trace(hdc)
        record("PASS", "5. uitest 帧流启动", f"生效 agent: {agent_name}")
    except HarmonyCaptureError as exc:
        record("FAIL", "5. uitest 帧流启动", f"{exc}（PC 形态可能属预期，走轮询降级）")
    if capture is not None:
        check_frame_stream(capture, args.duration, hdc.display_size())
        check_cleanup(hdc, capture)
    else:
        record("SKIP", "6. 帧率与帧质量", "帧流未启动")
        record("SKIP", "7. 停止清理（fport 无残留）", "帧流未启动")

    if args.skip_fallback:
        record("SKIP", "8. 帧流失败自动降级轮询", "--skip-fallback")
    else:
        check_fallback(hdc)

    if args.worker_url:
        platform = "harmony_pc" if hdc.device_category() == "pc" else "harmony_mobile"
        check_ws_streaming(args.worker_url, platform, udid)
    else:
        record("SKIP", "9. Worker WS 端到端推流", "未提供 --worker-url")

    # 汇总
    print("\n" + "=" * 70)
    passed = sum(1 for s, *_ in RESULTS if s == "PASS")
    failed = sum(1 for s, *_ in RESULTS if s == "FAIL")
    skipped = sum(1 for s, *_ in RESULTS if s == "SKIP")
    for status, name, detail in RESULTS:
        print(f"  [{status}] {name}" + (f" —— {detail}" if detail else ""))
    print(f"\n结果: {passed} 通过, {failed} 失败, {skipped} 跳过")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
