"""
鸿蒙平台执行引擎。

基于 HDC（HarmonyOS Device Connector）直连实现，支持 OCR/图像识别定位。
"""

import logging
import time
import tempfile
import os
from typing import Any, Optional

from worker.actions import ActionRegistry
from worker.config import PlatformConfig
from worker.platforms.base import PlatformManager
from worker.platforms.harmony_hdc import (
    HarmonyHdcWrapper,
    HarmonyError,
    DeviceNotFoundError,
    list_devices,
    _find_hdc_path,
)
from worker.task import Action, ActionResult, ActionStatus

logger = logging.getLogger(__name__)


class HarmonyPlatformManager(PlatformManager):
    """
    鸿蒙平台管理器。

    使用 HDC 直连控制鸿蒙设备。
    """

    SUPPORTED_ACTIONS: set[str] = {"start_app", "stop_app", "unlock_screen"}

    # 鸿蒙按键映射（KeyCode 参考鸿蒙 KeyEvent）
    KEY_MAP = {
        # 导航键
        "HOME": 1,
        "BACK": 2,
        "MENU": 2067,
        # 电源键
        "POWER": 18,
        # 音量键
        "VOLUME_UP": 16,
        "VOLUMEDOWN": 17,
        "VOLUME_MUTE": 22,
        # 功能键
        "ENTER": 2054,
        # 方向键
        "DPAD_UP": 2012,
        "DPAD_DOWN": 2013,
        "DPAD_LEFT": 2014,
        "DPAD_RIGHT": 2015,
        "DPAD_CENTER": 2016,
    }

    def __init__(self, config: PlatformConfig, ocr_client=None, unlock_config=None):
        """
        初始化鸿蒙平台管理器。

        Args:
            config: 平台配置
            ocr_client: OCR 客户端
            unlock_config: 解锁配置（可选）
        """
        super().__init__(config, ocr_client)
        self._device_wrappers: dict[str, HarmonyHdcWrapper] = {}
        self._hdc_path: Optional[str] = None
        self._unlock_config = unlock_config or {}  # 解锁配置

    @property
    def platform(self) -> str:
        """平台名称。"""
        return "harmony"

    def start(self) -> None:
        """
        启动鸿蒙平台。

        检查 HDC 工具是否可用。
        """
        if self._started:
            return

        # 查找 HDC 工具路径
        self._hdc_path = _find_hdc_path()

        if self._hdc_path is None:
            logger.warning("HDC 工具未找到，鸿蒙平台可能不可用")
        else:
            logger.info(f"HDC 工具已就绪: {self._hdc_path}")

        self._started = True
        logger.info("Harmony platform started")

    def stop(self) -> None:
        """
        停止鸿蒙平台。

        清理所有设备连接。
        """
        self._device_wrappers.clear()
        self._started = False
        logger.info("Harmony platform stopped")

    def is_available(self) -> bool:
        """
        检查平台是否可用。

        Returns:
            bool: 平台是否可用（HDC 工具存在）
        """
        return self._started and self._hdc_path is not None

    # ========== 设备服务管理 ==========

    def ensure_device_service(self, udid: str) -> tuple[str, str]:
        """
        确保设备服务可用（由 DeviceMonitor 调用）。

        Args:
            udid: 设备 UDID（序列号）

        Returns:
            tuple[str, str]: (status, message) - status 为 "online" 或 "faulty"
        """
        try:
            # 尝试获取或创建设备 wrapper
            wrapper = self._device_wrappers.get(udid)

            if wrapper:
                # 检查现有连接是否有效
                if wrapper.is_online():
                    return ("online", "OK")
                else:
                    # 连接失效，移除旧的 wrapper
                    del self._device_wrappers[udid]

            # 创建新的 wrapper
            wrapper = HarmonyHdcWrapper(udid, self._hdc_path)
            self._device_wrappers[udid] = wrapper

            logger.info(f"Harmony device service ready: {udid}")
            return ("online", "OK")

        except DeviceNotFoundError as e:
            logger.error(f"设备未找到: {udid}, {e}")
            return ("faulty", str(e))
        except HarmonyError as e:
            logger.error(f"设备服务初始化失败: {udid}, {e}")
            return ("faulty", str(e))
        except Exception as e:
            logger.error(f"Failed to ensure device service: {udid}, {e}")
            return ("faulty", str(e))

    def mark_device_faulty(self, udid: str) -> None:
        """
        标记设备为异常。

        Args:
            udid: 设备 UDID（序列号）
        """
        if udid in self._device_wrappers:
            del self._device_wrappers[udid]
        logger.info(f"Harmony device marked faulty: {udid}")

    def get_online_devices(self) -> list[str]:
        """
        获取在线设备列表。

        Returns:
            list[str]: 在线设备 UDID（序列号）列表
        """
        if not self._hdc_path:
            logger.warning("HDC 工具未找到，无法列出设备")
            return []

        try:
            devices = list_devices(self._hdc_path)
            logger.debug(f"在线鸿蒙设备: {devices}")
            return devices
        except Exception as e:
            logger.error(f"获取在线设备列表失败: {e}")
            return []

    # ========== 执行上下文管理 ==========

    def create_context(self, device_id: Optional[str] = None, options: Optional[dict] = None) -> Any:
        """
        创建执行上下文。

        Args:
            device_id: 设备 ID（序列号，鸿蒙平台必填）
            options: 其他选项（可选）

        Returns:
            HarmonyHdcWrapper: HDC wrapper 实例

        Raises:
            ValueError: 未提供 device_id
            DeviceNotFoundError: 设备未在线
        """
        if not device_id:
            raise ValueError("鸿蒙平台必须提供 device_id")

        # 尝试获取已有的 wrapper
        wrapper = self._device_wrappers.get(device_id)

        if wrapper and wrapper.is_online():
            logger.debug(f"使用已有的设备 wrapper: {device_id}")
            return wrapper

        # 创建新的 wrapper
        try:
            wrapper = HarmonyHdcWrapper(device_id, self._hdc_path)
            self._device_wrappers[device_id] = wrapper
            logger.info(f"创建新的设备 wrapper: {device_id}")
            return wrapper
        except HarmonyError as e:
            logger.error(f"创建设备 wrapper 失败: {device_id}, {e}")
            raise

    def close_context(self, context: Any, close_session: bool = False) -> None:
        """
        关闭执行上下文。

        Args:
            context: 执行上下文（HarmonyHdcWrapper）
            close_session: 是否关闭整个会话（True=移除 wrapper，False=保持 wrapper）
        """
        if not isinstance(context, HarmonyHdcWrapper):
            logger.warning(f"无效的上下文类型: {type(context)}")
            return

        if close_session:
            # 移除 wrapper
            serial = context.serial
            if serial in self._device_wrappers:
                del self._device_wrappers[serial]
                logger.info(f"关闭设备会话: {serial}")
        else:
            # 保持 wrapper 用于后续任务复用
            logger.debug(f"保持设备 wrapper 用于复用: {context.serial}")