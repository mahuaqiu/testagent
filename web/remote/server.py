"""
HTTP API 服务和命令行入口。

提供 RESTful API 用于任务提交、结果查询、会话管理。
可作为独立服务启动。

Usage:
    # 命令行启动
    python -m web.remote.server --port 8080 --cdp-port 9222

    # 代码启动
    from web.remote.server import create_app
    app = create_app()
    app.run(port=8080)
"""

import argparse
import json
import sys
from typing import Optional
import threading

# 使用 Flask 或 http.server
try:
    from flask import Flask, request, jsonify
    HAS_FLASK = True
except ImportError:
    HAS_FLASK = False
    from http.server import HTTPServer, BaseHTTPRequestHandler
    import urllib.parse

from web.remote.worker import Worker
from web.remote.task import Task, Action, TaskConfig
from web.remote.result import TaskResult


# 全局 Worker 实例
_worker: Optional[Worker] = None


def get_worker() -> Worker:
    """获取全局 Worker 实例。"""
    global _worker
    if _worker is None:
        raise RuntimeError("Worker not initialized. Call init_worker() first.")
    return _worker


def init_worker(
    cdp_port: int = 9222,
    cdp_endpoint: Optional[str] = None,
    headless: bool = True,
    **kwargs,
) -> Worker:
    """初始化并启动 Worker。"""
    global _worker
    _worker = Worker(
        cdp_port=cdp_port,
        cdp_endpoint=cdp_endpoint,
        headless=headless,
        **kwargs,
    )
    _worker.start()
    return _worker


if HAS_FLASK:
    # ==================== Flask 实现 ====================

    def create_app() -> Flask:
        """创建 Flask 应用。"""
        app = Flask(__name__)

        @app.route("/status", methods=["GET"])
        def status():
            """获取服务状态。"""
            try:
                worker = get_worker()
                return jsonify(worker.get_status())
            except Exception as e:
                return jsonify({"error": str(e)}), 500

        @app.route("/cdp-endpoint", methods=["GET"])
        def cdp_endpoint():
            """获取 CDP 端点。"""
            try:
                worker = get_worker()
                endpoint = worker.get_cdp_endpoint()
                return jsonify({"cdp_endpoint": endpoint})
            except Exception as e:
                return jsonify({"error": str(e)}), 500

        @app.route("/task", methods=["POST"])
        def submit_task():
            """提交任务。"""
            try:
                worker = get_worker()
                data = request.get_json()

                # 解析任务
                task = Task.from_dict(data)
                task_id = worker.submit_task(task)

                return jsonify({"task_id": task_id, "status": "pending"})
            except Exception as e:
                return jsonify({"error": str(e)}), 400

        @app.route("/task/execute", methods=["POST"])
        def execute_task():
            """提交并立即执行任务。"""
            try:
                worker = get_worker()
                data = request.get_json()

                # 解析任务
                task = Task.from_dict(data)
                result = worker.execute_task(task)

                return jsonify(result.to_dict())
            except Exception as e:
                return jsonify({"error": str(e)}), 500

        @app.route("/result/<task_id>", methods=["GET"])
        def get_result(task_id: str):
            """获取任务结果。"""
            try:
                worker = get_worker()
                result = worker.get_result(task_id)

                if result is None:
                    return jsonify({"error": "Result not found"}), 404

                # 检查是否需要包含截图数据
                include_screenshots = request.args.get("screenshots", "true").lower() == "true"
                return jsonify(result.to_dict(include_screenshots=include_screenshots))
            except Exception as e:
                return jsonify({"error": str(e)}), 500

        @app.route("/session", methods=["POST"])
        def create_session():
            """创建会话。"""
            try:
                worker = get_worker()
                data = request.get_json()
                user_id = data.get("user_id", "")
                context_options = data.get("context_options")

                session = worker.create_session(user_id, context_options)

                return jsonify({
                    "session_id": session.session_id,
                    "user_id": session.user_id,
                })
            except Exception as e:
                return jsonify({"error": str(e)}), 500

        @app.route("/session/<session_id>", methods=["DELETE"])
        def close_session(session_id: str):
            """关闭会话。"""
            try:
                worker = get_worker()
                success = worker.close_session(session_id)

                if success:
                    return jsonify({"status": "closed"})
                else:
                    return jsonify({"error": "Session not found"}), 404
            except Exception as e:
                return jsonify({"error": str(e)}), 500

        @app.route("/health", methods=["GET"])
        def health():
            """健康检查。"""
            return jsonify({"status": "ok"})

        return app


else:
    # ==================== 简易 HTTP 实现 ====================

    class RequestHandler(BaseHTTPRequestHandler):
        """简易 HTTP 请求处理器。"""

        def _send_json(self, data: dict, status: int = 200):
            """发送 JSON 响应。"""
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps(data, ensure_ascii=False).encode("utf-8"))

        def _read_json(self) -> dict:
            """读取 JSON 请求体。"""
            content_length = int(self.headers.get("Content-Length", 0))
            if content_length == 0:
                return {}
            body = self.rfile.read(content_length)
            return json.loads(body.decode("utf-8"))

        def do_GET(self):
            """处理 GET 请求。"""
            try:
                worker = get_worker()

                if self.path == "/status":
                    self._send_json(worker.get_status())

                elif self.path == "/cdp-endpoint":
                    self._send_json({"cdp_endpoint": worker.get_cdp_endpoint()})

                elif self.path == "/health":
                    self._send_json({"status": "ok"})

                elif self.path.startswith("/result/"):
                    task_id = self.path.split("/")[-1].split("?")[0]
                    result = worker.get_result(task_id)
                    if result:
                        self._send_json(result.to_dict())
                    else:
                        self._send_json({"error": "Result not found"}, 404)

                else:
                    self._send_json({"error": "Not found"}, 404)

            except Exception as e:
                self._send_json({"error": str(e)}, 500)

        def do_POST(self):
            """处理 POST 请求。"""
            try:
                worker = get_worker()
                data = self._read_json()

                if self.path == "/task":
                    task = Task.from_dict(data)
                    task_id = worker.submit_task(task)
                    self._send_json({"task_id": task_id, "status": "pending"})

                elif self.path == "/task/execute":
                    task = Task.from_dict(data)
                    result = worker.execute_task(task)
                    self._send_json(result.to_dict())

                elif self.path == "/session":
                    user_id = data.get("user_id", "")
                    session = worker.create_session(user_id)
                    self._send_json({
                        "session_id": session.session_id,
                        "user_id": session.user_id,
                    })

                else:
                    self._send_json({"error": "Not found"}, 404)

            except Exception as e:
                self._send_json({"error": str(e)}, 500)

        def do_DELETE(self):
            """处理 DELETE 请求。"""
            try:
                worker = get_worker()

                if self.path.startswith("/session/"):
                    session_id = self.path.split("/")[-1]
                    success = worker.close_session(session_id)
                    if success:
                        self._send_json({"status": "closed"})
                    else:
                        self._send_json({"error": "Session not found"}, 404)

                else:
                    self._send_json({"error": "Not found"}, 404)

            except Exception as e:
                self._send_json({"error": str(e)}, 500)

        def log_message(self, format, *args):
            """自定义日志格式。"""
            print(f"[HTTP] {args[0]}")


def create_app():
    """创建应用（兼容接口）。"""
    if HAS_FLASK:
        return create_app()
    else:
        return None


def run_server(
    port: int = 8080,
    cdp_port: int = 9222,
    cdp_endpoint: Optional[str] = None,
    headless: bool = True,
):
    """
    启动 HTTP 服务器。

    Args:
        port: HTTP 服务端口
        cdp_port: CDP 端口
        cdp_endpoint: 远程 CDP 端点
        headless: 是否无头模式
    """
    # 初始化并启动 Worker
    worker = init_worker(
        cdp_port=cdp_port,
        cdp_endpoint=cdp_endpoint,
        headless=headless,
    )

    print(f"[Server] Worker started, CDP endpoint: {worker.get_cdp_endpoint()}")

    if HAS_FLASK:
        # Flask 服务
        app = create_app()
        print(f"[Server] Starting Flask server on port {port}")
        app.run(host="0.0.0.0", port=port, threaded=True)
    else:
        # 简易 HTTP 服务
        server = HTTPServer(("0.0.0.0", port), RequestHandler)
        print(f"[Server] Starting HTTP server on port {port}")
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            print("\n[Server] Shutting down...")
        finally:
            server.server_close()
            worker.stop()


def main():
    """命令行入口。"""
    parser = argparse.ArgumentParser(description="Web Remote Test Worker Server")

    parser.add_argument(
        "--port",
        type=int,
        default=8080,
        help="HTTP API server port (default: 8080)",
    )
    parser.add_argument(
        "--cdp-port",
        type=int,
        default=9222,
        help="CDP debugging port (default: 9222)",
    )
    parser.add_argument(
        "--cdp-endpoint",
        type=str,
        default=None,
        help="Remote CDP endpoint URL (connect mode)",
    )
    parser.add_argument(
        "--headless",
        action="store_true",
        default=True,
        help="Run browser in headless mode (default: True)",
    )
    parser.add_argument(
        "--no-headless",
        action="store_true",
        help="Run browser with GUI",
    )

    args = parser.parse_args()

    headless = not args.no_headless

    print(f"""
╔════════════════════════════════════════════════════════════╗
║           Web Remote Test Worker Server                    ║
╠════════════════════════════════════════════════════════════╣
║  HTTP Port:    {args.port:<43} ║
║  CDP Port:     {args.cdp_port:<43} ║
║  Headless:     {str(headless):<43} ║
║  Mode:         {'connect' if args.cdp_endpoint else 'local':<43} ║
╚════════════════════════════════════════════════════════════╝
    """)

    try:
        run_server(
            port=args.port,
            cdp_port=args.cdp_port,
            cdp_endpoint=args.cdp_endpoint,
            headless=headless,
        )
    except KeyboardInterrupt:
        print("\n[Server] Shutdown complete.")
    except Exception as e:
        print(f"[Server] Error: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()