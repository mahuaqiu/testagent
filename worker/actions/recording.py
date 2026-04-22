"""录屏动作处理器。"""

import logging
import os
from datetime import datetime

from worker.actions.base import ActionExecutor
from worker.screen.frame_source import WindowsFrameSource
from worker.screen.manager import get_screen_manager, _screen_managers
from worker.task import Action, ActionResult, ActionStatus

logger = logging.getLogger(__name__)


class StartRecordingAction(ActionExecutor):
    """启动录屏动作。"""

    name = "start_recording"
    requires_context = False

    def execute(self, platform, action: Action, context=None) -> ActionResult:
        """
        启动录屏。

        Args:
            platform: 平台管理器
            action: 动作参数
                - value: 输出文件名（可选，默认自动生成）
                - params.fps: 帧率（默认 10）
                - params.timeout: 超时（毫秒，默认 7200000）
            context: 执行上下文
        """
        from worker.config import load_config

        # 获取配置
        config = load_config()
        output_dir = config.recording_output_dir
        filename = action.value or f"recording_{datetime.now():%Y%m%d_%H%M%S}.mp4"

        # 处理路径
        if os.path.isabs(filename):
            output_path = filename
        else:
            output_path = os.path.join(output_dir, filename)

        # 确保目录存在
        os.makedirs(output_dir, exist_ok=True)

        fps = action.params.get("fps", 10) if action.params else 10
        timeout_ms = action.params.get("timeout", 7200000) if action.params else 7200000

        # 获取设备 ID
        device_id = getattr(platform, "_current_device", None) or "windows"

        try:
            # 创建 FrameSource（Windows 默认）
            frame_source = WindowsFrameSource(fps=fps)

            screen_manager = get_screen_manager(device_id, frame_source)
            success = screen_manager.start_recording(output_path, fps, timeout_ms)

            if success:
                return ActionResult(
                    number=0,
                    action_type=self.name,
                    status=ActionStatus.SUCCESS,
                    output=output_path,
                )
            else:
                return ActionResult(
                    number=0,
                    action_type=self.name,
                    status=ActionStatus.FAILED,
                    error="Recording already in progress",
                )

        except Exception as e:
            logger.error(f"start_recording failed: {e}")
            return ActionResult(
                number=0,
                action_type=self.name,
                status=ActionStatus.FAILED,
                error=str(e),
            )


class StopRecordingAction(ActionExecutor):
    """停止录屏动作。"""

    name = "stop_recording"
    requires_context = False

    def execute(self, platform, action: Action, context=None) -> ActionResult:
        """
        停止录屏。

        Args:
            platform: 平台管理器
            action: 动作参数
            context: 执行上下文
        """
        device_id = getattr(platform, "_current_device", None) or "windows"

        try:
            if device_id not in _screen_managers:
                return ActionResult(
                    number=0,
                    action_type=self.name,
                    status=ActionStatus.FAILED,
                    error="No recording in progress",
                )

            screen_manager = _screen_managers[device_id]
            output_path = screen_manager.stop_recording()

            if output_path:
                return ActionResult(
                    number=0,
                    action_type=self.name,
                    status=ActionStatus.SUCCESS,
                    output=output_path,
                )
            else:
                return ActionResult(
                    number=0,
                    action_type=self.name,
                    status=ActionStatus.FAILED,
                    error="No recording in progress",
                )

        except Exception as e:
            logger.error(f"stop_recording failed: {e}")
            return ActionResult(
                number=0,
                action_type=self.name,
                status=ActionStatus.FAILED,
                error=str(e),
            )