#!/usr/bin/env python
r"""通过 Worker 正式任务接口手动采集鸿蒙设备截图。

运行前请先启动 Worker，并在项目虚拟环境中执行：

    .\venv\Scripts\Activate.ps1
    python tests/harmony_pc_screenshot_api.py --device-id DEVICE_SN --count 5

脚本会通过 ``/worker_devices`` 识别设备属于鸿蒙 PC 还是鸿蒙移动设备，
再使用对应的 ``platform`` 调用 ``POST /task/execute`` 的 screenshot action。
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import io
import json
from pathlib import Path
import time
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from PIL import Image


def request_json(url: str, *, method: str = "GET", payload: dict | None = None, timeout: float) -> dict:
    """调用 Worker HTTP 接口并解析 JSON 响应。"""
    data = None
    headers = {"Accept": "application/json"}
    if payload is not None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = "application/json"

    request = Request(url, data=data, headers=headers, method=method)
    try:
        with urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"Worker HTTP {exc.code}: {detail}") from exc
    except URLError as exc:
        raise RuntimeError(f"Worker 连接失败: {exc.reason}") from exc


def _device_id(device: object) -> str | None:
    """从 Worker 设备记录中读取设备 SN。"""
    if not isinstance(device, dict):
        return None
    value = device.get("device_id") or device.get("udid") or device.get("serial")
    return str(value) if value else None


def _is_online(device: object) -> bool:
    """判断 Worker 设备记录是否处于可执行状态。"""
    if not isinstance(device, dict):
        return False
    status = str(device.get("connection_status", device.get("status", "online"))).lower()
    return status not in {"offline", "disconnected", "faulty"}


def choose_device(
    worker_url: str,
    requested_device_id: str | None,
    timeout: float,
) -> tuple[str, str]:
    """通过 Worker 设备接口选择设备，并返回平台和设备 SN。"""
    response = request_json(f"{worker_url.rstrip('/')}/worker_devices", timeout=timeout)
    devices = response.get("devices", {})
    if not isinstance(devices, dict):
        raise RuntimeError("Worker 返回的 devices 格式无效")

    platform_devices: dict[str, list[object]] = {}
    for platform in ("harmony_pc", "harmony_mobile"):
        items = devices.get(platform, [])
        if not isinstance(items, list):
            raise RuntimeError(f"Worker 返回的 {platform} 设备列表格式无效")
        platform_devices[platform] = items

    if requested_device_id:
        matches = [
            (platform, device)
            for platform, items in platform_devices.items()
            for device in items
            if _device_id(device) == requested_device_id
        ]
        if not matches:
            raise RuntimeError(
                f"设备 {requested_device_id} 不在 Worker 当前的 harmony_pc 或 harmony_mobile 列表中"
            )
        if len(matches) > 1:
            raise RuntimeError(f"设备 {requested_device_id} 同时出现在多个鸿蒙平台列表中")
        platform, device = matches[0]
        if not _is_online(device):
            raise RuntimeError(f"设备 {requested_device_id} 当前不在线: platform={platform}")
        return platform, requested_device_id

    for platform in ("harmony_pc", "harmony_mobile"):
        for device in platform_devices[platform]:
            device_id = _device_id(device)
            if device_id and _is_online(device):
                return platform, device_id
    raise RuntimeError("Worker 当前没有在线的鸿蒙 PC 或鸿蒙移动设备")


def capture_screenshot(worker_url: str, platform: str, device_id: str, timeout: float) -> bytes:
    """通过 Worker screenshot action 获取一张 JPEG。"""
    payload = {
        "platform": platform,
        "device_id": device_id,
        "actions": [{"action_type": "screenshot", "value": "manual"}],
    }
    result = request_json(
        f"{worker_url.rstrip('/')}/task/execute",
        method="POST",
        payload=payload,
        timeout=timeout,
    )
    if result.get("status") != "success":
        raise RuntimeError(f"截图任务失败: {result.get('error') or result.get('status')}")

    actions = result.get("actions")
    if not isinstance(actions, list):
        raise RuntimeError("Worker 返回的 actions 格式无效")
    for action in actions:
        if not isinstance(action, dict):
            continue
        encoded = action.get("screenshot")
        if not isinstance(encoded, str) or not encoded:
            continue
        try:
            image_data = base64.b64decode(encoded, validate=True)
            with Image.open(io.BytesIO(image_data)) as image:
                image.verify()
        except Exception as exc:
            raise RuntimeError(f"Worker 返回的截图不是有效图片: {exc}") from exc
        return image_data
    raise RuntimeError("Worker 返回中没有 screenshot 数据")


def save_screenshot(
    output_dir: Path,
    platform: str,
    device_id: str,
    index: int,
    image_data: bytes,
) -> Path:
    """保存 Worker 返回的截图并返回文件路径。"""
    output_dir.mkdir(parents=True, exist_ok=True)
    timestamp = time.strftime("%Y%m%d_%H%M%S")
    digest = hashlib.sha256(image_data).hexdigest()[:12]
    path = output_dir / f"{platform}_{device_id}_{timestamp}_{index:03d}_{digest}.jpg"
    path.write_bytes(image_data)
    return path


def parse_args() -> argparse.Namespace:
    """解析命令行参数。"""
    parser = argparse.ArgumentParser(description="通过 Worker 接口采集鸿蒙设备截图")
    parser.add_argument("--device-id", help="鸿蒙设备的 SN/UDID，不传时自动选择在线设备")
    parser.add_argument("--worker-url", default="http://127.0.0.1:8088", help="Worker HTTP 地址")
    parser.add_argument(
        "--output-dir",
        default="test_output/harmony_screenshots",
        help="截图保存目录",
    )
    parser.add_argument("--count", type=int, default=1, help="截图数量，默认 1")
    parser.add_argument("--interval", type=float, default=1.0, help="连续截图间隔秒数，默认 1 秒")
    parser.add_argument("--timeout", type=float, default=120.0, help="单次 Worker 请求超时秒数")
    return parser.parse_args()


def main() -> int:
    """脚本入口。"""
    args = parse_args()
    if args.count < 1:
        print("--count 必须大于等于 1")
        return 2
    if args.interval < 0 or args.timeout <= 0:
        print("--interval 不能小于 0，--timeout 必须大于 0")
        return 2

    try:
        platform, device_id = choose_device(args.worker_url, args.device_id, args.timeout)
        output_dir = Path(args.output_dir)
        print(f"设备: {device_id}, 平台: {platform}")
        print(f"截图数量: {args.count}, 输出目录: {output_dir.resolve()}")

        for index in range(1, args.count + 1):
            if index > 1 and args.interval > 0:
                time.sleep(args.interval)
            image_data = capture_screenshot(args.worker_url, platform, device_id, args.timeout)
            path = save_screenshot(output_dir, platform, device_id, index, image_data)
            with Image.open(io.BytesIO(image_data)) as image:
                print(f"[{index}/{args.count}] {path} ({image.width}x{image.height}, {len(image_data)} bytes)")
    except Exception as exc:
        print(f"FAIL: {exc}")
        return 1

    print("完成")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
