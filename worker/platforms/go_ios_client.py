"""
go-ios CLI 客户端封装。

封装 go-ios 命令调用，提供设备发现、WDA 启动、端口转发等功能。
"""

import json
import logging
import os
import subprocess
import time
from typing import Any, Optional

import httpx

logger = logging.getLogger(__name__)


class GoIOSClient:
    """go-ios CLI 客户端。"""

    def __init__(
        self,
        go_ios_path: str,
        agent_port: int = 28100,
        timeout: int = 30,
    ):
        """
        初始化 GoIOSClient。

        Args:
            go_ios_path: go-ios 可执行文件路径（相对于 exe 目录或绝对路径）
            agent_port: go-ios agent HTTP API 端口
            timeout: 命令执行超时时间（秒）
        """
        self._go_ios_path = self._resolve_path(go_ios_path)
        self.agent_port = agent_port
        self.agent_host = "127.0.0.1"
        self.timeout = timeout
        self._http_client: Optional[httpx.Client] = None

    def _resolve_path(self, path: str) -> str:
        """解析 go-ios 路径（支持相对路径和绝对路径）。"""
        import sys
        if os.path.isabs(path):
            return path
        # 打包模式下相对于 exe 目录
        base_dir = os.path.dirname(sys.executable) if getattr(sys, 'frozen', False) else os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
        return os.path.join(base_dir, path)

    def _run_cmd(
        self,
        args: list[str],
        timeout: Optional[int] = None,
        check: bool = True,
    ) -> subprocess.CompletedProcess:
        """
        执行 go-ios 命令。

        Args:
            args: 命令参数（不含 ios.exe 本身）
            timeout: 超时时间
            check: 是否检查返回码

        Returns:
            CompletedProcess: 命令执行结果
        """
        cmd = [self._go_ios_path] + args
        logger.debug(f"Running go-ios command: {cmd}")
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout or self.timeout,
            check=check,
        )
        return result

    def _run_cmd_json(self, args: list[str], timeout: Optional[int] = None) -> Any:
        """执行 go-ios 命令并解析 JSON 输出。"""
        result = self._run_cmd(args, timeout=timeout, check=False)
        if result.returncode != 0:
            logger.warning(f"go-ios command failed: {result.stderr}")
            return None
        try:
            return json.loads(result.stdout)
        except json.JSONDecodeError as e:
            logger.error(f"Failed to parse go-ios JSON output: {e}")
            return None

    # ========== Agent 管理 ==========

    def start_agent(self) -> subprocess.Popen:
        """启动 go-ios agent（后台进程）。"""
        cmd = [self._go_ios_path, "tunnel", "start"]
        # Windows: 隐藏窗口，独立进程
        creationflags = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
        startupinfo = subprocess.STARTUPINFO()
        startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        startupinfo.wShowWindow = subprocess.SW_HIDE
        process = subprocess.Popen(
            cmd,
            stdin=None,
            stdout=None,
            stderr=subprocess.DEVNULL,
            creationflags=creationflags,
            startupinfo=startupinfo,
        )
        logger.info(f"go-ios agent started (PID: {process.pid})")
        return process

    def check_agent_health(self) -> bool:
        """检查 agent 健康状态。"""
        try:
            if not self._http_client:
                self._http_client = httpx.Client(timeout=5)
            resp = self._http_client.get(f"http://{self.agent_host}:{self.agent_port}/health")
            return resp.status_code == 200
        except Exception as e:
            logger.debug(f"Agent health check failed: {e}")
            return False

    def wait_agent_ready(self, timeout: int = 30) -> bool:
        """等待 agent 就绪。"""
        start = time.time()
        while time.time() - start < timeout:
            if self.check_agent_health():
                return True
            time.sleep(1)
        return False

    def get_tunnel_info(self, udid: str) -> Optional[dict]:
        """
        获取 iOS 17+ 设备的 tunnel 信息。

        Args:
            udid: 设备 UDID

        Returns:
            dict: tunnel 信息 {"address": "...", "rsdPort": ..., "udid": "..."} 或 None
        """
        try:
            if not self._http_client:
                self._http_client = httpx.Client(timeout=5)
            resp = self._http_client.get(f"http://{self.agent_host}:{self.agent_port}/tunnel/{udid}")
            if resp.status_code == 200:
                return resp.json()
            return None
        except Exception as e:
            logger.debug(f"Failed to get tunnel info for {udid}: {e}")
            return None

    def list_tunnels(self) -> list[dict]:
        """列出所有已建立的 tunnel。"""
        try:
            if not self._http_client:
                self._http_client = httpx.Client(timeout=5)
            resp = self._http_client.get(f"http://{self.agent_host}:{self.agent_port}/tunnels")
            if resp.status_code == 200:
                return resp.json()
            return []
        except Exception as e:
            logger.debug(f"Failed to list tunnels: {e}")
            return []

    def close(self) -> None:
        """关闭 HTTP 客户端。"""
        if self._http_client:
            self._http_client.close()
            self._http_client = None

    # ========== 设备发现 ==========

    def list_devices(self) -> list[dict]:
        """
        获取设备列表（含详细信息）。

        Returns:
            list[dict]: 设备列表，每个设备包含 udid, name, version, model 等
        """
        data = self._run_cmd_json(["list", "--details"])
        if not data:
            return []
        devices = data.get("deviceList", [])
        result = []
        for d in devices:
            # go-ios 的设备信息结构
            props = d.get("Properties", {})
            result.append({
                "udid": props.get("SerialNumber", ""),
                "name": "",  # 需要通过 info 命令获取
                "version": "",  # 需要通过 info 命令获取
                "model": props.get("ProductType", ""),
                "device_id": d.get("DeviceID", 0),
            })
        return result

    def get_device_info(self, udid: str) -> Optional[dict]:
        """
        获取设备详细信息。

        Args:
            udid: 设备 UDID

        Returns:
            dict: 设备信息
        """
        data = self._run_cmd_json(["--udid", udid, "info"])
        if not data:
            return None
        return {
            "udid": udid,
            "name": data.get("DeviceName", "Unknown"),
            "version": data.get("ProductVersion", "Unknown"),
            "model": data.get("ProductType", "Unknown"),
            "build_version": data.get("BuildVersion", "Unknown"),
        }

    def get_device_version(self, udid: str) -> str:
        """获取设备 iOS 版本。"""
        info = self.get_device_info(udid)
        return info.get("version", "") if info else ""

    # ========== WDA 启动 ==========

    def start_wda(
        self,
        udid: str,
        bundle_id: str,
        address: Optional[str] = None,
        rsd_port: Optional[int] = None,
    ) -> subprocess.Popen:
        """
        启动 WDA（后台进程）。

        Args:
            udid: 设备 UDID
            bundle_id: WDA bundle ID
            address: iOS 17+ tunnel 地址
            rsd_port: iOS 17+ tunnel RSD 端口

        Returns:
            subprocess.Popen: WDA 进程
        """
        args = ["--udid", udid, "runwda", "--bundleid", bundle_id]
        if address and rsd_port:
            args.extend(["--address", address, "--rsd-port", str(rsd_port)])
        cmd = [self._go_ios_path] + args
        # Windows: 隐藏窗口，独立进程
        creationflags = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
        startupinfo = subprocess.STARTUPINFO()
        startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        startupinfo.wShowWindow = subprocess.SW_HIDE
        process = subprocess.Popen(
            cmd,
            stdin=None,
            stdout=None,
            stderr=subprocess.DEVNULL,
            creationflags=creationflags,
            startupinfo=startupinfo,
        )
        logger.info(f"WDA started for {udid} (PID: {process.pid})")
        return process

    # ========== 端口转发 ==========

    def forward_port(
        self,
        udid: str,
        local_port: int,
        device_port: int,
        address: Optional[str] = None,
        rsd_port: Optional[int] = None,
    ) -> subprocess.Popen:
        """
        端口转发（后台进程）。

        Args:
            udid: 设备 UDID
            local_port: 本地端口
            device_port: 设备端口
            address: iOS 17+ tunnel 地址
            rsd_port: iOS 17+ tunnel RSD 端口

        Returns:
            subprocess.Popen: 端口转发进程
        """
        args = ["--udid", udid, "forward", str(local_port), str(device_port)]
        if address and rsd_port:
            args.extend(["--address", address, "--rsd-port", str(rsd_port)])
        cmd = [self._go_ios_path] + args
        # Windows: 隐藏窗口，独立进程
        creationflags = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
        startupinfo = subprocess.STARTUPINFO()
        startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        startupinfo.wShowWindow = subprocess.SW_HIDE
        process = subprocess.Popen(
            cmd,
            stdin=None,
            stdout=None,
            stderr=subprocess.DEVNULL,
            creationflags=creationflags,
            startupinfo=startupinfo,
        )
        logger.info(f"Port forward started: {local_port} -> {device_port} (PID: {process.pid})")
        return process

    # ========== 应用管理 ==========

    def launch_app(
        self,
        udid: str,
        bundle_id: str,
        address: Optional[str] = None,
        rsd_port: Optional[int] = None,
    ) -> bool:
        """启动应用。"""
        args = ["--udid", udid, "launch", bundle_id]
        if address and rsd_port:
            args.extend(["--address", address, "--rsd-port", str(rsd_port)])
        result = self._run_cmd(args, check=False)
        return result.returncode == 0

    def kill_app(
        self,
        udid: str,
        bundle_id: str,
        address: Optional[str] = None,
        rsd_port: Optional[int] = None,
    ) -> bool:
        """关闭应用。"""
        args = ["--udid", udid, "kill", bundle_id]
        if address and rsd_port:
            args.extend(["--address", address, "--rsd-port", str(rsd_port)])
        result = self._run_cmd(args, check=False)
        return result.returncode == 0

    def get_processes(
        self,
        udid: str,
        address: Optional[str] = None,
        rsd_port: Optional[int] = None,
    ) -> list[dict]:
        """获取运行的应用进程。"""
        args = ["--udid", udid, "ps", "--apps"]
        if address and rsd_port:
            args.extend(["--address", address, "--rsd-port", str(rsd_port)])
        data = self._run_cmd_json(args)
        if not data:
            return []
        return data