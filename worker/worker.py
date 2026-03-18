"""
Worker 主服务。

负责管理设备发现、平台管理器、任务调度、平台上报等核心功能。
"""

import base64
import logging
import sys
import threading
import time
import traceback
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Dict, List, Optional

from worker.config import WorkerConfig, PlatformConfig
from worker.discovery.host import HostDiscoverer, HostInfo
from worker.discovery.android import AndroidDiscoverer, AndroidDeviceInfo
from worker.discovery.ios import iOSDiscoverer, iOSDeviceInfo
from worker.reporter import Reporter, WorkerReport, WorkerCapabilities, DesktopInfo
from worker.platforms.base import PlatformManager
from worker.platforms.web import WebPlatformManager
from worker.platforms.android import AndroidPlatformManager
from worker.platforms.ios import iOSPlatformManager
from worker.platforms.windows import WindowsPlatformManager
from worker.platforms.mac import MacPlatformManager
from worker.task import Task, TaskResult, TaskStatus, ActionResult, ActionStatus
from worker.task.store import TaskStore, TaskEntry

from common.ocr_client import OCRClient, get_ocr_client

logger = logging.getLogger(__name__)


@dataclass
class WorkerStatus:
    """Worker 状态。"""

    worker_id: str
    status: str  # online / busy / offline
    started_at: datetime
    supported_platforms: List[str]
    devices_count: int


class TaskScheduler:
    """
    任务调度器。

    管理任务并发执行：
    - Windows/Mac/Web：全局单任务
    - Android/iOS：按设备并行
    """

    def __init__(self):
        # 平台全局锁
        self.platform_locks = {
            "windows": threading.Lock(),
            "mac": threading.Lock(),
            "web": threading.Lock(),
        }
        # 设备锁（动态创建）
        self.device_locks: Dict[str, threading.Lock] = {}
        self._lock = threading.Lock()

    def acquire(self, platform: str, device_id: Optional[str] = None, blocking: bool = True, timeout: float = -1) -> bool:
        """
        获取执行锁。

        Args:
            platform: 平台名称
            device_id: 设备 ID
            blocking: 是否阻塞等待
            timeout: 超时时间

        Returns:
            bool: 是否成功获取
        """
        lock = self._get_lock(platform, device_id)
        # blocking=False 时忽略 timeout 参数，直接非阻塞获取
        if not blocking:
            return lock.acquire(blocking=False)
        # blocking=True 时，timeout > 0 用实际值，否则 None 表示无限等待
        return lock.acquire(blocking=True, timeout=timeout if timeout > 0 else None)

    def release(self, platform: str, device_id: Optional[str] = None) -> None:
        """释放执行锁。"""
        lock = self._get_lock(platform, device_id)
        try:
            lock.release()
        except RuntimeError:
            pass  # 锁已被释放

    def _get_lock(self, platform: str, device_id: Optional[str]) -> threading.Lock:
        """获取对应的锁。"""
        if platform in self.platform_locks:
            return self.platform_locks[platform]
        elif device_id:
            with self._lock:
                if device_id not in self.device_locks:
                    self.device_locks[device_id] = threading.Lock()
                return self.device_locks[device_id]
        else:
            raise ValueError(f"device_id is required for platform: {platform}")


class Worker:
    """
    Worker 主服务。

    管理设备发现、平台管理器、任务执行、平台上报。
    """

    def __init__(self, config: WorkerConfig):
        """
        初始化 Worker。

        Args:
            config: Worker 配置
        """
        self.config = config
        self.worker_id = config.id
        self.port = config.port

        # 状态
        self._status = "offline"
        self._started = False
        self._started_at: Optional[datetime] = None

        # 宿主机信息
        self.host_info: Optional[HostInfo] = None
        self.supported_platforms: List[str] = []

        # 设备信息
        self.android_devices: List[AndroidDeviceInfo] = []
        self.ios_devices: List[iOSDeviceInfo] = []

        # 平台管理器
        self.platform_managers: Dict[str, PlatformManager] = {}

        # 任务调度器
        self.scheduler = TaskScheduler()

        # 任务存储（异步任务管理）
        self.task_store = TaskStore()

        # 上报客户端
        self.reporter: Optional[Reporter] = None

        # OCR 客户端
        self.ocr_client: Optional[OCRClient] = None

        # 后台线程
        self._device_monitor_thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()

    @property
    def status(self) -> str:
        return self._status

    def start(self) -> None:
        """启动 Worker。"""
        if self._started:
            logger.warning("Worker already started")
            return

        logger.info(f"Starting Worker {self.worker_id}...")

        # 1. 环境发现
        self._discover_environment()

        # 2. 初始化 OCR 客户端
        self._init_ocr_client()

        # 3. 初始化平台管理器
        self._init_platform_managers()

        # 4. 初始化上报客户端
        self._init_reporter()

        # 5. 上报初始状态
        self._report_devices()

        # 6. 启动设备监控
        self._start_device_monitor()

        self._status = "online"
        self._started = True
        self._started_at = datetime.now()

        logger.info(f"Worker {self.worker_id} started, supported platforms: {self.supported_platforms}")

    def stop(self) -> None:
        """停止 Worker。"""
        if not self._started:
            return

        logger.info(f"Stopping Worker {self.worker_id}...")

        # 停止设备监控
        self._stop_device_monitor()

        # 停止平台管理器
        for platform, manager in self.platform_managers.items():
            try:
                manager.stop()
            except Exception as e:
                logger.error(f"Failed to stop {platform} platform: {e}\n{traceback.format_exc()}")

        # 注销上报
        if self.reporter:
            try:
                self.reporter.unregister()
            except Exception as e:
                logger.warning(f"Failed to unregister: {e}\n{traceback.format_exc()}")
            self.reporter.close()

        # 关闭 OCR 客户端
        if self.ocr_client:
            self.ocr_client.close()

        self._status = "offline"
        self._started = False

        logger.info(f"Worker {self.worker_id} stopped")

    def _discover_environment(self) -> None:
        """发现宿主机环境。"""
        # 发现宿主机信息
        self.host_info = HostDiscoverer.discover()

        # 根据操作系统决定支持的平台
        self.supported_platforms = HostDiscoverer.get_supported_platforms()

        # 发现移动设备（仅 Windows）
        if self.host_info.os_type == "windows":
            self._discover_mobile_devices()

        logger.info(f"Host: {self.host_info.hostname} ({self.host_info.os_type})")
        logger.info(f"Supported platforms: {self.supported_platforms}")

    def _discover_mobile_devices(self) -> None:
        """发现移动设备。"""
        # Android 设备
        if AndroidDiscoverer.check_adb_available():
            self.android_devices = AndroidDiscoverer.discover()
            logger.info(f"Found {len(self.android_devices)} Android devices")
        else:
            logger.warning("ADB not available, skipping Android device discovery")

        # iOS 设备
        if iOSDiscoverer.check_libimobiledevice_available():
            self.ios_devices = iOSDiscoverer.discover()
            logger.info(f"Found {len(self.ios_devices)} iOS devices")
        else:
            logger.warning("libimobiledevice not available, skipping iOS device discovery")

    def _init_ocr_client(self) -> None:
        """初始化 OCR 客户端。"""
        try:
            self.ocr_client = OCRClient(
                base_url=self.config.ocr_service,
            )
            logger.info(f"OCR client initialized: {self.config.ocr_service}")
        except Exception as e:
            logger.warning(f"Failed to initialize OCR client: {e}\n{traceback.format_exc()}")

    def _init_platform_managers(self) -> None:
        """初始化平台管理器。"""
        for platform in self.supported_platforms:
            platform_config = PlatformConfig.from_dict(
                self.config.get_platform_config(platform)
            )

            try:
                if platform == "web":
                    manager = WebPlatformManager(platform_config, self.ocr_client)
                elif platform == "android":
                    manager = AndroidPlatformManager(platform_config, self.ocr_client)
                elif platform == "ios":
                    manager = iOSPlatformManager(platform_config, self.ocr_client)
                elif platform == "windows":
                    manager = WindowsPlatformManager(platform_config, self.ocr_client)
                elif platform == "mac":
                    manager = MacPlatformManager(platform_config, self.ocr_client)
                else:
                    continue

                self.platform_managers[platform] = manager
                logger.info(f"Platform manager initialized: {platform}")

            except Exception as e:
                logger.error(f"Failed to initialize {platform} platform: {e}\n{traceback.format_exc()}")

    def _init_reporter(self) -> None:
        """初始化上报客户端。"""
        if self.config.platform_api:
            self.reporter = Reporter(self.config)
            logger.info(f"Reporter initialized: {self.config.platform_api}")

    def _report_full(self) -> None:
        """全量上报。"""
        if not self.reporter:
            return

        # 构建设备列表
        devices = []

        # 移动设备
        for device in self.android_devices:
            devices.append(device)
        for device in self.ios_devices:
            devices.append(device)

        # 桌面信息
        if self.host_info:
            desktop = DesktopInfo(
                platform=self.host_info.os_type,
                resolution=self.host_info.display_resolution,
                scale=self.host_info.display_scale,
            )
            devices.append(desktop)

        # 构建能力
        capabilities = WorkerCapabilities(
            has_ocr=self.ocr_client is not None,
            browsers=["chromium", "firefox", "webkit"] if "web" in self.supported_platforms else [],
            max_sessions=5,
            image_matching=True,
        )

        # 构建上报数据
        report = WorkerReport(
            worker_id=self.worker_id,
            hostname=self.host_info.hostname if self.host_info else "unknown",
            ip_addresses=self.host_info.ip_addresses if self.host_info else [],
            os_type=self.host_info.os_type if self.host_info else "unknown",
            os_version=self.host_info.os_version if self.host_info else "unknown",
            supported_platforms=self.supported_platforms,
            status=self._status,
            port=self.port,
            devices=devices,
            capabilities=capabilities,
        )

        self.reporter.report_full(report)

    def _report_devices(self) -> None:
        """
        使用新格式上报设备信息。

        用于定期上报和设备变化时上报。
        """
        if not self.reporter:
            return

        data = self.get_devices()
        self.reporter.report_devices(data)

    def _start_device_monitor(self) -> None:
        """启动设备监控线程。"""
        if self.host_info and self.host_info.os_type != "windows":
            return  # 仅 Windows 需要监控移动设备

        self._stop_event.clear()
        self._device_monitor_thread = threading.Thread(
            target=self._device_monitor_loop,
            daemon=True,
        )
        self._device_monitor_thread.start()
        logger.info("Device monitor started")

    def _stop_device_monitor(self) -> None:
        """停止设备监控线程。"""
        self._stop_event.set()
        if self._device_monitor_thread:
            self._device_monitor_thread.join(timeout=5)
        logger.info("Device monitor stopped")

    def _device_monitor_loop(self) -> None:
        """设备监控循环。"""
        interval = self.config.device_check_interval

        while not self._stop_event.is_set():
            self._stop_event.wait(interval)

            if self._stop_event.is_set():
                break

            try:
                self._check_device_changes()
            except Exception as e:
                logger.error(f"Device monitor error: {e}\n{traceback.format_exc()}")

    def _check_device_changes(self) -> None:
        """检查设备变化。"""
        changes = []

        # 检查 Android 设备
        if AndroidDiscoverer.check_adb_available():
            new_devices = AndroidDiscoverer.discover()
            changes.extend(self._compare_devices("android", self.android_devices, new_devices))
            self.android_devices = new_devices

        # 检查 iOS 设备
        if iOSDiscoverer.check_libimobiledevice_available():
            new_devices = iOSDiscoverer.discover()
            changes.extend(self._compare_devices("ios", self.ios_devices, new_devices))
            self.ios_devices = new_devices

        # 如果有变化，上报
        if changes and self.reporter:
            self._report_devices()
            logger.info(f"Device changes detected: {len(changes)} changes")

    def _compare_devices(self, platform: str, old_list: List, new_list: List) -> List[str]:
        """比较设备列表变化。"""
        changes = []

        old_udids = {d.udid for d in old_list}
        new_udids = {d.udid for d in new_list}

        # 新增设备
        added = new_udids - old_udids
        if added:
            changes.extend([f"+{platform}:{udid}" for udid in added])

        # 移除设备
        removed = old_udids - new_udids
        if removed:
            changes.extend([f"-{platform}:{udid}" for udid in removed])

        return changes

    # ========== API 方法 ==========

    def get_status(self) -> WorkerStatus:
        """获取 Worker 状态。"""
        devices_count = len(self.android_devices) + len(self.ios_devices)

        return WorkerStatus(
            worker_id=self.worker_id,
            status=self._status,
            started_at=self._started_at or datetime.now(),
            supported_platforms=self.supported_platforms,
            devices_count=devices_count,
        )

    def get_devices(self) -> Dict[str, Any]:
        """
        获取设备信息（新格式）。

        Returns:
            Dict: 包含 ip, port, devices 的字典
        """
        devices: Dict[str, List[str]] = {}

        # 1. 根据操作系统添加桌面平台
        if self.host_info:
            if self.host_info.os_type == "windows":
                devices["windows"] = []
                devices["web"] = []
            elif self.host_info.os_type == "macos":
                devices["mac"] = []

        # 2. Android 设备（返回设备标识列表）
        if self.android_devices:
            devices["android"] = [d.udid for d in self.android_devices]

        # 3. iOS 设备（返回 UDID 列表）
        if self.ios_devices:
            devices["ios"] = [d.udid for d in self.ios_devices]

        # 4. 获取本机 IP
        ip = "unknown"
        if self.host_info and self.host_info.ip_addresses:
            ip = self.host_info.ip_addresses[0]

        return {
            "ip": ip,
            "port": self.port,
            "devices": devices,
        }

    def refresh_devices(self) -> Dict[str, List]:
        """刷新设备列表。"""
        self._discover_mobile_devices()
        return self.get_devices()

    def _validate_task(self, task: Task, manager: PlatformManager) -> Optional[TaskResult]:
        """
        验证任务。

        Args:
            task: 任务对象
            manager: 平台管理器

        Returns:
            TaskResult | None: 验证失败返回错误结果，通过返回 None
        """
        # 1. 平台支持验证
        if task.platform not in self.supported_platforms:
            return TaskResult(
                task_id=task.task_id,
                status=TaskStatus.FAILED,
                platform=task.platform,
                error=f"Platform not supported: {task.platform}",
            )

        # 2. device_id 验证（移动端必填）
        if task.platform in ["android", "ios"]:
            if not task.device_id:
                return TaskResult(
                    task_id=task.task_id,
                    status=TaskStatus.FAILED,
                    platform=task.platform,
                    error=f"device_id is required for {task.platform} platform",
                )

            # 验证设备是否连接
            if task.platform == "android":
                device_ids = [d.udid for d in self.android_devices]
            else:
                device_ids = [d.udid for d in self.ios_devices]

            if task.device_id not in device_ids:
                return TaskResult(
                    task_id=task.task_id,
                    status=TaskStatus.FAILED,
                    platform=task.platform,
                    error=f"Device not found: {task.device_id}",
                )

        # 3. action_type 验证
        supported_actions = manager.get_supported_actions()
        for i, action in enumerate(task.actions):
            action_type = action.action_type
            if action_type not in supported_actions:
                return TaskResult(
                    task_id=task.task_id,
                    status=TaskStatus.FAILED,
                    platform=task.platform,
                    error=f"Action not supported: {action_type} on {task.platform}",
                )

        return None

    def _needs_context(self, task: Task) -> bool:
        """
        检查任务是否需要创建 context。

        stop_app 动作不需要 context。
        """
        if not task.actions:
            return True
        # 如果所有动作都是不需要 context 的，则跳过
        no_context_actions = {"stop_app"}
        return not all(a.action_type in no_context_actions for a in task.actions)

    def execute_task(self, task: Task) -> TaskResult:
        """
        执行任务。

        Args:
            task: 任务对象

        Returns:
            TaskResult: 任务结果
        """
        platform = task.platform
        logger.info(
            f"Task started: task_id={task.task_id}, platform={platform}, "
            f"device_id={task.device_id}"
        )

        # 获取平台管理器
        manager = self.platform_managers.get(platform)
        if not manager:
            return TaskResult(
                task_id=task.task_id,
                status=TaskStatus.FAILED,
                platform=platform,
                error=f"Platform manager not available: {platform}",
            )

        # 前置验证
        validation_result = self._validate_task(task, manager)
        if validation_result:
            return validation_result

        # 启动平台（如果未启动）
        if not manager.is_available():
            try:
                manager.start()
            except Exception as e:
                exc_type, exc_value, exc_tb = sys.exc_info()
                line_no = exc_tb.tb_lineno if exc_tb else "unknown"
                return TaskResult(
                    task_id=task.task_id,
                    status=TaskStatus.FAILED,
                    platform=platform,
                    error=f"Line {line_no}: Failed to start platform: {e}",
                )

        # 获取执行锁
        acquired = self.scheduler.acquire(platform, task.device_id, blocking=False)
        if not acquired:
            return TaskResult(
                task_id=task.task_id,
                status=TaskStatus.FAILED,
                platform=platform,
                error="Device is busy, please retry later",
            )

        context = None
        try:
            self._status = "busy"

            # 检查是否只需要关闭会话（stop_app 动作不需要 context）
            needs_context = self._needs_context(task)

            # 创建执行上下文
            if needs_context:
                try:
                    context = manager.create_context(device_id=task.device_id, options=task.metadata)
                except Exception as e:
                    exc_type, exc_value, exc_tb = sys.exc_info()
                    line_no = exc_tb.tb_lineno if exc_tb else "unknown"
                    return TaskResult(
                        task_id=task.task_id,
                        status=TaskStatus.FAILED,
                        platform=platform,
                        error=f"Line {line_no}: Failed to create context: {e}",
                    )

            # 执行动作列表
            result = self._execute_actions(manager, context, task)

            return result

        except Exception as e:
            exc_type, exc_value, exc_tb = sys.exc_info()
            line_no = exc_tb.tb_lineno if exc_tb else "unknown"
            return TaskResult(
                task_id=task.task_id,
                status=TaskStatus.FAILED,
                platform=platform,
                error=f"Line {line_no}: {e}",
            )

        finally:
            self._status = "online"

            # 清理执行上下文（不关闭会话，保持资源复用）
            if context is not None:
                try:
                    manager.close_context(context, close_session=False)
                except Exception as e:
                    logger.warning(f"Failed to close context: {e}\n{traceback.format_exc()}")

            self.scheduler.release(platform, task.device_id)

    def _execute_actions(
        self,
        manager: PlatformManager,
        context: Any,
        task: Task,
        cancel_event: Optional[threading.Event] = None,
    ) -> TaskResult:
        """
        执行动作列表。

        Args:
            manager: 平台管理器
            context: 执行上下文
            task: 任务对象
            cancel_event: 取消信号（可选）

        Returns:
            TaskResult: 任务结果
        """
        started_at = datetime.now()
        actions_results = []

        for i, action in enumerate(task.actions):
            # 取消检查点：在执行每个 action 之前检查
            if cancel_event and cancel_event.is_set():
                # 只有多个 action 且不是最后一个时才取消
                total_actions = len(task.actions)
                if total_actions > 1 and i < total_actions - 1:
                    logger.info(f"Task cancelled at action {i}: task_id={task.task_id}")
                    return TaskResult(
                        task_id=task.task_id,
                        status=TaskStatus.CANCELLED,
                        platform=task.platform,
                        started_at=started_at,
                        finished_at=datetime.now(),
                        actions=actions_results,
                        error="Task cancelled by user",
                    )

            result = manager.execute_action(context, action)
            result.index = i
            actions_results.append(result)

            # 记录动作执行结果
            logger.debug(
                f"Action result: index={i}, type={action.action_type}, "
                f"status={result.status}, duration={result.duration_ms}ms"
            )

            # 如果动作失败且未配置继续，停止执行
            if result.status != ActionStatus.SUCCESS and not task.metadata.get("continue_on_error"):
                logger.warning(
                    f"Action failed: index={i}, type={action.action_type}, "
                    f"error={result.error}"
                )

                # 获取失败截图
                error_screenshot = None
                try:
                    screenshot_bytes = manager.get_screenshot(context)
                    error_screenshot = base64.b64encode(screenshot_bytes).decode("utf-8")
                except Exception as e:
                    logger.warning(f"Failed to get error screenshot: {e}\n{traceback.format_exc()}")

                return TaskResult(
                    task_id=task.task_id,
                    status=TaskStatus.FAILED,
                    platform=task.platform,
                    started_at=started_at,
                    finished_at=datetime.now(),
                    actions=actions_results,
                    error=result.error,
                    error_screenshot=error_screenshot,
                )

        result = TaskResult(
            task_id=task.task_id,
            status=TaskStatus.SUCCESS,
            platform=task.platform,
            started_at=started_at,
            finished_at=datetime.now(),
            actions=actions_results,
        )

        # 记录任务完成
        logger.info(
            f"Task completed: task_id={task.task_id}, status={result.status}, "
            f"duration={result.duration_ms}ms"
        )

        return result

    # ========== 同步/异步执行方法 ==========

    def execute_sync(
        self,
        platform: str,
        actions: List[Dict[str, Any]],
        device_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        同步执行任务（不生成 task_id）。

        Args:
            platform: 目标平台
            actions: 动作列表
            device_id: 设备 ID

        Returns:
            Dict: 执行结果（不含 task_id）
        """
        # 创建任务对象（不生成 task_id）
        task = Task.create(
            platform=platform,
            actions=actions,
            device_id=device_id,
            generate_id=False,  # 不生成 task_id
        )

        # 执行任务
        try:
            result = self.execute_task(task)
        except Exception as e:
            logger.error(f"execute_task failed: {e}\n{traceback.format_exc()}")
            raise

        # 返回结果（不含 task_id）
        return result.to_dict(include_task_id=False)

    def execute_async(
        self,
        platform: str,
        actions: List[Dict[str, Any]],
        device_id: Optional[str] = None,
    ) -> tuple:
        """
        异步执行任务（生成 task_id）。

        Args:
            platform: 目标平台
            actions: 动作列表
            device_id: 设备 ID

        Returns:
            Tuple[str, str]: (task_id, status)

        Raises:
            TaskConflictError: 设备/平台正被占用
        """
        # 检查冲突
        if self.task_store.is_busy(platform, device_id):
            busy_task_id = self.task_store.get_busy_task_id(platform, device_id)
            raise TaskConflictError(
                f"Device/Platform is busy",
                task_id=busy_task_id,
            )

        # 创建任务对象（生成 task_id）
        task = Task.create(
            platform=platform,
            actions=actions,
            device_id=device_id,
            generate_id=True,
        )

        # 创建任务条目
        entry = TaskEntry(
            task_id=task.task_id,
            task=task,
            status=TaskStatus.RUNNING,
        )

        # 存储任务
        self.task_store.store(entry)

        # 启动后台线程执行
        thread = threading.Thread(
            target=self._run_async_task,
            args=(entry,),
            daemon=True,
        )
        entry.thread = thread
        thread.start()

        logger.info(f"Async task started: task_id={task.task_id}")

        return task.task_id, "running"

    def _run_async_task(self, entry: TaskEntry) -> None:
        """
        后台线程执行异步任务。

        Args:
            entry: 任务条目
        """
        task = entry.task
        platform = task.platform

        try:
            # 获取平台管理器
            manager = self.platform_managers.get(platform)
            if not manager:
                entry.status = TaskStatus.FAILED
                entry.result = TaskResult(
                    task_id=task.task_id,
                    status=TaskStatus.FAILED,
                    platform=platform,
                    error=f"Platform manager not available: {platform}",
                )
                return

            # 前置验证
            validation_result = self._validate_task(task, manager)
            if validation_result:
                entry.status = TaskStatus.FAILED
                entry.result = validation_result
                return

            # 启动平台（如果未启动）
            if not manager.is_available():
                try:
                    manager.start()
                except Exception as e:
                    entry.status = TaskStatus.FAILED
                    entry.result = TaskResult(
                        task_id=task.task_id,
                        status=TaskStatus.FAILED,
                        platform=platform,
                        error=f"Failed to start platform: {e}",
                    )
                    return

            # 获取执行锁
            acquired = self.scheduler.acquire(platform, task.device_id, blocking=False)
            if not acquired:
                entry.status = TaskStatus.FAILED
                entry.result = TaskResult(
                    task_id=task.task_id,
                    status=TaskStatus.FAILED,
                    platform=platform,
                    error="Device is busy, please retry later",
                )
                return

            context = None
            try:
                # 创建执行上下文
                context = manager.create_context(device_id=task.device_id, options=task.metadata)

                # 执行动作列表（支持取消）
                result = self._execute_actions(
                    manager, context, task, cancel_event=entry.cancel_event
                )

                entry.result = result
                entry.status = result.status

            finally:
                # 清理执行上下文（不关闭会话，保持资源复用）
                if context is not None:
                    try:
                        manager.close_context(context, close_session=False)
                    except Exception as e:
                        logger.warning(f"Failed to close context: {e}\n{traceback.format_exc()}")

                self.scheduler.release(platform, task.device_id)

        except Exception as e:
            logger.error(f"Async task error: task_id={task.task_id}, error={e}\n{traceback.format_exc()}")
            entry.status = TaskStatus.FAILED
            entry.result = TaskResult(
                task_id=task.task_id,
                status=TaskStatus.FAILED,
                platform=task.platform,
                error=str(e),
            )

        finally:
            # 更新任务状态到 TaskStore
            self.task_store.update_status(task.task_id, entry.status, entry.result)
            # 清理忙碌状态（任务已完成）
            self.task_store.clear_busy(platform, task.device_id)
            logger.info(
                f"Async task completed: task_id={task.task_id}, status={entry.status}"
            )

    def get_task_result(self, task_id: str) -> Optional[Dict[str, Any]]:
        """
        获取任务结果（一次性查询，查询后销毁）。

        Args:
            task_id: 任务 ID

        Returns:
            Dict | None: 任务结果，不存在返回 None
        """
        entry = self.task_store.pop(task_id)
        if entry is None:
            return None

        # 如果任务还在执行中，返回当前状态
        if entry.status == TaskStatus.RUNNING:
            return {
                "task_id": entry.task_id,
                "status": "running",
            }

        # 返回完整结果
        if entry.result:
            return entry.result.to_dict(include_task_id=True)

        return {
            "task_id": entry.task_id,
            "status": entry.status.value,
        }

    def cancel_task(self, task_id: str) -> tuple:
        """
        取消任务。

        Args:
            task_id: 任务 ID

        Returns:
            Tuple[bool, str]: (是否成功, 消息)
        """
        entry = self.task_store.get(task_id)
        if entry is None:
            return False, "Task not found"

        # 检查任务状态
        if entry.status in [TaskStatus.SUCCESS, TaskStatus.FAILED, TaskStatus.CANCELLED]:
            # 任务已完成，直接删除
            self.task_store.remove(task_id)
            return True, f"Task already {entry.status.value}, removed from store"

        # 设置取消标志
        entry.cancel_event.set()
        entry.status = TaskStatus.CANCELLED

        # 等待线程结束（最多等待 5 秒）
        if entry.thread and entry.thread.is_alive():
            entry.thread.join(timeout=5.0)

        # 删除任务
        self.task_store.remove(task_id)

        logger.info(f"Task cancelled: task_id={task_id}")

        return True, "Task cancelled"


class TaskConflictError(Exception):
    """任务冲突异常。"""

    def __init__(self, message: str, task_id: Optional[str] = None):
        super().__init__(message)
        self.task_id = task_id