"""任务附件服务。"""

from worker.artifacts.models import ArtifactRef
from worker.artifacts.service import ArtifactService

__all__ = ["ArtifactRef", "ArtifactService"]
