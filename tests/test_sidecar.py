"""测试 Windows sidecar 通信"""
import json
import subprocess
import sys

def test_sidecar():
    # 启动进程
    proc = subprocess.Popen(
        [r"D:\code\autotest\tools\windows-screen-sidecar.exe"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        bufsize=1,
    )

    print(f"PID: {proc.pid}", file=sys.stderr)

    # 健康检查
    req_id = 1
    payload = json.dumps({"id": req_id, "cmd": "health", "params": {}}, ensure_ascii=False)
    proc.stdin.write(payload + "\n")
    proc.stdin.flush()
    response = proc.stdout.readline()
    print(f"Health response: {response.strip()[:100]}", file=sys.stderr)

    # session_open
    req_id = 2
    payload = json.dumps({
        "id": req_id,
        "cmd": "session_open",
        "params": {"session_id": "test", "monitor": 1, "idle_fps": 1, "active_fps": 15}
    }, ensure_ascii=False)
    proc.stdin.write(payload + "\n")
    proc.stdin.flush()
    response = proc.stdout.readline()
    print(f"Session open response: {response.strip()[:200]}", file=sys.stderr)

    if not response or not response.strip():
        print("ERROR: Empty response for session_open!", file=sys.stderr)
        # 检查进程状态
        retcode = proc.poll()
        print(f"Process poll() = {retcode}", file=sys.stderr)
        if retcode is not None:
            stderr_output = proc.stderr.read()
            print(f"Process exited with code {retcode}", file=sys.stderr)
            print(f"Stderr: {stderr_output[:500]}", file=sys.stderr)
        proc.terminate()
        return

    # snapshot
    req_id = 3
    payload = json.dumps({
        "id": req_id,
        "cmd": "snapshot",
        "params": {"session_id": "test", "format": "jpeg", "quality": 80}
    }, ensure_ascii=False)
    proc.stdin.write(payload + "\n")
    proc.stdin.flush()
    response = proc.stdout.readline()
    print(f"Snapshot response length: {len(response)}", file=sys.stderr)

    # shutdown
    req_id = 4
    payload = json.dumps({"id": req_id, "cmd": "shutdown", "params": {}}, ensure_ascii=False)
    proc.stdin.write(payload + "\n")
    proc.stdin.flush()

    proc.wait(timeout=5)
    print(f"Process exited with code: {proc.returncode}", file=sys.stderr)

if __name__ == "__main__":
    test_sidecar()