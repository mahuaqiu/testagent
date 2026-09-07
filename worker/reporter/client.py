"""
平台上报客户端。
"""

import logging
from typing import Optional, Dict, List

import httpx

from worker.config import WorkerConfig

logger = logging.getLogger(__name__)


class Reporter:
    """
    平台上报客户端。

    负责向配置平台上报 Worker 状态和设备注册信息。
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

        self._client = httpx.Client(timeout=10.0,trust_env=False)
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
        namespace: str,
        version: Optional[str] = None,
        config_version: Optional[str] = None,
        scripts: Optional[Dict[str, str]] = None,
    ) -> bool:
        """
        调用设备注册接口（新格式）。

        Args:
            ip: 机器 IP 地址
            port: 机器端口
            devices: 设备列表，key 为 device_type，value 为 device_sn 列表
            version: 机器版本（可选）
            config_version: 配置版本（可选）
            scripts: 脚本版本信息（可选）

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
                "namespace": namespace,
                "version": version,
                "devices": devices,
                "config_version": config_version,
                "scripts": scripts or {},
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

    def close(self):
        """关闭客户端连接。"""
        self._client.close()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()
