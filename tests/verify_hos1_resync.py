"""端到端验证脚本：真实子进程管道下 HOS1 滑动重对齐（独立运行，非 pytest 用例）。

运行方式：
    ./venv/Scripts/python.exe tests/verify_hos1_resync.py

子进程模拟 SDK native 库直写 fd 1 的行为：
1. JVM 极早期向 fd 1 写 [0.000s] 诊断行（生产现场的首个故障点）；
2. 随后写出合法 HOS1 帧，中段再混入诊断文本。

父进程按生产路径 iter_messages(popen.stdout) 读取，验证真实管道
短读/阻塞/EOF 语义下滑动窗口重对齐仍然正确。真机验证仍需打包
部署到故障设备，观察 WARNING 日志与官方链路是否连接成功。
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from worker.platforms.harmony_official.protocol import (  # noqa: E402
    BridgeMessageType,
    iter_messages,
)

CHILD = r"""
import struct, sys, time
out = sys.stdout.buffer
# 模拟 native 库绕过任何 Java 重定向直写 fd 1
out.write(b"[0.000s] HOScrcpy native diag line one\n")
out.write(b"[0.001s] another diag line\n")
out.flush()
def msg(t, p):
    return struct.pack(">4sBBI", b"HOS1", 1, t, len(p)) + p
out.write(msg(1, b"serial=HOS1-verify"))
out.flush()
time.sleep(0.2)
out.write(msg(2, b"\x00\x00\x00\x01\x67frame-data"))
out.write(b"[0.456s] mid-stream native log\n")
out.write(msg(5, b"stats capture_messages=2"))
out.flush()
"""


def main() -> None:
    child = subprocess.Popen(
        [sys.executable, "-c", CHILD],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        bufsize=0,
    )

    types = []
    for message in iter_messages(child.stdout):
        types.append(message.message_type)
        print(f"received: type={message.message_type.name} payload={message.payload[:40]!r}")

    child.wait(timeout=5)
    assert types == [
        BridgeMessageType.READY,
        BridgeMessageType.H264,
        BridgeMessageType.STATS,
    ], f"消息序列不符: {types}"
    print("PASS: 3 条 HOS1 消息全部正确解析，诊断文本被滑动跳过")


if __name__ == "__main__":
    main()
