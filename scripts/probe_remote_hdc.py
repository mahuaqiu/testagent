# -*- coding: utf-8 -*-
"""远程调试脚本：通过 105 Worker 的 cmd_exec 通道抓取真机 HDC 输出。

用途：鸿蒙设备发现返回 0 台，但手动 hdc list targets 能看到设备。
本脚本远程执行 hdc 命令，打印原始输出（repr 形式，暴露列分隔符/状态列），
用于确认 parse_target_lines / is_ready 的过滤是否误杀真机。

用法：
    python scripts/probe_remote_hdc.py [worker_url]
    默认 worker_url = http://192.168.0.105:8088
"""

import json
import sys
import urllib.request

WORKER = sys.argv[1] if len(sys.argv) > 1 else "http://192.168.0.105:8088"
HDC = r'"D:\Test Worker\tools\hdc\hdc.exe"'
UDID = "3QC0124A10000066"

# 对照实验：worker 的 shell() 把整条命令包上双引号后经 subprocess list 传参，
# hdc.exe 实际收到的 argv 是带 literal 双引号的单参数（\" 转义形式）；
# 而此前 cmd_exec 手工验证时外层引号被 C runtime 消费，hdc 收到干净命令。
# 下面每条命令都测两种形态：clean=干净单参数，wrapped=模拟 worker 的内嵌引号。
COMMANDS = [
    ("devicetype clean", f'{HDC} -t {UDID} shell "param get const.product.devicetype"'),
    ("devicetype wrapped", f'{HDC} -t {UDID} shell "\\"param get const.product.devicetype\\""'),
    ("screen clean", f'{HDC} -t {UDID} shell "hidumper -s 10 -a screen"'),
    ("screen wrapped", f'{HDC} -t {UDID} shell "\\"hidumper -s 10 -a screen\\""'),
    ("model wrapped", f'{HDC} -t {UDID} shell "\\"param get const.product.model\\""'),
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
    with urllib.request.urlopen(request, timeout=60) as response:
        return json.loads(response.read().decode("utf-8"))


def main() -> None:
    print(f"Worker: {WORKER}")
    for label, cmd in COMMANDS:
        print("=" * 70)
        print(f"[{label}] {cmd}")
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
        print(f"  stdout(repr): {stdout!r}")
        if stderr:
            print(f"  stderr(repr): {stderr!r}")
        # 对 list targets -v 额外逐行打印列拆分结果，直接对照解析器行为
        if "-v" in cmd and stdout:
            import re

            for line in stdout.splitlines():
                line = line.strip()
                if not line:
                    continue
                columns = re.split(r"\s+", line)
                print(f"  行: {line!r} -> 列: {columns}")


if __name__ == "__main__":
    main()
