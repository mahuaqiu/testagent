"""Worker 启动恢复逻辑。"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


def recover_interrupted_tasks(repository: Any) -> int:
    """将上次 Worker 未完成的任务标记为 interrupted。

    恢复逻辑只处理持久化状态和活动租约，不尝试自动重放动作。
    是否重试由平台根据任务幂等性决定。
    """
    mark_interrupted = getattr(repository, "mark_interrupted", None)
    if mark_interrupted is None:
        logger.warning("Task repository does not support startup recovery")
        return 0
    count = int(mark_interrupted())
    if count:
        logger.warning("Recovered %d unfinished task(s) as interrupted", count)
    return count

