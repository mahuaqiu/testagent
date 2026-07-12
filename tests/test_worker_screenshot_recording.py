#!/usr/bin/env python
"""Worker 截图和录制测试脚本。

用法:
    python test_worker_screenshot_recording.py [--host HOST] [--port PORT]

示例:
    python test_worker_screenshot_recording.py
    python test_worker_screenshot_recording.py --host 127.0.0.1 --port 8088
"""

import argparse
import json
import os
import sys
import time
import uuid
from pathlib import Path
from typing import Any

import requests


def make_request(host: str, port: int, platform: str, actions: list, device_id: str = None, window: dict = None) -> dict:
    """发送任务请求到 Worker。"""
    url = f"http://{host}:{port}/task/execute"

    payload = {
        "platform": platform,
        "actions": actions,
    }

    if device_id:
        payload["device_id"] = device_id

    if window:
        payload["window"] = window

    print(f"\n=== 请求 ===")
    print(f"URL: {url}")
    print(f"Payload: {json.dumps(payload, ensure_ascii=False, indent=2)}")

    response = requests.post(url, json=payload, timeout=120)
    response.raise_for_status()
    result = response.json()

    print(f"\n=== 响应 ===")
    # 打印响应，截断过长的 base64
    result_copy = json.loads(json.dumps(result))
    if "actions" in result_copy:
        for action in result_copy.get("actions", []):
            if "screenshot" in action and action["screenshot"]:
                action["screenshot"] = action["screenshot"][:100] + "..."
    print(json.dumps(result_copy, ensure_ascii=False, indent=2))

    return result


def save_screenshot(result: dict, prefix: str = "screenshot") -> str:
    """从结果中提取并保存截图。"""
    # 判断任务是否成功 (Worker 返回 status 字段，不是 success)
    if result.get("status") != "success":
        print(f"任务失败: {result.get('error') or result.get('status')}")
        return None

    screenshots = result.get("screenshots", [])

    # 从 actions 中提取截图数据（Worker 实际返回格式）
    if not screenshots:
        for action in result.get("actions", []):
            if action.get("screenshot"):
                screenshots.append({
                    "image_b64": action["screenshot"],
                    "label": action.get("output", ""),
                })

    if not screenshots:
        print("没有截图数据")
        return None

    for i, screenshot in enumerate(screenshots):
        image_b64 = screenshot.get("image_b64")
        if not image_b64:
            continue

        import base64
        image_data = base64.b64decode(image_b64)

        output_dir = Path("test_output")
        output_dir.mkdir(exist_ok=True)
        filename = str(output_dir / f"{prefix}_{i}_{int(time.time())}.png")
        with open(filename, "wb") as f:
            f.write(image_data)

        print(f"截图已保存: {filename} ({len(image_data)} bytes)")
        return filename

    return None


def save_recording(result: dict, prefix: str = "recording") -> str:
    """从结果中提取录制文件路径。"""
    # 判断任务是否成功
    if result.get("status") != "success":
        print(f"任务失败: {result.get('error') or result.get('status')}")
        return None

    # 从 actions 中查找录制结果（Worker 实际返回格式）
    for action in result.get("actions", []):
        output_path = action.get("output")
        if output_path and os.path.exists(output_path):
            file_size = os.path.getsize(output_path)
            print(f"录制文件: {output_path} ({file_size} bytes)")
            return output_path

        # 如果路径不以根目录开头，尝试在项目目录下查找
        if output_path and not os.path.isabs(output_path):
            candidate = os.path.join(os.path.dirname(__file__), output_path)
            if os.path.exists(candidate):
                file_size = os.path.getsize(candidate)
                print(f"录制文件: {candidate} ({file_size} bytes)")
                return candidate

    print("没有找到录制文件")
    return None


def test_screenshot(host: str, port: int, platform: str) -> str:
    """测试截图功能。"""
    print("\n" + "="*60)
    print("测试 1: 截图")
    print("="*60)

    actions = [
        {
            "action_type": "screenshot",
            "value": "full",
        }
    ]

    result = make_request(host, port, platform, actions)
    filename = save_screenshot(result, f"{platform}_screenshot")
    return filename


def test_recording(host: str, port: int, platform: str, duration_seconds: int = 20) -> str:
    """测试录制功能（录制中截图2次）。"""
    print("\n" + "="*60)
    print(f"测试 2: 录制 ({duration_seconds} 秒，录制中截图2次)")
    print("="*60)

    # 生成唯一的输出路径
    output_dir = Path("test_output")
    output_dir.mkdir(exist_ok=True)
    output_path = str("D:/code/autotest/test_output/" +   f"test_recording_{int(time.time())}.mp4")

    actions = [
        {
            "action_type": "start_recording",
            "value": output_path,
            "fps": 20,
            "watermark":True
        }
    ]

    print(f"\n开始录制: {output_path}")
    result = make_request(host, port, platform, actions)

    # 使用正确的字段判断成功
    if result.get("status") != "success":
        print(f"开始录制失败: {result.get('error')}")
        return None

    # 录制中截图2次
    print(f"录制中... (等待 {duration_seconds} 秒，中途截图2次)")

    # 第一次截图：录制进行到 1/3 时
    time.sleep(duration_seconds / 3)
    print(f"\n录制中截图 #1...")
    screenshot_actions = [
        {
            "action_type": "screenshot",
            "value": "full",
        }
    ]
    result = make_request(host, port, platform, screenshot_actions)
    save_screenshot(result, f"{platform}_during_recording_1")

    # 第二次截图：录制进行到 2/3 时
    time.sleep(duration_seconds / 3)
    print(f"\n录制中截图 #2...")
    result = make_request(host, port, platform, screenshot_actions)
    save_screenshot(result, f"{platform}_during_recording_2")

    # 等待剩余时间
    time.sleep(duration_seconds / 3)

    print(f"\n停止录制")
    actions = [
        {
            "action_type": "stop_recording",
        }
    ]
    result = make_request(host, port, platform, actions)
    filename = save_recording(result, "recording")
    return filename


def test_screenshot_after_recording(host: str, port: int, platform: str) -> str:
    """测试录制后的截图（验证资源释放）。"""
    print("\n" + "="*60)
    print("测试 3: 录制后截图（验证资源释放）")
    print("="*60)

    actions = [
        {
            "action_type": "screenshot",
            "monitor": 1,
            "value": "full",
        }
    ]

    result = make_request(host, port, platform, actions)
    filename = save_screenshot(result, f"{platform}_after_recording")
    return filename


def test_screenshot_window_class(host: str, port: int, class_name: str) -> str:
    """测试窗口级截图（通过 class 名称）。"""
    print("\n" + "="*60)
    print(f"测试窗口级截图: class={class_name}")
    print("="*60)

    actions = [
        {
            "action_type": "screenshot",
            "value": "full",
        }
    ]

    # 使用 window 参数指定窗口类名
    result = make_request(host, port, "windows", actions, window={"class": class_name})
    filename = save_screenshot(result, f"window_{class_name}")
    return filename


def main():
    parser = argparse.ArgumentParser(description="Worker 截图和录制测试")
    parser.add_argument("--host", default="192.168.0.102", help="Worker 主机地址")
    parser.add_argument("--port", type=int, default=8088, help="Worker 端口")
    parser.add_argument("--platform", default="windows", choices=["windows", "web"], help="测试平台")
    parser.add_argument("--recording-duration", type=int, default=10, help="录制时长（秒）")
    parser.add_argument("--skip-screenshot", action="store_true", help="跳过截图测试")
    parser.add_argument("--skip-recording", action="store_true", help="跳过录制测试")
    parser.add_argument("--window-class", type=str, default=None, help="窗口��名（用于窗口级截图测试）")
    args = parser.parse_args()

    print("="*60)
    print("Worker 截图和录制测试")
    print("="*60)
    print(f"Worker: {args.host}:{args.port}")
    print(f"平台: {args.platform}")
    print(f"录制时长: {args.recording_duration} 秒")

    # 检查 Worker 是否可用
    try:
        response = requests.get(f"http://{args.host}:{args.port}/worker_devices", timeout=5)
        response.raise_for_status()
        devices = response.json()
        print(f"\nWorker 可用，设备: {json.dumps(devices, indent=2)}")
    except Exception as e:
        print(f"\n无法连接到 Worker: {e}")
        print("请确保 Worker 已启动: python -m worker.main")
        sys.exit(1)

    results = {}

    # 测试 1: 截图
    if not args.skip_screenshot:
        try:
            results["screenshot"] = test_screenshot(args.host, args.port, args.platform)
        except Exception as e:
            print(f"截图测试失败: {e}")
            import traceback
            traceback.print_exc()
    else:
        print("\n跳过截图测试")

    # 测试 2: 录制
    if not args.skip_recording:
        try:
            results["recording"] = test_recording(args.host, args.port, args.platform, args.recording_duration)
        except Exception as e:
            print(f"录制测试失败: {e}")
            import traceback
            traceback.print_exc()
    else:
        print("\n跳过录制测试")

    # 测试 3: 录制后截图
    if not args.skip_screenshot and not args.skip_recording:
        try:
            results["screenshot_after"] = test_screenshot_after_recording(args.host, args.port, args.platform)
        except Exception as e:
            print(f"录制后截图测试失败: {e}")
            import traceback
            traceback.print_exc()

    # 测试 4: 窗口级截图（通过 class）
    if args.window_class:
        try:
            results["window_screenshot"] = test_screenshot_window_class(args.host, args.port, args.window_class)
        except Exception as e:
            print(f"窗口级截图测试失败: {e}")
            import traceback
            traceback.print_exc()

    # 总结
    print("\n" + "="*60)
    print("测试结果总结")
    print("="*60)

    all_passed = True

    if "screenshot" in results and results["screenshot"]:
        print(f"截图测试通过: {results['screenshot']}")
    elif not args.skip_screenshot:
        print("截图测试失败")
        all_passed = False

    if "recording" in results and results["recording"]:
        print(f"录制测试通过: {results['recording']}")
    elif not args.skip_recording:
        print("录制测试失败")
        all_passed = False

    if "screenshot_after" in results and results["screenshot_after"]:
        print(f"录制后截图测试通过: {results['screenshot_after']}")
    elif not args.skip_screenshot and not args.skip_recording:
        print("录制后截图测试失败")
        all_passed = False

    if "window_screenshot" in results and results["window_screenshot"]:
        print(f"窗口级截图测试通过: {results['window_screenshot']}")
    elif args.window_class:
        print("窗口级截图测试失败")
        all_passed = False

    if all_passed:
        print("\n所有测试通过!")
    else:
        print("\n部分测试失败")
        sys.exit(1)


if __name__ == "__main__":
    main()
