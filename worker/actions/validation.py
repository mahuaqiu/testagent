"""Action 请求校验。"""

from __future__ import annotations

import base64
import binascii
from typing import Any

from worker.errors import WorkerError


def validate_action_dict(action: dict[str, Any], *, max_command_length: int = 8192) -> None:
    """校验通用动作参数，保留各执行器的业务语义。"""
    action_type = action.get("action_type")
    if not isinstance(action_type, str) or not action_type.strip():
        raise WorkerError("INVALID_REQUEST", "action_type is required", http_status=400)

    timeout = action.get("timeout", 30000)
    if not isinstance(timeout, int) or timeout < 1:
        raise WorkerError("INVALID_REQUEST", "timeout must be a positive integer", http_status=400)

    threshold = action.get("threshold", 0.9)
    if not isinstance(threshold, (int, float)) or not 0 <= threshold <= 1:
        raise WorkerError("INVALID_REQUEST", "threshold must be between 0 and 1", http_status=400)

    for field in ("index", "anchor_index", "target_index"):
        value = action.get(field)
        if value is not None and (not isinstance(value, int) or value < 0):
            raise WorkerError("INVALID_REQUEST", f"{field} must be non-negative", http_status=400)

    region = action.get("region")
    if region is not None:
        if (
            not isinstance(region, list)
            or len(region) != 4
            or not all(isinstance(value, int) for value in region)
            or region[0] < 0
            or region[1] < 0
            or region[2] <= region[0]
            or region[3] <= region[1]
        ):
            raise WorkerError("INVALID_REQUEST", "region must be [x1,y1,x2,y2]", http_status=400)

    for field in ("duration", "wait", "time", "click_duration", "recording_timeout"):
        value = action.get(field)
        if value is not None and (not isinstance(value, int) or value < 0):
            raise WorkerError("INVALID_REQUEST", f"{field} must be non-negative", http_status=400)

    if action_type.startswith("image_") or action_type.endswith("_image"):
        template = action.get("image_base64")
        if template:
            try:
                base64.b64decode(template, validate=True)
            except (ValueError, binascii.Error) as exc:
                raise WorkerError("INVALID_REQUEST", "image_base64 is invalid", http_status=400) from exc

    if action_type == "cmd_exec":
        command = action.get("value")
        if not isinstance(command, str) or not command.strip():
            raise WorkerError("INVALID_REQUEST", "cmd_exec value is required", http_status=400)
        if len(command) > max_command_length:
            raise WorkerError("INVALID_REQUEST", "cmd_exec value is too long", http_status=400)


def validate_actions(actions: list[dict[str, Any]], *, max_actions: int = 500) -> None:
    """校验任务动作列表。"""
    if not isinstance(actions, list) or not actions:
        raise WorkerError("INVALID_REQUEST", "actions must not be empty", http_status=400)
    if len(actions) > max_actions:
        raise WorkerError("INVALID_REQUEST", "too many actions", http_status=400)
    for action in actions:
        if not isinstance(action, dict):
            raise WorkerError("INVALID_REQUEST", "each action must be an object", http_status=400)
        validate_action_dict(action)
