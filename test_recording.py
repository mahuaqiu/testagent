"""
临时测试脚本：调用本地 Worker 测试录制功能
"""
import requests
import json
import time
import sys

# Worker 地址
WORKER_URL = "http://localhost:8088"

def test_recording():
    """测试录制功能"""

    # 测试1: 带水印录制（默认）
    print("=" * 50)
    print("测试1: 启动带水印的录制（默认）")
    print("=" * 50)

    task_request = {
        "platform": "windows",
        "actions": [
            {
                "action_type": "start_recording",
                "value": "d:/temp/test_watermark.mp4",
                "params": {
                    "fps": 15,
                    "watermark": True  # 默认就是 True
                }
            }
        ]
    }

    response = requests.post(f"{WORKER_URL}/task/execute", json=task_request, timeout=30)
    print(f"状态码: {response.status_code}")
    result = response.json()
    print(f"响应: {json.dumps(result, ensure_ascii=False, indent=2)}")

    if result.get("status") != "success":
        print("❌ 录制启动失败")
        return

    print("✅ 录制已启动，等待 3 秒...")
    time.sleep(30)

    # 测试2: 停止录制
    print("\n" + "=" * 50)
    print("测试2: 停止录制")
    print("=" * 50)

    stop_request = {
        "platform": "windows",
        "actions": [
            {
                "action_type": "stop_recording"
            }
        ]
    }

    response = requests.post(f"{WORKER_URL}/task/execute", json=stop_request, timeout=30)
    print(f"状态码: {response.status_code}")
    result = response.json()
    print(f"响应: {json.dumps(result, ensure_ascii=False, indent=2)}")

    if result.get("status") == "success":
        print(f"✅ 录制已停止，文件: {result.get('output')}")
    else:
        print("❌ 停止录制失败")


def test_idempotent_stop():
    """测试停止录制的幂等性"""

    print("\n" + "=" * 50)
    print("测试5: 停止录制的幂等性")
    print("=" * 50)

    # 先启动一个录制
    task_request = {
        "platform": "windows",
        "actions": [
            {
                "action_type": "start_recording",
                "value": "d:/temp/test_idempotent.mp4"
            }
        ]
    }

    response = requests.post(f"{WORKER_URL}/task/execute", json=task_request, timeout=30)
    result = response.json()
    print(f"启动响应: {result.get('status')}")

    time.sleep(1)

    # 第一次停止
    print("第一次停止...")
    stop_request = {
        "platform": "windows",
        "actions": [{"action_type": "stop_recording"}]
    }
    response = requests.post(f"{WORKER_URL}/task/execute", json=stop_request, timeout=30)
    result1 = response.json()
    print(f"第一次停止: {result1.get('status')}")

    # 第二次停止（幂等测试）
    print("第二次停止（幂等测试）...")
    response = requests.post(f"{WORKER_URL}/task/execute", json=stop_request, timeout=30)
    result2 = response.json()
    print(f"第二次停止: {result2.get('status')}")

    if result2.get("status") == "success":
        print("✅ 幂等性测试通过")
    else:
        print(f"⚠️ 幂等性测试结果: {result2}")


if __name__ == "__main__":
    # 先检查 Worker 是否运行
    try:
        response = requests.get(f"{WORKER_URL}/worker_devices", timeout=5)
        print(f"Worker 状态: {response.status_code}")
    except requests.exceptions.ConnectionError:
        print("❌ 无法连接到 Worker，请确保 Worker 已启动（localhost:8080）")
        sys.exit(1)

    # 运行测试
    test_recording()