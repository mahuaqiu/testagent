# -*- coding: utf-8 -*-
"""远程探测脚本：确认鸿蒙真机上 GPU 频点/负载、温度、功耗、FPS/Jank、线程 CPU 的可行性。

通过 105 Worker 的 cmd_exec 通道执行 hdc shell，逐项打印原始输出，
用于决定 perfharmony 下一步能支持哪些指标。

用法：
    python scripts/probe_metrics_feasibility.py [worker_url]
    默认 worker_url = http://192.168.0.105:8088
"""

import json
import sys
import urllib.request

WORKER = sys.argv[1] if len(sys.argv) > 1 else "http://192.168.0.105:8088"
HDC = r'"D:\Test Worker\tools\hdc\hdc.exe"'
UDID = "3QC0124A10000066"

COMMANDS = [
    (
        "-r 不带 PKG（纯系统内存）",
        f'{HDC} -t {UDID} shell "/system/bin/SP_daemon -N 1 -r; echo EXIT=$?"',
    ),
    (
        "无 PKG 系统组合 -c -g -t -p -net -d -r",
        f'{HDC} -t {UDID} shell "/system/bin/SP_daemon -N 1 -c -g -t -p -net -d -r; echo EXIT=$?"',
    ),
]


def run_remote(cmd: str) -> dict:
    """通过 Worker 同步任务接口在宿主机执行命令。"""
    payload = json.dumps(
        {
            "platform": "windows",
            "device_id": None,
            "actions": [{"action_type": "cmd_exec", "value": cmd}],
        }
    ).encode("utf-8")
    request = urllib.request.Request(
        f"{WORKER}/task/execute",
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=90) as response:
        return json.loads(response.read().decode("utf-8"))


def main() -> None:
    print(f"Worker: {WORKER}")
    for label, cmd in COMMANDS:
        print("=" * 70)
        print(f"[{label}]")
        try:
            result = run_remote(cmd)
        except Exception as exc:  # noqa: BLE001 调试脚本直接打印异常
            print(f"  请求失败: {exc}")
            continue
        actions = result.get("actions") or []
        action0 = actions[0] if actions else {}
        stdout = action0.get("stdout") or action0.get("output") or ""
        stderr = action0.get("stderr") or ""
        print(f"  status={result.get('status')} exit_code={action0.get('exit_code')}")
        for line in stdout.splitlines():
            print(f"  | {line}")
        if stderr:
            print(f"  stderr(repr): {stderr!r}")


if __name__ == "__main__":
    main()
