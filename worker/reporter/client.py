"""
平台上报客户端。
"""

import logging
from typing import Optional, Dict, Any, List

import httpx

from worker.config import WorkerConfig
from worker.reporter.models import WorkerReport, HeartbeatReport, DeviceChangeEvent

logger = logging.getLogger(__name__)


class Reporter:
    """
    平台上报客户端。

    负责向配置平台上报 Worker 状态、设备信息、心跳等。
    """

    def __init__(self, config: WorkerConfig):
        """
        初始化上报客户端。

        Args:
            config: Worker 配置
        """
        self.config = config
        self.platform_api = config.platform_api
        self.worker_id = config.id
        self.namespace = config.namespace

        self._client = httpx.Client(timeout=10.0)
        self._enabled = bool(self.platform_api)

        if not self._enabled:
            logger.warning("Platform API not configured, reporting disabled")

    @property
    def enabled(self) -> bool:
        """上报是否启用。"""
        return self._enabled

    def register_env(
        self,
        ip: str,
        port: int,
        devices: Dict[str, List[str]],
        version: Optional[str] = None,
    ) -> bool:
        """
        调用设备注册接口（新格式）。

        Args:
            ip: 机器 IP 地址
            port: 机器端口
            devices: 设备列表，key 为 device_type，value 为 device_sn 列表
            version: 机器版本（可选）

        Returns:
            bool: 注册是否成功
        """
        if not self._enabled:
            logger.debug("Reporting disabled, skipping env register")
            return True

        try:
            url = f"{self.platform_api}/api/core/env/register"
            payload = {
                "ip": ip,
                "port": str(port),
                "namespace": self.namespace,
                "version": version,
                "devices": devices,
            }

            response = self._client.post(
                url,
                json=payload,
            )
            response.raise_for_status()

            result = response.json()
            if result.get("status") == "success":
                logger.info(f"Env register sent successfully to {url}")
                return True
            else:
                logger.error(f"Env register failed: {result.get('result', 'Unknown error')}")
                return False

        except httpx.HTTPStatusError as e:
            logger.error(f"Failed to register env (HTTP {e.response.status_code}): {e}")
            return False

        except Exception as e:
            logger.error(f"Failed to register env: {e}")
            return False

    def report_full(self, report: WorkerReport) -> bool:
        """
        全量上报 Worker 信息。

        Args:
            report: Worker 上报数据

        Returns:
            bool: 上报是否成功
        """
        if not self._enabled:
            logger.debug("Reporting disabled, skipping full report")
            return True

        try:
            url = f"{self.platform_api}/register"
            response = self._client.post(
                url,
                json=report.to_dict(),
            )
            response.raise_for_status()

            logger.info(f"Full report sent successfully to {url}")
            return True

        except httpx.HTTPStatusError as e:
            logger.error(f"Failed to report (HTTP {e.response.status_code}): {e}")
            return False

        except Exception as e:
            logger.error(f"Failed to report: {e}")
            return False

    def report_heartbeat(self, heartbeat: HeartbeatReport) -> bool:
        """
        心跳上报。

        Args:
            heartbeat: 心跳数据

        Returns:
            bool: 上报是否成功
        """
        if not self._enabled:
            return True

        try:
            url = f"{self.platform_api}/heartbeat"
            response = self._client.post(
                url,
                json=heartbeat.to_dict(),
            )
            response.raise_for_status()

            logger.debug(f"Heartbeat sent to {url}")
            return True

        except Exception as e:
            logger.warning(f"Heartbeat failed: {e}")
            return False

    def report_device_change(self, event: DeviceChangeEvent) -> bool:
        """
        设备变化上报。

        Args:
            event: 设备变化事件

        Returns:
            bool: 上报是否成功
        """
        if not self._enabled:
            return True

        try:
            url = f"{self.platform_api}/device/change"
            response = self._client.post(
                url,
                json=event.to_dict(),
            )
            response.raise_for_status()

            logger.info(f"Device change reported: {event.event_type} {event.platform}")
            return True

        except Exception as e:
            logger.error(f"Failed to report device change: {e}")
            return False

    def report_devices(self, data: dict) -> bool:
        """
        使用新格式上报设备信息。

        Args:
            data: 设备信息数据（包含 ip, port, devices）

        Returns:
            bool: 上报是否成功
        """
        if not self._enabled:
            logger.debug("Reporting disabled, skipping devices report")
            return True

        try:
            url = f"{self.platform_api}/devices"
            response = self._client.post(
                url,
                json=data,
            )
            response.raise_for_status()

            logger.info(f"Devices report sent successfully to {url}")
            return True

        except httpx.HTTPStatusError as e:
            logger.error(f"Failed to report devices (HTTP {e.response.status_code}): {e}")
            return False

        except Exception as e:
            logger.error(f"Failed to report devices: {e}")
            return False

    def unregister(self) -> bool:
        """
        注销 Worker。

        Returns:
            bool: 注销是否成功
        """
        if not self._enabled:
            return True

        try:
            url = f"{self.platform_api}/unregister"
            response = self._client.delete(
                url,
                params={"worker_id": self.worker_id},
            )
            response.raise_for_status()

            logger.info(f"Worker unregistered: {self.worker_id}")
            return True

        except Exception as e:
            logger.error(f"Failed to unregister: {e}")
            return False

    def close(self):
        """关闭客户端连接。"""
        self._client.close()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()