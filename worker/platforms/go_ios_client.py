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

from common.utils import run_cmd, popen_cmd
from common.packaging import is_packaged, get_base_dir

logger = logging.getLogger(__name__)


class GoIOSClient:
    """go-ios CLI 客户端。"""

    def __init__(
        self,
        go_ios_path: str,
        agent_port: int = 60105,
        timeout: int = 30,
    ):
        """
        初始化 GoIOSClient。

        Args:
            go_ios_path: go-ios 可执行文件路径（相对于 exe 目录或绝对路径）
            agent_port: go-ios agent HTTP API 端口（默认 60105）
            timeout: 命令执行超时时间（秒）
        """
        self._go_ios_path = self._resolve_path(go_ios_path)
        self.agent_port = agent_port
        self.agent_host = "127.0.0.1"
        self.timeout = timeout
        self._http_client: Optional[httpx.Client] = None

    def _resolve_path(self, path: str) -> str:
        """解析 go-ios 路径（支持相对路径和绝对路径）。"""
        if os.path.isabs(path):
            return path
        # 打包模式下相对于 exe 目录
        return os.path.join(get_base_dir(), path)

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
        result = run_cmd(
            cmd,
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

    def check_process_alive(self, process: subprocess.Popen) -> bool:
        """检查进程是否存活（用于判断 WDA/端口转发进程启动成功）。

        Args:
            process: 进程对象

        Returns:
            bool: True 表示进程存活（启动成功），False 表示进程已退出（启动失败）
        """
        if process is None:
            return False
        # poll() 返回 None 表示进程仍在运行，返回退出码表示已退出
        is_alive = process.poll() is None
        logger.info(f"Process alive check: PID={process.pid}, alive={is_alive}, exit_code={process.poll()}")
        return is_alive

    def wait_process_alive(self, process: subprocess.Popen, timeout: int = 5) -> bool:
        """等待进程稳定运行（用于判断 WDA 进程启动成功）。

        Args:
            process: 进程对象
            timeout: 等待时间（秒）

        Returns:
            bool: True 表示进程存活，False 表示进程已退出
        """
        start = time.time()
        while time.time() - start < timeout:
            if not self.check_process_alive(process):
                logger.warning(f"Process {process.pid} exited during wait period")
                return False
            time.sleep(0.5)
        logger.info(f"Process {process.pid} is stable after {timeout}s wait")
        return True

    def check_port_forward_ready(self, local_port: int, timeout: int = 2) -> bool:
        """检查端口转发是否就绪（通过检查本地端口是否被监听）。

        Args:
            local_port: 本地端口
            timeout: 超时时间（秒）

        Returns:
            bool: True 表示端口已监听，False 表示端口未监听
        """
        import socket
        start = time.time()
        while time.time() - start < timeout:
            try:
                sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                sock.settimeout(1)
                result = sock.connect_ex(('127.0.0.1', local_port))
                sock.close()
                if result == 0:
                    logger.info(f"Port forward ready check: port {local_port} is listening")
                    return True
            except Exception as e:
                logger.debug(f"Port forward check error: {e}")
            time.sleep(0.5)
        logger.warning(f"Port forward ready check: port {local_port} is not listening after {timeout}s")
        return False

    # ========== Agent 管理 ==========

    def start_agent(self) -> subprocess.Popen:
        """启动 go-ios agent（后台进程，使用 userspace TUN 模式避免 IPv6 问题）。"""
        # 使用 --userspace 参数启动 agent，避免 Windows IPv6 路由配置问题
        cmd = [self._go_ios_path, "tunnel", "start", "--userspace"]
        # 独立进程标志（popen_cmd 会自动合并隐藏窗口标志）
        creationflags = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
        # stdin/stdout/stderr 都设置为 DEVNULL，确保进程完全独立，不依赖父进程
        process = popen_cmd(
            cmd,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=creationflags,
        )
        logger.info(f"go-ios agent started (PID: {process.pid}, userspace mode)")
        return process

    def check_agent_health(self) -> bool:
        """检查 agent 进程是否运行（/health 接口）。"""
        try:
            if not self._http_client:
                self._http_client = httpx.Client(timeout=5, trust_env=False)
            resp = self._http_client.get(f"http://{self.agent_host}:{self.agent_port}/health")
            logger.debug(f"Agent health check: status={resp.status_code}, url=http://{self.agent_host}:{self.agent_port}/health")
            return resp.status_code == 200
        except Exception as e:
            logger.info(f"Agent health check failed: {e}, url=http://{self.agent_host}:{self.agent_port}/health")
            return False

    def check_agent_ready(self) -> bool:
        """检查 agent 是否就绪（/ready 接口，包括设备连接）。"""
        try:
            if not self._http_client:
                self._http_client = httpx.Client(timeout=5, trust_env=False)
            resp = self._http_client.get(f"http://{self.agent_host}:{self.agent_port}/ready")
            logger.debug(f"Agent ready check: status={resp.status_code}, url=http://{self.agent_host}:{self.agent_port}/ready")
            return resp.status_code == 200
        except Exception as e:
            logger.info(f"Agent ready check failed: {e}, url=http://{self.agent_host}:{self.agent_port}/ready")
            return False

    def wait_agent_ready(self, timeout: int = 30) -> bool:
        """等待 agent 就绪。"""
        start = time.time()
        logger.info(f"Waiting for agent ready (timeout={timeout}s)...")
        while time.time() - start < timeout:
            if self.check_agent_ready():
                elapsed = int(time.time() - start)
                logger.info(f"Agent ready after {elapsed}s")
                return True
            time.sleep(1)
        logger.warning(f"Agent not ready after {timeout}s timeout")
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
                self._http_client = httpx.Client(timeout=5, trust_env=False)
            url = f"http://{self.agent_host}:{self.agent_port}/tunnel/{udid}"
            resp = self._http_client.get(url)
            logger.info(f"Get tunnel info: status={resp.status_code}, udid={udid}, url={url}")
            if resp.status_code == 200:
                data = resp.json()
                logger.info(f"Tunnel info for {udid}: address={data.get('address')}, rsdPort={data.get('rsdPort')}")
                return data
            return None
        except Exception as e:
            logger.warning(f"Failed to get tunnel info for {udid}: {e}")
            return None

    def list_tunnels(self) -> list[dict]:
        """列出所有已建立的 tunnel。"""
        try:
            if not self._http_client:
                self._http_client = httpx.Client(timeout=5, trust_env=False)
            url = f"http://{self.agent_host}:{self.agent_port}/tunnels"
            resp = self._http_client.get(url)
            logger.info(f"List tunnels: status={resp.status_code}, url={url}")
            if resp.status_code == 200:
                tunnels = resp.json()
                logger.info(f"Found {len(tunnels)} active tunnels")
                return tunnels
            return []
        except Exception as e:
            logger.warning(f"Failed to list tunnels: {e}")
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
            logger.warning("go-ios list --details returned no data")
            return []
        devices = data.get("deviceList", [])
        logger.debug(f"go-ios found {len(devices)} devices in deviceList")
        result = []
        for d in devices:
            # go-ios list --details 返回的结构
            udid = d.get("Udid", "")
            if udid:
                result.append({
                    "udid": udid,
                    "name": d.get("DeviceName", ""),
                    "version": d.get("ProductVersion", ""),
                    "model": d.get("ProductType", ""),
                    "device_id": d.get("DeviceID", 0),
                })
        logger.info(f"go-ios list_devices found {len(result)} devices")
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
        testrunner_bundle_id: str = "",
        xctest_config: str = "WebDriverAgentRunner.xctest",
        address: Optional[str] = None,
        rsd_port: Optional[int] = None,
    ) -> subprocess.Popen:
        """
        启动 WDA（后台进程）。

        Args:
            udid: 设备 UDID
            bundle_id: WDA bundle ID (--bundleid)
            testrunner_bundle_id: Test Runner Bundle ID (--testrunnerbundleid)
            xctest_config: XCTest Config (--xctestconfig)
            address: iOS 17+ tunnel 地址（可选，不传则 go-ios 自动从 agent 获取）
            rsd_port: iOS 17+ tunnel RSD 端口（可选，不传则 go-ios 自动从 agent 获取）

        Returns:
            subprocess.Popen: WDA 进程
        """
        args = ["--udid", udid, "runwda", "--bundleid", bundle_id]
        # 添加 go-ios runwda 必需的参数
        if testrunner_bundle_id:
            args.extend(["--testrunnerbundleid", testrunner_bundle_id])
        if xctest_config:
            args.extend(["--xctestconfig", xctest_config])
        # 注意：不手动传递 address 和 rsd_port，让 go-ios 自动从 agent HTTP API 获取
        # 这样可以避免 IPv6 地址变化导致连接失败的问题
        cmd = [self._go_ios_path] + args
        logger.info(f"WDA command: {' '.join(cmd)}")
        # 使用 popen_cmd 统一处理黑框问题
        creationflags = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
        # stdin/stdout/stderr 都设置为 DEVNULL，确保进程完全独立
        process = popen_cmd(
            cmd,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=creationflags,
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
            address: iOS 17+ tunnel 地址（可选，不传则 go-ios 自动从 agent 获取）
            rsd_port: iOS 17+ tunnel RSD 端口（可选，不传则 go-ios 自动从 agent 获取）

        Returns:
            subprocess.Popen: 端口转发进程
        """
        args = ["--udid", udid, "forward", str(local_port), str(device_port)]
        # 注意：不手动传递 address 和 rsd_port，让 go-ios 自动从 agent HTTP API 获取
        cmd = [self._go_ios_path] + args
        logger.info(f"Forward command: {' '.join(cmd)}")
        # 使用 popen_cmd 统一处理黑框问题
        creationflags = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
        # stdin/stdout/stderr 都设置为 DEVNULL，确保进程完全独立
        process = popen_cmd(
            cmd,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=creationflags,
        )
        logger.info(f"Port forward started: {local_port} -> {device_port} (PID: {process.pid})")
        return process

    # ========== 应用管理 ==========

    def launch_app(self, udid: str, bundle_id: str) -> bool:
        """启动应用。"""
        args = ["--udid", udid, "launch", bundle_id]
        result = self._run_cmd(args, check=False)
        return result.returncode == 0

    def kill_app(self, udid: str, bundle_id: str) -> bool:
        """关闭应用。"""
        args = ["--udid", udid, "kill", bundle_id]
        result = self._run_cmd(args, check=False)
        return result.returncode == 0

    def get_processes(self, udid: str) -> list[dict]:
        """获取运行的应用进程。"""
        args = ["--udid", udid, "ps", "--apps"]
        data = self._run_cmd_json(args)
        if not data:
            return []
        return data