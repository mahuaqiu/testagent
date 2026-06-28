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
                - value: 输出路径，支持两种格式：
                  - 目录路径（如 d:\\recorder）→ 自动生成文件名
                  - 文件路径（如 d:\\recorder\\test.mp4）→ 直接使用
                  - 为空时使用默认配置目录 + 自动生成文件名
                - fps: 帧率（默认 10）
                - timeout: 超时（毫秒，默认 7200000）
                - monitor: 显示器选择（默认 1，主屏幕）
                - audio: 是否录制音频（默认 false）
                - watermark: 是否开启水印（默认 true）
            context: 执行上下文
        """
        from worker.config import load_config

        # 获取配置
        config = load_config()
        output_dir = config.recording_output_dir

        # 处理路径：支持目录和文件两种格式
        # - 如果 action.value 为空，使用默认目录 + 自动生成文件名
        # - 如果 action.value 是目录（不以 .mp4 结尾），自动生成文件名
        # - 如果 action.value 是文件路径（以 .mp4 结尾），直接使用
        filename = action.value
        if not filename:
            # 没有指定，使用默认目录 + 自动生成文件名
            output_path = os.path.join(output_dir, f"recording_{datetime.now():%Y%m%d_%H%M%S}.mp4")
        elif os.path.isabs(filename):
            if filename.lower().endswith('.mp4'):
                # 是完整文件路径，直接使用
                output_path = filename
            else:
                # 是目录路径，自动生成文件名
                os.makedirs(filename, exist_ok=True)
                output_path = os.path.join(filename, f"recording_{datetime.now():%Y%m%d_%H%M%S}.mp4")
        else:
            # 相对��径，视为文件名
            output_path = os.path.join(output_dir, filename)

        # 确保输出文件的目录存在
        output_file_dir = os.path.dirname(output_path)
        if output_file_dir:
            os.makedirs(output_file_dir, exist_ok=True)

        fps = action.fps or 10
        timeout_ms = action.timeout if action.timeout != 30000 else 7200000
        monitor = action.monitor
        audio = action.audio
        watermark = action.watermark

        # 获取设备 ID
        device_id = getattr(platform, "_current_device", None) or "windows"

        try:
            # 创建 FrameSource（Windows 默认）
            frame_source = WindowsFrameSource(fps=fps, monitor=monitor)

            screen_manager = get_screen_manager(device_id, frame_source)
            success = screen_manager.start_recording(output_path, fps, timeout_ms, audio, monitor, watermark)

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
        停止录屏。幂等操作：没有录制进行时也返回成功。

        Args:
            platform: 平台管理器
            action: 动作参数
            context: 执行上下文
        """
        device_id = getattr(platform, "_current_device", None) or "windows"

        try:
            # 幂等处理：即使没有录制进行，也返回成功
            if device_id not in _screen_managers:
                return ActionResult(
                    number=0,
                    action_type=self.name,
                    status=ActionStatus.SUCCESS,
                    output="",
                )

            screen_manager = _screen_managers[device_id]
            output_path = screen_manager.stop_recording()

            # 无论是否有录制在进行，都返回成功（幂等）
            return ActionResult(
                number=0,
                action_type=self.name,
                status=ActionStatus.SUCCESS,
                output=output_path or "",
            )

        except Exception as e:
            logger.error(f"stop_recording failed: {e}")
            return ActionResult(
                number=0,
                action_type=self.name,
                status=ActionStatus.FAILED,
                error=str(e),
            )